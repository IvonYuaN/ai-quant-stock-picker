from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from aqsp.risk import circuit_breaker as breaker_mod
from aqsp.risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


class _Now:
    def __init__(self, value: date) -> None:
        self._value = value

    def date(self) -> date:
        return self._value


def test_circuit_breaker_cooldown_until_is_release_date(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "risk_state.json"
    state_file.write_text(
        json.dumps(
            {
                "cooldown_until": "2026-07-12",
                "last_triggered_date": "2026-07-07",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        breaker_mod,
        "now_shanghai",
        lambda: _Now(date(2026, 7, 12)),
    )
    breaker = CircuitBreaker(
        config=CircuitBreakerConfig(state_file=str(state_file)),
    )

    status = breaker.check(
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        monthly_pnl_pct=0.0,
    )

    assert not status.triggered
    assert status.level == "none"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["cooldown_until"] is None
