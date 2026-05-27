from __future__ import annotations

import pandas as pd

from aqsp.ledger import ExecutionConfig, append_predictions, read_ledger, strategy_weights_from_ledger, validate_predictions
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
    append_predictions(ledger, [pick], execution=ExecutionConfig(horizon_days=1, fee_bps=0, slippage_bps=0))
    summary = validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {"date": "2026-01-02", "open": 9.9, "high": 10.1, "low": 9.8, "close": 10},
                    {"date": "2026-01-03", "open": 10.1, "high": 10.6, "low": 10.0, "close": 10.5},
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
    append_predictions(ledger, [pick], execution=ExecutionConfig(horizon_days=1, fee_bps=10, slippage_bps=10))
    validate_predictions(
        ledger,
        {
            "600000": pd.DataFrame(
                [
                    {"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
                    {"date": "2026-01-03", "open": 11, "high": 11.2, "low": 10.8, "close": 11},
                ]
            )
        },
    )
    row = read_ledger(ledger)[0]
    assert row["entry_price"] == 11.011
    assert row["return_pct"] < -0.15


def test_strategy_weights_need_enough_history(tmp_path) -> None:
    ledger = tmp_path / "predictions.jsonl"
    ledger.write_text(
        "\n".join(
            [
                '{"status":"validated","return_pct":2,"strategies":["volume_breakout"]}',
                '{"status":"validated","return_pct":3,"strategies":["volume_breakout"]}',
                '{"status":"validated","return_pct":-1,"strategies":["volume_breakout"]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    weights = strategy_weights_from_ledger(ledger)
    assert weights["volume_breakout"] > 1.0
