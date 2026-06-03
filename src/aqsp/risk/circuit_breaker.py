from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class CircuitBreakerConfig:
    daily_loss_pct: float = 3.0
    weekly_loss_pct: float = 6.0
    monthly_loss_pct: float = 10.0
    cooldown_days: int = 5
    state_file: str = "data/risk_state.json"


@dataclass(frozen=True)
class BreakerStatus:
    triggered: bool
    reason: str
    level: str
    daily_pnl_pct: float
    weekly_pnl_pct: float
    monthly_pnl_pct: float
    cooldown_until: Optional[str] = None


@dataclass
class CircuitBreaker:
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _cooldown_until: Optional[date] = field(default=None, repr=False)
    _last_triggered_date: Optional[date] = field(default=None, repr=False)

    def __post_init__(self):
        self._load_state()

    def _load_state(self):
        state_file = Path(self.config.state_file)
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if state.get("cooldown_until"):
                    self._cooldown_until = date.fromisoformat(state["cooldown_until"])
                if state.get("last_triggered_date"):
                    self._last_triggered_date = date.fromisoformat(
                        state["last_triggered_date"]
                    )
            except (json.JSONDecodeError, ValueError):
                pass

    def _save_state(self):
        state_file = Path(self.config.state_file)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "cooldown_until": self._cooldown_until.isoformat()
            if self._cooldown_until
            else None,
            "last_triggered_date": self._last_triggered_date.isoformat()
            if self._last_triggered_date
            else None,
        }
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def check(
        self, daily_pnl_pct: float, weekly_pnl_pct: float, monthly_pnl_pct: float
    ) -> BreakerStatus:
        if self._cooldown_until is not None:
            today = now_shanghai().date()
            if today < self._cooldown_until:
                return BreakerStatus(
                    triggered=True,
                    reason=f"组合保护冷却期中，至 {self._cooldown_until.isoformat()} 解除",
                    level="cooldown",
                    daily_pnl_pct=daily_pnl_pct,
                    weekly_pnl_pct=weekly_pnl_pct,
                    monthly_pnl_pct=monthly_pnl_pct,
                    cooldown_until=self._cooldown_until.isoformat(),
                )
            else:
                self._cooldown_until = None
                self._save_state()

        if monthly_pnl_pct <= -self.config.monthly_loss_pct:
            return self._trigger(
                "monthly",
                f"月度组合亏损 {monthly_pnl_pct:.2f}% 触及 {self.config.monthly_loss_pct:.1f}% 止损线",
                daily_pnl_pct,
                weekly_pnl_pct,
                monthly_pnl_pct,
            )

        if weekly_pnl_pct <= -self.config.weekly_loss_pct:
            return self._trigger(
                "weekly",
                f"周度组合亏损 {weekly_pnl_pct:.2f}% 触及 {self.config.weekly_loss_pct:.1f}% 止损线",
                daily_pnl_pct,
                weekly_pnl_pct,
                monthly_pnl_pct,
            )

        if daily_pnl_pct <= -self.config.daily_loss_pct:
            return self._trigger(
                "daily",
                f"单日组合亏损 {daily_pnl_pct:.2f}% 触及 {self.config.daily_loss_pct:.1f}% 止损线",
                daily_pnl_pct,
                weekly_pnl_pct,
                monthly_pnl_pct,
            )

        return BreakerStatus(
            triggered=False,
            reason="正常",
            level="none",
            daily_pnl_pct=daily_pnl_pct,
            weekly_pnl_pct=weekly_pnl_pct,
            monthly_pnl_pct=monthly_pnl_pct,
        )

    def _trigger(
        self,
        level: str,
        reason: str,
        daily_pnl_pct: float,
        weekly_pnl_pct: float,
        monthly_pnl_pct: float,
    ) -> BreakerStatus:
        today = now_shanghai().date()
        self._last_triggered_date = today
        self._cooldown_until = today + timedelta(days=self.config.cooldown_days)
        self._save_state()
        return BreakerStatus(
            triggered=True,
            reason=reason,
            level=level,
            daily_pnl_pct=daily_pnl_pct,
            weekly_pnl_pct=weekly_pnl_pct,
            monthly_pnl_pct=monthly_pnl_pct,
            cooldown_until=self._cooldown_until.isoformat(),
        )

    def reset(self) -> None:
        self._cooldown_until = None
        self._last_triggered_date = None
        self._save_state()

    def is_in_cooldown(self) -> bool:
        if self._cooldown_until is None:
            return False
        return now_shanghai().date() < self._cooldown_until
