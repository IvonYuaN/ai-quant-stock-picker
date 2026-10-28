"""Bounded current-industry evidence for position explanations, never backtests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

import astock
from industry_source_contract import (
    INDUSTRY_PARAMS as _INDUSTRY_PARAMS,
    INDUSTRY_REFERER,
    INDUSTRY_SOURCE,
    INDUSTRY_URL as _INDUSTRY_URL,
    IndustrySourceLimits,
    parse_industry_rows,
)


@dataclass(frozen=True)
class PositionContextSourceConfig(IndustrySourceLimits):
    # Bound public explanation I/O, independently of trading thresholds.
    batch_size: int = 100


# The clist endpoint can reject a request while ulist still works. Use the same
# public dataapi contract already used by aqsp.data.concept_board (#333), with
# industry rather than concept membership. The pure parser owns the units.
_INDUSTRY_HEADERS = {"User-Agent": astock.UA, "Referer": INDUSTRY_REFERER}


def _rows(payload: Any) -> list[dict]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    diff = data.get("diff") if isinstance(data, Mapping) else None
    if isinstance(diff, Mapping):
        diff = list(diff.values())
    return [row for row in diff if isinstance(row, dict)] if isinstance(diff, list) else []


def _fetch(codes: tuple[str, ...], config: PositionContextSourceConfig) -> dict:
    def get(url: str, *, params: dict, headers: dict) -> Any:
        response = astock.em_get(url, params=params, headers=headers, timeout=config.timeout_seconds)
        response.raise_for_status()
        raw = response.content
        if len(raw) > config.max_response_bytes:
            raise ValueError("行业依据响应超过大小预算")
        return json.loads(raw)

    memberships = _rows(get(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "secids": ",".join(f"{1 if astock.get_prefix(code) == 'sh' else 0}.{code}" for code in codes),
            "fields": "f12,f100,f124",
            "fltt": 2,
        },
        headers={"User-Agent": astock.UA},
    ))
    if not memberships:
        return {}
    sectors = _parse_industries(get(_INDUSTRY_URL, params=_INDUSTRY_PARAMS,
                                    headers=_INDUSTRY_HEADERS), config)
    return _combine_rows(codes, memberships, sectors)


def _parse_industries(payload: Any, config: PositionContextSourceConfig) -> list[dict]:
    """Validate the complete single response and normalize dataapi percentages."""
    return parse_industry_rows(payload, max_rows=config.max_industry_rows)


def _combine_rows(codes: tuple[str, ...], memberships: list[dict], sectors: list[dict]) -> dict:
    by_name = {row.get("f14"): row for row in sectors if isinstance(row.get("f14"), str)}
    result = {}
    seen: set[str] = set()
    for row in memberships:
        code, industry = str(row.get("f12") or ""), row.get("f100")
        if code not in codes or not isinstance(industry, str) or not industry.strip():
            continue
        if code in seen:
            raise ValueError('同代码行业归属重复，不能确认唯一板块')
        seen.add(code)
        sector = by_name.get(industry)
        if sector is None:
            continue
        result[code] = {
            "industry": industry,
            "membership_as_of": astock.epoch_iso(row.get("f124")),
            "name": industry,
            "change_pct": sector.get("f3"),
            "as_of": astock.epoch_iso(sector.get("f124")),
            "source": INDUSTRY_SOURCE + "（同源个股行业归属）",
        }
    return result


def load_position_sectors(codes: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
    """Use the existing shared TTL/negative cache; failures remain unknown.

    At most one stock batch and one complete industry response per refresh.
    Source times remain intact even when SWR returns an older cached response.
    No per-stock requests, inferred concepts, portfolio writes or akshare import.
    """
    import market

    config = PositionContextSourceConfig()
    symbols = tuple(sorted({code for code in codes if isinstance(code, str) and len(code) == 6 and code.isdigit()}))
    if not symbols or len(symbols) > config.batch_size:
        return {}
    digest = hashlib.sha256(",".join(symbols).encode()).hexdigest()

    def fetch_optional() -> dict:
        try:
            return _fetch(symbols, config)
        except Exception:  # noqa: BLE001 -- negative-cache upstream failures too
            return {}

    try:
        result = market._cached(
            f"position_sectors_{digest}", fetch_optional
        )
        return {code: row for code, row in result.items() if code in symbols and isinstance(row, Mapping)} if isinstance(result, Mapping) else {}
    except Exception:  # noqa: BLE001 -- optional evidence never blocks hard risk checks
        return {}


async def fetch_position_sectors(codes: Sequence[str], client: httpx.AsyncClient, *, max_response_bytes: int) -> dict:
    """Fetch the existing two batch endpoints without retry/proxy fallback.

    The caller owns the shared cache lock and cancellable total request budget.
    This variant does not invoke the legacy synchronous transport or spawn work.
    """
    config = PositionContextSourceConfig()
    symbols = tuple(sorted({code for code in codes if isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()}))
    if not symbols or len(symbols) > config.batch_size:
        return {}

    async def get(url: str, params: dict[str, Any]) -> Any:
        chunks, size = [], 0
        async with client.stream("GET", url, params=params,
                                 headers=_INDUSTRY_HEADERS if url == _INDUSTRY_URL else {"User-Agent": astock.UA}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_response_bytes:
                    raise ValueError("行业依据响应超过大小预算")
                chunks.append(chunk)
        return json.loads(b"".join(chunks))

    memberships = _rows(await get("https://push2.eastmoney.com/api/qt/ulist.np/get", {
        "secids": ",".join(f"{1 if astock.get_prefix(code) == 'sh' else 0}.{code}" for code in symbols),
        "fields": "f12,f100,f124", "fltt": 2,
    }))
    if not memberships:
        return {}
    sectors = _parse_industries(await get(_INDUSTRY_URL, _INDUSTRY_PARAMS), config)
    return _combine_rows(symbols, memberships, sectors)
