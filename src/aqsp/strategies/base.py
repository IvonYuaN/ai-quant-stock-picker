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
        """按分值排序；**并列分一律以 symbol 升序做确定性 tie-break**。

        为什么必须显式 tie-break：调用方的 ``scores`` 常由 ``set`` 派生
        （见 ``CompositeStrategy.calculate_score`` 里的 ``all_symbols``），而
        ``set`` 的迭代顺序在 CPython 中受 ``PYTHONHASHSEED`` 影响；``sorted``
        又是稳定排序，于是**并列分的前后顺序会随进程变化**。叠加
        ``select_stocks`` 末尾的 ``[:n]`` 截断，就会让评分完全相同的票在
        top-n 边界上"换人"——同一份数据、同一套参数，跨进程跑出不同选股与
        不同回测收益（实测 3y −13.90% vs −15.30%、5y 44.90% vs 47.65%）。

        注意因子打分会大量落在边界值上（如 RSI 越界直接返回 0.0/1.0、
        三连涨族的离散档位），并列不是小概率事件，必须消除该依赖。
        """
        return sorted(
            scores.keys(),
            key=lambda x: (scores[x] if ascending else -scores[x], x),
        )

    def select_top(self, scores: Dict[str, float], n: int) -> List[str]:
        ranked = self.rank(scores, ascending=False)
        return ranked[:n]
