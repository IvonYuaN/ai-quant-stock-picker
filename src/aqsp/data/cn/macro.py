"""China macroeconomic data source.

Fetches key macro indicators (CPI, PPI, PMI, M2, LPR) from akshare and
produces a composite macro climate signal.  Like northbound, margin, and
sentiment factors, macro data enters ledger context and debate context
only — it never directly modifies the deterministic score.

Data sources (akshare):
- ``macro_china_cpi_yearly`` — CPI year-over-year
- ``macro_china_pp_yearly`` — PPI year-over-year
- ``macro_china_pmi_yearly`` — Manufacturing PMI
- ``macro_china_money_supply`` — M2 money supply + YoY growth
- ``macro_china_lpr`` — LPR 1Y/5Y rates

All fetches degrade gracefully to cache on failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data"
_CACHE_FILE = _CACHE_DIR / "macro_history.csv"

_PMI_EXPANSION_THRESHOLD = 50.0
_M2_GROWTH_HEALTHY_RANGE = (8.0, 14.0)
_CPI_HEALTHY_RANGE = (0.0, 3.0)


def _load_cache(cache_path: Path) -> pd.DataFrame:
    if not cache_path.exists():
        return pd.DataFrame(columns=["date", "indicator", "value", "prev_value"])
    try:
        df = pd.read_csv(cache_path, dtype={"date": str, "indicator": str})
        for col in ("value", "prev_value"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "indicator", "value", "prev_value"])


def _save_cache(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df = (
        df.drop_duplicates(subset=["date", "indicator"], keep="last")
        .sort_values(["indicator", "date"])
        .reset_index(drop=True)
    )
    df.to_csv(cache_path, index=False, encoding="utf-8")


def _try_fetch_cpi() -> list[dict[str, object]]:
    """Fetch latest CPI year-over-year."""
    try:
        import akshare as ak

        raw = ak.macro_china_cpi_yearly()
        if raw is None or raw.empty:
            return []
        df = raw.rename(columns={"日期": "date", "今值": "value", "前值": "prev_value"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df["indicator"] = "cpi_yoy"
        rows: list[dict[str, object]] = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "indicator": "cpi_yoy",
                    "value": float(row.get("value", np.nan)),
                    "prev_value": float(row.get("prev_value", np.nan)),
                }
            )
        return rows
    except Exception as exc:
        logger.debug("CPI 抓取失败: %s", exc)
        return []


def _try_fetch_pmi() -> list[dict[str, object]]:
    """Fetch latest manufacturing PMI."""
    try:
        import akshare as ak

        raw = ak.macro_china_pmi_yearly()
        if raw is None or raw.empty:
            return []
        df = raw.rename(columns={"日期": "date", "今值": "value", "前值": "prev_value"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        rows: list[dict[str, object]] = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "indicator": "pmi",
                    "value": float(row.get("value", np.nan)),
                    "prev_value": float(row.get("prev_value", np.nan)),
                }
            )
        return rows
    except Exception as exc:
        logger.debug("PMI 抓取失败: %s", exc)
        return []


def _try_fetch_m2() -> list[dict[str, object]]:
    """Fetch latest M2 year-over-year growth."""
    try:
        import akshare as ak

        raw = ak.macro_china_money_supply()
        if raw is None or raw.empty:
            return []
        m2_col = "货币和准货币(M2)-同比增长"
        date_col = "月份"
        if m2_col not in raw.columns or date_col not in raw.columns:
            return []
        df = raw[[date_col, m2_col]].copy()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(
            df["date"].str.replace("份", "", regex=False), errors="coerce"
        )
        df = df.dropna(subset=["date", "value"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        df["indicator"] = "m2_yoy"
        df["prev_value"] = df["value"].shift(1)
        rows: list[dict[str, object]] = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "indicator": "m2_yoy",
                    "value": float(row["value"]),
                    "prev_value": float(row.get("prev_value", np.nan)),
                }
            )
        return rows
    except Exception as exc:
        logger.debug("M2 抓取失败: %s", exc)
        return []


def _try_fetch_lpr() -> list[dict[str, object]]:
    """Fetch latest LPR 1Y rate."""
    try:
        import akshare as ak

        raw = ak.macro_china_lpr()
        if raw is None or raw.empty:
            return []
        df = raw[["TRADE_DATE", "LPR1Y"]].copy()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "value"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        df["indicator"] = "lpr_1y"
        df["prev_value"] = df["value"].shift(1)
        rows: list[dict[str, object]] = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "indicator": "lpr_1y",
                    "value": float(row["value"]),
                    "prev_value": float(row.get("prev_value", np.nan)),
                }
            )
        return rows
    except Exception as exc:
        logger.debug("LPR 抓取失败: %s", exc)
        return []


def fetch_macro_data(
    cache_path: Path | None = None,
) -> pd.DataFrame:
    """Fetch macroeconomic indicators and cache them.

    Returns a DataFrame with columns:
    date, indicator, value, prev_value
    """
    cache_path = cache_path or _CACHE_FILE
    cached = _load_cache(cache_path)
    cached_keys = set()
    if not cached.empty:
        for _, row in cached.iterrows():
            cached_keys.add((str(row["date"]), str(row["indicator"])))

    new_rows: list[dict[str, object]] = []
    for fetch_fn in (_try_fetch_cpi, _try_fetch_pmi, _try_fetch_m2, _try_fetch_lpr):
        try:
            rows = fetch_fn()
        except Exception as exc:
            logger.debug("宏观数据抓取异常: %s", exc)
            rows = []
        for row in rows:
            key = (str(row["date"]), str(row["indicator"]))
            if key not in cached_keys:
                new_rows.append(row)

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        combined = (
            pd.concat([cached, new_df], ignore_index=True)
            if not cached.empty
            else new_df
        )
        _save_cache(combined, cache_path)
        cached = combined

    return cached.copy()


def get_macro_summary(
    df: pd.DataFrame | None = None,
    cache_path: Path | None = None,
) -> dict[str, object]:
    """Return a macro climate summary for context display.

    Keys: cpi_yoy, pmi, m2_yoy, lpr_1y, climate (expansion/contraction/neutral),
    climate_detail.
    """
    if df is None:
        df = fetch_macro_data(cache_path=cache_path)
    if df is None or df.empty:
        return {
            "cpi_yoy": None,
            "pmi": None,
            "m2_yoy": None,
            "lpr_1y": None,
            "climate": "unknown",
            "climate_detail": "宏观数据不可用",
        }

    def _latest(indicator: str) -> float | None:
        sub = df[df["indicator"] == indicator].dropna(subset=["value"])
        if sub.empty:
            return None
        sub = sub.sort_values("date")
        return float(sub.iloc[-1]["value"])

    cpi = _latest("cpi_yoy")
    pmi = _latest("pmi")
    m2 = _latest("m2_yoy")
    lpr = _latest("lpr_1y")

    signals: list[str] = []
    score = 0

    if pmi is not None:
        if pmi >= _PMI_EXPANSION_THRESHOLD:
            score += 1
            signals.append(f"PMI {pmi:.1f} 荣枯线上方")
        else:
            score -= 1
            signals.append(f"PMI {pmi:.1f} 荣枯线下方")

    if cpi is not None:
        lo, hi = _CPI_HEALTHY_RANGE
        if lo <= cpi <= hi:
            score += 1
            signals.append(f"CPI {cpi:.1f}% 温和区间")
        elif cpi < lo:
            signals.append(f"CPI {cpi:.1f}% 通缩压力")
        else:
            score -= 1
            signals.append(f"CPI {cpi:.1f}% 通胀压力")

    if m2 is not None:
        lo, hi = _M2_GROWTH_HEALTHY_RANGE
        if lo <= m2 <= hi:
            score += 1
            signals.append(f"M2 {m2:.1f}% 增速适中")
        elif m2 < lo:
            score -= 1
            signals.append(f"M2 {m2:.1f}% 增速偏低")
        else:
            signals.append(f"M2 {m2:.1f}% 增速偏高")

    if lpr is not None:
        signals.append(f"LPR 1Y {lpr:.2f}%")

    if score > 0:
        climate = "expansion"
    elif score < 0:
        climate = "contraction"
    else:
        climate = "neutral"

    return {
        "cpi_yoy": cpi,
        "pmi": pmi,
        "m2_yoy": m2,
        "lpr_1y": lpr,
        "climate": climate,
        "climate_detail": "；".join(signals) if signals else "数据不足",
    }


def compute_macro_climate_score(
    df: pd.DataFrame | None = None,
    cache_path: Path | None = None,
) -> float:
    """Compute a normalized macro climate score from -1.0 to 1.0.

    Returns 0.0 on insufficient data.  Positive = expansionary,
    negative = contractionary.
    """
    summary = get_macro_summary(df=df, cache_path=cache_path)
    if summary["climate"] == "unknown":
        return 0.0
    cpi = summary["cpi_yoy"]
    pmi = summary["pmi"]
    m2 = summary["m2_yoy"]
    components: list[float] = []
    if pmi is not None:
        components.append(1.0 if pmi >= _PMI_EXPANSION_THRESHOLD else -1.0)
    if cpi is not None:
        lo, hi = _CPI_HEALTHY_RANGE
        if lo <= cpi <= hi:
            components.append(0.5)
        elif cpi < lo:
            components.append(-0.5)
        else:
            components.append(-1.0)
    if m2 is not None:
        lo, hi = _M2_GROWTH_HEALTHY_RANGE
        if lo <= m2 <= hi:
            components.append(0.5)
        elif m2 < lo:
            components.append(-0.5)
        else:
            components.append(0.0)
    if not components:
        return 0.0
    score = float(np.mean(components))
    return round(max(-1.0, min(1.0, score)), 4)
