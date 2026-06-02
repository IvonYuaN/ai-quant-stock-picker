from __future__ import annotations

import json

import pandas as pd

from aqsp.core.types import RunMetadata
from aqsp.ledger import (
    ExecutionConfig,
    LearnerConfig,
    PerformanceLearner,
    append_predictions,
    ledger_rows_to_frame,
    read_ledger,
    strategy_weights_from_ledger,
    validate_predictions,
)
from aqsp.models import PickResult


def test_ledger_validates_pending_prediction(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-01-02",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9.5,
        take_profit=11,
        position="10%-30%",
        strategies=("volume_breakout",),
    )
    append_predictions(
        ledger,
        [pick],
        execution=ExecutionConfig(horizon_days=1, fee_bps=0, slippage_bps=0),
    )
    summary = validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {
                        "date": "2026-01-02",
                        "open": 9.9,
                        "high": 10.1,
                        "low": 9.8,
                        "close": 10,
                    },
                    {
                        "date": "2026-01-03",
                        "open": 10.1,
                        "high": 10.6,
                        "low": 10.0,
                        "close": 10.5,
                    },
                ]
            )
        },
    )
    rows = read_ledger(ledger)
    assert summary.checked == 1
    assert summary.wins == 1
    assert rows[0]["status"] == "validated"
    assert rows[0]["entry_price"] == 10.1
    assert rows[0]["return_pct"] == 3.9604


def test_append_predictions_is_idempotent_for_same_signal_run(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-01-02",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9.5,
        take_profit=11,
        position="10%-30%",
        strategies=("volume_breakout",),
    )

    for _ in range(2):
        append_predictions(
            ledger,
            [pick],
            execution=ExecutionConfig(horizon_days=1, fee_bps=0, slippage_bps=0),
            thresholds_version="2026.05.29",
            regime="stable_bull",
        )

    rows = read_ledger(ledger)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600000"
    assert rows[0]["thresholds_version"] == "2026.05.29"


def test_ledger_rows_to_frame_returns_dataframe_when_rows_exist() -> None:
    rows = [{"symbol": "600000", "signal_date": "2026-01-02", "status": "pending"}]

    df = ledger_rows_to_frame(rows)

    assert list(df["symbol"]) == ["600000"]
    assert list(df["status"]) == ["pending"]


def test_validation_uses_next_open_not_signal_close(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-01-02",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9,
        take_profit=20,
        position="10%-30%",
        strategies=("volume_breakout",),
    )
    append_predictions(
        ledger,
        [pick],
        execution=ExecutionConfig(horizon_days=1, fee_bps=10, slippage_bps=10),
    )
    validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {
                        "date": "2026-01-02",
                        "open": 10,
                        "high": 10,
                        "low": 10,
                        "close": 10,
                    },
                    {
                        "date": "2026-01-03",
                        "open": 11,
                        "high": 11.2,
                        "low": 10.8,
                        "close": 11,
                    },
                ]
            )
        },
    )
    row = read_ledger(ledger)[0]
    assert row["entry_price"] == 11.011
    assert row["return_pct"] < -0.15


def _make_validated_entries(
    count: int, strategy: str, return_pct: float = 2.0
) -> list[str]:
    entries = []
    for i in range(count):
        month = 1 + i // 28
        day = 1 + i % 28
        entries.append(
            json.dumps(
                {
                    "status": "validated",
                    "signal_date": f"2026-{month:02d}-{day:02d}",
                    "return_pct": return_pct,
                    "strategies": [strategy],
                }
            )
        )
    return entries


def test_strategy_weights_need_enough_history(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    entries = _make_validated_entries(30, "volume_breakout", return_pct=2.0)
    ledger.write_text("\n".join(entries) + "\n", encoding="utf-8")
    weights = strategy_weights_from_ledger(ledger)
    assert weights["volume_breakout"] > 1.0


def test_strategy_weights_rejects_insufficient_signal_days(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    entries = _make_validated_entries(5, "volume_breakout", return_pct=2.0)
    ledger.write_text("\n".join(entries) + "\n", encoding="utf-8")
    weights = strategy_weights_from_ledger(ledger)
    assert "volume_breakout" not in weights


def test_learner_filters_not_executable(tmp_path) -> None:
    entries = []
    for i in range(35):
        status = "not_executable" if i < 10 else "validated"
        month = 1 + i // 28
        day = 1 + i % 28
        entries.append(
            json.dumps(
                {
                    "status": status,
                    "signal_date": f"2026-{month:02d}-{day:02d}",
                    "return_pct": 2.0,
                    "strategies": ["volume_breakout"],
                }
            )
        )
    df = pd.DataFrame([json.loads(e) for e in entries])
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30),
        weight_history_path=tmp_path / "weight_history.jsonl",
    )
    result = learner.learn_from_ledger(df)
    perf = result["volume_breakout"]
    assert perf.recent_performance.total_picks == 25
    assert perf.recent_performance.win_rate == 1.0


def test_learner_aggregates_per_signal_day(tmp_path) -> None:
    entries = []
    for i in range(30):
        month = 1 + i // 28
        day = 1 + i % 28
        sd = f"2026-{month:02d}-{day:02d}"
        entries.append(
            json.dumps(
                {
                    "status": "validated",
                    "signal_date": sd,
                    "return_pct": 2.0,
                    "strategies": ["volume_breakout"],
                }
            )
        )
        entries.append(
            json.dumps(
                {
                    "status": "validated",
                    "signal_date": sd,
                    "return_pct": 4.0,
                    "strategies": ["volume_breakout"],
                }
            )
        )
    df = pd.DataFrame([json.loads(e) for e in entries])
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30),
        weight_history_path=tmp_path / "weight_history.jsonl",
    )
    result = learner.learn_from_ledger(df)
    perf = result["volume_breakout"]
    assert perf.recent_performance.independent_signal_days == 30
    assert perf.recent_performance.total_picks == 60
    assert abs(perf.recent_performance.avg_return - 0.03) < 1e-4


