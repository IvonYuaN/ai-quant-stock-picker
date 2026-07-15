from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from aqsp.core.time import get_previous_trading_day, is_trading_day, today_shanghai
from aqsp.freshness import assert_fresh_data
from aqsp.risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


def _latest_expected_market_date() -> date:
    today = today_shanghai()
    return today if is_trading_day(today) else get_previous_trading_day(today)


def _n_trade_days_ago(base: date, n: int) -> date:
    current = base
    for _ in range(n):
        current = get_previous_trading_day(current)
    return current


class TestFreshnessIntegration:
    def _make_frames(self, latest_date: str) -> dict[str, pd.DataFrame]:
        dates = pd.date_range(end=latest_date, periods=30, freq="B")
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1_000_000,
                "amount": 100_000_000,
                "symbol": "600519",
                "name": "贵州茅台",
                "suspended": False,
                "limit_up": 110.0,
                "limit_down": 90.0,
            }
        )
        return {"600519": df}

    def test_fresh_data_passes(self):
        latest = _latest_expected_market_date().isoformat()
        frames = self._make_frames(latest)
        result = assert_fresh_data(frames, max_lag_days=3)
        assert result.isoformat() == latest

    def test_stale_data_raises(self):
        stale_date = _n_trade_days_ago(_latest_expected_market_date(), 4).isoformat()
        frames = self._make_frames(stale_date)
        with pytest.raises(RuntimeError, match="stale"):
            assert_fresh_data(frames, max_lag_days=3)

    def test_no_data_raises(self):
        with pytest.raises(RuntimeError, match="no valid market data"):
            assert_fresh_data({}, max_lag_days=3)

    def test_empty_frame_raises(self):
        frames = {"600519": pd.DataFrame()}
        with pytest.raises(RuntimeError, match="no valid market data"):
            assert_fresh_data(frames, max_lag_days=3)

    def test_boundary_lag_2_days_passes(self):
        boundary_date = _n_trade_days_ago(_latest_expected_market_date(), 2).isoformat()
        frames = self._make_frames(boundary_date)
        result = assert_fresh_data(frames, max_lag_days=3)
        assert result.isoformat() == boundary_date

    def test_boundary_lag_4_days_fails(self):
        stale_date = _n_trade_days_ago(_latest_expected_market_date(), 4).isoformat()
        frames = self._make_frames(stale_date)
        with pytest.raises(RuntimeError, match="stale"):
            assert_fresh_data(frames, max_lag_days=3)


