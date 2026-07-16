from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from aqsp.core.time import now_shanghai
from aqsp.core.types import RunMetadata
from aqsp.core.errors import DataError
from aqsp.ledger import (
    ExecutionConfig,
    execution_config_from_thresholds,
    LearnerConfig,
    PerformanceLearner,
    StrategyDecayDetector,
    append_predictions,
    append_run_event,
    ledger_rows_to_frame,
    read_ledger,
    strategy_weights_from_ledger,
    validate_predictions,
    write_ledger,
)
from aqsp.ledger.learner import format_decay_alerts
from aqsp.models import PickResult


def test_execution_config_defaults_load_from_thresholds() -> None:
    execution = execution_config_from_thresholds()

    assert execution.fee_bps == pytest.approx(3.0)
    assert execution.slippage_bps == pytest.approx(20.0)
    assert execution.benchmark_symbol == "000300"
    assert execution.limit_up_pct == pytest.approx(0.10)
    assert execution.limit_down_pct == pytest.approx(0.10)


def test_fallback_limit_pct_uses_symbol_board_thresholds(tmp_path) -> None:
    from aqsp.ledger.base import _check_executable

    entry_bar = pd.Series({"open": 120.0, "high": 120.0, "low": 120.0})
    assert _check_executable(entry_bar, 100.0, {"symbol": "300750"}) == (
        False,
        "limit_up_at_open",
    )
    main_board_bar = pd.Series({"open": 110.0, "high": 110.0, "low": 110.0})
    assert _check_executable(main_board_bar, 100.0, {"symbol": "600000"}) == (
        False,
        "limit_up_at_open",
    )
    bse_bar = pd.Series({"open": 130.0, "high": 130.0, "low": 130.0})
    assert _check_executable(bse_bar, 100.0, {"symbol": "830000"}) == (
        False,
        "limit_up_at_open",
    )


def test_check_executable_blocks_when_prev_close_missing() -> None:
    from aqsp.ledger.base import _check_executable

    entry_bar = pd.Series({"open": 10.0, "high": 10.2, "low": 9.8})

    assert _check_executable(entry_bar, math.nan, {"symbol": "600000"}) == (
        False,
        "missing_prev_close",
    )
    assert _check_executable(entry_bar, 0.0, {"symbol": "600000"}) == (
        False,
        "missing_prev_close",
    )


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


def test_append_predictions_persists_finite_short_term_metrics(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600276",
        name="恒瑞医药",
        date="2026-07-16",
        close=52.31,
        score=82,
        rating="buy_candidate",
        entry_type="ma_pullback",
        ideal_buy=52.31,
        stop_loss=48.6,
        take_profit=59.4,
        position="10%",
        strategies=("ma_pullback",),
        metrics={
            "ret5_pct": 4.25,
            "ret20_pct": 12.8,
            "volume_ratio": 1.42,
            "rsi12": 63.7,
            "bias20_pct": float("nan"),
        },
    )

    append_predictions(ledger, [pick])

    row = read_ledger(ledger)[0]
    assert row["ret5_pct"] == 4.25
    assert row["ret20_pct"] == 12.8
    assert row["volume_ratio"] == 1.42
    assert row["rsi12"] == 63.7
    assert "bias20_pct" not in row


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


def test_append_predictions_keeps_concurrent_writes(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"

    def _append(idx: int) -> None:
        pick = PickResult(
            symbol=f"600{idx:03d}",
            name=f"测试{idx}",
            date="2026-01-02",
            close=10 + idx,
            score=60 + idx,
            rating="buy_candidate",
            entry_type="volume_breakout",
            ideal_buy=10 + idx,
            stop_loss=9,
            take_profit=12,
            position="10%-30%",
            strategies=("volume_breakout",),
        )
        append_predictions(
            ledger,
            [pick],
            thresholds_version="test",
            regime="stable_bull",
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(_append, range(12)))

    rows = read_ledger(ledger)
    assert len(rows) == 12
    assert {row["symbol"] for row in rows} == {f"600{idx:03d}" for idx in range(12)}


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


def test_validation_leaves_excess_return_unknown_when_benchmark_missing(
    tmp_path,
) -> None:
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
        stop_loss=0,
        take_profit=0,
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
    assert summary.checked == 1
    assert row["benchmark_return_pct"] is None
    assert row["excess_return_pct"] is None
    assert summary.avg_excess_pct == 0.0


def test_validation_marks_not_executable_before_horizon_complete(tmp_path) -> None:
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
        execution=ExecutionConfig(
            horizon_days=3,
            fee_bps=0,
            slippage_bps=0,
            limit_up_pct=0.10,
            limit_down_pct=0.10,
        ),
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
                        "open": 11,
                        "high": 11,
                        "low": 11,
                        "close": 11,
                    },
                ]
            )
        },
    )

    row = read_ledger(ledger)[0]
    assert summary.checked == 0
    assert summary.skipped_not_executable == 1
    assert summary.not_executable_reasons == {"limit_up_at_open": 1}
    assert row["status"] == "not_executable"
    assert row["entry_date"] == "2026-01-03"
    assert row["not_executable_reason"] == "limit_up_at_open"


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


