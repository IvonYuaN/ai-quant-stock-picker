from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol
import pandas as pd


OhlcvFrame = pd.DataFrame


@dataclass(frozen=True)
class SignalDay:
    date: str
    symbol: str
    name: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    suspended: bool = False
    limit_up: float = 0.0
    limit_down: float = 0.0
    adj_factor: float = 1.0


@dataclass(frozen=True)
class PickResult:
    symbol: str
    name: str
    date: str
    close: float
    score: float
    rating: str
    entry_type: str
    ideal_buy: float
    stop_loss: float
    take_profit: float
    position: str
    strategies: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunMetadata:
    requested_source: str
    actual_source: str
    explicit_symbol_count: int
    resolved_symbol_count: int
    fetched_frame_count: int
    screened_count: int
    final_count: int
    min_price: float
    max_price: float
    min_avg_amount: float
    online_factors_enabled: bool
    thresholds_version: str
    regime: str = ""
    max_universe: int = 0


@dataclass(frozen=True)
class SignalScore:
    strategy_id: str
    score: float
    reasons: tuple[str, ...]
    fired: bool


class Strategy(Protocol):
    id: str
    version: str
    hypothesis: str
    regime_required: tuple[str, ...]

    def evaluate(self, df: pd.DataFrame, regime: str) -> SignalScore: ...


class DataSource(Protocol):
    name: str

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]: ...

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]: ...

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]: ...

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]: ...
