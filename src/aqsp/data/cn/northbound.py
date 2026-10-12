from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from aqsp.core.runtime import runtime_data_root


def _default_cache_path() -> Path:
    """默认缓存路径：``runtime_data_root()/pit_cache/<name>``。

    与 ``data/{longhubang,lockup,suspend_resume,holder_num,announcement,concept_board}``
    同源（issue #336）。

    历史缺陷：本文件位于 ``src/aqsp/data/cn/``，而原实现写
    ``Path(__file__).resolve().parents[3] / "data"`` —— ``parents[3]`` 实为 ``<repo>/src``，
    于是缓存落到 ``<repo>/src/data/``（**release 树内部**）：prod 每次部署即被清空（永远冷启动），
    release 只读时 ``mkdir`` 失败 ⇒ 静默退化为「无缓存」。同一错误还被 ``.gitignore`` 的
    ``src/data/`` 与 ``config/strategy_sources.yaml`` 的 ``absorbed_from`` 两处固化。
    """
    base = runtime_data_root() / "pit_cache"
    base.mkdir(parents=True, exist_ok=True)
    return base / "northbound_history.csv"


_NORTHBOUND_COL_MAP: dict[str, str] = {
    "日期": "date",
    "当日净流入": "net_flow",
    "买入成交额": "buy_amount",
    "卖出成交额": "sell_amount",
}


def _load_cache(cache_path: Path) -> pd.DataFrame:
    if not cache_path.exists():
        return pd.DataFrame(columns=["date", "net_flow", "buy_amount", "sell_amount"])
    try:
        df = pd.read_csv(cache_path, dtype={"date": str})
        for col in ("net_flow", "buy_amount", "sell_amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "net_flow", "buy_amount", "sell_amount"])


def _save_cache(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df = (
        df.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    df.to_csv(cache_path, index=False, encoding="utf-8")


def fetch_northbound_flow(
    days: int = 60,
    cache_path: Path | None = None,
) -> pd.DataFrame:
    cache_path = cache_path or _default_cache_path()
    cached = _load_cache(cache_path)

    try:
        import akshare as ak

        raw = ak.stock_hsgt_north_net_flow_in_em()
        if raw is None or raw.empty:
            return cached
        df = raw.rename(columns=_NORTHBOUND_COL_MAP)
        keep_cols = [
            c
            for c in ("date", "net_flow", "buy_amount", "sell_amount")
            if c in df.columns
        ]
        df = df[keep_cols].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        for col in ("net_flow", "buy_amount", "sell_amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if not cached.empty:
            df = pd.concat([cached, df], ignore_index=True)
        _save_cache(df, cache_path)
    except Exception:
        if cached.empty:
            return cached
        df = cached

    if "date" in df.columns:
        df = df.sort_values("date").tail(days).reset_index(drop=True)
    return df


def compute_northbound_factor(
    df: pd.DataFrame,
    window: int = 5,
) -> float:
    if df is None or df.empty or "net_flow" not in df.columns:
        return 0.0
    series = df["net_flow"].dropna()
    if len(series) < window:
        return 0.0
    recent = series.iloc[-window:]
    mean = series.mean()
    std = series.std()
    if std == 0 or np.isnan(std):
        return 0.0
    z = (recent.iloc[-1] - mean) / std
    if np.isnan(z) or np.isinf(z):
        return 0.0
    return round(float(z), 4)
