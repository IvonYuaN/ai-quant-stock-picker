from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from aqsp.indicators import normalize_ohlcv


def load_csv(path: str | Path) -> dict[str, pd.DataFrame]:
    df = normalize_ohlcv(pd.read_csv(path, dtype={"symbol": str, "代码": str}))
    if df["symbol"].eq("").all():
        return {"CSV": df}
    return {str(symbol): part.reset_index(drop=True) for symbol, part in df.groupby("symbol")}


def fetch_akshare(symbols: list[str], days: int = 260, adjust: str = "qfq") -> dict[str, pd.DataFrame]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("akshare is not installed; run: pip install -e '.[data]'") from exc

    end = date.today()
    start = end - timedelta(days=max(days * 2, 365))
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjust,
        )
        if df.empty:
            continue
        df["代码"] = symbol
        out[symbol] = normalize_ohlcv(df).tail(days).reset_index(drop=True)
    return out
