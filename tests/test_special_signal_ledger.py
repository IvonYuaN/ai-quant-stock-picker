from __future__ import annotations

import pytest

from aqsp.core.errors import DataError
from aqsp.core.types import RunMetadata
from aqsp.ledger.base import ExecutionConfig, read_ledger
from aqsp.ledger.special_signals import (
    SpecialSignalLedgerRow,
    append_special_strategy_signals,
)


def _signal(strategy_id: str = "morning_breakout") -> SpecialSignalLedgerRow:
    return SpecialSignalLedgerRow(
        symbol="600000",
        name="测试股票",
        signal_close=10.5,
        score=75.0,
        strategy_id=strategy_id,
        sub_strategy="强势观察",
        reasons=("量价确认",),
        risks=("高开风险",),
        stop_loss=9.8,
        take_profit=11.0,
        confidence=0.72,
        position="20%",
        ideal_buy=10.5,
    )


def _run_metadata(**overrides: object) -> RunMetadata:
    values: dict[str, object] = {
        "requested_source": "online_first",
        "actual_source": "eastmoney",
        "source_freshness_tier": "realtime",
        "source_coverage_tier": "multi_dimensional",
        "source_local_status": "present",
        "source_health_label": "healthy",
        "source_health_message": "ok",
        "fallback_used": False,
        "explicit_symbol_count": 1,
        "resolved_symbol_count": 1,
        "fetched_frame_count": 1,
        "screened_count": 1,
        "final_count": 1,
        "min_price": 1.0,
        "max_price": 1000.0,
        "min_avg_amount": 1.0,
        "online_factors_enabled": False,
        "thresholds_version": "1.1.11",
        "data_latest_trade_date": "2026-06-22",
        "data_lag_days": 0,
        "workload": "live_short",
    }
    values.update(overrides)
    return RunMetadata(**values)


def test_append_special_strategy_signals_writes_paper_required_fields(tmp_path) -> None:
    path = tmp_path / "predictions.jsonl"

    append_special_strategy_signals(
        path,
        [_signal()],
        signal_date="2026-06-22",
        created_at="2026-06-22T15:01:00+08:00",
        thresholds_version="1.1.11",
        regime="stable_bull",
        execution=ExecutionConfig(horizon_days=2, fee_bps=3.0, slippage_bps=20.0),
    )

    rows = read_ledger(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["strategies"] == ["morning_breakout"]
    assert row["take_profit"] == 11.0
    assert row["horizon_days"] == 2
    assert row["fee_bps"] == 3.0
    assert row["slippage_bps"] == 20.0
    assert row["regime_at_signal"] == "stable_bull"
    assert row["signal_day_group"] == "2026-06-22_morning_breakout"


def test_append_special_strategy_signals_persists_live_short_provenance(
    tmp_path,
) -> None:
    path = tmp_path / "predictions.jsonl"

    append_special_strategy_signals(
        path,
        [_signal()],
        signal_date="2026-06-22",
        created_at="2026-06-22T15:01:00+08:00",
        thresholds_version="1.1.11",
        run_metadata=_run_metadata(),
    )

    row = read_ledger(path)[0]
    assert row["run_requested_source"] == "online_first"
    assert row["run_actual_source"] == "eastmoney"
    assert row["run_source_freshness_tier"] == "realtime"
    assert row["run_source_coverage_tier"] == "multi_dimensional"
    assert row["run_source_local_status"] == "present"
    assert row["run_source_health_label"] == "healthy"
    assert row["run_source_health_message"] == "ok"
    assert row["run_data_latest_trade_date"] == "2026-06-22"
    assert row["run_workload"] == "live_short"


def test_append_special_strategy_signals_fails_closed_when_live_short_provenance_is_missing(
    tmp_path,
) -> None:
    path = tmp_path / "predictions.jsonl"

    with pytest.raises(DataError, match="actual_source"):
        append_special_strategy_signals(
            path,
            [_signal()],
            signal_date="2026-06-22",
            created_at="2026-06-22T15:01:00+08:00",
            thresholds_version="1.1.11",
            run_metadata=_run_metadata(actual_source=""),
        )

    assert read_ledger(path) == []


def test_append_special_strategy_signals_fails_closed_when_live_short_metadata_is_omitted(
    tmp_path,
) -> None:
    path = tmp_path / "predictions.jsonl"

    with pytest.raises(DataError, match="run_metadata"):
        append_special_strategy_signals(
            path,
            [_signal()],
            signal_date="2026-06-22",
            created_at="2026-06-22T15:01:00+08:00",
            thresholds_version="1.1.11",
            workload="live_short",
        )

    assert read_ledger(path) == []


def test_append_special_strategy_signals_rejects_negative_live_short_lag(
    tmp_path,
) -> None:
    path = tmp_path / "predictions.jsonl"

    with pytest.raises(DataError, match="data_lag_days"):
        append_special_strategy_signals(
            path,
            [_signal()],
            signal_date="2026-06-22",
            created_at="2026-06-22T15:01:00+08:00",
            thresholds_version="1.1.11",
            run_metadata=_run_metadata(data_lag_days=-1),
        )

    assert read_ledger(path) == []


def test_append_special_strategy_signals_dedupes_and_preserves_status(tmp_path) -> None:
    path = tmp_path / "predictions.jsonl"
    kwargs = {
        "signal_date": "2026-06-22",
        "created_at": "2026-06-22T15:01:00+08:00",
        "thresholds_version": "1.1.11",
    }

    append_special_strategy_signals(path, [_signal()], **kwargs)
    rows = read_ledger(path)
    rows[0]["status"] = "entered"
    from aqsp.ledger.base import write_ledger

    write_ledger(path, rows)
    append_special_strategy_signals(path, [_signal()], **kwargs)

    rows = read_ledger(path)
    assert len(rows) == 1
    assert rows[0]["status"] == "entered"


def test_append_special_strategy_signals_keeps_distinct_sub_strategies(
    tmp_path,
) -> None:
    path = tmp_path / "predictions.jsonl"
    kwargs = {
        "signal_date": "2026-06-22",
        "created_at": "2026-06-22T15:01:00+08:00",
        "thresholds_version": "1.1.11",
    }

    append_special_strategy_signals(
        path,
        [
            _signal("closing_premium"),
            SpecialSignalLedgerRow(
                **{
                    **_signal("closing_premium").__dict__,
                    "sub_strategy": "尾盘承接",
                }
            ),
        ],
        **kwargs,
    )

    rows = read_ledger(path)
    assert len(rows) == 2
    assert {row["sub_strategy"] for row in rows} == {"强势观察", "尾盘承接"}


def test_append_special_strategy_signals_uses_advisory_lock(
    tmp_path, monkeypatch
) -> None:
    import aqsp.ledger.special_signals as module

    path = tmp_path / "predictions.jsonl"
    lock_calls: list[str] = []
    original_lock = module.advisory_lock

    def tracking_lock(lock_path):
        lock_calls.append(str(lock_path))
        return original_lock(lock_path)

    monkeypatch.setattr(module, "advisory_lock", tracking_lock)

    append_special_strategy_signals(
        path,
        [_signal()],
        signal_date="2026-06-22",
        created_at="2026-06-22T15:01:00+08:00",
        thresholds_version="1.1.11",
    )

    assert lock_calls == [str(path)]
