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


class LethalFilter(ABC):
    name: str
    hypothesis: str

    @abstractmethod
    def check(
        self, symbol: str, df: pd.DataFrame, **kwargs: object
    ) -> FilterResult: ...
