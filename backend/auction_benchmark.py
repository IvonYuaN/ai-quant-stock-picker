"""Read-only adapter for TickFlow's short-term auction benchmark feed."""
from __future__ import annotations

from datetime import date, timedelta
import logging
import math
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://fuyao.aicubes.cn"
_BENCHMARK_PATH = "/api/a-share/auction/short-term-benchmark"
_SYMBOL_RE = re.compile(r"^(\d{6})(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)
_MAX_ITEMS = 1000


def _empty_payload(state: str, message: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": state,
        "source": "fuyao",
        "requested_date": None,
        "trade_date": None,
        "count": 0,
        "items": [],
    }
    if message:
        payload["message"] = message
    return payload


def _normalize_items(raw_items: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw_items, list) or len(raw_items) > _MAX_ITEMS:
        return None
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        raw_symbol = str(raw.get("ticker") or raw.get("thscode") or "").strip()
        match = _SYMBOL_RE.fullmatch(raw_symbol)
        if match is None:
            continue
        auction_pct: float | None = None
        try:
            candidate = float(raw.get("auction_pct"))
            if math.isfinite(candidate):
                auction_pct = candidate
        except (TypeError, ValueError):
            pass
        tags = raw.get("tags")
        items.append(
            {
                "symbol": match.group(1),
                "thscode": str(raw.get("thscode") or raw_symbol)[:24],
                "name": str(raw.get("name") or match.group(1))[:80],
                "auction_pct": auction_pct,
                "tags": [str(tag)[:80] for tag in tags[:20] if tag is not None]
                if isinstance(tags, list)
                else [],
                "day0_oc": None,
                "d1_pct": None,
            }
        )
    return items


def _enrich_raw_returns(
    items: list[dict[str, Any]], trade_date: date
) -> tuple[list[dict[str, Any]], str | None]:
    """Attach observed raw-price returns; cross-day results require PIT factors."""
    if not items:
        return items, None
    try:
        from aqsp.data.source_factory import build_data_source

        source = build_data_source("sqlite_db", cache=None)
        latest = source.get_latest_daily_date()
        if latest is None or latest < trade_date:
            return items, latest.isoformat() if latest else None
        end = min(latest, trade_date + timedelta(days=14))
        symbols = [str(item["symbol"]) for item in items]
        frames = source.fetch_daily(symbols, trade_date, end, adjust="")
        if not frames:
            return items, latest.isoformat()
        session_dates = source.get_available_daily_dates(trade_date, end)

        daily_rows: dict[str, dict[date, tuple[float, float]]] = {}
        for symbol, frame in frames.items():
            if frame is None or frame.empty or not {"date", "open", "close"} <= set(frame):
                continue
            rows: dict[date, tuple[float, float]] = {}
            for raw_date, raw_open, raw_close in zip(
                frame["date"], frame["open"], frame["close"], strict=False
            ):
                try:
                    parsed_date = date.fromisoformat(str(raw_date)[:10])
                    open_price = float(raw_open)
                    close_price = float(raw_close)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(open_price) or not math.isfinite(close_price):
                    continue
                if open_price <= 0 or close_price <= 0:
                    continue
                rows[parsed_date] = (open_price, close_price)
            daily_rows[str(symbol)] = rows

        next_session = min((day for day in session_dates if day > trade_date), default=None)
        required_dates = [trade_date, next_session] if next_session else [trade_date]
        factors = source.get_point_in_time_adjustment_factors(symbols, required_dates)
        for item in items:
            rows = daily_rows.get(str(item["symbol"]), {})
            day0 = rows.get(trade_date)
            if day0:
                item["day0_oc"] = day0[1] / day0[0] - 1.0
            if day0 and next_session:
                next_row = rows.get(next_session)
                symbol_factors = factors.get(str(item["symbol"]), {})
                current_factor = symbol_factors.get(trade_date)
                next_factor = symbol_factors.get(next_session)
                if next_row and current_factor and next_factor:
                    current_value = float(current_factor.value)
                    next_value = float(next_factor.value)
                    if (
                        math.isfinite(current_value)
                        and math.isfinite(next_value)
                        and current_value > 0
                        and next_value > 0
                    ):
                        item["d1_pct"] = (
                            next_row[1] * next_value / (day0[1] * current_value) - 1.0
                        )
        return items, max(session_dates).isoformat() if session_dates else latest.isoformat()
    except Exception as exc:  # noqa: BLE001 -- benchmark remains useful without local returns
        logger.warning(
            "auction_benchmark_return_enrichment_unavailable",
            extra={"error_type": type(exc).__name__},
        )
        return items, None


def get_auction_benchmark(requested_date: date | None = None) -> dict[str, Any]:
    """Fetch one benchmark date; missing credentials and provider failures stay explicit."""
    api_key = os.environ.get("FUYAO_API_KEY", "").strip()
    if not api_key:
        result = _empty_payload("source_unavailable", "需要在服务端配置 FUYAO_API_KEY")
        result["requested_date"] = requested_date.isoformat() if requested_date else None
        return result

    params = {"date": requested_date.isoformat()} if requested_date else {}
    try:
        response = requests.get(
            f"{_BASE_URL}{_BENCHMARK_PATH}",
            params=params,
            headers={"X-api-key": api_key},
            timeout=10,
        )
        response.raise_for_status()
        envelope = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "auction_benchmark_provider_unavailable",
            extra={"error_type": type(exc).__name__},
        )
        result = _empty_payload("no_data", "竞价数据源暂不可用；请重试")
        result["requested_date"] = requested_date.isoformat() if requested_date else None
        return result

    if not isinstance(envelope, dict) or envelope.get("code") not in (None, 0, "0"):
        logger.warning("auction_benchmark_invalid_envelope")
        result = _empty_payload("no_data", "竞价数据源返回无效响应；请重试")
        result["requested_date"] = requested_date.isoformat() if requested_date else None
        return result

    data = envelope.get("data")
    if not isinstance(data, dict):
        result = _empty_payload("no_data", "竞价数据源未返回名单；请重试")
        result["requested_date"] = requested_date.isoformat() if requested_date else None
        return result
    try:
        trade_date = date.fromisoformat(str(data.get("date")))
    except ValueError:
        result = _empty_payload("no_data", "竞价数据日期缺失或无效；请重试")
        result["requested_date"] = requested_date.isoformat() if requested_date else None
        return result

    items = _normalize_items(data.get("item"))
    if items is None:
        result = _empty_payload("no_data", "竞价名单格式无效；请重试")
        result["requested_date"] = requested_date.isoformat() if requested_date else None
        return result
    items, returns_as_of = _enrich_raw_returns(items, trade_date)
    return {
        "state": "ok",
        "source": "fuyao",
        "requested_date": requested_date.isoformat() if requested_date else None,
        "trade_date": trade_date.isoformat(),
        "count": len(items),
        "returns_source": "sqlite_db" if returns_as_of else None,
        "returns_price_mode": "raw" if returns_as_of else None,
        "d1_adjustment_method": "pit_factor_ratio",
        "returns_as_of": returns_as_of,
        "items": items,
    }