class TestCircuitBreakerIntegration:
    def _make_config(self, tmp_path: Path) -> CircuitBreakerConfig:
        return CircuitBreakerConfig(
            daily_loss_pct=3.0,
            weekly_loss_pct=6.0,
            monthly_loss_pct=10.0,
            cooldown_days=5,
            state_file=str(tmp_path / "risk_state.json"),
        )

    def test_no_trigger_when_pnl_healthy(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker = CircuitBreaker(config=config)
        status = breaker.check(
            daily_pnl_pct=-1.0, weekly_pnl_pct=-2.0, monthly_pnl_pct=-3.0
        )
        assert not status.triggered
        assert status.reason == "正常"

    def test_daily_loss_triggers(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker = CircuitBreaker(config=config)
        status = breaker.check(
            daily_pnl_pct=-3.5, weekly_pnl_pct=-2.0, monthly_pnl_pct=-3.0
        )
        assert status.triggered
        assert status.level == "daily"
        assert "单日" in status.reason
        assert "-3.50%" in status.reason
        assert "-350.00%" not in status.reason

    def test_weekly_loss_triggers(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker = CircuitBreaker(config=config)
        status = breaker.check(
            daily_pnl_pct=-1.0, weekly_pnl_pct=-7.0, monthly_pnl_pct=-3.0
        )
        assert status.triggered
        assert status.level == "weekly"
        assert "周度" in status.reason

    def test_monthly_loss_triggers(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker = CircuitBreaker(config=config)
        status = breaker.check(
            daily_pnl_pct=-1.0, weekly_pnl_pct=-2.0, monthly_pnl_pct=-11.0
        )
        assert status.triggered
        assert status.level == "monthly"
        assert "月度" in status.reason

    def test_cooldown_persists_across_instances(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker1 = CircuitBreaker(config=config)
        status1 = breaker1.check(
            daily_pnl_pct=-4.0, weekly_pnl_pct=-2.0, monthly_pnl_pct=-3.0
        )
        assert status1.triggered
        assert status1.cooldown_until is not None

        breaker2 = CircuitBreaker(config=config)
        assert breaker2.is_in_cooldown()
        status2 = breaker2.check(
            daily_pnl_pct=0.0, weekly_pnl_pct=0.0, monthly_pnl_pct=0.0
        )
        assert status2.triggered
        assert status2.level == "cooldown"

    def test_reset_clears_cooldown(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker = CircuitBreaker(config=config)
        breaker.check(daily_pnl_pct=-4.0, weekly_pnl_pct=-2.0, monthly_pnl_pct=-3.0)
        assert breaker.is_in_cooldown()

        breaker.reset()
        assert not breaker.is_in_cooldown()

        status = breaker.check(
            daily_pnl_pct=0.0, weekly_pnl_pct=0.0, monthly_pnl_pct=0.0
        )
        assert not status.triggered

    def test_state_file_created(self, tmp_path):
        config = self._make_config(tmp_path)
        breaker = CircuitBreaker(config=config)
        breaker.check(daily_pnl_pct=-4.0, weekly_pnl_pct=-2.0, monthly_pnl_pct=-3.0)

        state_file = tmp_path / "risk_state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["cooldown_until"] is not None
        assert state["last_triggered_date"] is not None

    def test_corrupt_state_file_fails_closed(self, tmp_path):
        config = self._make_config(tmp_path)
        state_file = tmp_path / "risk_state.json"
        state_file.write_text("{broken", encoding="utf-8")

        breaker = CircuitBreaker(config=config)

        assert breaker.is_in_cooldown()
        status = breaker.check(
            daily_pnl_pct=0.0, weekly_pnl_pct=0.0, monthly_pnl_pct=0.0
        )
        assert status.triggered
        assert status.level == "cooldown"


class TestCLIIntegration:
    def _make_stale_csv(self, tmp_path: Path, days_old: int = 7) -> Path:
        stale_date = date.today() - timedelta(days=days_old)
        dates = pd.date_range(end=stale_date, periods=30, freq="B")
        df = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "symbol": "600519",
                "name": "贵州茅台",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1_000_000,
                "amount": 100_000_000,
                "suspended": False,
                "limit_up": 110.0,
                "limit_down": 90.0,
            }
        )
        csv_path = tmp_path / "stale_data.csv"
        df.to_csv(csv_path, index=False)
        return csv_path

    def test_stale_data_exits_nonzero(self, tmp_path):
        from aqsp.cli import main

        csv_path = self._make_stale_csv(tmp_path, days_old=7)
        with patch("aqsp.core.time.is_trading_day", return_value=True):
            result = main(
                [
                    "run",
                    "--csv",
                    str(csv_path),
                    "--max-data-lag-days",
                    "3",
                    "--skip-validation",
                ]
            )

        assert result == 1

    def test_csv_run_is_rejected_before_circuit_breaker(self, tmp_path):
        from aqsp.cli import main
        from aqsp.risk.circuit_breaker import CircuitBreakerConfig

        csv_path = self._make_stale_csv(tmp_path, days_old=1)

        state_file = tmp_path / "risk_state.json"
        today = date.today()
        cooldown_until = (today + timedelta(days=5)).isoformat()
        state_file.write_text(
            json.dumps(
                {
                    "cooldown_until": cooldown_until,
                    "last_triggered_date": today.isoformat(),
                }
            ),
            encoding="utf-8",
        )

        with patch("aqsp.cli.CircuitBreaker") as MockBreaker:
            mock_instance = CircuitBreaker(
                config=CircuitBreakerConfig(state_file=str(state_file))
            )
            MockBreaker.return_value = mock_instance

            with patch("aqsp.core.time.is_trading_day", return_value=True):
                result = main(
                    [
                        "run",
                        "--csv",
                        str(csv_path),
                        "--max-data-lag-days",
                        "3",
                        "--skip-validation",
                    ]
                )
            # CSV is historical-only and must fail the live_short boundary
            # before any circuit-breaker state is consulted.
            assert result == 1


def test_circuit_breaker_config_from_thresholds() -> None:
    from aqsp.risk.circuit_breaker import CircuitBreakerConfig
    from aqsp.strategies.thresholds import RiskThresholds, Thresholds

    config = CircuitBreakerConfig.from_thresholds(
        Thresholds(
            risk=RiskThresholds(
                circuit_breaker_daily_loss_pct=2.5,
                circuit_breaker_weekly_loss_pct=5.5,
                circuit_breaker_monthly_loss_pct=9.0,
                circuit_breaker_cooldown_days=4,
            )
        ),
        state_file="tmp/risk_state.json",
    )

    assert config.daily_loss_pct == 2.5
    assert config.weekly_loss_pct == 5.5
    assert config.monthly_loss_pct == 9.0
    assert config.cooldown_days == 4
    assert config.state_file == "tmp/risk_state.json"

    def test_csv_run_does_not_resolve_online_symbols_when_local_file_is_provided(
        self, tmp_path, monkeypatch
    ):
        from aqsp.cli import main

        csv_path = self._make_stale_csv(tmp_path, days_old=1)

        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError("should not resolve online symbols for --csv runs")

        monkeypatch.setattr("aqsp.cli._resolve_run_symbols", _fail_if_called)

        result = main(
            [
                "run",
                "--csv",
                str(csv_path),
                "--max-data-lag-days",
                "3",
                "--skip-validation",
            ]
        )

        assert result in {0, 1, 2}

    def test_briefing_includes_watch_only_rows_from_latest_signal_date(
        self, tmp_path, monkeypatch
    ):
        from aqsp.cli import main

        ledger = tmp_path / "predictions.jsonl"
        ledger.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "signal_date": "2026-06-02",
                            "symbol": "300750",
                            "name": "宁德时代",
                            "signal_close": 431.96,
                            "score": 16.0,
                            "rating": "avoid",
                            "position": "watch",
                            "portfolio_action": "keep",
                            "entry_type": "relative_strength",
                            "reasons": ["MACD 动能改善"],
                            "risks": ["流动性过滤"],
                            "status": "watch_only",
                        },
                        ensure_ascii=False,
                    )
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        output = tmp_path / "briefing.md"
        monkeypatch.setattr("aqsp.cli.load_research_summary", lambda: None)

        result = main(["briefing", "--ledger", str(ledger), "--output", str(output)])

        content = output.read_text(encoding="utf-8")
        assert result == 0
        assert "300750 宁德时代" in content
        assert "候选观察池" in content or "仅观察" in content

    def test_briefing_recovers_name_from_older_ledger_rows_for_same_symbol(
        self, tmp_path, monkeypatch
    ):
        from aqsp.cli import main

        ledger = tmp_path / "predictions.jsonl"
        ledger.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "signal_date": "2026-06-01",
                            "symbol": "300750",
                            "name": "宁德时代",
                            "signal_close": 420.0,
                            "score": 8.0,
                            "rating": "watch",
                            "position": "watch",
                            "portfolio_action": "keep",
                            "entry_type": "relative_strength",
                            "reasons": ["MACD 动能改善"],
                            "risks": ["流动性过滤"],
                            "status": "pending",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "signal_date": "2026-06-02",
                            "symbol": "300750",
                            "name": "",
                            "signal_close": 431.96,
                            "score": 16.0,
                            "rating": "watch",
                            "position": "watch",
                            "portfolio_action": "keep",
                            "entry_type": "relative_strength",
                            "reasons": ["MACD 动能改善"],
                            "risks": ["流动性过滤"],
                            "status": "watch_only",
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        output = tmp_path / "briefing.md"
        monkeypatch.setattr("aqsp.cli.load_research_summary", lambda: None)

        result = main(["briefing", "--ledger", str(ledger), "--output", str(output)])

        content = output.read_text(encoding="utf-8")
        assert result == 0
        assert "300750 宁德时代" in content
        assert "300750 |" not in content
