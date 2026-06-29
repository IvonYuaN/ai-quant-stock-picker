from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CorrelationResult:
    matrix: dict[str, dict[str, float]]
    high_corr_pairs: list[tuple[str, str, float]]
    avg_correlation: float
    high_corr_threshold: float = 0.7


def compute_correlation(
    frames: dict[str, pd.DataFrame],
    symbols: list[str],
    window: int = 20,
    high_corr_threshold: float = 0.7,
) -> CorrelationResult:
    available = [s for s in symbols if s in frames and len(frames[s]) >= 2]
    if len(available) < 2:
        return CorrelationResult(
            matrix={},
            high_corr_pairs=[],
            avg_correlation=0.0,
        )

    returns_dict: dict[str, pd.Series] = {}
    for sym in available:
        df = frames[sym]
        if "close" not in df.columns:
            continue
        frame = df.copy()
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame.dropna(subset=["date"]).sort_values("date")
            close = pd.to_numeric(frame["close"], errors="coerce")
            ret = close.pct_change()
            ret.index = frame["date"]
            ret = ret.dropna().tail(window)
        else:
            close = pd.to_numeric(frame["close"], errors="coerce").tail(window + 1)
            ret = close.pct_change().dropna()
            ret = ret.reset_index(drop=True)
        if len(ret) >= 2:
            returns_dict[sym] = ret

    valid_symbols = sorted(returns_dict.keys())
    if len(valid_symbols) < 2:
        return CorrelationResult(
            matrix={},
            high_corr_pairs=[],
            avg_correlation=0.0,
        )

    returns_df = pd.DataFrame(returns_dict).dropna(how="any")
    if len(returns_df) < 2:
        return CorrelationResult(
            matrix={},
            high_corr_pairs=[],
            avg_correlation=0.0,
        )
    corr_df = returns_df.corr()

    matrix: dict[str, dict[str, float]] = {}
    for s1 in valid_symbols:
        matrix[s1] = {}
        for s2 in valid_symbols:
            val = corr_df.loc[s1, s2]
            matrix[s1][s2] = round(float(val), 4) if not np.isnan(val) else 1.0

    high_corr_pairs: list[tuple[str, str, float]] = []
    off_diag_values: list[float] = []
    for i, s1 in enumerate(valid_symbols):
        for j, s2 in enumerate(valid_symbols):
            if j <= i:
                continue
            val = matrix[s1][s2]
            off_diag_values.append(val)
            if val > high_corr_threshold:
                if s1 < s2:
                    high_corr_pairs.append((s1, s2, val))
                else:
                    high_corr_pairs.append((s2, s1, val))

    high_corr_pairs.sort(key=lambda x: x[2], reverse=True)

    avg_corr = float(np.mean(off_diag_values)) if off_diag_values else 0.0

    return CorrelationResult(
        matrix=matrix,
        high_corr_pairs=high_corr_pairs,
        avg_correlation=round(avg_corr, 4),
        high_corr_threshold=high_corr_threshold,
    )


def format_correlation(result: CorrelationResult) -> str:
    lines = [f"- 平均相关系数: {result.avg_correlation:.2f}"]

    if result.high_corr_pairs:
        lines.append(f"- 高相关性配对（> {result.high_corr_threshold:.2f}）:")
        for s1, s2, corr in result.high_corr_pairs:
            lines.append(f"  - {s1} ↔ {s2}: {corr:.2f}")
    else:
        lines.append("- 无高相关性配对")

    return "\n".join(lines)
