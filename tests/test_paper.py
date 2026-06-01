from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aqsp.paper import (
    PaperSummary,
    read_paper_trades,
    render_paper_report,
    sync_paper_trades,
)


def _write_signal(path: Path, **overrides: object) -> None:
    row = {
        "id": "sig-1",
        "status": "pending",
        "signal_date": "2026-05-27",
        "symbol": "600519",
        "name": "贵州茅台",
        "signal_close": 100.0,
        "rating": "buy_candidate",
        "score": 70,
        "strategies": ["momentum"],
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "horizon_days": 2,
        "fee_bps": 0,
        "slippage_bps": 0,
    }
    row.update(overrides)
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-05-27",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000,
            },
            {
                "date": "2026-05-28",
                "open": 101.0,
                "high": 104.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1000,
            },
            {
                "date": "2026-05-29",
                "open": 103.0,
                "high": 105.0,
                "low": 102.0,
                "close": 104.0,
                "volume": 1000,
            },
        ]
    )


def test_paper_skips_avoid_signals(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, rating="avoid")

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    assert summary.opened == 0
    assert summary.skipped == 1
    assert read_paper_trades(trades) == []


def test_paper_skips_watch_signals(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, rating="watch")

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    assert summary.opened == 0
    assert summary.skipped == 1
    assert read_paper_trades(trades) == []


def test_paper_opens_trade_from_next_open(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, horizon_days=3)

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    rows = read_paper_trades(trades)
    assert summary.opened == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["entry_date"] == "2026-05-28"
    assert rows[0]["entry_price"] == 101.0


def test_paper_marks_signal_pending_entry_when_next_open_missing(
    tmp_path: Path,
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, signal_date="2026-05-29")

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    assert summary.opened == 0
    assert summary.pending_entry == 1
    assert summary.skipped == 0
    rows = read_paper_trades(trades)
    assert rows[0]["status"] == "pending_entry"
    assert rows[0]["signal_id"] == "sig-1"


def test_paper_converts_pending_entry_when_next_open_arrives(
    tmp_path: Path,
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, signal_date="2026-05-29")
    trades.write_text(
        json.dumps(
            {
                "id": "paper-1",
                "signal_id": "sig-1",
                "symbol": "600519",
                "name": "贵州茅台",
                "signal_date": "2026-05-29",
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "horizon_days": 3,
                "fee_bps": 0,
                "slippage_bps": 0,
                "status": "pending_entry",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    frame = pd.concat(
        [
            _frame(),
            pd.DataFrame(
                [
                    {
                        "date": "2026-06-01",
                        "open": 105.0,
                        "high": 106.0,
                        "low": 104.0,
                        "close": 105.5,
                        "volume": 1000,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": frame},
    )

    rows = read_paper_trades(trades)
    assert summary.opened == 1
    assert summary.pending_entry == 0
    assert rows[0]["id"] == "paper-1"
    assert rows[0]["status"] == "open"
    assert rows[0]["entry_date"] == "2026-06-01"
    assert rows[0]["entry_price"] == 105.0


def test_paper_converts_pending_entry_to_not_executable_when_limit_up(
    tmp_path: Path,
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, signal_date="2026-05-29")
    trades.write_text(
        json.dumps(
            {
                "id": "paper-1",
                "signal_id": "sig-1",
                "symbol": "600519",
                "name": "贵州茅台",
                "signal_date": "2026-05-29",
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "horizon_days": 3,
                "fee_bps": 0,
                "slippage_bps": 0,
                "status": "pending_entry",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    frame = pd.concat(
        [
            _frame(),
            pd.DataFrame(
                [
                    {
                        "date": "2026-06-01",
                        "open": 114.296,
                        "high": 114.296,
                        "low": 114.296,
                        "close": 114.296,
                        "volume": 1000,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": frame},
    )

    rows = read_paper_trades(trades)
    assert summary.opened == 0
    assert summary.not_executable == 1
    assert summary.pending_entry == 0
    assert rows[0]["id"] == "paper-1"
    assert rows[0]["status"] == "not_executable"
    assert rows[0]["not_executable_reason"] == "limit_up_at_open"


def test_paper_closes_after_horizon_when_enough_data(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, horizon_days=2)

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    rows = read_paper_trades(trades)
    assert summary.opened == 1
    assert summary.closed == 1
    assert rows[0]["status"] == "closed"
    assert rows[0]["exit_reason"] == "horizon_close"
    assert rows[0]["return_pct"] == 2.9703


def test_paper_does_not_duplicate_open_symbol(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, horizon_days=3)
    trades.write_text(
        json.dumps(
            {
                "id": "trade-old",
                "signal_id": "old",
                "symbol": "600519",
                "status": "open",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    rows = read_paper_trades(trades)
    assert summary.opened == 0
    assert summary.skipped == 1
    assert len(rows) == 1


def test_paper_closes_old_position_before_opening_new_same_symbol(
    tmp_path: Path,
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, signal_date="2026-05-28", horizon_days=3)
    trades.write_text(
        json.dumps(
            {
                "id": "trade-old",
                "signal_id": "old",
                "symbol": "600519",
                "status": "open",
                "entry_date": "2026-05-27",
                "entry_price": 100.0,
                "stop_loss": 0,
                "take_profit": 0,
                "horizon_days": 2,
                "fee_bps": 0,
                "slippage_bps": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    rows = read_paper_trades(trades)
    assert summary.closed == 1
    assert summary.opened == 1
    assert summary.open_positions == 1
    assert [row["status"] for row in rows] == ["closed", "open"]
    assert rows[1]["signal_id"] == "sig-1"
    assert rows[1]["entry_date"] == "2026-05-29"


def test_paper_records_not_executable_without_open_position(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, signal_close=100.0, limit_up_pct=0.1)
    frame = _frame()
    frame.loc[1, ["open", "high", "low", "close"]] = [110.0, 110.0, 110.0, 110.0]

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": frame},
    )

    rows = read_paper_trades(trades)
    assert summary.opened == 0
    assert summary.open_positions == 0
    assert summary.not_executable == 1
    assert summary.skipped == 0
    assert rows[0]["status"] == "not_executable"
    assert rows[0]["not_executable_reason"] == "limit_up_at_open"


def test_paper_report_uses_order_safe_wording() -> None:
    report = render_paper_report(
        summary=PaperSummary(
            opened=0,
            closed=0,
            open_positions=0,
            pending_entry=2,
            skipped=1,
        ),
        trades=[],
    )

    assert "不下单" in report
    assert "buy_candidate / strong_buy_candidate" in report
    assert "等待入场数据: 2" in report
    assert "avoid" not in report
    assert "虚拟买入" not in report
    assert "无 open 虚拟持仓" in report
