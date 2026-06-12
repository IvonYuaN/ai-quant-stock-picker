from __future__ import annotations

from pathlib import Path

from aqsp.web import dashboard_beginner
from aqsp.web.data_provider import (
    DashboardSummary,
    DashboardPaperSummary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_beginner_dashboard_has_no_sample_account_or_fake_holdings() -> None:
    source = (
        PROJECT_ROOT / "src" / "aqsp" / "web" / "dashboard_beginner.py"
    ).read_text(encoding="utf-8")

    assert "get_sample_account" not in source
    assert "get_sample_positions" not in source
    assert "贵州茅台" not in source
    assert "五粮液" not in source
    assert "总资产" not in source
    assert "真实账户" not in source
    assert "真实持仓" not in source
    assert "下单决定" not in source
    assert "AQSP Beginner Dashboard" not in source
    assert "先看今天主链推荐和阻塞原因" not in source
    assert "收盘前确认承接和隔夜价值" not in source


def test_beginner_dashboard_time_lanes_include_midday_and_review_flow() -> None:
    lanes = dashboard_beginner._TIME_LANES

    assert [lane.code for lane in lanes] == [
        "09:25",
        "10:00",
        "12:00",
        "14:40",
        "15:30",
        "21:00",
    ]
    assert any(lane.name == "午盘回看" and lane.task_id == "intraday" for lane in lanes)
    assert any(lane.task_id == "closing_review" for lane in lanes)
    assert any(lane.task_id == "briefing" for lane in lanes)


def test_beginner_dashboard_builds_positions_from_real_provider_frame(
    monkeypatch,
) -> None:
    import pandas as pd

    class _FakeProvider:
        def summarize(self) -> DashboardSummary:
            return DashboardSummary(
                signal_count=1,
                latest_signal_date="2026-06-11",
                open_positions=1,
                pending_entries=0,
                not_executable=0,
                closed_trades=0,
                execution_logs=0,
            )

        def task_snapshots(self, signal_date: str = "") -> tuple[()]:
            return ()

        def paper_summary(self, signal_date: str = "") -> DashboardPaperSummary:
            return DashboardPaperSummary(
                signal_date=signal_date,
                open_positions=1,
                pending_entries=0,
                not_executable=0,
                closed_trades=0,
                open_position_lines=(),
                event_lines=(),
                action_summary_lines=(),
            )

        def open_positions_frame(self, *, signal_date: str = "") -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "代码": "600000",
                        "名称": "浦发银行",
                        "纸面入场日": "2026-06-10",
                        "纸面入场价": "9.80",
                        "止损": "9.30",
                        "止盈": "10.80",
                        "持有周期": "3",
                    }
                ]
            )

        def date_overview(self, signal_date: str) -> None:
            return None

        def timeline_frame(self, limit: int = 12) -> pd.DataFrame:
            return pd.DataFrame()

    dashboard_beginner.get_provider.clear()
    dashboard_beginner.load_runtime_snapshot.clear()
    dashboard_beginner.build_positions.clear()
    monkeypatch.setattr(dashboard_beginner, "get_provider", lambda: _FakeProvider())

    positions = dashboard_beginner.build_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "600000"
    assert positions[0].name == "浦发银行"
    assert positions[0].entry_price == 9.8
    assert positions[0].stop_loss == 9.3
    assert positions[0].take_profit == 10.8
    assert positions[0].horizon_days == 3


def test_beginner_dashboard_exposes_glossary_for_new_users() -> None:
    glossary = dashboard_beginner.BEGINNER_GLOSSARY

    assert "技术指标" in glossary
    assert "交易规则" in glossary
    assert any(term == "T+1" for term, _ in glossary["交易规则"])
    assert any(term == "bias20" for term, _ in glossary["技术指标"])
