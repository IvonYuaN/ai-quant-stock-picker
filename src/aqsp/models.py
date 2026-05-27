from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScreeningConfig:
    mode: str = "close"
    min_bars: int = 80
    min_avg_amount: float = 50_000_000
    max_bias20: float = 18.0
    stop_loss_buffer: float = 0.03


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
    reasons: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)
