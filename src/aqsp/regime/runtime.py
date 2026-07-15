from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math

import pandas as pd

from aqsp.regime.hmm_detector import HMMRegimeDetector
from aqsp.regime.strategy_mixer import (
    canonical_regime_from_hmm,
    resolve_regime_label,
)
from aqsp.strategies.thresholds import Thresholds

_HMM_REGIME_LABELS = {
    "bull": "牛市",
    "bear": "熊市",
    "sideways": "震荡",
}


@dataclass(frozen=True)
class RuntimeRegimeContext:
    regime: str
    hmm_regime: str
    confidence: float
    annualized_volatility: float
    detector: str


def format_runtime_regime_lines(
    context: RuntimeRegimeContext,
) -> tuple[str, ...]:
    if not context.regime:
        return ()
    hmm_label = _HMM_REGIME_LABELS.get(context.hmm_regime, context.hmm_regime or "未知")
    regime_label = resolve_regime_label(context.regime)
    return (
        "运行判定: "
        f"HMM {hmm_label} | 置信度 {context.confidence:.0%} | "
        f"年化波动 {context.annualized_volatility:.1%} | 映射 {regime_label}",
    )


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
    hmm_detector: HMMRegimeDetector | None = None,
    thresholds: Thresholds | None = None,
    as_of: str | date | None = None,
) -> str:
    return detect_runtime_regime_context(
        frames,
        benchmark_symbol=benchmark_symbol,
        hmm_detector=hmm_detector,
        thresholds=thresholds,
        as_of=as_of,
    ).regime


def detect_runtime_regime_context(
    frames: dict[str, pd.DataFrame],
    *,
    benchmark_symbol: str | None,
    hmm_detector: HMMRegimeDetector | None = None,
    thresholds: Thresholds | None = None,
    as_of: str | date | None = None,
) -> RuntimeRegimeContext:
    if not benchmark_symbol:
        return RuntimeRegimeContext("", "", 0.0, 0.0, "missing_benchmark")

    bench_frame = frames.get(benchmark_symbol)
    if bench_frame is None or bench_frame.empty:
        return RuntimeRegimeContext("", "", 0.0, 0.0, "missing_benchmark")

    bench_frame = _frame_as_of(bench_frame, as_of)
    if bench_frame.empty:
        return RuntimeRegimeContext("", "", 0.0, 0.0, "cutoff_before_data")

    current_thresholds = thresholds or Thresholds()
    detector = hmm_detector or HMMRegimeDetector(
        min_data_points=max(5, int(current_thresholds.regime.min_sample_size))
    )
    result = detector.detect_regime(bench_frame)
    annualized_volatility = float(result.volatility) * math.sqrt(252.0)
    regime = canonical_regime_from_hmm(
        str(result.regime),
        annualized_volatility=annualized_volatility,
        volatility_high=float(current_thresholds.regime.volatility_high),
    )
    return RuntimeRegimeContext(
        regime=regime,
        hmm_regime=str(result.regime),
        confidence=float(result.confidence),
        annualized_volatility=annualized_volatility,
        detector="hmm_regime_detector",
    )


def _frame_as_of(
    frame: pd.DataFrame,
    as_of: str | date | None,
) -> pd.DataFrame:
    """Keep only observations visible at the signal cutoff."""
    ordered = frame.sort_values("date").copy()
    if as_of is None:
        return ordered
    cutoff = pd.Timestamp(as_of)
    dates = pd.to_datetime(ordered["date"], errors="coerce")
    return ordered.loc[dates <= cutoff].copy()