def test_strategy_weights_ignore_not_executable_rows_and_string_strategies(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        {
            "status": "validated",
            "signal_date": f"2026-01-{day:02d}",
            "return_pct": 2.0,
            "strategies": "volume_breakout",
        }
        for day in range(1, 31)
    ]
    rows.append(
        {
            "status": "not_executable",
            "signal_date": "2026-02-01",
            "return_pct": -99.0,
            "strategies": "volume_breakout",
        }
    )
    write_ledger(ledger, rows)

    weights = strategy_weights_from_ledger(ledger)

    assert weights["volume_breakout"] > 1.0


def test_performance_learner_defaults_to_30_independent_signal_days() -> None:
    assert LearnerConfig().min_independent_signal_days == 30


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


def test_learner_ignores_unresolved_rows_and_not_executable_rows_when_learning(
    tmp_path,
) -> None:
    rows = [
        {
            "status": "validated",
            "signal_date": f"2026-01-{day:02d}",
            "return_pct": 2.0,
            "strategies": ["volume_breakout"],
        }
        for day in range(1, 31)
    ]
    rows.extend(
        [
            {
                "status": "pending",
                "signal_date": "2026-02-01",
                "return_pct": -99.0,
                "strategies": ["volume_breakout"],
            },
            {
                "status": "not_executable",
                "signal_date": "2026-02-02",
                "return_pct": -99.0,
                "strategies": ["volume_breakout"],
            },
        ]
    )

    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30),
        weight_history_path=tmp_path / "weight_history.jsonl",
    )

    performance = learner.learn_from_ledger(pd.DataFrame(rows))["volume_breakout"]

    assert performance.recent_performance.independent_signal_days == 30
    assert performance.recent_performance.total_picks == 30
    assert performance.recent_performance.win_rate == 1.0


def test_validation_keeps_string_strategies_in_independent_execution_feedback(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    write_ledger(
        ledger,
        [
            {
                "status": "pending",
                "symbol": "600000",
                "signal_date": "2026-01-02",
                "rating": "buy_candidate",
                "signal_close": 10.0,
                "strategies": "volume_breakout",
            }
        ],
    )

    summary = validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {
                        "date": "2026-01-02",
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                    },
                    {
                        "date": "2026-01-03",
                        "open": 11.0,
                        "high": 11.0,
                        "low": 11.0,
                        "close": 11.0,
                    },
                ]
            )
        },
    )

    assert summary.checked == 0
    assert summary.strategy_not_executable_rates == {"volume_breakout": 1.0}


def test_validation_rejects_missing_close_with_explainable_data_error(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    write_ledger(
        ledger,
        [
            {
                "status": "pending",
                "symbol": "600000",
                "signal_date": "2026-01-02",
                "rating": "buy_candidate",
                "signal_close": 10.0,
                "strategies": ["volume_breakout"],
            }
        ],
    )

    with pytest.raises(DataError, match=r"symbol=600000.*close"):
        validate_predictions(
            ledger,
            {
                "600000": pd.DataFrame(
                    [
                        {
                            "date": "2026-01-02",
                            "open": 10.0,
                            "high": 10.0,
                            "low": 10.0,
                        },
                        {
                            "date": "2026-01-03",
                            "open": 11.0,
                            "high": 11.0,
                            "low": 11.0,
                        },
                    ]
                )
            },
        )


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


def test_learner_does_not_write_weight_history_by_default(tmp_path) -> None:
    entries = _make_validated_entries(30, "volume_breakout", return_pct=2.0)
    df = pd.DataFrame([json.loads(e) for e in entries])
    history_path = tmp_path / "weight_history.jsonl"
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30),
        weight_history_path=history_path,
    )
    learner.learn_from_ledger(df)
    assert not history_path.exists()


