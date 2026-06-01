from __future__ import annotations

from datetime import date, timedelta

from aqsp.core.errors import DataError
from aqsp.data.tushare_pit import TusharePitClient


def load_optional_index_constituents(
    index_code: str,
    as_of: date,
    *,
    lookback_days: int = 31,
    client: TusharePitClient | None = None,
) -> list[str]:
    try:
        pit_client = client or TusharePitClient()
    except (RuntimeError, ValueError):
        return []
    try:
        weights = pit_client.fetch_index_weights(
            index_code,
            as_of - timedelta(days=lookback_days),
            as_of,
        )
    except DataError:
        return []
    if (
        weights.empty
        or "trade_date" not in weights.columns
        or "symbol" not in weights.columns
    ):
        return []

    normalized = weights.copy()
    latest_trade_date = str(normalized["trade_date"].dropna().max())
    latest = normalized[normalized["trade_date"] == latest_trade_date].copy()
    if latest.empty:
        return []
    if "weight" in latest.columns:
        latest["weight"] = latest["weight"].fillna(0.0)
        latest = latest.sort_values(["weight", "symbol"], ascending=[False, True])
    else:
        latest = latest.sort_values("symbol")

    seen: set[str] = set()
    symbols: list[str] = []
    for symbol in latest["symbol"].astype(str):
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols
