from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from aqsp.core.time import now_shanghai

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data"
_CACHE_FILE = _CACHE_DIR / "margin_history.csv"

_MARGIN_DETAIL_COL_MAP: dict[str, str] = {
    "日期": "date",
    "融资余额(元)": "margin_balance",
    "融资买入额(元)": "margin_buy",
    "融券卖出量(股)": "short_sell",
    "证券代码": "symbol",
    "标的证券代码": "symbol",
}


def _load_cache(cache_path: Path) -> pd.DataFrame:
    if not cache_path.exists():
        return pd.DataFrame(
            columns=["date", "symbol", "margin_balance", "margin_buy", "short_sell"]
        )
    try:
        df = pd.read_csv(cache_path, dtype={"date": str, "symbol": str})
        for col in ("margin_balance", "margin_buy", "short_sell"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(
            columns=["date", "symbol", "margin_balance", "margin_buy", "short_sell"]
        )


def _save_cache(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df = (
        df.drop_duplicates(subset=["date", "symbol"], keep="last")
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )
    df.to_csv(cache_path, index=False, encoding="utf-8")


def _try_fetch_single_date(date_str: str) -> pd.DataFrame | None:
    try:
        import akshare as ak
    except ImportError:
        return None

    for api_name in ("stock_margin_detail_szse", "stock_margin_detail_sse"):
        try:
            fn = getattr(ak, api_name, None)
            if fn is None:
                continue
            raw = fn(date=date_str)
            if raw is None or raw.empty:
                continue
            df = raw.rename(columns=_MARGIN_DETAIL_COL_MAP)
            keep = [
                c
                for c in (
                    "date",
                    "symbol",
                    "margin_balance",
                    "margin_buy",
                    "short_sell",
                )
                if c in df.columns
            ]
            df = df[keep].copy()
            if "date" not in df.columns:
                df["date"] = date_str
            else:
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            if "symbol" not in df.columns:
                continue
            for col in ("margin_balance", "margin_buy", "short_sell"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        except Exception:
            continue
    return None


def fetch_margin_data(
    symbol: str,
    days: int = 60,
    cache_path: Path | None = None,
) -> pd.DataFrame:
    cache_path = cache_path or _CACHE_FILE
    cached = _load_cache(cache_path)
    if not cached.empty and "symbol" in cached.columns:
        symbol_cached = cached[cached["symbol"] == symbol]
    else:
        symbol_cached = pd.DataFrame(
            columns=["date", "symbol", "margin_balance", "margin_buy", "short_sell"]
        )

    today = now_shanghai().date()
    new_frames: list[pd.DataFrame] = []
    for offset in range(1, 8):
        date_str = (today - timedelta(days=offset)).strftime("%Y%m%d")
        df = _try_fetch_single_date(date_str)
        if df is not None and not df.empty:
            new_frames.append(df)

    if new_frames:
        fetched = pd.concat(new_frames, ignore_index=True)
        if not cached.empty:
            combined = pd.concat([cached, fetched], ignore_index=True)
        else:
            combined = fetched
        _save_cache(combined, cache_path)
        result = (
            combined[combined["symbol"] == symbol]
            if "symbol" in combined.columns
            else pd.DataFrame()
        )
    else:
        result = symbol_cached

    if not result.empty and "date" in result.columns:
        result = result.sort_values("date").tail(days).reset_index(drop=True)
    return result


def compute_margin_factor(
    symbol: str,
    window: int = 5,
    cache_path: Path | None = None,
) -> float:
    df = fetch_margin_data(symbol, days=window * 3, cache_path=cache_path)
    if df is None or df.empty or "margin_balance" not in df.columns:
        return 0.0
    series = df["margin_balance"].dropna()
    if len(series) < window:
        return 0.0
    recent = series.iloc[-window:]
    if recent.iloc[0] == 0:
        return 0.0
    change = (recent.iloc[-1] - recent.iloc[0]) / abs(recent.iloc[0])
    if np.isnan(change) or np.isinf(change):
        return 0.0
    return round(float(change), 4)