def test_learner_writes_weight_history_when_explicitly_recording(tmp_path) -> None:
    entries = _make_validated_entries(30, "volume_breakout", return_pct=2.0)
    df = pd.DataFrame([json.loads(e) for e in entries])
    history_path = tmp_path / "weight_history.jsonl"
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30),
        weight_history_path=history_path,
    )
    learner.learn_from_ledger(df, record_history=True)
    assert history_path.exists()
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["strategy"] == "volume_breakout"
    assert "old_weight" in record
    assert "new_weight" in record


def test_learner_uses_weight_history_for_restart_cooldown(tmp_path) -> None:
    entries = _make_validated_entries(30, "volume_breakout", return_pct=4.0)
    df = pd.DataFrame([json.loads(e) for e in entries])
    history_path = tmp_path / "weight_history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "timestamp": now_shanghai().isoformat(timespec="seconds"),
                "strategy": "volume_breakout",
                "old_weight": 1.0,
                "new_weight": 0.8,
                "reason": "learner_update",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    learner = PerformanceLearner(
        config=LearnerConfig(
            min_independent_signal_days=30,
            weight_change_cooldown_days=30,
        ),
        weight_history_path=history_path,
    )
    weights = learner.compute_weights(df)
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()

    assert weights["volume_breakout"] == 0.8
    assert len(lines) == 1


def test_learner_weights_use_recent_rolling_window(tmp_path) -> None:
    rows = []
    for i, signal_date in enumerate(pd.date_range("2026-01-01", periods=30)):
        rows.append(
            {
                "status": "validated",
                "signal_date": signal_date.date().isoformat(),
                "return_pct": 4.0,
                "strategies": ["volume_breakout"],
            }
        )
    for signal_date in pd.date_range("2026-05-01", periods=30):
        rows.append(
            {
                "status": "validated",
                "signal_date": signal_date.date().isoformat(),
                "return_pct": -4.0,
                "strategies": ["volume_breakout"],
            }
        )
    learner = PerformanceLearner(
        config=LearnerConfig(
            min_independent_signal_days=30,
            rolling_window_days=45,
        ),
        weight_history_path=tmp_path / "weight_history.jsonl",
    )

    weights = learner.compute_weights(pd.DataFrame(rows))

    assert weights["volume_breakout"] < 1.0


def test_learner_records_regime_weight_history_when_enabled(tmp_path) -> None:
    rows = [
        {
            "status": "validated",
            "signal_date": f"2026-05-{day:02d}",
            "return_pct": 3.0,
            "strategies": ["volume_breakout"],
            "regime_at_signal": "bull",
        }
        for day in range(1, 31)
    ]
    history_path = tmp_path / "weight_history.jsonl"
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=30, by_regime=True),
        weight_history_path=history_path,
    )

    perf = learner.learn_from_ledger(pd.DataFrame(rows), record_history=True)[
        "volume_breakout"
    ]

    assert perf.regime_weights is not None
    assert perf.regime_weights["bull"] > 1.0
    history = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(item["strategy"] == "volume_breakout:bull" for item in history)


def test_decay_detector_accepts_naive_signal_dates() -> None:
    df = pd.DataFrame(
        [
            {
                "status": "validated",
                "signal_date": signal_date,
                "return_pct": -3.0,
                "strategies": ["volume_breakout"],
            }
            for signal_date in ("2026-06-01", "2026-06-02")
        ]
    )

    alerts = StrategyDecayDetector(lookback_days=30).detect(df)

    assert isinstance(alerts, list)


def test_decay_detector_formats_lookback_days_when_alert_triggered() -> None:
    df = pd.DataFrame(
        [
            {
                "status": "validated",
                "signal_date": "2026-06-01",
                "return_pct": -3.0,
                "strategies": ["volume_breakout"],
            },
            {
                "status": "validated",
                "signal_date": "2026-06-02",
                "return_pct": -2.0,
                "strategies": ["volume_breakout"],
            },
        ]
    )

    alerts = StrategyDecayDetector(lookback_days=30).detect(df)
    text = format_decay_alerts(alerts)

    assert "近30天胜率" in text


