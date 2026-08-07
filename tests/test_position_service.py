"""Tests for portfolio.position_service module."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from aqsp.portfolio.position_service import (
    PositionReport,
    PositionStatus,
    build_tracker_from_ledger,
    format_position_report,
    get_position_report,
)
from aqsp.portfolio.position_tracker import PositionTracker


def _write_ledger(path: Path, records: list[dict]) -> None:
    """Write a list of dicts as JSONL to *path*."""
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    path.write_text(text + "\n", encoding="utf-8")


def _open_record(
    symbol: str,
    name: str = "",
    entry_price: float = 10.0,
    entry_date: str = "2026-05-28",
    status: str = "open",
) -> dict:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "status": status,
        "entry_price": entry_price,
        "entry_date": entry_date,
        "signal_date": entry_date,
        "stop_loss": entry_price * 0.92,
        "take_profit": entry_price * 1.15,
        "score": 65.0,
        "rating": "buy_candidate",
    }


class TestBuildTrackerFromLedger:
    """Tests for build_tracker_from_ledger."""

    def test_returns_empty_tracker_when_ledger_missing(self, tmp_path: Path) -> None:
        tracker = build_tracker_from_ledger(tmp_path / "nonexistent.jsonl")
        assert isinstance(tracker, PositionTracker)
        assert len(tracker.positions) == 0

    def test_returns_empty_tracker_when_ledger_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        path.write_text("", encoding="utf-8")
        tracker = build_tracker_from_ledger(path)
        assert len(tracker.positions) == 0

    def test_loads_open_positions_into_tracker(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-28"),
            _open_record("000858", "五粮液", 150.0, "2026-05-28"),
        ])
        tracker = build_tracker_from_ledger(
            path, today=date(2026, 5, 30)
        )
        assert len(tracker.positions) == 2
        assert tracker.has_position("600519")
        assert tracker.has_position("000858")

    def test_skips_closed_and_not_executable(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-28", status="open"),
            _open_record("000858", "五粮液", 150.0, "2026-05-28", status="closed"),
            _open_record("300750", "宁德时代", 200.0, "2026-05-28", status="not_executable"),
        ])
        tracker = build_tracker_from_ledger(
            path, today=date(2026, 5, 30)
        )
        assert len(tracker.positions) == 1
        assert tracker.has_position("600519")
        assert not tracker.has_position("000858")
        assert not tracker.has_position("300750")

    def test_skips_pending_entry_without_entry_price(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        rec = _open_record("600519", "贵州茅台", 0.0, "2026-05-28", status="pending_entry")
        _write_ledger(path, [rec])
        tracker = build_tracker_from_ledger(path)
        assert len(tracker.positions) == 0

    def test_t1_unfreeze_applied_for_positions_bought_before_today(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-28"),
        ])
        # Position bought on 05-28, current date 05-30 → should be unfrozen.
        tracker = build_tracker_from_ledger(
            path, today=date(2026, 5, 30)
        )
        pos = tracker.get_position("600519")
        assert pos is not None
        assert pos.is_fully_sellable
        assert pos.frozen_shares == 0

    def test_t1_frozen_when_bought_today(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-30"),
        ])
        # Position bought today → should be frozen.
        tracker = build_tracker_from_ledger(
            path, today=date(2026, 5, 30)
        )
        pos = tracker.get_position("600519")
        assert pos is not None
        assert not pos.is_fully_sellable
        assert pos.frozen_shares == 100
        assert pos.available_shares == 0

    def test_falls_back_to_signal_date_when_entry_date_missing(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "paper.jsonl"
        rec = _open_record("600519", "贵州茅台", 1800.0, "2026-05-28")
        del rec["entry_date"]
        rec["signal_date"] = "2026-05-27"
        _write_ledger(path, [rec])
        tracker = build_tracker_from_ledger(
            path, today=date(2026, 5, 30)
        )
        assert tracker.has_position("600519")

    def test_skips_records_with_no_valid_date(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        rec = _open_record("600519", "贵州茅台", 1800.0, "2026-05-28")
        del rec["entry_date"]
        del rec["signal_date"]
        _write_ledger(path, [rec])
        tracker = build_tracker_from_ledger(path)
        assert len(tracker.positions) == 0


class TestGetPositionReport:
    """Tests for get_position_report."""

    def test_returns_empty_report_when_no_positions(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        path.write_text("", encoding="utf-8")
        report = get_position_report(path)
        assert not report.has_positions
        assert report.total_positions == 0

    def test_counts_frozen_and_sellable_correctly(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-28"),
            _open_record("000858", "五粮液", 150.0, "2026-05-30"),
            _open_record("300750", "宁德时代", 200.0, "2026-05-27"),
        ])
        report = get_position_report(path, today=date(2026, 5, 30))
        assert report.total_positions == 3
        # 600519 (05-28) and 300750 (05-27) are sellable, 000858 (05-30) frozen.
        assert report.fully_sellable_count == 2
        assert report.t1_frozen_count == 1

    def test_report_contains_position_status_objects(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-28"),
        ])
        report = get_position_report(path, today=date(2026, 5, 30))
        assert len(report.positions) == 1
        status = report.positions[0]
        assert isinstance(status, PositionStatus)
        assert status.symbol == "600519"
        assert status.name == "贵州茅台"
        assert status.cost_basis == 1800.0
        assert status.entry_date == "2026-05-28"

    def test_frozen_positions_sorted_first(self, tmp_path: Path) -> None:
        path = tmp_path / "paper.jsonl"
        _write_ledger(path, [
            _open_record("600519", "贵州茅台", 1800.0, "2026-05-28"),
            _open_record("000858", "五粮液", 150.0, "2026-05-30"),
        ])
        report = get_position_report(path, today=date(2026, 5, 30))
        # Frozen position (000858, bought today) should be first.
        assert report.positions[0].symbol == "000858"
        assert report.positions[0].is_t1_frozen
        assert report.positions[1].symbol == "600519"
        assert not report.positions[1].is_t1_frozen


class TestFormatPositionReport:
    """Tests for format_position_report."""

    def test_returns_empty_lines_when_no_positions(self) -> None:
        report = PositionReport()
        assert format_position_report(report) == []

    def test_outputs_summary_line_with_counts(self) -> None:
        report = PositionReport(
            positions=(
                PositionStatus(
                    symbol="600519",
                    name="贵州茅台",
                    total_shares=100,
                    available_shares=100,
                    frozen_shares=0,
                    cost_basis=1800.0,
                    entry_date="2026-05-28",
                    is_t1_frozen=False,
                ),
            ),
            total_positions=1,
            fully_sellable_count=1,
            t1_frozen_count=0,
        )
        lines = format_position_report(report)
        assert len(lines) == 1
        assert "持仓概览" in lines[0]
        assert "可卖 1" in lines[0]
        assert "T+1 冻结 0" in lines[0]

    def test_outputs_frozen_detail_when_t1_frozen(self) -> None:
        report = PositionReport(
            positions=(
                PositionStatus(
                    symbol="000858",
                    name="五粮液",
                    total_shares=100,
                    available_shares=0,
                    frozen_shares=100,
                    cost_basis=150.0,
                    entry_date="2026-05-30",
                    is_t1_frozen=True,
                ),
            ),
            total_positions=1,
            fully_sellable_count=0,
            t1_frozen_count=1,
        )
        lines = format_position_report(report)
        assert len(lines) >= 2
        assert "T+1 冻结" in lines[1]
        assert "五粮液" in lines[2]
        assert "150.00" in lines[2]

    def test_limits_frozen_detail_to_five(self) -> None:
        positions = tuple(
            PositionStatus(
                symbol=f"60000{i}",
                name=f"股票{i}",
                total_shares=100,
                available_shares=0,
                frozen_shares=100,
                cost_basis=10.0,
                entry_date="2026-05-30",
                is_t1_frozen=True,
            )
            for i in range(7)
        )
        report = PositionReport(
            positions=positions,
            total_positions=7,
            t1_frozen_count=7,
        )
        lines = format_position_report(report)
        # Summary + frozen header + 5 detail + overflow
        assert len(lines) == 8
        assert "其他 2 只" in lines[-1]
