"""Market sentiment quantitative data source.

Provides limit-up/limit-down pool statistics and a composite sentiment
z-score.  Like northbound and margin factors, the sentiment factor enters
ledger context and debate context only — it never directly modifies the
deterministic score.

Data sources (akshare):
- ``stock_zt_pool_em`` — 涨停板池 (limit-up pool)
- ``stock_zt_pool_zbgc_em`` — 炸板股池 (broken limit-up pool)
- ``stock_zt_pool_previous_em`` — 昨日涨停股池 (yesterday limit-up pool)

All fetches degrade gracefully to cache on failure.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd

from aqsp.core.time import now_shanghai

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data"
_CACHE_FILE = _CACHE_DIR / "sentiment_history.csv"

_ZT_COL_MAP: dict[str, str] = {
    "日期": "date",
    "代码": "symbol",
    "名称": "name",
    "涨跌幅": "pct_change",
    "成交额": "amount",
    "流通市值": "circ_mv",
    "换手率": "turnover",
    "封板资金": "seal_money",
    "炸板次数": "broken_count",
    "涨停统计": "zt_stat",
    "连板数": "consecutive_days",
    "所属行业": "industry",
}


def _load_cache(cache_path: Path) -> pd.DataFrame:
    if not cache_path.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "limit_up_count",
                "broken_count",
                "max_consecutive",
                "total_amount",
            ]
        )
    try:
        df = pd.read_csv(cache_path, dtype={"date": str})
        for col in (
            "limit_up_count",
            "broken_count",
            "max_consecutive",
            "total_amount",
        ):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(
            columns=[
                "date",
                "limit_up_count",
                "broken_count",
                "max_consecutive",
                "total_amount",
            ]
        )


def _save_cache(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df = (
        df.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    df.to_csv(cache_path, index=False, encoding="utf-8")


def _try_fetch_zt_pool(date_str: str) -> dict[str, int | float] | None:
    """Fetch limit-up pool stats for a single date.

    Returns a dict with limit_up_count, max_consecutive, total_amount,
    broken_count, or None on failure.
    """
    try:
        import akshare as ak
    except ImportError:
        return None

    stats: dict[str, int | float] = {
        "limit_up_count": 0,
        "max_consecutive": 0,
        "total_amount": 0.0,
        "broken_count": 0,
    }

    try:
        raw = ak.stock_zt_pool_em(date=date_str)
        if raw is not None and not raw.empty:
            df = raw.rename(columns=_ZT_COL_MAP)
            stats["limit_up_count"] = len(df)
            if "consecutive_days" in df.columns:
                cons = pd.to_numeric(df["consecutive_days"], errors="coerce")
                stats["max_consecutive"] = int(cons.max()) if cons.notna().any() else 0
            if "amount" in df.columns:
                amt = pd.to_numeric(df["amount"], errors="coerce")
                stats["total_amount"] = float(amt.sum()) if amt.notna().any() else 0.0
    except Exception as exc:
        logger.debug("涨停板池抓取失败 %s: %s", date_str, exc)

    try:
        raw_zb = ak.stock_zt_pool_zbgc_em(date=date_str)
        if raw_zb is not None and not raw_zb.empty:
            stats["broken_count"] = len(raw_zb)
    except Exception as exc:
        logger.debug("炸板池抓取失败 %s: %s", date_str, exc)

    if stats["limit_up_count"] == 0 and stats["broken_count"] == 0:
        return None
    return stats


def fetch_sentiment_data(
    days: int = 60,
    cache_path: Path | None = None,
) -> pd.DataFrame:
    """Fetch market sentiment statistics for recent trading days.

    Returns a DataFrame with columns:
    date, limit_up_count, broken_count, max_consecutive, total_amount
    """
    cache_path = cache_path or _CACHE_FILE
    cached = _load_cache(cache_path)

    today = now_shanghai().date()
    new_rows: list[dict[str, object]] = []
    cached_dates = set(cached["date"].astype(str) if not cached.empty else [])

    for offset in range(0, 10):
        d = today - timedelta(days=offset)
        date_str = d.strftime("%Y%m%d")
        date_iso = d.strftime("%Y-%m-%d")
        if date_iso in cached_dates:
            continue
        stats = _try_fetch_zt_pool(date_str)
        if stats is not None:
            new_rows.append({"date": date_iso, **stats})

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        combined = (
            pd.concat([cached, new_df], ignore_index=True)
            if not cached.empty
            else new_df
        )
        _save_cache(combined, cache_path)
        cached = combined

    if not cached.empty and "date" in cached.columns:
        cached = cached.sort_values("date").tail(days).reset_index(drop=True)
    return cached


def compute_sentiment_factor(
    df: pd.DataFrame | None = None,
    window: int = 5,
    cache_path: Path | None = None,
) -> float:
    """Compute a composite sentiment z-score from limit-up counts.

    Returns the z-score of the latest day's limit-up count relative to
    the full series mean/std.  Positive = sentiment running hot,
    negative = sentiment cold.  Returns 0.0 on insufficient data.
    """
    if df is None:
        df = fetch_sentiment_data(days=window * 4, cache_path=cache_path)
    if df is None or df.empty or "limit_up_count" not in df.columns:
        return 0.0
    series = df["limit_up_count"].dropna()
    if len(series) < window:
        return 0.0
    mean = series.mean()
    std = series.std()
    if std == 0 or np.isnan(std):
        return 0.0
    z = (series.iloc[-1] - mean) / std
    if np.isnan(z) or np.isinf(z):
        return 0.0
    return round(float(z), 4)


def get_sentiment_summary(
    df: pd.DataFrame | None = None,
    cache_path: Path | None = None,
) -> dict[str, object]:
    """Return a sentiment summary dict for context display.

    Keys: limit_up_count, broken_count, max_consecutive,
    sentiment_z, temperature (hot/neutral/cold).
    """
    if df is None:
        df = fetch_sentiment_data(cache_path=cache_path)
    if df is None or df.empty:
        return {
            "limit_up_count": 0,
            "broken_count": 0,
            "max_consecutive": 0,
            "sentiment_z": 0.0,
            "temperature": "unknown",
        }
    latest = df.iloc[-1]
    z = compute_sentiment_factor(df)
    if z > 1.0:
        temperature = "hot"
    elif z < -1.0:
        temperature = "cold"
    else:
        temperature = "neutral"
    return {
        "limit_up_count": int(latest.get("limit_up_count", 0)),
        "broken_count": int(latest.get("broken_count", 0)),
        "max_consecutive": int(latest.get("max_consecutive", 0)),
        "sentiment_z": z,
        "temperature": temperature,
    }