def test_learner_tolerates_scalar_or_nan_strategies(tmp_path) -> None:
    rows = pd.DataFrame(
        [
            {
                "status": "validated",
                "signal_date": "2026-06-01",
                "return_pct": 2.0,
                "strategies": "volume_breakout",
            },
            {
                "status": "validated",
                "signal_date": "2026-06-02",
                "return_pct": -1.0,
                "strategies": 1.23,
            },
            {
                "status": "validated",
                "signal_date": "2026-06-03",
                "return_pct": 1.0,
                "strategies": float("nan"),
            },
        ]
    )
    learner = PerformanceLearner(
        config=LearnerConfig(min_independent_signal_days=1),
        weight_history_path=tmp_path / "weight_history.jsonl",
    )

    result = learner.learn_from_ledger(rows)

    assert "volume_breakout" in result
    assert "1.23" in result


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
    assert summary.not_executable_reasons == {"limit_up_at_open": 1}
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
        circuit_breaker_triggered=True,
        circuit_breaker_reason="单日亏损触发",
        market_context_overview="海外风险偏好改善",
        market_context_lines=("运行判定: HMM stable_bull", "跨市主线: AI 算力"),
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
    assert row["run_circuit_breaker_triggered"] is True
    assert row["run_circuit_breaker_reason"] == "单日亏损触发"
    assert row["run_market_context_overview"] == "海外风险偏好改善"
    assert row["run_market_context_lines"] == [
        "运行判定: HMM stable_bull",
        "跨市主线: AI 算力",
    ]
    assert row["thresholds_version"] == "1.0.0"


def test_formal_ledger_rejects_missing_provenance_when_workload_is_live_short(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-07-13",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9.5,
        take_profit=11,
        position="10%-30%",
    )
    metadata = RunMetadata(
        requested_source="online_first",
        actual_source="eastmoney",
        source_freshness_tier="realtime",
        source_coverage_tier="multi_dimensional",
        source_local_status="present",
        source_health_label="healthy",
        source_health_message="ok",
        fallback_used=False,
        explicit_symbol_count=1,
        resolved_symbol_count=1,
        fetched_frame_count=1,
        screened_count=1,
        final_count=1,
        min_price=1,
        max_price=1000,
        min_avg_amount=1,
        online_factors_enabled=False,
        thresholds_version="2026.07.13",
        data_latest_trade_date="2026-07-13",
        data_lag_days=0,
        workload="live_short",
    )
    with pytest.raises(DataError, match="source_health_label"):
        metadata = RunMetadata(
            **{
                **metadata.__dict__,
                "source_health_label": "",
            }
        )
        append_predictions(ledger, [pick], run_metadata=metadata)


