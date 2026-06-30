from __future__ import annotations

from argparse import Namespace
from datetime import date
from datetime import datetime
import json
import logging
from pathlib import Path
import inspect

import pandas as pd
import pytest
from aqsp.core.errors import MissingDataError


@pytest.fixture(autouse=True)
def _force_runtime_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)


@pytest.fixture(autouse=True)
def _isolated_runtime_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(tmp_path / "notify_state.json"))
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )


def _fresh_frame(day: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [day],
            "symbol": ["600000"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [1000],
            "amount": [10200.0],
            "suspended": [False],
            "limit_up": [11.22],
            "limit_down": [9.18],
        }
    )


def test_special_strategy_ledger_guard_blocks_non_trading_day(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)

    allowed, reason = cli_mod._special_strategy_ledger_write_allowed(
        {"600000": _fresh_frame("2026-06-22")},
        max_data_lag_days=1,
    )

    assert allowed is False
    assert "非交易日" in reason


def test_special_strategy_ledger_guard_requires_fresh_data(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)
    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 26))

    allowed, reason = cli_mod._special_strategy_ledger_write_allowed(
        {"600000": _fresh_frame("2026-06-20")},
        max_data_lag_days=0,
    )

    assert allowed is False
    assert "数据新鲜度未通过" in reason


def test_run_morning_breakout_skips_non_trading_day_before_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    monkeypatch.setattr(cli_mod, "_fetch_special_strategy_frames", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not fetch on holiday")))

    args = Namespace(
        symbols="600000",
        source="auto",
        pool="all",
        max_universe=0,
        max_data_lag_days=1,
        benchmark_symbol="000300",
        top=5,
        notify=False,
        output="",
        report="",
        ledger="data/predictions.jsonl",
    )

    assert cli_mod.run_morning_breakout(args) == 0
    assert "今日非交易日，跳过早盘策略" in capsys.readouterr().out


def test_run_closing_premium_skips_non_trading_day_before_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    monkeypatch.setattr(cli_mod, "_fetch_special_strategy_frames", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not fetch on holiday")))

    args = Namespace(
        symbols="600000",
        source="auto",
        pool="all",
        max_universe=0,
        max_data_lag_days=1,
        benchmark_symbol="000300",
        top=5,
        notify=False,
        output="",
        report="",
        ledger="data/predictions.jsonl",
    )

    assert cli_mod.run_closing_premium(args) == 0
    assert "今日非交易日，跳过尾盘策略" in capsys.readouterr().out


def test_fetch_special_strategy_frames_requires_today_intraday(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    frames = {
        "600000": _fresh_frame("2026-06-25"),
        "000300": _fresh_frame("2026-06-25"),
    }

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (frames, "eastmoney"),
    )

    class FakeIntradayService:
        def __init__(self, _source) -> None:
            pass

        def merge_intraday_bar_into_daily(self, *_args, **_kwargs):
            raise MissingDataError("600000", reason="分时数据不含 2026-06-26 当日 bar")

    monkeypatch.setattr(cli_mod, "IntradayService", FakeIntradayService)
    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 26))

    with pytest.raises(MissingDataError, match="当日 bar"):
        cli_mod._fetch_special_strategy_frames(
            "eastmoney",
            ["600000"],
            benchmark_symbol="000300",
        )


def test_special_strategy_runtime_ready_requires_enabled_and_regime(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    class FakeThresholdConfig:
        enabled = True

    class FakeStrategy:
        thresholds = object()
        cfg = FakeThresholdConfig()
        regime_required = ("stable_bull",)

    monkeypatch.setattr(
        cli_mod,
        "_detect_runtime_regime",
        lambda *_args, **_kwargs: "stable_bear",
    )

    allowed, regime, reason = cli_mod._special_strategy_runtime_ready(
        strategy=FakeStrategy(),
        frames={"000300": _fresh_frame("2026-06-26")},
        benchmark_symbol="000300",
    )

    assert allowed is False
    assert regime == "stable_bear"
    assert "市场状态不匹配" in reason


def test_special_strategy_runtime_ready_blocks_disabled_threshold(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    class FakeThresholdConfig:
        enabled = False

    class FakeStrategy:
        thresholds = object()
        mb = FakeThresholdConfig()
        regime_required = ()

    monkeypatch.setattr(
        cli_mod,
        "_detect_runtime_regime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not detect regime")
        ),
    )

    allowed, regime, reason = cli_mod._special_strategy_runtime_ready(
        strategy=FakeStrategy(),
        frames={"000300": _fresh_frame("2026-06-26")},
        benchmark_symbol="000300",
    )

    assert allowed is False
    assert regime == ""
    assert reason == "策略已禁用"


