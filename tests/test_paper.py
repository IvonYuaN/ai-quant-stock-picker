from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

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


def test_paper_default_execution_costs_use_thresholds(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, fee_bps=None, slippage_bps=None)

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    row = read_paper_trades(trades)[0]
    assert summary.opened == 1
    assert row["fee_bps"] == pytest.approx(3.0)
    assert row["slippage_bps"] == pytest.approx(20.0)


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


@pytest.mark.parametrize(
    "quality_fields",
    [
        {"quality_gate_action": "observe"},
        {"quality_gate_action": "blocked"},
        {"observation_only": True},
    ],
)
def test_paper_does_not_create_entry_for_quality_blocked_signals(
    tmp_path: Path, quality_fields: dict[str, object]
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(signals, **quality_fields)

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    assert summary.opened == 0
    assert summary.pending_entry == 0
    assert summary.skipped == 1
    assert read_paper_trades(trades) == []


def test_paper_persists_quality_context_on_open_trade(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(
        signals,
        quality_gate_status="clean",
        quality_gate_action="clean",
        quality_gate_reasons=("确认充分",),
        paper_review_eligible=True,
        observation_only=False,
        technical_evidence=("趋势：均线多头", "量能：放量确认"),
        technical_evidence_count=2,
        technical_quality_status="clean",
        data_quality_status="watch",
        data_quality_alerts=("盘口延迟",),
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    row = read_paper_trades(trades)[0]
    assert summary.opened == 1
    assert row["quality_gate_action"] == "clean"
    assert row["quality_gate_reasons"] == ["确认充分"]
    assert row["paper_review_eligible"] is True
    assert row["observation_only"] is False
    assert row["technical_evidence"] == ["趋势：均线多头", "量能：放量确认"]
    assert row["data_quality_status"] == "watch"
    assert row["data_quality_alerts"] == ["盘口延迟"]


def test_paper_demotes_existing_quality_blocked_pending_entry(
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
                "status": "pending_entry",
                "quality_gate_action": "blocked",
                "paper_review_eligible": False,
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

    row = read_paper_trades(trades)[0]
    assert summary.opened == 0
    assert summary.pending_entry == 0
    assert row["status"] == "watch_only"


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


def test_paper_sync_can_scope_new_trades_to_signal_dates(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    rows = [
        {
            "id": "sig-1",
            "status": "pending",
            "signal_date": "2026-05-27",
            "symbol": "600519",
            "rating": "buy_candidate",
            "score": 70,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "horizon_days": 2,
        },
        {
            "id": "sig-2",
            "status": "pending",
            "signal_date": "2026-05-28",
            "symbol": "600519",
            "rating": "buy_candidate",
            "score": 71,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "horizon_days": 2,
        },
    ]
    signals.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
        signal_dates={"2026-05-27"},
    )

    paper_rows = read_paper_trades(trades)
    assert summary.opened == 1
    assert len(paper_rows) == 1
    assert paper_rows[0]["signal_id"] == "sig-1"
    assert paper_rows[0]["signal_date"] == "2026-05-27"


def test_paper_carries_candidate_context_into_open_and_pending_rows(
    tmp_path: Path,
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(
        signals,
        portfolio_action="downgrade",
        candidate_status="观察阻塞",
        candidate_blocker="板块集中度过高",
        candidate_next_step="等待板块回落后再评估",
        candidate_review_window="午后",
        candidate_review_priority="medium",
    )

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": _frame()},
    )

    assert summary.opened == 1
    open_row = read_paper_trades(trades)[0]
    assert open_row["portfolio_action"] == "downgrade"
    assert open_row["candidate_status"] == "观察阻塞"
    assert open_row["candidate_blocker"] == "板块集中度过高"
    assert open_row["candidate_next_step"] == "等待板块回落后再评估"
    assert open_row["candidate_review_window"] == "午后"
    assert open_row["candidate_review_priority"] == "medium"

    pending_signals = tmp_path / "pending-signals.jsonl"
    pending_trades = tmp_path / "pending-paper.jsonl"
    _write_signal(
        pending_signals,
        signal_date="2026-05-29",
        portfolio_action="promote",
        candidate_status="延续上升",
        candidate_next_step="等待开盘承接确认",
        candidate_review_window="开盘前后",
        candidate_review_priority="high",
    )

    pending_summary = sync_paper_trades(
        signal_ledger=pending_signals,
        paper_ledger=pending_trades,
        frames={"600519": _frame()},
    )

    assert pending_summary.pending_entry == 1
    pending_row = read_paper_trades(pending_trades)[0]
    assert pending_row["status"] == "pending_entry"
    assert pending_row["portfolio_action"] == "promote"
    assert pending_row["candidate_status"] == "延续上升"
    assert pending_row["candidate_next_step"] == "等待开盘承接确认"


def test_paper_carries_candidate_context_into_not_executable_rows(
    tmp_path: Path,
) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    _write_signal(
        signals,
        signal_close=100.0,
        limit_up_pct=0.1,
        portfolio_action="downgrade",
        candidate_status="观察阻塞",
        candidate_blocker="涨停无法追入",
        candidate_next_step="等待开板后再评估",
        candidate_review_window="次日开盘",
        candidate_review_priority="high",
        thresholds_version="1.1.11",
        regime_at_signal="stable_bull",
        signal_day_group="2026-05-27_morning_breakout",
        entry_type="next_open",
        sub_strategy="强势观察",
        position="20%",
        benchmark_symbol="000300",
    )
    frame = _frame()
    frame.loc[1, ["open", "high", "low", "close"]] = [110.0, 110.0, 110.0, 110.0]

    summary = sync_paper_trades(
        signal_ledger=signals,
        paper_ledger=trades,
        frames={"600519": frame},
    )

    assert summary.not_executable == 1
    row = read_paper_trades(trades)[0]
    assert row["status"] == "not_executable"
    assert row["candidate_status"] == "观察阻塞"
    assert row["candidate_blocker"] == "涨停无法追入"
    assert row["candidate_next_step"] == "等待开板后再评估"
    assert row["stop_loss"] == 95.0
    assert row["take_profit"] == 110.0
    assert row["horizon_days"] == 2
    assert row["fee_bps"] == 0
    assert row["slippage_bps"] == 0
    assert row["score"] == 70
    assert row["rating"] == "buy_candidate"
    assert row["strategies"] == ["momentum"]
    assert row["thresholds_version"] == "1.1.11"
    assert row["regime_at_signal"] == "stable_bull"
    assert row["signal_day_group"] == "2026-05-27_morning_breakout"
    assert row["entry_type"] == "next_open"
    assert row["sub_strategy"] == "强势观察"
    assert row["position"] == "20%"
    assert row["benchmark_symbol"] == "000300"
    assert row["limit_up_pct"] == 0.1


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


def test_paper_concurrent_sync_keeps_unique_signal_rows(tmp_path: Path) -> None:
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "paper.jsonl"
    signal_rows = []
    frames = {}
    for idx in range(12):
        symbol = f"6005{idx:02d}"
        row = {
            "id": f"sig-{idx}",
            "status": "pending",
            "signal_date": "2026-05-27",
            "symbol": symbol,
            "name": symbol,
            "signal_close": 100.0,
            "rating": "buy_candidate",
            "score": 70,
            "strategies": ["momentum"],
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "horizon_days": 3,
            "fee_bps": 0,
            "slippage_bps": 0,
        }
        signal_rows.append(json.dumps(row, ensure_ascii=False))
        frames[symbol] = _frame()
    signals.write_text("\n".join(signal_rows) + "\n", encoding="utf-8")

    def sync_once() -> PaperSummary:
        return sync_paper_trades(
            signal_ledger=signals,
            paper_ledger=trades,
            frames=frames,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda _: sync_once(), range(6)))

    rows = read_paper_trades(trades)
    signal_ids = [row["signal_id"] for row in rows]
    assert len(rows) == 12
    assert set(signal_ids) == {f"sig-{idx}" for idx in range(12)}


def test_read_paper_trades_skips_corrupt_jsonl_line(tmp_path: Path, caplog) -> None:
    trades = tmp_path / "paper.jsonl"
    trades.write_text(
        json.dumps({"id": "ok-1", "status": "open"}, ensure_ascii=False)
        + "\n"
        + "{bad-json\n"
        + json.dumps({"id": "ok-2", "status": "closed"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    rows = read_paper_trades(trades)

    assert [row["id"] for row in rows] == ["ok-1", "ok-2"]
    assert "JSON 损坏，已跳过" in caplog.text


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
    assert "无 open 纸面持有记录" in report