def test_formal_ledger_persists_workload_provenance(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    pick = PickResult(
        symbol="600000",
        name="测试",
        date="2026-07-13",
        close=10,
        score=70,
        rating="buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=10,
        stop_loss=9.5,
        take_profit=11,
        position="10%-30%",
    )
    metadata = RunMetadata(
        requested_source="online_first",
        actual_source="eastmoney",
        source_freshness_tier="realtime",
        source_coverage_tier="multi_dimensional",
        source_local_status="present",
        source_health_label="healthy",
        source_health_message="ok",
        fallback_used=False,
        explicit_symbol_count=1,
        resolved_symbol_count=1,
        fetched_frame_count=1,
        screened_count=1,
        final_count=1,
        min_price=1,
        max_price=1000,
        min_avg_amount=1,
        online_factors_enabled=False,
        thresholds_version="2026.07.13",
        data_latest_trade_date="2026-07-13",
        data_lag_days=0,
        task_id="intraday",
        workload="live_short",
    )
    append_predictions(ledger, [pick], run_metadata=metadata)
    assert read_ledger(ledger)[0]["run_workload"] == "live_short"


def test_append_run_event_records_circuit_breaker_without_signal_payload(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    metadata = RunMetadata(
        requested_source="auto",
        actual_source="tdx_vipdoc",
        source_freshness_tier="end_of_day",
        source_coverage_tier="history_core",
        source_local_status="present",
        source_health_label="healthy",
        source_health_message="ok",
        fallback_used=False,
        explicit_symbol_count=0,
        resolved_symbol_count=5000,
        fetched_frame_count=5000,
        screened_count=20,
        final_count=0,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000,
        online_factors_enabled=False,
        thresholds_version="1.0.0",
        data_latest_trade_date="2026-06-17",
        data_lag_days=0,
        regime="bear",
        max_universe=0,
        task_id="daily",
        circuit_breaker_triggered=True,
        circuit_breaker_reason="单日亏损触发",
        market_context_overview="组合保护期只观察",
        market_context_lines=("运行判定: HMM bear",),
    )

    append_run_event(
        ledger,
        event_date="2026-06-17",
        status="blocked_by_circuit_breaker",
        reason="单日亏损触发",
        run_metadata=metadata,
        details={"daily_pnl_pct": -8.2},
    )
    append_run_event(
        ledger,
        event_date="2026-06-17",
        status="blocked_by_circuit_breaker",
        reason="单日亏损触发",
        run_metadata=metadata,
        details={"daily_pnl_pct": -8.2},
    )

    rows = read_ledger(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "__RUN__"
    assert row["status"] == "blocked_by_circuit_breaker"
    assert row["reason"] == "单日亏损触发"
    assert row["daily_pnl_pct"] == -8.2
    assert row["run_circuit_breaker_triggered"] is True
    assert row["run_resolved_symbol_count"] == 5000
    assert row["run_market_context_overview"] == "组合保护期只观察"
    assert row["run_market_context_lines"] == ["运行判定: HMM bear"]


def test_append_predictions_persists_portfolio_and_debate_fields(tmp_path) -> None:
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
        metrics={
            "portfolio_action": "promote",
            "candidate_status": "延续上升",
            "candidate_blocker": "",
            "candidate_next_step": "先复核数据质量: 最新日期延迟4天",
            "candidate_review_window": "盘中确认后",
            "candidate_review_priority": "high",
            "data_quality_status": "critical",
            "data_quality_alerts": ("最新日期延迟4天", "开盘跳空: +12.00%"),
            "stop_method": "atr_trailing",
            "sector": "公用事业",
            "industry": "电力",
            "strategy_weight_snapshot": {
                "regime": "stable_bull",
                "strategy_weights": {"volume_breakout": 1.2},
                "base_blend_weight": 0.7,
                "regime_blend_weight": 0.3,
            },
            "composite_score_raw": 0.812345,
            "composite_score_normalized": 91.2345,
            "base_score_before_composite": 72.0,
            "final_score_after_composite": 77.77,
            "debate_id": "debate-600900-20260529",
            "debate_disagreement_score": 0.42,
            "debate_final_vote": {
                "bull": "bullish",
                "risk_control": "neutral",
                "cross_market": "bullish",
            },
            "debate_active_roles": ("bull", "risk_control", "cross_market"),
            "debate_active_role_summary": "技术多头、风控、跨市传导",
            "debate_role_selection_summary": "因外盘风险偏好修复加入跨市传导",
            "debate_role_selection_plan": "多头看技术，风控看回撤，跨市看传导链",
            "debate_research_verdict": "倾向优先纸面复核",
            "debate_primary_risk_gate": "需确认电力防御持续性",
            "debate_next_trigger": "先确认高开后承接",
            "support_points": ("外盘风险偏好改善，对权重修复形成支撑",),
            "opposition_points": ("若只是单日脉冲，次日承接可能不足",),
            "watch_items": ("观察次日竞价是否继续强化权重修复",),
            "role_reliability_lines": ("跨市场: 近21天 7/10 (70%)｜当前权重 0.18",),
            "debate_historical_context_note": "历史校验: 强证据 2/3 (67%)；冲突主导 1/3",
            "debate_historical_context_bucket": "strong_supportive",
            "debate_historical_context_sample_count": 3,
            "debate_historical_context_accuracy": 2 / 3,
            "cross_market_primary_theme": "外盘风险偏好修复",
            "cross_market_linkage_basis": "风险偏好映射",
            "cross_market_action": "重点跟踪",
            "cross_market_strength": "中",
            "cross_market_lead_window": "次日竞价-1日",
            "cross_market_observation_window": "次日-3日",
            "cross_market_priority_score": 2,
            "cross_market_themes": ("外盘风险偏好修复",),
            "cross_market_rule_ids": ("us_risk_on",),
            "cross_market_first_order_targets": (
                "AI链高弹性",
                "算力/芯片",
                "机器人成长",
            ),
            "cross_market_second_order_targets": (
                "软件",
                "半导体设备",
                "科创弹性标的",
            ),
            "cross_market_pressure_targets": ("高股息防御",),
            "cross_market_execution_watchpoints": ("次日竞价成长方向是否强于防御",),
            "cross_market_transmission_path": (
                "美股科技与风险资产先修复风险偏好",
                "A股高弹性成长与AI链在竞价和早盘先反馈",
            ),
            "cross_market_validation_signals": ("次日竞价高弹性方向明显强于防御方向",),
            "cross_market_invalidation_signals": ("美股强但A股竞价无明显风险偏好跟随",),
            "cross_market_chain_summary": "风险偏好映射｜领先窗 次日竞价-1日｜确认 次日竞价高弹性方向明显强于防御方向｜失效 美股强但A股竞价无明显风险偏好跟随",
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 1,
            "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
            "news_catalyst_judgement": "supports",
            "news_catalyst_priority_score": 3,
            "news_catalyst_support_count": 1,
            "news_catalyst_oppose_count": 0,
            "news_catalyst_review_count": 1,
            "news_catalyst_supports": ("英伟达 Physical AI 平台发布",),
            "news_catalyst_opposes": (),
            "news_catalyst_needs_review": ("海外映射需要A股竞价确认",),
            "news_catalyst_lead": "600900 长江电力 偏多｜风险偏好修复｜美股科技大涨",
            "news_catalyst_source": "Reuters",
            "news_catalyst_url": "https://example.com/news",
        },
        adjusted_score=75.5,
        recommended_adjustment="raise",
        debate_consensus="bullish",
        confidence=83.0,
        regime_score=68.0,
    )

    append_predictions(
        ledger,
        [pick],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )

    row = read_ledger(ledger)[0]
    assert row["portfolio_action"] == "promote"
    assert row["candidate_status"] == "延续上升"
    assert row["candidate_next_step"] == "先复核数据质量: 最新日期延迟4天"
    assert row["candidate_review_window"] == "盘中确认后"
    assert row["candidate_review_priority"] == "high"
    assert row["data_quality_status"] == "critical"
    assert row["data_quality_alerts"] == ["最新日期延迟4天", "开盘跳空: +12.00%"]
    assert row["stop_method"] == "atr_trailing"
    assert row["adjusted_score"] == 75.5
    assert row["recommended_adjustment"] == "raise"
    assert row["debate_consensus"] == "bullish"
    assert row["debate_id"] == "debate-600900-20260529"
    assert row["debate_disagreement_score"] == 0.42
    assert row["debate_final_vote"] == {
        "bull": "bullish",
        "risk_control": "neutral",
        "cross_market": "bullish",
    }
    assert row["debate_active_roles"] == ["bull", "risk_control", "cross_market"]
    assert row["debate_active_role_summary"] == "技术多头、风控、跨市传导"
    assert row["debate_role_selection_summary"] == "因外盘风险偏好修复加入跨市传导"
    assert row["debate_role_selection_plan"] == "多头看技术，风控看回撤，跨市看传导链"
    assert row["debate_research_verdict"] == "倾向优先纸面复核"
    assert row["debate_primary_risk_gate"] == "需确认电力防御持续性"
    assert row["debate_next_trigger"] == "先确认高开后承接"
    assert row["support_points"] == ["外盘风险偏好改善，对权重修复形成支撑"]
    assert row["opposition_points"] == ["若只是单日脉冲，次日承接可能不足"]
    assert row["watch_items"] == ["观察次日竞价是否继续强化权重修复"]
    assert row["role_reliability_lines"] == ["跨市场: 近21天 7/10 (70%)｜当前权重 0.18"]
    assert (
        row["debate_historical_context_note"]
        == "历史校验: 强证据 2/3 (67%)；冲突主导 1/3"
    )
    assert row["debate_historical_context_bucket"] == "strong_supportive"
    assert row["debate_historical_context_sample_count"] == 3
    assert row["debate_historical_context_accuracy"] == 2 / 3
    assert row["cross_market_primary_theme"] == "外盘风险偏好修复"
    assert row["cross_market_linkage_basis"] == "风险偏好映射"
    assert row["cross_market_action"] == "重点跟踪"
    assert row["cross_market_lead_window"] == "次日竞价-1日"
    assert row["cross_market_first_order_targets"] == [
        "AI链高弹性",
        "算力/芯片",
        "机器人成长",
    ]
    assert row["cross_market_pressure_targets"] == ["高股息防御"]
    assert row["cross_market_validation_signals"] == [
        "次日竞价高弹性方向明显强于防御方向"
    ]
    assert row["cross_market_invalidation_signals"] == [
        "美股强但A股竞价无明显风险偏好跟随"
    ]
    assert row["cross_market_support_event_count"] == 2
    assert row["cross_market_conflict_event_count"] == 1
    assert row["cross_market_evidence_stack_summary"] == "同向 2 条｜反向 1 条"
    assert row["news_catalyst_judgement"] == "supports"
    assert row["news_catalyst_priority_score"] == 3
    assert row["news_catalyst_support_count"] == 1
    assert row["news_catalyst_supports"] == ["英伟达 Physical AI 平台发布"]
    assert row["news_catalyst_needs_review"] == ["海外映射需要A股竞价确认"]
    assert (
        row["news_catalyst_lead"] == "600900 长江电力 偏多｜风险偏好修复｜美股科技大涨"
    )
    assert row["news_catalyst_source"] == "Reuters"
    assert row["news_catalyst_url"] == "https://example.com/news"
    assert row["confidence"] == 83.0
    assert row["strategy_weight_snapshot"]["strategy_weights"] == {
        "volume_breakout": 1.2
    }
    assert row["strategy_weight_snapshot"]["base_blend_weight"] == 0.7
    assert row["composite_score_raw"] == 0.812345
    assert row["composite_score_normalized"] == 91.2345
    assert row["base_score_before_composite"] == 72.0
    assert row["final_score_after_composite"] == 77.77
    assert row["regime_score"] == 68.0
    assert row["sector"] == "公用事业"
    assert row["industry"] == "电力"


def test_append_predictions_updates_existing_row_for_same_signal_key(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    original = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=60,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="watch",
        metrics={"portfolio_action": "keep"},
    )
    updated = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.8,
        score=72,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.8,
        stop_loss=26.5,
        take_profit=31.5,
        position="30%-50%",
        metrics={"portfolio_action": "promote"},
        adjusted_score=75.5,
        recommended_adjustment="raise",
        debate_consensus="bullish",
        confidence=83.0,
        regime_score=68.0,
    )

    append_predictions(
        ledger,
        [original],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )
    append_predictions(
        ledger,
        [updated],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )

    rows = read_ledger(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["score"] == 72
    assert row["rating"] == "strong_buy_candidate"
    assert row["portfolio_action"] == "promote"
    assert row["status"] == "pending"
    assert row["adjusted_score"] == 75.5


def test_append_predictions_preserves_validated_row_when_same_signal_key(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    original = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=60,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="10%-30%",
        metrics={"portfolio_action": "keep"},
    )
    updated = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=99.9,
        score=99,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=99.9,
        stop_loss=88.8,
        take_profit=120.0,
        position="watch",
        metrics={"portfolio_action": "demote"},
    )

    append_predictions(
        ledger,
        [original],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )
    rows = read_ledger(ledger)
    rows[0].update(
        {
            "status": "validated",
            "entry_date": "2026-06-01",
            "entry_price": 28.0,
            "exit_date": "2026-06-03",
            "exit_price": 29.0,
            "return_pct": 3.5714,
            "win": True,
        }
    )
    write_ledger(ledger, rows)

    append_predictions(
        ledger,
        [updated],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )

    row = read_ledger(ledger)[0]
    assert row["status"] == "validated"
    assert row["score"] == 60
    assert row["rating"] == "buy_candidate"
    assert row["portfolio_action"] == "keep"
    assert row["entry_price"] == 28.0
    assert row["return_pct"] == 3.5714
    assert row["win"] is True


def test_append_predictions_preserves_not_executable_row_when_same_signal_key(
    tmp_path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    original = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=60,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="10%-30%",
    )
    updated = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.8,
        score=72,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.8,
        stop_loss=26.5,
        take_profit=31.5,
        position="30%-50%",
    )

    append_predictions(
        ledger,
        [original],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )
    rows = read_ledger(ledger)
    rows[0].update(
        {
            "status": "not_executable",
            "entry_date": "2026-06-01",
            "not_executable_reason": "limit_up_at_open",
        }
    )
    write_ledger(ledger, rows)

    append_predictions(
        ledger,
        [updated],
        thresholds_version="1.0.0",
        regime="stable_bull",
    )

    row = read_ledger(ledger)[0]
    assert row["status"] == "not_executable"
    assert row["score"] == 60
    assert row["not_executable_reason"] == "limit_up_at_open"
    assert row["entry_date"] == "2026-06-01"
