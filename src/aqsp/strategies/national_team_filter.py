"""Example strategy using national team holdings as a factor.

This demonstrates how to integrate the national team tracking module
into the strategy system.
"""

from __future__ import annotations

from typing import Dict
import logging

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.data.national_team import NationalTeamTracker

_logger = logging.getLogger(__name__)


class NationalTeamFilterStrategy(BaseStrategy):
    """Filter stocks with national team holdings.

    This is a simple boolean factor strategy: 1.0 if national team holds,
    0.0 otherwise. Can be combined with other strategies for institutional
    bias confirmation.
    """

    name: str = "national_team_filter"

    def __init__(self, config: StrategyConfig):
        super().__init__(
            config,
            id="national_team_filter",
            version="0.1.0",
            hypothesis="国家队持仓作为机构认可度信号，表明长期价值判断",
        )
        try:
            self.tracker = NationalTeamTracker()
        except Exception as exc:
            _logger.warning("Failed to initialize NationalTeamTracker: %s", exc)
            self.tracker = None

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """Calculate national team score for each symbol.

        Returns 1.0 for symbols with national team holdings, 0.0 otherwise.
        """
        scores = {}

        if self.tracker is None:
            _logger.warning("NationalTeamTracker not available; returning 0 for all symbols")
            return {symbol: 0.0 for symbol in data.keys()}

        for symbol in data.keys():
            try:
                has_holding = self.tracker.has_national_team_holding(symbol)
                scores[symbol] = 1.0 if has_holding else 0.0
            except Exception as exc:
                _logger.warning("Failed to check national team holding for %s: %s", symbol, exc)
                scores[symbol] = 0.0

        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        """This strategy only uses symbol-level data, not time series."""
        return 0.0


# Example usage:
if __name__ == "__main__":
    from aqsp.strategies.base import StrategyConfig

    config = StrategyConfig(
        name="national_team_filter",
        enabled=True,
        weight=0.5,
    )
    strategy = NationalTeamFilterStrategy(config)

    # Example data
    example_data = {
        "600000": pd.DataFrame({"date": ["2024-01-01"], "close": [10.0]}),
        "601398": pd.DataFrame({"date": ["2024-01-01"], "close": [5.0]}),
    }

    scores = strategy.calculate_score(example_data)
    print("Scores:", scores)
