from __future__ import annotations

import pandas as pd

from aqsp.regime.detector import RegimeDetector
from aqsp.strategies.thresholds import Thresholds


def build_synthetic_regime_frame(
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    normalized_parts: list[pd.DataFrame] = []
    for symbol, frame in frames.items():
        if frame is None or frame.empty or "date" not in frame.columns:
            continue
        part = frame.sort_values("date").copy()
        if "close" not in part.columns or "volume" not in part.columns:
            continue
        part["close"] = pd.to_numeric(part["close"], errors="coerce")
        part["volume"] = pd.to_numeric(part["volume"], errors="coerce")
        part = part.dropna(subset=["date", "close", "volume"])
        if part.empty:
            continue
        first_close = float(part["close"].iloc[0])
        if first_close <= 0:
            continue
        normalized_parts.append(
            pd.DataFrame(
                {
                    "date": pd.to_datetime(part["date"], errors="coerce"),
                    "symbol": symbol,
                    "close_norm": part["close"] / first_close * 100.0,
                    "volume": part["volume"],
                }
            ).dropna(subset=["date"])
        )
    if not normalized_parts:
        return None
    merged = pd.concat(normalized_parts, ignore_index=True)
    synthetic = (
        merged.groupby("date", as_index=False)
        .agg(close=("close_norm", "mean"), volume=("volume", "mean"))
        .sort_values("date")
        .reset_index(drop=True)
    )
    if synthetic.empty:
        return None
    synthetic["date"] = synthetic["date"].dt.strftime("%Y-%m-%d")
    synthetic["symbol"] = "synthetic_market"
    synthetic["name"] = "Synthetic Market Breadth"
    return synthetic[["date", "symbol", "name", "close", "volume"]]


def detect_runtime_regime(
    frames: dict[str, pd.DataFrame],
    *,
    benchmark_symbol: str | None,
    detector: RegimeDetector | None = None,
    thresholds: Thresholds | None = None,
) -> str:
    regime_detector = detector or RegimeDetector(thresholds=thresholds)
    if benchmark_symbol:
        bench_frame = frames.get(benchmark_symbol)
        if bench_frame is not None and not bench_frame.empty:
            return regime_detector.detect({benchmark_symbol: bench_frame}).name
    synthetic = build_synthetic_regime_frame(
        {
            symbol: df
            for symbol, df in frames.items()
            if not benchmark_symbol or symbol != benchmark_symbol
        }
    )
    if synthetic is None or synthetic.empty:
        return ""
    regime = regime_detector.detect({"synthetic_market": synthetic}).name
    return "" if regime == "unknown" else regime