def test_run_evolve_uses_full_runtime_universe_when_auto_resolving_symbols(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        seen["max_universe"] = kwargs["max_universe"]
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        max_universe=0,
        apply=False,
        output="",
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert seen["pool_name"] == ""
    assert seen["symbols"] == ""
    assert seen["max_universe"] == 0
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_scheduled_keeps_learning_weights_proposal_only() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)

    assert "strategy_weights_from_ledger(args.ledger)" not in source
    assert "learner.compute_weights(ledger_df)" in source
    assert "strategy_weights_for_regime(thresholds, regime)" in source
    assert "未应用到本次筛选" in source


def test_run_scheduled_runtime_weights_exclude_learner_proposals() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)
    proposal_at = source.index("weight_proposals = learner.compute_weights(ledger_df)")
    runtime_weight_at = source.index(
        "weights = strategy_weights_for_regime(thresholds, regime)"
    )
    snapshot_at = source.index("strategy_weights=weights")

    assert proposal_at < runtime_weight_at < snapshot_at
    between = source[proposal_at:runtime_weight_at]
    assert "weights.update(weight_proposals)" not in between
    assert "weight_proposals[" not in source[runtime_weight_at:snapshot_at]


def test_run_scheduled_executability_feedback_applies_runtime_downweights() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)
    feedback_at = source.index("executability_adjustments, executability_reasons = (")
    config_at = source.index("config = ScreeningConfig(")
    between = source[feedback_at:config_at]

    assert "不可成交反馈降权:" in between
    assert "weights[strategy_id]" in between
    assert "strategy_weight_reasons[strategy_id] = reason" in between


def test_formal_runtime_ledger_path_uses_formal_ledger_for_intraday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_LEDGER", "data/predictions.jsonl")

    assert (
        cli_mod._formal_runtime_ledger_path(
            "data/intraday_predictions.jsonl",
            task_id="intraday",
        )
        == "data/predictions.jsonl"
    )
    assert (
        cli_mod._formal_runtime_ledger_path(
            "data/midday_predictions.jsonl",
            task_id="midday",
        )
        == "data/predictions.jsonl"
    )
    assert (
        cli_mod._formal_runtime_ledger_path(
            "data/predictions.jsonl",
            task_id="daily",
        )
        == "data/predictions.jsonl"
    )


def test_run_scheduled_skips_runtime_chain_on_non_trading_day(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: datetime(2026, 6, 19).date())
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-trading day must not resolve universe")
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-trading day must not write ledger")
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-trading day must not notify")
        ),
    )

    args = Namespace(
        mode="close",
        symbols="",
        csv="",
        source="auto",
        limit=5,
        max_universe=0,
        min_avg_amount=10_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=3.0,
        slippage_bps=20.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    assert cli_mod.run_scheduled(args) == 0


def test_run_scheduled_dispatches_through_service_boundary(monkeypatch) -> None:
    import aqsp.cli as cli_mod
    import aqsp.services.scheduled as scheduled_service

    seen: dict[str, object] = {}

    def fake_service(args, *, legacy_runner):
        seen["args"] = args
        seen["legacy_runner"] = legacy_runner
        return 7

    monkeypatch.setattr(scheduled_service, "run_scheduled_service", fake_service)
    args = Namespace()

    assert cli_mod.run_scheduled(args) == 7
    assert seen["args"] is args
    assert seen["legacy_runner"] is cli_mod._run_scheduled_legacy


def test_run_scheduled_validates_ledger_before_circuit_breaker_pnl() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)

    assert source.index("validate_predictions(formal_ledger_path, frames)") < source.index(
        "_compute_real_pnl("
    )


