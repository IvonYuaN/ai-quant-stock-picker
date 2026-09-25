from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FilterResult:
    symbol: str
    passed: bool
    reason: str
    filter_name: str
    # 数据缺失（保护层对该票未生效，静默放行）——调用方应据此告警，不可当健康放行。
    data_missing: bool = False


class LethalFilter(ABC):
    name: str
    hypothesis: str

    @abstractmethod
    def check(
        self, symbol: str, df: pd.DataFrame, **kwargs: object
    ) -> FilterResult: ...
