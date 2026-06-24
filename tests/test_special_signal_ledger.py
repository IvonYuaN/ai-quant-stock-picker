from __future__ import annotations

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
