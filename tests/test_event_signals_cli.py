"""Tests for `aqsp.cli` `event-signals` subcommand (B strategy half, silent-failure fix).

Key behaviours:
- Switch off (default): `run_event_signals` prints a skip message and returns 0
- Switch on (env override): `run_event_signals` proceeds to fetch
- `config/goal_switches.yaml` has the `event_driven_signals` entry
- `EventDrivenStrategy` and `format_event_signals` are importable
- Non-trading-day guard returns 0 before any fetch
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        source="auto",
        symbols="",
        pool="all",
        max_universe=0,
        max_data_lag_days=1,
        benchmark_symbol="000300",
        top=5,
        output="",
        ledger="data/predictions.jsonl",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# 1. Switch off (default) → skip and return 0
# ---------------------------------------------------------------------------


def test_run_event_signals_skipped_when_switch_off(monkeypatch, capsys) -> None:
    """Default env (no override) → switch defaults False → skip and return 0."""
    import aqsp.cli as cli_mod

    monkeypatch.delenv("AQSP_GOAL_SWITCH_EVENT_DRIVEN_SIGNALS", raising=False)

    # _resolve_run_symbols must not be called (skip happens before data fetch)
    resolve_called = []
    monkeypatch.setattr(
        "aqsp.cli._resolve_run_symbols",
        lambda *a, **kw: resolve_called.append(True) or ["600000"],
    )

    result = cli_mod.run_event_signals(_make_args())
    captured = capsys.readouterr()
    assert result == 0
    # The skip message should be printed
    assert (
        "未启用" in captured.out
        or "跳过" in captured.out
        or "walk-forward" in captured.out
    )
    # No data resolution should have happened
    assert len(resolve_called) == 0


def test_run_event_signals_skipped_when_env_false(monkeypatch, capsys) -> None:
    """Explicit env override to 'false' → same skip behaviour."""
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_GOAL_SWITCH_EVENT_DRIVEN_SIGNALS", "false")

    result = cli_mod.run_event_signals(_make_args())
    captured = capsys.readouterr()
    assert result == 0
    assert "未启用" in captured.out or "跳过" in captured.out


# ---------------------------------------------------------------------------
# 2. Non-trading-day guard → returns 0 immediately
# ---------------------------------------------------------------------------


def test_run_event_signals_non_trading_day_skips_before_switch_check(
    monkeypatch, capsys
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    # Switch ON, but non-trading-day guard fires first
    monkeypatch.setenv("AQSP_GOAL_SWITCH_EVENT_DRIVEN_SIGNALS", "true")

    result = cli_mod.run_event_signals(_make_args())
    captured = capsys.readouterr()
    assert result == 0
    assert "非交易日" in captured.out


# ---------------------------------------------------------------------------
# 3. goal_switches.yaml has the event_driven_signals entry
# ---------------------------------------------------------------------------


def test_goal_switches_yaml_has_event_driven_signals() -> None:
    path = Path("config/goal_switches.yaml")
    assert path.exists(), "config/goal_switches.yaml not found"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    switches = data.get("switches", {})
    assert "event_driven_signals" in switches, (
        "event_driven_signals switch missing from goal_switches.yaml"
    )
    assert switches["event_driven_signals"]["enabled"] is False
    assert "walk-forward" in switches["event_driven_signals"]["purpose"]


# ---------------------------------------------------------------------------
# 4. EventDrivenStrategy and format_event_signals are importable
# ---------------------------------------------------------------------------


def test_event_driven_strategy_importable() -> None:
    from aqsp.strategies.event_driven import EventDrivenStrategy, format_event_signals

    strat = EventDrivenStrategy()
    assert strat.name == "event_driven"
    # Empty signals → friendly message
    msg = format_event_signals([])
    assert "无事件异动信号" in msg


def test_event_calendar_from_cache_does_not_raise(monkeypatch, tmp_path) -> None:
    """EventCalendar.from_cache() with a non-existent cache → empty calendar, no error."""
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    from aqsp.features.event_calendar import EventCalendar

    cal = EventCalendar.from_cache()
    assert cal is not None
    assert cal.is_empty()


# ---------------------------------------------------------------------------
# 5. CLI dispatcher source contains the routing
# ---------------------------------------------------------------------------


def test_cli_dispatcher_source_routes_event_signals() -> None:
    import inspect
    import aqsp.cli as cli_mod

    src = inspect.getsource(cli_mod)
    assert "run_event_signals" in src
    assert '"event-signals"' in src


# ---------------------------------------------------------------------------
# 6. Switch ON: verifies the code path actually runs the strategy
# ---------------------------------------------------------------------------


def test_run_event_signals_switch_on_fetches_and_format(
    monkeypatch, capsys, tmp_path
) -> None:
    """With switch ON, patched fetch, and no symbols → returns 1 gracefully."""
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_GOAL_SWITCH_EVENT_DRIVEN_SIGNALS", "true")
    # _resolve_run_symbols returns [] → "无法解析股票池" → return 1
    monkeypatch.setattr("aqsp.cli._resolve_run_symbols", lambda *a, **kw: [])

    result = cli_mod.run_event_signals(_make_args())
    captured = capsys.readouterr()
    assert result == 1
    assert "无法解析股票池" in captured.out


def test_run_event_signals_switch_on_with_symbols_no_frames(
    monkeypatch, capsys
) -> None:
    """With switch ON and symbols provided, but fetch fails → returns 1."""
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_GOAL_SWITCH_EVENT_DRIVEN_SIGNALS", "true")

    def _raise_fetch(*a, **kw):
        raise Exception("network unavailable")

    monkeypatch.setattr("aqsp.cli._fetch_special_strategy_frames", _raise_fetch)
    monkeypatch.setattr("aqsp.cli._resolve_run_symbols", lambda *a, **kw: ["600000"])

    # Patch is_trading_day to True (already in fixture)
    result = cli_mod.run_event_signals(_make_args(symbols="600000"))
    captured = capsys.readouterr()
    # fetch raised → frames = {} → "无法获取数据" → return 1
    assert result == 1
    assert "无法获取数据" in captured.out