def test_run_screen_injects_threshold_screening_config(monkeypatch) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.strategies.thresholds import (
        RiskThresholds,
        ScoringThresholds,
        Thresholds,
    )

    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": "2026-06-22",
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                }
            ]
        )
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *_args, **_kwargs: ["600519"]
    )
    monkeypatch.setattr(cli_mod, "latest_trade_date", lambda *_args: "2026-06-22")
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(
        cli_mod,
        "_detect_runtime_regime",
        lambda *_args, **_kwargs: "stable_bull",
    )
    monkeypatch.setattr(
        cli_mod,
        "load_thresholds",
        lambda: Thresholds(
            scoring=ScoringThresholds(max_bias20=9.0),
            risk=RiskThresholds(soft_stop_loss_pct=0.07, max_position_pct=0.12),
        ),
    )

    def fake_screen_universe(_frames, config):
        captured["config"] = config
        return [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date="2026-06-22",
                close=1505.0,
                score=60.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-12%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=(),
            )
        ]

    monkeypatch.setattr(cli_mod, "screen_universe", fake_screen_universe)
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "to_dataframe", lambda picks: pd.DataFrame())

    args = Namespace(
        csv="",
        source="auto",
        symbols="600519",
        benchmark_symbol="000300",
        pool="",
        min_avg_amount=50_000_000,
        mode="close",
        limit=1,
        report="",
        output_csv="",
        enable_online_factors=False,
    )

    assert cli_mod.run_screen(args) == 0
    config = captured["config"]
    assert config.max_bias20 == 9.0
    assert config.stop_loss_buffer == 0.07
    assert config.max_position_pct == 0.12


def test_run_scheduled_composite_rescore_updates_frozen_pick_results(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    latest = "2026-06-15"
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "000001": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000001",
                    "name": "平安银行",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000,
                    "amount": 10100000.0,
                    "suspended": False,
                    "limit_up": 11.11,
                    "limit_down": 9.09,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519", "000001"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "stable_bull"
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: type(
            "R",
            (),
            {"matrix": {}, "high_corr_pairs": ()},
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.format_snapshot_diff", lambda *_args, **_kwargs: ""
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())

    base_picks = [
        PickResult(
            symbol="600519",
            name="贵州茅台",
            date=latest,
            close=1505.0,
            score=60.0,
            rating="buy_candidate",
            entry_type="next_open",
            ideal_buy=1505.0,
            stop_loss=1450.0,
            take_profit=1600.0,
            position="10%-30%",
            strategies=("ma_pullback",),
            reasons=("趋势回踩",),
            risks=(),
        ),
        PickResult(
            symbol="000001",
            name="平安银行",
            date=latest,
            close=10.1,
            score=80.0,
            rating="buy_candidate",
            entry_type="next_open",
            ideal_buy=10.1,
            stop_loss=9.7,
            take_profit=11.0,
            position="10%-30%",
            strategies=("ma_pullback",),
            reasons=("趋势回踩",),
            risks=(),
        ),
    ]
    monkeypatch.setattr(
        cli_mod, "screen_universe", lambda *_args, **_kwargs: list(base_picks)
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )

    class FakeCompositeStrategy:
        def __init__(self, thresholds=None):
            self.thresholds = thresholds

        def calculate_score(self, data, regime="unknown"):
            return {"600519": 0.9, "000001": 0.3}

    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy", FakeCompositeStrategy
    )

    captured: dict[str, list[PickResult]] = {}

    def fake_to_dataframe(picks):
        captured["picks"] = list(picks)
        return pd.DataFrame(
            [
                {
                    "symbol": pick.symbol,
                    "score": pick.score,
                    "regime_score": pick.regime_score,
                }
                for pick in picks
            ]
        )

    monkeypatch.setattr(cli_mod, "to_dataframe", fake_to_dataframe)
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")

    args = Namespace(
        mode="close",
        symbols="600519,000001",
        csv="",
        source="auto",
        limit=2,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    rescored = captured["picks"]
    assert [pick.symbol for pick in rescored] == ["600519", "000001"]
    assert rescored[0].regime_score == 100.0
    assert rescored[0].score == 72.0
    assert rescored[1].regime_score == 33.33
    assert rescored[1].score == 66.0


def test_run_scheduled_skips_formal_ledger_writes_when_circuit_breaker_triggers(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    latest = "2026-06-15"
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    order: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda *_args, **_kwargs: order.append("validate"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_compute_real_pnl",
        lambda *_args, **_kwargs: order.append("pnl") or (-4.0, 0.0, 0.0),
    )
    monkeypatch.setattr(cli_mod, "_count_independent_signal_days", lambda *_, **__: 35)
    monkeypatch.setattr(cli_mod, "_detect_runtime_regime", lambda *_, **__: "")
    monkeypatch.setattr("aqsp.data.anomaly.detect_anomalies", lambda *_, **__: [])
    monkeypatch.setattr("aqsp.data.freshness.check_freshness", lambda *_, **__: [])
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_, **__: type("R", (), {"matrix": {}, "high_corr_pairs": ()})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: type(
            "C", (), {"calculate_score": lambda self, *_args, **_kwargs: {}}
        )(),
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("circuit breaker must stop before screening")
        ),
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(cli_mod, "to_dataframe", lambda picks: pd.DataFrame())
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "snapshot should not be saved while circuit breaker is active"
            )
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "formal ledger should not be written while circuit breaker is active"
            )
        ),
    )

    class TriggeredBreaker:
        def check(self, **_kwargs):
            return type(
                "Status", (), {"triggered": True, "reason": "单日组合亏损触发"}
            )()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: TriggeredBreaker())

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=False,
        notify=False,
    )

    assert cli_mod.run_scheduled(args) == 2
    assert order == ["validate", "pnl"]


