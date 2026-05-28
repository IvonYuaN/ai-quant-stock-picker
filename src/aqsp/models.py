from __future__ import annotations

from dataclasses import dataclass, field

from aqsp.core.types import PickResult  # noqa: F401


__all__ = ["PickResult", "ScreeningConfig"]


@dataclass(frozen=True)
class ScreeningConfig:
    mode: str = "close"
    min_bars: int = 80
    min_avg_amount: float = 50_000_000
    max_bias20: float = 18.0
    stop_loss_buffer: float = 0.03
    strategy_weights: dict[str, float] = field(default_factory=dict)