def test_learner_writes_weight_history(tmp_path) -> None:
    entries = _make_validated_entries(30, "volume_breakout", return_pct=2.0)
    df = pd.DataFrame([json.loads(e) for e in entries])
    history_path = tmp_path / "weight_history.jsonl"
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30),
        weight_history_path=history_path,
    )
    learner.learn_from_ledger(df)
    assert history_path.exists()
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["strategy"] == "volume_breakout"
    assert "old_weight" in record
    assert "new_weight" in record


def test_validation_marks_limit_up_at_open_not_executable(tmp_path) -> None:
    """P0-2: 一字涨停信号 status=not_executable,不计入胜率。"""
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-01-02",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9,
        take_profit=20,
        position="10%-30%",
        strategies=("volume_breakout",),
    )
    append_predictions(
        ledger,
        [pick],
        execution=ExecutionConfig(horizon_days=1, fee_bps=0, slippage_bps=0),
    )
    summary = validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {
                        "date": "2026-01-02",
                        "open": 10,
                        "high": 10,
                        "low": 10,
                        "close": 10,
                        "volume": 1000,
                    },
                    # 次日一字涨停:开 = 高 = 低 = 11,涨幅 10%
                    {
                        "date": "2026-01-03",
                        "open": 11,
                        "high": 11,
                        "low": 11,
                        "close": 11,
                        "volume": 100,
                    },
                ]
            )
        },
    )
    rows = read_ledger(ledger)
    assert summary.checked == 0
    assert summary.skipped_not_executable == 1
    assert rows[0]["status"] == "not_executable"
    assert rows[0]["not_executable_reason"] == "limit_up_at_open"


def test_validation_executes_when_open_below_limit_up(tmp_path) -> None:
    """对照组:开盘没顶到涨停,正常计入。"""
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-01-02",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9,
        take_profit=20,
        position="10%-30%",
        strategies=("volume_breakout",),
    )
    append_predictions(
        ledger,
        [pick],
        execution=ExecutionConfig(horizon_days=1, fee_bps=0, slippage_bps=0),
    )
    summary = validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {
                        "date": "2026-01-02",
                        "open": 10,
                        "high": 10,
                        "low": 10,
                        "close": 10,
                        "volume": 1000,
                    },
                    {
                        "date": "2026-01-03",
                        "open": 10.5,
                        "high": 11,
                        "low": 10.4,
                        "close": 10.8,
                        "volume": 1000,
                    },
                ]
            )
        },
    )
    rows = read_ledger(ledger)
    assert summary.checked == 1
    assert summary.skipped_not_executable == 0
    assert rows[0]["status"] == "validated"


def test_validation_marks_watch_signal_as_watch_only(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-01-02",
        close=10,
        score=45,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=10,
        stop_loss=9,
        take_profit=20,
        position="watch",
        strategies=("low_vol_trend",),
    )
    append_predictions(
        ledger,
        [pick],
        execution=ExecutionConfig(horizon_days=1, fee_bps=0, slippage_bps=0),
    )
    summary = validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {
                        "date": "2026-01-02",
                        "open": 10,
                        "high": 10,
                        "low": 10,
                        "close": 10,
                    },
                    {
                        "date": "2026-01-03",
                        "open": 10.5,
                        "high": 11,
                        "low": 10.4,
                        "close": 10.8,
                    },
                ]
            )
        },
    )

    rows = read_ledger(ledger)
    assert summary.checked == 0
    assert summary.wins == 0
    assert rows[0]["status"] == "watch_only"


def test_append_predictions_writes_run_metadata_when_provided(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
        strategies=("rps_relative_strength",),
    )
    metadata = RunMetadata(
        requested_source="auto",
        actual_source="tdx_vipdoc",
        source_freshness_tier="end_of_day",
        source_coverage_tier="history_core",
        source_local_status="present",
        source_health_label="healthy",
        source_health_message="tdx_vipdoc 健康；源成功/失败 3/0",
        fallback_used=False,
        explicit_symbol_count=0,
        resolved_symbol_count=100,
        fetched_frame_count=101,
        screened_count=8,
        final_count=1,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000,
        online_factors_enabled=False,
        thresholds_version="1.0.0",
        data_latest_trade_date="2026-05-29",
        data_lag_days=0,
        regime="stable_bull",
        max_universe=100,
    )

    append_predictions(
        ledger,
        [pick],
        thresholds_version="1.0.0",
        regime="stable_bull",
        run_metadata=metadata,
    )

    row = read_ledger(ledger)[0]
    assert row["run_requested_source"] == "auto"
    assert row["run_actual_source"] == "tdx_vipdoc"
    assert row["run_source_freshness_tier"] == "end_of_day"
    assert row["run_source_coverage_tier"] == "history_core"
    assert row["run_source_local_status"] == "present"
    assert row["run_source_health_label"] == "healthy"
    assert row["run_source_health_message"] == "tdx_vipdoc 健康；源成功/失败 3/0"
    assert row["run_fallback_used"] is False
    assert row["run_resolved_symbol_count"] == 100
    assert row["run_fetched_frame_count"] == 101
    assert row["run_final_count"] == 1
    assert row["run_online_factors_enabled"] is False
    assert row["run_data_latest_trade_date"] == "2026-05-29"
    assert row["run_data_lag_days"] == 0