def test_run_scheduled_intraday_keeps_observation_output_during_circuit_breaker(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    latest = "2026-06-15"
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "_compute_real_pnl", lambda *_args, **_kwargs: (-4.0, 0.0, 0.0))
    monkeypatch.setattr(cli_mod, "_count_independent_signal_days", lambda *_, **__: 35)
    monkeypatch.setattr(cli_mod, "_detect_runtime_regime", lambda *_, **__: "")
    monkeypatch.setattr("aqsp.data.anomaly.detect_anomalies", lambda *_, **__: [])
    monkeypatch.setattr("aqsp.data.freshness.check_freshness", lambda *_, **__: [])
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_, **__: type("R", (), {"matrix": {}, "high_corr_pairs": ()})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: type(
            "C", (), {"calculate_score": lambda self, *_args, **_kwargs: {}}
        )(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_screen_universe_with_thresholds",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=72.0,
                rating="watch",
                entry_type="observe",
                ideal_buy=1498.0,
                stop_loss=1470.0,
                take_profit=1535.0,
                position="observe",
                strategies=("observation",),
                reasons=("观察候选",),
            )
        ],
    )
    monkeypatch.setattr(cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks)
    monkeypatch.setattr(cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks)
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(cli_mod, "to_dataframe", lambda picks: pd.DataFrame([{"symbol": p.symbol} for p in picks]))
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot should stay disabled during circuit breaker")
        ),
    )
    appended_events: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "append_run_event",
        lambda *_args, **kwargs: appended_events.append(str(kwargs.get("status"))),
    )
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("formal ledger should stay disabled during circuit breaker")
        ),
    )

    class TriggeredBreaker:
        def check(self, **_kwargs):
            return type(
                "Status", (), {"triggered": True, "reason": "组合保护冷却期中，至 2026-07-01 解除"}
            )()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: TriggeredBreaker())

    args = Namespace(
        mode="open",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "intraday.md"),
        output_csv=str(tmp_path / "intraday.csv"),
        ledger=str(tmp_path / "intraday_predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=False,
        notify=False,
    )

    assert cli_mod.run_scheduled(args) == 0
    assert appended_events == ["blocked_by_circuit_breaker"]
    report_text = (tmp_path / "intraday.md").read_text(encoding="utf-8")
    assert report_text.startswith("# report")
    assert "## 组合保护" in report_text


