from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from aqsp.strategies.thresholds import load_thresholds


@dataclass(frozen=True)
class PortfolioConstraint:
    max_weight: float = 0.1
    max_sector_weight: float = 0.3
    max_correlation: float = 0.8
    min_diversification: int = 10


@dataclass(frozen=True)
class SectorAllocation:
    sector: str
    weight: float
    count: int


@dataclass(frozen=True)
class PortfolioResult:
    symbols: List[str]
    weights: Dict[str, float]
    sector_allocations: List[SectorAllocation]
    max_correlation: float
    diversification_score: float


class DiversificationEngine:
    def __init__(self, constraints: PortfolioConstraint = None):
        self.constraints = constraints or PortfolioConstraint()
        self.thresholds = load_thresholds()

    def optimize(
        self,
        scores: Dict[str, float],
        sector_map: Dict[str, str],
        correlation_matrix: Optional[pd.DataFrame] = None,
    ) -> PortfolioResult:

        sorted_symbols = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        weights = {}
        sector_counts: Dict[str, int] = {}
        sector_weights: Dict[str, float] = {}

        for symbol in sorted_symbols:
            sector = sector_map.get(symbol, "unknown")

            if sector_weights.get(sector, 0) >= self.constraints.max_sector_weight:
                continue

            if sum(weights.values()) >= 1.0:
                break

            max_possible = min(
                self.constraints.max_weight,
                self.constraints.max_sector_weight - sector_weights.get(sector, 0),
                1.0 - sum(weights.values()),
            )

            weight = max_possible * scores[symbol]

            weights[symbol] = weight
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            sector_weights[sector] = sector_weights.get(sector, 0) + weight

        weights = self._normalize_weights(weights)

        sector_allocations = [
            SectorAllocation(sector=sector, weight=weight, count=sector_counts[sector])
            for sector, weight in sector_weights.items()
        ]

        max_corr = self._calculate_max_correlation(
            list(weights.keys()), correlation_matrix
        )
        diversification = self._calculate_diversification(weights, sector_counts)

        return PortfolioResult(
            symbols=list(weights.keys()),
            weights=weights,
            sector_allocations=sector_allocations,
            max_correlation=max_corr,
            diversification_score=diversification,
        )

    def _normalize_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        total = sum(weights.values())
        if total == 0:
            return weights
        return {k: v / total for k, v in weights.items()}

    def _calculate_max_correlation(
        self, symbols: List[str], correlation_matrix: Optional[pd.DataFrame]
    ) -> float:
        if correlation_matrix is None or len(symbols) < 2:
            return 0.0

        symbols_in_matrix = [s for s in symbols if s in correlation_matrix.columns]
        if len(symbols_in_matrix) < 2:
            return 0.0

        sub_matrix = correlation_matrix.loc[symbols_in_matrix, symbols_in_matrix]
        np.fill_diagonal(sub_matrix.values, 0)

        return float(sub_matrix.max().max())

    def _calculate_diversification(
        self, weights: Dict[str, float], sector_counts: Dict[str, int]
    ) -> float:
        if not weights:
            return 0.0

        symbol_count = len(weights)
        sector_count = len(sector_counts)

        concentration_penalty = np.sum(np.array(list(weights.values())) ** 2)
        ideal_concentration = 1 / symbol_count

        diversification = (1 - concentration_penalty) / (1 - ideal_concentration)
        sector_bonus = min(sector_count / 5, 1.0)

        return min((diversification + sector_bonus) / 2, 1.0)

    def validate(
        self, weights: Dict[str, float], sector_map: Dict[str, str]
    ) -> Tuple[bool, List[str]]:
        errors = []

        if sum(weights.values()) > 1.0 + 1e-6:
            errors.append("总权重超过1.0")

        for symbol, weight in weights.items():
            if weight > self.constraints.max_weight:
                errors.append(f"{symbol}权重超过限制")

        sector_weights: Dict[str, float] = {}
        for symbol, weight in weights.items():
            sector = sector_map.get(symbol, "unknown")
            sector_weights[sector] = sector_weights.get(sector, 0) + weight

        for sector, weight in sector_weights.items():
            if weight > self.constraints.max_sector_weight:
                errors.append(f"{sector}板块权重超过限制")

        if len(weights) < self.constraints.min_diversification:
            errors.append(f"股票数量不足{self.constraints.min_diversification}只")

        return len(errors) == 0, errors
