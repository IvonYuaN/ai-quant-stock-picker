from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Any
import pandas as pd

from aqsp.core.types import SignalScore


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    enabled: bool = True
    weight: float = 1.0
    params: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    name: str = "base"

    def __init__(
        self,
        config: StrategyConfig,
        *,
        id: str,
        version: str,
        hypothesis: str,
        regime_required: tuple[str, ...] = (),
    ):
        if not hypothesis:
            raise ValueError("hypothesis 不允许为空字符串")
        self.config = config
        self.id = id
        self.version = version
        self.hypothesis = hypothesis
        self.regime_required = regime_required

    def evaluate(self, df: pd.DataFrame, regime: str) -> SignalScore:
        if self.regime_required and regime not in self.regime_required:
            return SignalScore(
                strategy_id=self.id,
                score=0.0,
                reasons=(),
                fired=False,
            )
        score = float(self._calculate_single_score(df))
        fired = bool(score > 0)
        return SignalScore(
            strategy_id=self.id,
            score=score,
            reasons=(),
            fired=fired,
        )

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        return 0.0

    @abstractmethod
    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        pass

    def validate_data(self, data: Dict[str, pd.DataFrame]) -> None:
        for symbol, df in data.items():
            if df is None or df.empty:
                raise ValueError(f"No data for symbol: {symbol}")

    def normalize_scores(self, scores: Dict[str, float]) -> Dict[str, float]:
        if not scores:
            return scores

        values = list(scores.values())
        min_val = min(values)
        max_val = max(values)

        if max_val == min_val:
            return {k: 0.5 for k in scores}

        return {k: (v - min_val) / (max_val - min_val) for k, v in scores.items()}

    def rank(self, scores: Dict[str, float], ascending: bool = False) -> List[str]:
        return sorted(scores.keys(), key=lambda x: scores[x], reverse=not ascending)

    def select_top(self, scores: Dict[str, float], n: int) -> List[str]:
        ranked = self.rank(scores, ascending=False)
        return ranked[:n]