def test_run_scheduled_logs_learning_proposal_failure(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    latest = "2026-06-15"
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "stable_bull"
    )
    monkeypatch.setattr(
        cli_mod, "_compute_real_pnl", lambda *_args, **_kwargs: (0.0, 0.0, 0.0)
    )
    monkeypatch.setattr(
        "aqsp.ledger.base.read_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ledger boom")),
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: type(
            "R", (), {"matrix": {}, "high_corr_pairs": ()}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.format_snapshot_diff", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: type(
            "C", (), {"calculate_score": lambda self, *_args, **_kwargs: {}}
        )(),
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=60.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=(),
            )
        ],
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(
        cli_mod, "to_dataframe", lambda picks: pd.DataFrame([{"symbol": "600519"}])
    )
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=True,
        notify=False,
    )

    with caplog.at_level(logging.WARNING):
        exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    assert "学习权重提案计算失败，按无提案继续: ledger boom" in caplog.text


def test_run_mine_factors_uses_full_runtime_universe_when_auto_resolving_symbols(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeMiner:
        def __init__(self, min_ic: float, min_ir: float):
            self.min_ic = min_ic
            self.min_ir = min_ir

        def mine_factors(self, frames):
            return []

    class FakeLibrary:
        def load(self) -> None:
            return None

        def add_factor(self, factor) -> bool:
            return False

        def save(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.AutoFactorMiner",
        FakeMiner,
    )
    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.FactorLibrary",
        FakeLibrary,
    )

    args = Namespace(
        source="eastmoney",
        min_ic=0.03,
        min_ir=0.5,
        output="",
        report="",
    )

    exit_code = cli_mod.run_mine_factors(args)

    assert exit_code == 0
    assert seen["pool_name"] == ""
    assert seen["symbols"] == ""
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_mine_factors_stores_results_as_inactive_research_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod

    added: list[dict] = []

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeMiner:
        def __init__(self, min_ic: float, min_ir: float):
            self.min_ic = min_ic
            self.min_ir = min_ir

        def mine_factors(self, frames):
            return [
                {
                    "name": "demo_factor",
                    "category": "price",
                    "formula": "close / open",
                    "lookback_period": 5,
                    "params": {},
                    "evaluation": {
                        "ic_mean": 0.04,
                        "ic_ir": 0.8,
                        "sample_size": 120,
                    },
                }
            ]

    class FakeLibrary:
        def load(self) -> None:
            return None

        def add_factor(self, factor) -> bool:
            added.append(factor)
            return True

        def save(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.AutoFactorMiner",
        FakeMiner,
    )
    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.FactorLibrary",
        FakeLibrary,
    )

    output = tmp_path / "factors.json"
    args = Namespace(
        source="eastmoney",
        min_ic=0.03,
        min_ir=0.5,
        output=str(output),
        report="",
    )

    exit_code = cli_mod.run_mine_factors(args)

    assert exit_code == 0
    assert added[0]["name"] == "demo_factor"
    assert added[0]["is_active"] is False
    assert added[0]["status"] == "research_candidate"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["is_active"] is False


def test_factor_library_treats_missing_active_flag_as_inactive(tmp_path: Path) -> None:
    from aqsp.strategies.auto_factor_mining import FactorLibrary

    library = FactorLibrary(str(tmp_path / "factor_library.json"))
    library.factors = [
        {"name": "legacy_missing_flag"},
        {"name": "disabled_factor", "is_active": False},
        {"name": "approved_factor", "is_active": True},
    ]

    assert [factor["name"] for factor in library.get_active_factors()] == [
        "approved_factor"
    ]

    assert library.add_factor({"name": "new_research_factor"}) is True
    assert library.factors[-1]["is_active"] is False
    assert library.factors[-1]["status"] == "research_candidate"


