"""Stop-loss service integration tests.

Tests the bridge between paper ledger and StopLossManager:
1. Empty ledger → empty report
2. Open position in profit → no alert
3. Open position with loss exceeding threshold → alert triggered
4. Missing frame data → skipped
5. Zero entry price → skipped
6. Format output correctness
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from aqsp.risk.stop_loss import StopLossConfig, StopLossManager
from aqsp.risk.stop_loss_service import (
    StopLossReport,
    _latest_close,
    check_open_position_stop_losses,
    format_stop_loss_report,
)


def _write_paper_ledger(path: Path, trades: list[dict]) -> None:
    lines = [json.dumps(t, ensure_ascii=False) for t in trades]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_frame(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": closes})


def _manager_with_stop(stop_pct: float = -0.08) -> StopLossManager:
    return StopLossManager(
        config=StopLossConfig(
            single_stock_stop=stop_pct,
            portfolio_stop=-0.15,
            trailing_stop_pct=0.05,
            enable_trailing=False,
        )
    )


class TestCheckOpenPositionStopLosses:
    """Tests for check_open_position_stop_losses."""

    def test_returns_empty_when_ledger_does_not_exist(self, tmp_path: Path) -> None:
        report = check_open_position_stop_losses(
            tmp_path / "nonexistent.jsonl",
            frames={},
        )
        assert report.total_open_positions == 0
        assert report.alerts == ()
        assert report.triggered is False

    def test_returns_empty_when_no_open_positions(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(ledger, [{"symbol": "600519", "status": "closed"}])
        report = check_open_position_stop_losses(ledger, frames={})
        assert report.total_open_positions == 0

    def test_no_alert_when_position_in_profit(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(
            ledger,
            [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "status": "open",
                    "entry_price": 100.0,
                    "stop_loss": 92.0,
                }
            ],
        )
        frames = {"600519": _make_frame([100.0, 105.0, 110.0])}
        report = check_open_position_stop_losses(
            ledger, frames, manager=_manager_with_stop()
        )
        assert report.total_open_positions == 1
        assert report.checked_positions == 1
        assert report.alerts == ()
        assert report.triggered is False

    def test_alert_triggered_when_loss_exceeds_threshold(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(
            ledger,
            [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "status": "open",
                    "entry_price": 100.0,
                    "stop_loss": 92.0,
                }
            ],
        )
        # 91.0 is -9%, exceeds the -8% threshold
        frames = {"600519": _make_frame([100.0, 95.0, 91.0])}
        report = check_open_position_stop_losses(
            ledger, frames, manager=_manager_with_stop(stop_pct=-0.08)
        )
        assert report.total_open_positions == 1
        assert report.checked_positions == 1
        assert len(report.alerts) == 1
        alert = report.alerts[0]
        assert alert.symbol == "600519"
        assert alert.name == "贵州茅台"
        assert alert.entry_price == 100.0
        assert alert.current_price == 91.0
        assert alert.pnl_pct == pytest.approx(-0.09, abs=0.001)
        assert report.triggered is True
        assert "600519" in report.triggered_symbols

    def test_skipped_when_frame_missing(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(
            ledger,
            [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "status": "open",
                    "entry_price": 100.0,
                    "stop_loss": 92.0,
                }
            ],
        )
        report = check_open_position_stop_losses(
            ledger, frames={}, manager=_manager_with_stop()
        )
        assert report.total_open_positions == 1
        assert report.checked_positions == 0
        assert "600519" in report.skipped
        assert report.alerts == ()

    def test_skipped_when_entry_price_zero(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(
            ledger,
            [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "status": "open",
                    "entry_price": 0.0,
                    "stop_loss": 0.0,
                }
            ],
        )
        frames = {"600519": _make_frame([100.0, 90.0])}
        report = check_open_position_stop_losses(
            ledger, frames, manager=_manager_with_stop()
        )
        assert report.checked_positions == 0
        assert "600519" in report.skipped

    def test_mixed_positions_some_triggered_some_not(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(
            ledger,
            [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "status": "open",
                    "entry_price": 100.0,
                    "stop_loss": 92.0,
                },
                {
                    "symbol": "000858",
                    "name": "五粮液",
                    "status": "open",
                    "entry_price": 200.0,
                    "stop_loss": 184.0,
                },
                {
                    "symbol": "000001",
                    "name": "平安银行",
                    "status": "closed",
                    "entry_price": 10.0,
                    "stop_loss": 9.2,
                },
            ],
        )
        # 600519 at 91.0 (-9%) → triggered
        # 000858 at 210.0 (+5%) → not triggered
        frames = {
            "600519": _make_frame([100.0, 95.0, 91.0]),
            "000858": _make_frame([200.0, 205.0, 210.0]),
        }
        report = check_open_position_stop_losses(
            ledger, frames, manager=_manager_with_stop()
        )
        assert report.total_open_positions == 2  # closed trade excluded
        assert report.checked_positions == 2
        assert len(report.alerts) == 1
        assert report.alerts[0].symbol == "600519"

    def test_uses_default_manager_when_none_provided(self, tmp_path: Path) -> None:
        ledger = tmp_path / "paper.jsonl"
        _write_paper_ledger(
            ledger,
            [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "status": "open",
                    "entry_price": 100.0,
                    "stop_loss": 92.0,
                }
            ],
        )
        frames = {"600519": _make_frame([100.0, 105.0])}
        # Should not raise — default manager loads from thresholds.yaml
        report = check_open_position_stop_losses(ledger, frames)
        assert report.total_open_positions == 1


class TestFormatStopLossReport:
    """Tests for format_stop_loss_report."""

    def test_empty_report_produces_no_lines(self) -> None:
        report = StopLossReport()
        assert format_stop_loss_report(report) == []

    def test_report_with_no_alerts_shows_ok(self) -> None:
        report = StopLossReport(
            total_open_positions=3,
            checked_positions=3,
        )
        lines = format_stop_loss_report(report)
        assert any("3/3" in line for line in lines)
        assert any("无止损触发" in line for line in lines)

    def test_report_with_alerts_shows_details(self) -> None:
        from aqsp.risk.stop_loss_service import StopLossAlert

        report = StopLossReport(
            alerts=(
                StopLossAlert(
                    symbol="600519",
                    name="贵州茅台",
                    entry_price=100.0,
                    current_price=91.0,
                    pnl_pct=-0.09,
                    reason="单只股票止损触发",
                    stop_loss_price=92.0,
                ),
            ),
            total_open_positions=2,
            checked_positions=2,
        )
        lines = format_stop_loss_report(report)
        assert any("触发止损 1 只" in line for line in lines)
        assert any("贵州茅台" in line for line in lines)
        assert any("91.00" in line for line in lines)

    def test_report_with_skipped_shows_skipped(self) -> None:
        report = StopLossReport(
            total_open_positions=2,
            checked_positions=1,
            skipped=("000001",),
        )
        lines = format_stop_loss_report(report)
        assert any("000001" in line for line in lines)


class TestLatestClose:
    """Tests for _latest_close helper."""

    def test_returns_none_for_none_frame(self) -> None:
        assert _latest_close(None) is None

    def test_returns_none_for_empty_frame(self) -> None:
        assert _latest_close(pd.DataFrame()) is None

    def test_returns_last_close(self) -> None:
        frame = _make_frame([10.0, 20.0, 30.0])
        assert _latest_close(frame) == 30.0

    def test_returns_none_for_zero_close(self) -> None:
        frame = pd.DataFrame({"date": ["2026-01-01"], "close": [0.0]})
        assert _latest_close(frame) is None
