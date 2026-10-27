"""Bounded current-industry evidence for position explanations, never backtests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

import astock


@dataclass(frozen=True)
class PositionContextSourceConfig:
    # Match the existing industry clist page size; bound optional explanation I/O.
    batch_size: int = 100
    timeout_seconds: int = 4
    max_industry_pages: int = 8


def _rows(payload: Any) -> list[dict]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    diff = data.get("diff") if isinstance(data, Mapping) else None
    if isinstance(diff, Mapping):
        diff = list(diff.values())
    return [row for row in diff if isinstance(row, dict)] if isinstance(diff, list) else []


def _fetch(codes: tuple[str, ...], config: PositionContextSourceConfig) -> dict:
    memberships = _rows(astock.em_get(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "secids": ",".join(f"{1 if astock.get_prefix(code) == 'sh' else 0}.{code}" for code in codes),
            "fields": "f12,f100,f124",
            "fltt": 2,
        },
        headers={"User-Agent": astock.UA},
        timeout=config.timeout_seconds,
    ).json())
    if not memberships:
        return {}
    sectors: list[dict] = []
    for page in range(1, config.max_industry_pages + 1):
        payload = astock.em_get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params=_industry_params(page, config), headers={"User-Agent": astock.UA},
            timeout=config.timeout_seconds,
        ).json()
        if not _append_industry_page(payload, memberships, sectors, config):
            break
    else:
        raise ValueError("行业依据超过分页预算")
    return _combine_rows(codes, memberships, sectors)


def _industry_params(page: int, config: PositionContextSourceConfig) -> dict[str, Any]:
    return {"pn": page, "pz": config.batch_size, "po": 0, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f12", "fs": "m:90+t:2", "fields": "f12,f14,f3,f124"}


def _append_industry_page(payload: Any, memberships: list[dict], sectors: list[dict],
                          config: PositionContextSourceConfig) -> bool:
    """Return whether unmatched source memberships require another bounded page."""
    rows = _rows(payload)
    known = {row.get('f14') for row in sectors}
    names = [row.get('f14') for row in rows]
    if any(not isinstance(name, str) or not name.strip() or name in known for name in names) or len(set(names)) != len(names):
        raise ValueError('行业分页重复或名称无效')
    sectors.extend(rows)
    targets = {row['f100'] for row in memberships if isinstance(row.get('f100'), str) and row['f100'].strip()}
    if targets <= {row.get('f14') for row in sectors}:
        return False
    data = payload.get('data') if isinstance(payload, Mapping) else None
    total = data.get('total') if isinstance(data, Mapping) else None
    if total is not None:
        if type(total) is not int or total < len(sectors):
            raise ValueError('行业源总量无效')
        if len(sectors) == total:
            return False
        if len(rows) != config.batch_size:
            raise ValueError('行业分页不完整')
        return True
    return len(rows) == config.batch_size


def _combine_rows(codes: tuple[str, ...], memberships: list[dict], sectors: list[dict]) -> dict:
    by_name = {row.get("f14"): row for row in sectors if isinstance(row.get("f14"), str)}
    result = {}
    for row in memberships:
        code, industry = str(row.get("f12") or ""), row.get("f100")
        if code not in codes or not isinstance(industry, str) or not industry.strip():
            continue
        sector = by_name.get(industry)
        if sector is None:
            continue
        result[code] = {
            "industry": industry,
            "membership_as_of": astock.epoch_iso(row.get("f124")),
            "name": industry,
            "change_pct": sector.get("f3"),
            "as_of": astock.epoch_iso(sector.get("f124")),
            "source": "东方财富行业行情（同源个股行业归属）",
        }
    return result


def load_position_sectors(codes: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
    """Use the existing shared TTL/negative cache; failures remain unknown.

    At most one bounded stock batch and one industry batch per cache refresh.
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
        import json
        chunks, size = [], 0
        async with client.stream("GET", url, params=params, headers={"User-Agent": astock.UA}) as response:
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
    sectors: list[dict] = []
    for page in range(1, config.max_industry_pages + 1):
        payload = await get("https://push2.eastmoney.com/api/qt/clist/get", _industry_params(page, config))
        if not _append_industry_page(payload, memberships, sectors, config):
            break
    else:
        raise ValueError("行业依据超过分页预算")
    return _combine_rows(symbols, memberships, sectors)