def test_run_discover_marks_output_as_research_only(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.optimizer.pattern_discovery import DiscoveredPattern

    monkeypatch.setattr(
        "aqsp.ledger.base.read_ledger",
        lambda _path: [{"symbol": "600519", "signal_date": "2026-06-01"}],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeEngine:
        def __init__(self, min_sample_size: int, min_win_rate: float) -> None:
            assert min_sample_size == 12
            assert min_win_rate == 0.58

        def discover(self, ledger_df, frames):
            assert not ledger_df.empty
            assert "600519" in frames
            return [
                DiscoveredPattern(
                    pattern_id="pat_demo001",
                    pattern_type="breakout",
                    description="突破后延续",
                    conditions={"lookback_days": 60},
                    historical_win_rate=0.62,
                    historical_avg_return=3.4,
                    sample_size=28,
                    confidence=0.74,
                    first_seen="2026-01-01",
                    last_seen="2026-06-01",
                )
            ]

    monkeypatch.setattr(
        "aqsp.optimizer.pattern_discovery.PatternDiscoveryEngine", FakeEngine
    )

    output_path = tmp_path / "patterns.json"
    report_path = tmp_path / "patterns.md"
    args = Namespace(
        ledger="data/predictions.jsonl",
        source="eastmoney",
        min_sample=12,
        min_winrate=0.58,
        output=str(output_path),
        report=str(report_path),
    )

    exit_code = cli_mod.run_discover(args)

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload[0]["status"] == "research_candidate"
    assert payload[0]["proposal_only"] is True
    assert payload[0]["applied"] is False
    assert payload[0]["uses_forward_returns"] is True

    report_text = report_path.read_text(encoding="utf-8")
    assert "研究形态发现报告" in report_text
    assert "仅供研究复核" in report_text
    assert "自动写入主链" in report_text


def test_run_evolve_prefers_aqsp_symbols_when_configured(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["symbols"] = symbols
        seen["pool_name"] = kwargs["pool_name"]
        return ["600519", "300750"]

    monkeypatch.setenv("AQSP_SYMBOLS", "600519,300750")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object(), "300750": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        max_universe=0,
        apply=False,
        output="",
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert seen["symbols"] == "600519,300750"
    assert seen["pool_name"] == ""
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_evolve_writes_result_when_evolution_succeeds(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.auto_evolution import EvolutionResult

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return EvolutionResult(
                strategy_name=strategy_name,
                old_params={"momentum_weight": 0.3},
                new_params={"momentum_weight": 0.4},
                performance_improvement=0.12,
                confidence=0.85,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                reason="performance_improvement",
            )

        def _apply_evolution(self, _result) -> None:
            raise AssertionError("should not apply when apply=False")

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    output = tmp_path / "evolution_result.json"
    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=False,
        output=str(output),
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["strategy_name"] == "composite"
    assert payload["new_params"]["momentum_weight"] == 0.4
    assert payload["performance_improvement"] == 0.12
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False


def test_run_evolve_apply_is_proposal_only(monkeypatch, tmp_path: Path) -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.auto_evolution import EvolutionResult

    applied = False

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return EvolutionResult(
                strategy_name=strategy_name,
                old_params={"momentum_weight": 0.3},
                new_params={"momentum_weight": 0.4},
                performance_improvement=0.12,
                confidence=0.95,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                reason="performance_improvement",
            )

        def _apply_evolution(self, _result) -> None:
            nonlocal applied
            applied = True

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    output = tmp_path / "evolution_result.json"
    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=True,
        output=str(output),
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert applied is False
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert payload["status"] == "proposal_only"


def test_run_optimize_apply_writes_proposal_without_touching_thresholds(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.optimizer.param_optimizer import OptimizationResult
    from aqsp.strategies.thresholds import Thresholds

    applied: list[dict[str, float]] = []

    monkeypatch.setattr(cli_mod, "load_thresholds", lambda: Thresholds(version="test"))
    monkeypatch.setattr(cli_mod, "_get_hs300_symbols", lambda _as_of=None: ["600519"])
    monkeypatch.setattr(cli_mod, "_walkforward_fetch_days", lambda *_args: 120)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {
            "600519": object(),
            "000001": object(),
            "000002": object(),
            "000003": object(),
            "000004": object(),
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "_apply_best_params",
        lambda params: applied.append(params),
    )

    class FakeFrame:
        empty = False

        def __getitem__(self, _key):
            return self

        def astype(self, _type):
            return self

        def __ge__(self, _other):
            return self

        def __le__(self, _other):
            return self

        def __and__(self, _other):
            return self

        @property
        def loc(self):
            return self

        def __len__(self):
            return 120

        def copy(self):
            return self

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {
            symbol: FakeFrame()
            for symbol in ["600519", "000001", "000002", "000003", "000004"]
        },
    )

    monkeypatch.setattr(
        "aqsp.optimizer.param_optimizer.create_walkforward_evaluator",
        lambda **_kwargs: lambda _params: 1.0,
    )

    class FakeOptimizer:
        def __init__(self, *_args, **_kwargs):
            return None

        def optimize(self, *_args, **_kwargs):
            return OptimizationResult(
                best_params={"composite.momentum_weight": 0.4},
                best_score=1.23,
                all_results=[],
                n_trials=1,
                method="grid",
            )

    monkeypatch.setattr(
        "aqsp.optimizer.param_optimizer.GridSearchOptimizer",
        FakeOptimizer,
    )

    output = tmp_path / "optimization_result.json"
    args = Namespace(
        method="grid",
        trials=1,
        symbols="600519,000001,000002,000003,000004",
        start="2026-01-01",
        end="2026-06-01",
        source="sqlite_db",
        engine="builtin",
        output=str(output),
        apply=True,
    )

    exit_code = cli_mod.run_optimize(args)

    assert exit_code == 0
    assert applied == []
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["best_params"] == {"composite.momentum_weight": 0.4}
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False


def test_auto_evolution_apply_writes_proposal_without_touching_thresholds(
    tmp_path: Path,
) -> None:
    from aqsp.strategies.auto_evolution import AutoEvolution, EvolutionResult

    thresholds_path = tmp_path / "thresholds.yaml"
    thresholds_path.write_text(
        "version: test\nstrategies:\n  composite:\n    momentum_weight: 0.3\n",
        encoding="utf-8",
    )
    original = thresholds_path.read_text(encoding="utf-8")
    evolution = AutoEvolution(
        thresholds_path=str(thresholds_path),
        data_dir=str(tmp_path / "evolution"),
    )
    result = EvolutionResult(
        strategy_name="composite",
        old_params={"momentum_weight": 0.3},
        new_params={"momentum_weight": 0.4},
        performance_improvement=0.12,
        confidence=0.9,
        timestamp=datetime(2026, 6, 3, 12, 0, 0),
        reason="test",
    )

    evolution._apply_evolution(result)

    assert thresholds_path.read_text(encoding="utf-8") == original
    proposal_path = tmp_path / "evolution" / "threshold_proposals.jsonl"
    payload = json.loads(proposal_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False
    assert payload["new_params"] == {"momentum_weight": 0.4}


def test_direct_walkforward_defaults_match_threshold_costs() -> None:
    from aqsp.backtest.walk_forward import WalkForwardTester
    from aqsp.research_engine import WalkForwardEngineConfig

    config = WalkForwardEngineConfig(
        train_days=120, test_days=30, purge_days=5, horizon_days=3
    )
    assert config.fee_bps == 3.0
    assert config.slippage_bps == 20.0
    tester = WalkForwardTester(strategy=object())
    assert tester.fee_bps == 3.0
    assert tester.slippage_bps == 20.0


def test_execution_cost_defaults_are_loaded_from_thresholds() -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.thresholds import Thresholds

    thresholds = Thresholds()

    assert tuple(
        round(value, 4)
        for value in cli_mod._resolve_execution_cost_bps(
            thresholds,
            fee_bps=None,
            slippage_bps=None,
        )
    ) == (3.0, 20.0)
    assert cli_mod._resolve_execution_cost_bps(
        thresholds,
        fee_bps=8.0,
        slippage_bps=5.0,
    ) == (8.0, 5.0)
