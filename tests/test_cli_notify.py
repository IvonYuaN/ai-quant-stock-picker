from __future__ import annotations

from argparse import Namespace
from datetime import date
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from unittest.mock import MagicMock

from aqsp.core.types import PickResult
from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.debate import DebateResult
from aqsp.portfolio.manager import PortfolioDecisionSummary
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.portfolio.snapshot import PickSnapshot, SnapshotDiff

TEST_TRADE_DAY = date(2026, 6, 26)


@pytest.fixture(autouse=True)
def _isolated_notify_state(monkeypatch, tmp_path: Path) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(tmp_path / "notify_state.json"))
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )
    # Notification tests use explicit fixtures for message content. Keep live
    # catalyst workers out of unrelated scheduled-flow tests.
    monkeypatch.setattr(cli_mod, "_should_build_market_context", lambda _task_id: False)


@pytest.fixture(autouse=True)
def _force_trading_day(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: TEST_TRADE_DAY)
    monkeypatch.setattr("aqsp.core.time.today_shanghai", lambda: TEST_TRADE_DAY)
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: TEST_TRADE_DAY)
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)


def test_run_briefing_email_subject_uses_shanghai_today(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    ledger.write_text(
        '{"signal_date":"2026-06-12","status":"watch_only","symbol":"600000",'
        '"name":"浦发银行","signal_close":10.0,"score":55,"rating":"watch"}\n',
        encoding="utf-8",
    )
    sent: dict[str, str] = {}

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 13))
    monkeypatch.setattr(
        "aqsp.briefing.enhance_briefing", lambda briefing, enable_llm: briefing
    )
    monkeypatch.setattr(
        "aqsp.briefing.email_notifier.load_email_config_from_env", lambda: object()
    )
    monkeypatch.setattr(
        "aqsp.briefing.email_notifier.send_briefing_email",
        lambda cfg, subject, markdown_body: sent.setdefault("subject", subject) or True,
    )

    exit_code = cli_mod.run_briefing(
        Namespace(
            ledger=str(ledger),
            output=str(output),
            enable_llm=False,
            notify=False,
            email=True,
        )
    )

    assert exit_code == 0
    assert sent["subject"] == "aqsp briefing 2026-06-13"


def test_execution_summary_uses_observation_when_pm_has_no_allocations() -> None:
    import aqsp.cli as cli_mod

    pick = PickResult(
        symbol="000001",
        name="平安银行",
        date="2026-06-09",
        close=11.07,
        score=85,
        rating="buy_candidate",
        entry_type="trend_pullback",
        ideal_buy=11.07,
        stop_loss=10.74,
        take_profit=12.01,
        position="watch",
    )
    summary = PortfolioDecisionSummary(
        promote_count=0,
        downgrade_count=1,
        keep_count=0,
        top_focus=("000001 平安银行",),
        watchlist=("000001 平安银行",),
        allocations=(),
        cash_reserve=1.0,
        allocation_note="今日无纸面复核主线，建议保留现金等待下一轮信号。",
    )

    line = cli_mod._build_execution_summary_line([pick], summary)

    assert "今日无纸面复核对象" in line
    assert "继续观察" in line
    assert "首选" not in line


def test_news_catalysts_cli_sends_research_notification(monkeypatch, capsys) -> None:
    import aqsp.cli as cli_mod
    from aqsp.news.catalysts import CatalystReport

    sent: list[str] = []
    report = CatalystReport(
        date="2026-06-11",
        generated_at="2026-06-11T08:40:00+08:00",
        events=(),
        source_status="empty",
    )

    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: (
            sent.append(markdown)
            or [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
    )
    monkeypatch.setattr(
        "aqsp.news.build_catalyst_report",
        lambda **_kwargs: report,
    )

    exit_code = cli_mod.main(
        [
            "news-catalysts",
            "--symbols",
            "300001",
            "--names",
            "300001:样本电子",
            "--notify",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "消息面雷达-2026-06-11" in output
    assert "news notify serverchan: ok (HTTP 200)" in output
    assert sent and "## 结论" in sent[0]


def test_news_catalysts_cli_suppresses_notification_when_sources_failed(
    monkeypatch, capsys
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.news.catalysts import CatalystReport

    sent: list[str] = []
    report = CatalystReport(
        date="2026-06-11",
        generated_at="2026-06-11T08:40:00+08:00",
        events=(),
        source_status="failed",
        warnings=("全市场快讯获取失败: timeout",),
    )

    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: (
            sent.append(markdown)
            or [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
    )
    monkeypatch.setattr(
        "aqsp.news.build_catalyst_report",
        lambda **_kwargs: report,
    )

    exit_code = cli_mod.main(
        [
            "news-catalysts",
            "--notify",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "消息面雷达-2026-06-11" in output
    assert "notification suppressed" in output
    assert sent == []


def test_news_catalysts_cli_writes_structured_runtime_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.news.catalysts import CatalystReport

    report = CatalystReport(
        date="2026-06-11",
        generated_at="2026-06-11T08:40:00+08:00",
        events=(),
        source_status="partial",
        warnings=("国际源超时",),
        event_status="no_valid_news",
    )
    monkeypatch.setattr(
        "aqsp.news.build_catalyst_report", lambda **_kwargs: report
    )
    output = tmp_path / "news.json"

    assert (
        cli_mod.main(
            [
                "news-catalysts",
                "--json-output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_status"] == "partial"
    assert payload["warnings"] == ["国际源超时"]


def test_run_briefing_prints_notify_channel_results(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    ledger.write_text(
        '{"signal_date":"2026-06-12","status":"watch_only","symbol":"600000",'
        '"name":"浦发银行","signal_close":10.0,"score":55,"rating":"watch"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "aqsp.briefing.enhance_briefing", lambda briefing, enable_llm: briefing
    )
    monkeypatch.setattr(
        "aqsp.briefing.notifier.send_smart_summary_card", lambda briefing: None
    )
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")],
    )

    exit_code = cli_mod.run_briefing(
        Namespace(
            ledger=str(ledger),
            output=str(output),
            enable_llm=False,
            notify=True,
            email=False,
        )
    )

    output_text = capsys.readouterr().out
    assert exit_code == 0
    assert "briefing notify serverchan: ok (HTTP 200)" in output_text


def test_run_briefing_dedupes_same_date_notification(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    state_path = tmp_path / "notify_state.json"
    ledger.write_text(
        '{"signal_date":"2026-06-12","status":"watch_only","symbol":"600000",'
        '"name":"浦发银行","signal_close":10.0,"score":55,"rating":"watch"}\n',
        encoding="utf-8",
    )
    calls: list[str] = []

    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(state_path))
    monkeypatch.setattr(
        "aqsp.briefing.enhance_briefing", lambda briefing, enable_llm: briefing
    )
    monkeypatch.setattr(
        "aqsp.briefing.notifier.send_smart_summary_card", lambda briefing: None
    )
    monkeypatch.setattr(
        "aqsp.notification_runtime.dispatch_notification",
        lambda markdown, **_kwargs: (
            calls.append(markdown)
            or [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
    )

    args = Namespace(
        ledger=str(ledger),
        output=str(output),
        enable_llm=False,
        notify=True,
        email=False,
    )

    assert cli_mod.run_briefing(args) == 0
    assert cli_mod.run_briefing(args) == 0

    output_text = capsys.readouterr().out
    assert len(calls) == 1
    assert "briefing notify: skipped duplicate" in output_text


def test_run_briefing_rehydrates_debate_summary_metrics_from_ledger(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    ledger.write_text(
        '{"signal_date":"2026-06-12","status":"watch_only","symbol":"600000",'
        '"name":"浦发银行","signal_close":10.0,"score":55,"rating":"watch",'
        '"cross_market_primary_theme":"外盘风险偏好修复",'
        '"cross_market_linkage_basis":"风险偏好映射",'
        '"cross_market_action":"重点跟踪",'
        '"cross_market_lead_window":"次日竞价-1日",'
        '"cross_market_observation_window":"次日-3日",'
        '"cross_market_validation_signals":["次日竞价高弹性方向明显强于防御方向"],'
        '"cross_market_invalidation_signals":["美股强但A股竞价无明显风险偏好跟随"],'
        '"portfolio_action":"keep","debate_research_verdict":"倾向优先纸面复核",'
        '"debate_primary_risk_gate":"先确认银行板块承接",'
        '"debate_next_trigger":"先确认次日成交质量",'
        '"support_points":["外盘风险偏好改善，对银行权重形成支撑"],'
        '"opposition_points":["若只是单日脉冲，次日承接可能不足"],'
        '"watch_items":["观察北向强弱是否在次日延续"],'
        '"role_reliability_lines":["跨市场: 近21天 7/10 (70%)｜当前权重 0.18"]}\n',
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _capture_briefing(briefing, enable_llm):
        captured["briefing"] = briefing
        return briefing

    monkeypatch.setattr("aqsp.briefing.enhance_briefing", _capture_briefing)
    monkeypatch.setattr(
        "aqsp.briefing.notifier.send_smart_summary_card", lambda briefing: None
    )

    exit_code = cli_mod.run_briefing(
        Namespace(
            ledger=str(ledger),
            output=str(output),
            enable_llm=False,
            notify=False,
            email=False,
        )
    )

    briefing = captured["briefing"]
    assert exit_code == 0
    assert briefing.picks[0].metrics["cross_market_primary_theme"] == "外盘风险偏好修复"
    assert briefing.picks[0].metrics["cross_market_linkage_basis"] == "风险偏好映射"
    assert briefing.picks[0].metrics["cross_market_validation_signals"] == (
        "次日竞价高弹性方向明显强于防御方向",
    )
    assert briefing.picks[0].metrics["debate_research_verdict"] == "倾向优先纸面复核"
    assert briefing.picks[0].metrics["debate_primary_risk_gate"] == "先确认银行板块承接"
    assert briefing.picks[0].metrics["debate_next_trigger"] == "先确认次日成交质量"
    assert briefing.picks[0].metrics["support_points"] == (
        "外盘风险偏好改善，对银行权重形成支撑",
    )
    assert briefing.picks[0].metrics["opposition_points"] == (
        "若只是单日脉冲，次日承接可能不足",
    )
    assert briefing.picks[0].metrics["watch_items"] == ("观察北向强弱是否在次日延续",)
    assert briefing.picks[0].metrics["role_reliability_lines"] == (
        "跨市场: 近21天 7/10 (70%)｜当前权重 0.18",
    )
    assert briefing.portfolio_summary is not None
    assert briefing.portfolio_summary.debate_focus == (
        "600000 浦发银行 | 倾向优先纸面复核",
    )
    assert briefing.portfolio_summary.debate_risk_gates == (
        "600000 浦发银行 | 先确认银行板块承接",
    )
    assert briefing.portfolio_summary.debate_next_triggers == (
        "600000 浦发银行 | 先确认次日成交质量",
    )


def test_run_briefing_rehydrates_candidate_quality_boundary_from_ledger(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    ledger.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-12",
                "status": "watch_only",
                "symbol": "600000",
                "name": "浦发银行",
                "signal_close": 10.0,
                "score": 72,
                "rating": "buy_candidate",
                "quality_gate_action": "observe",
                "quality_gate_status": "observe",
                "quality_gate_reasons": ["缺少量价确认"],
                "paper_review_eligible": False,
                "observation_only": True,
                "portfolio_action": "observation_only",
                "candidate_status": "质量观察",
                "candidate_blocker": "",
                "candidate_next_step": "等待短线动量与量能重新确认后，再评估纸面复核",
                "candidate_review_window": "下一次量价确认时",
                "candidate_review_priority": "low",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "aqsp.briefing.enhance_briefing",
        lambda briefing, enable_llm: captured.setdefault("briefing", briefing)
        or briefing,
    )
    monkeypatch.setattr(
        "aqsp.briefing.notifier.send_smart_summary_card", lambda briefing: None
    )

    exit_code = cli_mod.run_briefing(
        Namespace(
            ledger=str(ledger),
            output=str(output),
            enable_llm=False,
            notify=False,
            email=False,
        )
    )

    pick = captured["briefing"].picks[0]
    assert exit_code == 0
    assert pick.metrics["quality_gate_action"] == "observe"
    assert pick.metrics["quality_gate_status"] == "observe"
    assert pick.metrics["quality_gate_reasons"] == ("缺少量价确认",)
    assert pick.metrics["paper_review_eligible"] is False
    assert pick.metrics["observation_only"] is True
    assert pick.metrics["portfolio_action"] == "observation_only"
    assert pick.metrics["candidate_status"] == "质量观察"
    assert pick.metrics["candidate_next_step"].startswith("等待短线动量")
    assert captured["briefing"].portfolio_summary.allocations == ()


def test_run_briefing_ignores_stale_source_metadata_from_previous_date(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    ledger.write_text(
        "\n".join(
            [
                '{"signal_date":"2026-07-07","status":"run_completed_no_picks","symbol":"__RUN__",'
                '"run_requested_source":"auto","run_actual_source":"eastmoney",'
                '"run_source_freshness_tier":"realtime","run_source_coverage_tier":"multi_dimensional",'
                '"run_source_health_label":"healthy","run_source_health_message":"old ok",'
                '"run_data_latest_trade_date":"2026-07-07","run_data_lag_days":0}',
                '{"signal_date":"2026-07-08","status":"watch_only","symbol":"600000",'
                '"name":"浦发银行","signal_close":10.0,"score":55,"rating":"watch",'
                '"portfolio_action":"keep","strategies":["ma_pullback"],"reasons":["趋势回踩"],"risks":[]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "aqsp.briefing.enhance_briefing", lambda briefing, enable_llm: briefing
    )
    monkeypatch.setattr(
        "aqsp.briefing.notifier.send_smart_summary_card", lambda briefing: None
    )

    exit_code = cli_mod.run_briefing(
        Namespace(
            ledger=str(ledger),
            output=str(output),
            enable_llm=False,
            notify=False,
            email=False,
        )
    )

    text = output.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "暂无最近一次运行的数据源状态记录" in text
    assert "eastmoney" not in text
    assert "old ok" not in text


def test_run_briefing_rehydrates_market_context_from_ledger(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    ledger = tmp_path / "predictions.jsonl"
    output = tmp_path / "briefing.md"
    ledger.write_text(
        "\n".join(
            [
                '{"signal_date":"2026-07-08","status":"watch_only","symbol":"600000",'
                '"name":"浦发银行","signal_close":10.0,"score":55,"rating":"watch",'
                '"portfolio_action":"keep","strategies":["ma_pullback"],"reasons":["趋势回踩"],"risks":[],'
                '"regime_at_signal":"stable_bull",'
                '"run_requested_source":"auto","run_actual_source":"sina",'
                '"run_source_freshness_tier":"realtime","run_source_coverage_tier":"multi_dimensional",'
                '"run_source_health_label":"fallback","run_source_health_message":"fallback 到 sina",'
                '"run_data_latest_trade_date":"2026-07-08","run_data_lag_days":0,'
                '"run_market_context_overview":"AI 算力跨市主线",'
                '"run_market_context_lines":["运行判定: HMM stable_bull","跨市主线: AI 算力"]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "aqsp.briefing.enhance_briefing", lambda briefing, enable_llm: briefing
    )
    monkeypatch.setattr(
        "aqsp.briefing.notifier.send_smart_summary_card", lambda briefing: None
    )

    exit_code = cli_mod.run_briefing(
        Namespace(
            ledger=str(ledger),
            output=str(output),
            enable_llm=False,
            notify=False,
            email=False,
        )
    )

    text = output.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "AI 算力跨市主线" in text
    assert "运行判定: HMM stable_bull" in text
    assert "最新交易日 2026-07-08 / 延迟 0 天" in text


def test_dispatch_notification_once_dedupes_when_notifier_is_patched(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import aqsp.cli as cli_mod

    calls: list[str] = []
    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(tmp_path / "notify_state.json"))
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: (
            calls.append(markdown)
            or [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")]
        ),
    )

    first = cli_mod._dispatch_notification_once(
        "# 测试通知",
        prefix="test notify",
        mode="summary",
        kind="test:2026-06-22",
        summary_markdown="测试摘要",
    )
    second = cli_mod._dispatch_notification_once(
        "# 测试通知",
        prefix="test notify",
        mode="summary",
        kind="test:2026-06-22",
        summary_markdown="测试摘要",
    )

    assert len(first) == 1
    assert second == []
    assert calls == ["测试摘要"]
    assert "test notify: skipped duplicate" in capsys.readouterr().out


def test_run_closing_review_prints_notify_failure(monkeypatch, capsys) -> None:
    import aqsp.cli as cli_mod

    class FakeReview:
        date = "2026-06-15"

    monkeypatch.setattr(
        "aqsp.briefing.closing_review.ClosingReviewer",
        lambda ledger_path: type(
            "R",
            (),
            {"review_today": lambda self, date=None: FakeReview()},
        )(),
    )
    monkeypatch.setattr(
        "aqsp.briefing.closing_review.format_daily_review",
        lambda review: "# review",
    )
    monkeypatch.setattr(
        cli_mod,
        "build_closing_review_notification",
        lambda **_kwargs: "# notify",
    )
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("notify boom")),
    )

    exit_code = cli_mod.run_closing_review(
        Namespace(date="", weekly=False, output="", notify=True)
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "review notify failed: notify boom" in output


def test_execution_summary_uses_paper_review_when_pm_has_allocations() -> None:
    import aqsp.cli as cli_mod

    pick = PickResult(
        symbol="600519",
        name="贵州茅台",
        date="2026-06-09",
        close=1500.0,
        score=88,
        rating="buy_candidate",
        entry_type="trend_pullback",
        ideal_buy=1498.0,
        stop_loss=1450.0,
        take_profit=1600.0,
        position="20%",
    )
    summary = PortfolioDecisionSummary(
        promote_count=1,
        downgrade_count=0,
        keep_count=0,
        top_focus=("600519 贵州茅台",),
        watchlist=(),
        allocations=(
            PortfolioAllocation(
                symbol="600519",
                name="贵州茅台",
                weight=0.2,
                rationale=("主链评分 88",),
            ),
        ),
        cash_reserve=0.8,
        allocation_note="纸面仓位上限 20%",
    )

    line = cli_mod._build_execution_summary_line([pick], summary)

    assert "优先纸面复核" in line
    assert "观察参考" in line
    assert "防守" in line
    assert "首选" not in line
    assert "买点" not in line


def test_run_scheduled_notify_prepends_source_status_banner(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    # This test validates notification ordering and source status; it must not
    # launch the live multi-source catalyst subprocess graph.
    monkeypatch.setattr(cli_mod, "_should_build_market_context", lambda _task_id: False)

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    # 直接 monkeypatch _check_notification_gate，让它总是返回双门通过
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )  # 35 >= 30，冷启动通过
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda *_args, **_kwargs: None,
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: (
            "fallback",
            "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
            True,
        ),
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    assert seen
    assert seen[0].startswith("# 收盘研究日报-")
    assert "## 数据" in seen[0]
    assert "## 结果" in seen[0]
    assert seen[0].index("## 数据") < seen[0].index("## 结果")
    assert "auto -> eastmoney" in seen[0]
    assert "- 健康: fallback" in seen[0]
    assert "## 🧭" not in seen[0]


def test_run_scheduled_enriches_pick_name_from_symbol_map(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "300750": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "300750",
                    "name": "300750",
                    "open": 430.0,
                    "high": 435.0,
                    "low": 428.0,
                    "close": 432.0,
                    "volume": 1000,
                    "amount": 432000000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["300750"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "_load_optional_symbol_name_map",
        lambda symbols: {"300750": "宁德时代"} if "300750" in symbols else {},
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="300750",
                name="300750",
                date=latest,
                close=432.0,
                score=71.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=432.0,
                stop_loss=420.0,
                take_profit=460.0,
                position="watch",
                strategies=("bowl_rebound",),
                reasons=("MACD 动能改善",),
                risks=("流动性过滤",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda markdown: [])

    args = Namespace(
        mode="close",
        symbols="300750",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "300750 宁德时代" in report
    assert "300750 300750" not in report


def test_optional_symbol_name_map_reads_project_env_without_export(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    db_path = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "INSERT INTO stocks (ts_code, name) VALUES (?, ?)",
            ("600036.SH", "招商银行"),
        )
    env_path = tmp_path / ".env"
    env_path.write_text(f"AQSP_SQLITE_DB_PATH={db_path}\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AQSP_SQLITE_DB_PATH", raising=False)

    assert cli_mod._load_optional_symbol_name_map(["600036"]) == {"600036": "招商银行"}


def test_run_scheduled_sends_gate_block_alert_when_notify_is_disabled_by_gate(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")
    monkeypatch.setenv("AQSP_GATE_NOTIFY", "true")
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (
            False,
            ["冷启动未满 30 天（当前 12 天）", "DSR 未过门（0.08 < 0.20）"],
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 12
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    assert seen
    assert seen[0].startswith(f"# 通知未放行-{latest}")
    assert "本次正常通知未放行" in seen[0]
    assert "冷启动未满 30 天" in seen[0]
    assert "## 阻塞" in seen[0]
    report_text = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "未通过 walk-forward 双门验证" in report_text


def test_run_scheduled_uses_env_notify_when_cli_notify_is_false(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_NOTIFY", "true")
    monkeypatch.setenv("AQSP_GATE_NOTIFY", "true")
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )

    latest = TEST_TRADE_DAY.isoformat()
    frame = pd.DataFrame(
        [
            {
                "date": latest,
                "symbol": "600519",
                "name": "贵州茅台",
                "open": 1500.0,
                "high": 1510.0,
                "low": 1490.0,
                "close": 1505.0,
                "volume": 1000,
                "amount": 150500000.0,
                "suspended": False,
                "limit_up": 1655.5,
                "limit_down": 1354.5,
            }
        ]
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (False, ["冷启动未满 30 天"]),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: ({"600519": frame, "000300": frame}, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 12
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod, "notify_markdown", lambda markdown: seen.append(markdown) or []
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
    )

    assert cli_mod.run_scheduled(args) == 0
    assert seen and seen[0].startswith(f"# 通知未放行-{latest}")


def test_run_scheduled_ignores_env_notify_without_daily_task_id(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_NOTIFY", "true")
    monkeypatch.delenv("AQSP_RUN_TASK_ID", raising=False)
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )

    latest = TEST_TRADE_DAY.isoformat()
    frame = pd.DataFrame(
        [
            {
                "date": latest,
                "symbol": "600519",
                "name": "贵州茅台",
                "open": 1500.0,
                "high": 1510.0,
                "low": 1490.0,
                "close": 1505.0,
                "volume": 1000,
                "amount": 150500000.0,
                "suspended": False,
                "limit_up": 1655.5,
                "limit_down": 1354.5,
            }
        ]
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (False, ["冷启动未满 30 天"]),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: ({"600519": frame, "000300": frame}, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 12
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod, "notify_markdown", lambda markdown: seen.append(markdown) or []
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
    )

    assert cli_mod.run_scheduled(args) == 0
    assert seen == []


def test_run_monitor_notifies_warning_when_warning_notify_enabled(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_MONITOR_NOTIFY_WARNINGS", "true")
    warning = MagicMock(triggered=True, severity="warning", name="disk", message="low")
    sent: list[list[object]] = []

    class DummyChecker:
        def __init__(self, config_path: str):
            self.config_path = config_path

        def check_all(self):
            return [warning]

    monkeypatch.setattr("aqsp.monitor.checker.MonitorChecker", DummyChecker)
    monkeypatch.setattr("aqsp.monitor.notifier.format_alert", lambda alerts: "alert")
    monkeypatch.setattr(
        "aqsp.monitor.notifier.send_alerts",
        lambda alerts: sent.append(list(alerts)),
    )

    exit_code = cli_mod.run_monitor(
        Namespace(
            config=str(tmp_path / "monitors.yaml"),
            notify=True,
            dry_run=False,
            notify_critical_only=False,
        )
    )

    assert exit_code == 0
    assert sent == [[warning]]


def test_run_scheduled_debate_writes_back_adjustment_keeps_runtime_score(
    monkeypatch, tmp_path
) -> None:
    """辩论结论回写到解释层，但不改写 runtime 候选结论。

    委员会附件契约：
    - pick.score（runtime 原始分）与顺序保持不变，用于溯源。
    - Agent 的调整分与建议不写回 PickResult，只保留讨论证据。
    """
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        symbol: pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": symbol,
                    "name": name,
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": 1000,
                    "amount": close * 1000,
                    "suspended": False,
                    "limit_up": close * 1.1,
                    "limit_down": close * 0.9,
                }
            ]
        )
        for symbol, name, close in (
            ("600519", "贵州茅台", 1505.0),
            ("300750", "宁德时代", 432.0),
        )
    }
    original_picks = [
        PickResult(
            symbol="600519",
            name="贵州茅台",
            date=latest,
            close=1505.0,
            score=80.0,
            rating="buy_candidate",
            entry_type="next_open",
            ideal_buy=1505.0,
            stop_loss=1450.0,
            take_profit=1600.0,
            position="10%-30%",
            strategies=("ma_pullback",),
        ),
        PickResult(
            symbol="300750",
            name="宁德时代",
            date=latest,
            close=432.0,
            score=40.0,
            rating="buy_candidate",
            entry_type="next_open",
            ideal_buy=432.0,
            stop_loss=420.0,
            take_profit=460.0,
            position="watch",
            strategies=("bowl_rebound",),
        ),
    ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod, "_check_notification_gate", lambda **_kwargs: (True, [])
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519", "300750"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: list(original_picks),
    )
    monkeypatch.setattr(cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: MagicMock(warnings=()),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(matrix={}, high_corr_pairs=()),
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )

    captured_roles: dict[str, tuple[str, ...]] = {}

    class DummyDebateCoordinator:
        def __init__(self, *_args, **_kwargs):
            captured_roles["roles"] = tuple(
                role.value for role in _kwargs.get("roles", ())
            )

        def run_debate(
            self,
            pick,
            _df,
            *,
            signal_date: str,
            market_context_lines=(),
        ):
            adjusted_score = 95.0 if pick.symbol == "300750" else 10.0
            disagreement_score = 0.1 if pick.symbol == "300750" else 0.8
            return DebateResult(
                debate_id=f"debate-{pick.symbol}",
                symbol=pick.symbol,
                name=pick.name,
                original_score=pick.score,
                rating=pick.rating,
                thresholds_version="test",
                related_signal_date=signal_date,
                final_consensus="bullish",
                final_vote={AgentRole.BULL: "bullish"},
                disagreement_score=disagreement_score,
                adjustment_weight=0.7,
                adjusted_score=adjusted_score,
                recommended_adjustment="raise",
                adjustment_reason="测试：低分票被建议上调",
                research_verdict=f"{pick.symbol} 倾向优先纸面复核",
                primary_risk_gate=f"{pick.symbol} 先卡住追高回撤",
                next_trigger=f"{pick.symbol} 先确认次日成交质量",
                support_points=(f"{pick.symbol} 支持观点",),
                opposition_points=(f"{pick.symbol} 反对观点",),
                watch_items=(f"{pick.symbol} 待确认事项",),
                role_selection_summary=f"{pick.symbol} 选角: 技术多头 + 跨市传导",
                role_selection_plan=f"{pick.symbol} 角色分工: 多头看技术，跨市看传导",
                role_reliability_lines=(f"{pick.symbol} 角色可信度 7/10",),
                cross_market_support_event_count=2,
                cross_market_conflict_event_count=1,
                cross_market_evidence_stack_summary=f"{pick.symbol} 同向 2 条｜反向 1 条",
                market_context_lines=tuple(market_context_lines),
            )

    monkeypatch.setattr(cli_mod, "AShareDebateCoordinator", DummyDebateCoordinator)

    captured: list[PickResult] = []

    def fake_append_predictions(_path, picks, **_kwargs):
        captured.extend(picks)

    monkeypatch.setattr(cli_mod, "append_predictions", fake_append_predictions)
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "Bundle",
            (),
            {
                "picks": list(picks),
                "decisions": (),
                "summary": PortfolioDecisionSummary(
                    promote_count=0,
                    downgrade_count=0,
                    keep_count=len(picks),
                    top_focus=(),
                    watchlist=(),
                    allocations=(),
                    cash_reserve=1.0,
                    allocation_note="测试",
                ),
            },
        )(),
    )
    monkeypatch.setattr("aqsp.portfolio.snapshot.save_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda *args, **kwargs: type(
            "Composite",
            (),
            {"calculate_score": lambda self, data, regime="unknown": {}},
        )(),
    )

    args = Namespace(
        mode="close",
        symbols="600519,300750",
        csv="",
        source="auto",
        limit=2,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
        enable_debate=True,
        pool="",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "closing")

    exit_code = cli_mod.run_scheduled(args)
    debate_rows = [
        line
        for line in (tmp_path / "data" / "debate_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert exit_code == 0
    assert "bull" in captured_roles["roles"]
    assert "bear" in captured_roles["roles"]
    assert [pick.symbol for pick in captured] == ["600519", "300750"]
    # runtime 原始评分与顺序保持不变（溯源用）
    assert [pick.score for pick in captured] == [80.0, 40.0]
    assert [pick.recommended_adjustment for pick in captured] == ["keep", "keep"]
    assert [pick.adjusted_score for pick in captured] == [0.0, 0.0]
    assert captured[0].metrics["debate_id"] == "debate-600519"
    assert captured[0].metrics["debate_disagreement_score"] == 0.8
    assert captured[0].metrics["debate_final_vote"] == {"bull": "bullish"}
    assert captured[0].metrics["debate_active_roles"] == ["bull"]
    assert captured[0].metrics["debate_active_role_summary"] == "技术多头"
    assert captured[0].metrics["debate_research_verdict"] == "600519 倾向优先纸面复核"
    assert captured[0].metrics["debate_primary_risk_gate"] == "600519 先卡住追高回撤"
    assert captured[0].metrics["debate_next_trigger"] == "600519 先确认次日成交质量"
    assert (
        captured[0].metrics["debate_role_selection_summary"]
        == "600519 选角: 技术多头 + 跨市传导"
    )
    assert (
        captured[0].metrics["debate_role_selection_plan"]
        == "600519 角色分工: 多头看技术，跨市看传导"
    )
    assert captured[0].metrics["support_points"] == ["600519 支持观点"]
    assert captured[0].metrics["opposition_points"] == ["600519 反对观点"]
    assert captured[0].metrics["watch_items"] == ["600519 待确认事项"]
    assert captured[0].metrics["role_reliability_lines"] == ["600519 角色可信度 7/10"]
    assert captured[0].metrics["cross_market_support_event_count"] == 2
    assert captured[0].metrics["cross_market_conflict_event_count"] == 1
    assert (
        captured[0].metrics["cross_market_evidence_stack_summary"]
        == "600519 同向 2 条｜反向 1 条"
    )
    assert captured[1].metrics["debate_research_verdict"] == "300750 倾向优先纸面复核"
    assert len(debate_rows) == 2
    serialized_rows = [json.loads(row) for row in debate_rows]
    assert {row["symbol"] for row in serialized_rows} == {"600519", "300750"}
    assert {row["related_signal_date"] for row in serialized_rows} == {latest}
    assert {row["candidate_signal_date"] for row in serialized_rows} == {latest}
    assert all(row["candidate_fingerprint"] for row in serialized_rows)
    assert any(
        row["symbol"] == "300750" and row["disagreement_score"] == 0.1
        for row in serialized_rows
    )


def test_debate_record_merge_keeps_same_symbol_date_distinct_candidates() -> None:
    import aqsp.cli as cli_mod

    retained: dict[str, dict] = {}
    cli_mod._merge_debate_records(
        retained,
        {
            "old": {
                "symbol": "600519",
                "related_signal_date": "2026-06-05",
                "task_id": "main_chain",
                "candidate_fingerprint": "fp-a",
                "created_at": "2026-06-05T09:40:00+08:00",
            },
            "new": {
                "symbol": "600519",
                "related_signal_date": "2026-06-05",
                "task_id": "closing_premium",
                "candidate_fingerprint": "fp-b",
                "created_at": "2026-06-05T15:10:00+08:00",
            },
        },
    )

    assert len(retained) == 2
    assert {row["candidate_fingerprint"] for row in retained.values()} == {
        "fp-a",
        "fp-b",
    }


def test_serialize_debate_result_includes_market_context_lines() -> None:
    import aqsp.cli as cli_mod

    result = DebateResult(
        debate_id="debate-ctx",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="buy_candidate",
        market_context_lines=(
            "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
            "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
        ),
    )

    payload = cli_mod.serialize_debate_result(result)

    assert payload["market_context_lines"] == [
        "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
        "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
    ]
    assert payload["support_points"] == []
    assert payload["opposition_points"] == []
    assert payload["watch_items"] == []
    assert payload["research_verdict"] == ""
    assert payload["primary_risk_gate"] == ""
    assert payload["next_trigger"] == ""
    assert payload["role_reliability_lines"] == []
    assert payload["cross_market_support_event_count"] == 0
    assert payload["cross_market_conflict_event_count"] == 0
    assert payload["cross_market_evidence_stack_summary"] == ""
    assert payload["deterministic_score"] == result.original_score
    assert payload["deterministic_score_unchanged"] is True
    assert payload["advisory_only"] is True
    assert payload["realtime_blocked"] is False


def test_serialize_debate_result_preserves_discussion_rounds() -> None:
    import aqsp.cli as cli_mod
    from aqsp.briefing.debate import AgentOpinion, DebateRound

    result = DebateResult(
        debate_id="debate-rounds",
        symbol="300750",
        name="宁德时代",
        original_score=72.0,
        rating="buy_candidate",
        rounds=[
            DebateRound(
                round_num=1,
                summary="首轮观点",
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="bullish",
                        confidence=0.7,
                        arguments=["趋势仍在"],
                    )
                ],
            ),
            DebateRound(
                round_num=2,
                summary="二轮复核",
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="neutral",
                        confidence=0.6,
                        arguments=["等待承接"],
                        counterarguments=["量能尚未确认"],
                    )
                ],
            ),
        ],
    )

    payload = cli_mod.serialize_debate_result(result)

    assert [item["round_num"] for item in payload["rounds"]] == [1, 2]
    assert payload["rounds"][1]["opinions"][0]["counterarguments"] == [
        "量能尚未确认"
    ]


def test_resolve_pick_debate_roles_adds_policy_role_for_event_context(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
    monkeypatch.delenv("AQSP_DEBATE_FOCUS_ROLES", raising=False)
    monkeypatch.delenv("AQSP_DEBATE_DISABLED_ROLES", raising=False)

    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-06-26",
        close=430.0,
        score=82.0,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=430.0,
        stop_loss=418.0,
        take_profit=455.0,
        position="watch",
        metrics={
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "优先复核",
            "cross_market_priority_score": 3,
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 0,
        },
    )
    runtime = type(
        "Runtime",
        (),
        {
            "roles": (
                "risk_control",
                "sector_leader",
                "cross_market",
                "northbound",
                "retail_mood",
            ),
            "role_runtime": (),
        },
    )()

    roles = cli_mod._resolve_pick_debate_roles(
        runtime,
        pick=pick,
        market_context_lines=(
            "传导推演[海外物理AI叙事升温]: 动作 优先复核。",
            "政策跟踪: 工信部继续强调机器人产业链支持。",
        ),
    )

    assert roles == (
        "risk_control",
        "cross_market",
        "sector_leader",
        "bull",
        "policy_sensitive",
        "northbound",
        "retail_mood",
    )


def test_run_scheduled_report_omits_low_signal_control_sections(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda *_args, **_kwargs: None,
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"600519": {"600519": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    report_path = tmp_path / "latest.md"
    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(report_path),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    content = report_path.read_text(encoding="utf-8")
    assert "## 今日重点看板" in content
    assert "## 数据异常检测" not in content
    assert "## 数据新鲜度" not in content
    assert "## 候选股相关性" not in content
    assert "## 策略衰减告警" not in content


def test_run_scheduled_notify_continues_when_benchmark_frame_missing(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"600519": {"600519": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("warning", "benchmark unavailable", False),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert seen
    assert "当前市况" not in report
    assert "- 市场标签: unknown" in report
    assert "## 今日重点看板" in report
    assert "贵州茅台" in report


def test_run_scheduled_gate_block_adds_actionable_unlock_guidance(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (
            False,
            [
                "冷启动未满: 3/30 个独立信号日",
                "双门 sidecar 无有效回测周期（n_periods=0）—— 疑似占位/测试数据，需真正跑 walkforward 后重写",
            ],
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 3
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="watch",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="watch",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"600519": {"600519": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "未通过 walk-forward 双门验证" in report
    assert "当前还差 27 天" in report
    assert "walkforward" in report
    assert "。；" not in report


def test_run_scheduled_fails_closed_on_regime_when_benchmark_missing(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 23))
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 23))
    dates = pd.date_range(end="2026-06-23", periods=80, freq="B")

    def make_frame(symbol: str, start_close: float) -> pd.DataFrame:
        closes = [start_close + idx * 0.8 for idx in range(len(dates))]
        volumes = [100000000 + idx * 1000000 for idx in range(len(dates))]
        return pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "symbol": symbol,
                "name": symbol,
                "open": closes,
                "high": [value + 1.0 for value in closes],
                "low": [value - 1.0 for value in closes],
                "close": closes,
                "volume": volumes,
                "amount": [close * volume for close, volume in zip(closes, volumes)],
                "suspended": False,
                "limit_up": 0.0,
                "limit_down": 0.0,
            }
        )

    frames = {
        "600519": make_frame("600519", 1500.0),
        "300750": make_frame("300750", 200.0),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519", "300750"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=str(dates[-1].date()),
                close=1563.2,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1563.2,
                stop_loss=1500.0,
                take_profit=1620.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"600519": {"600519": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("warning", "benchmark unavailable", False),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="600519,300750",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert seen
    assert "- 市场标签: unknown" in report
    assert "当前市况: 稳定上涨" not in report
    assert "benchmark unavailable" in report


def test_run_scheduled_report_downgrades_realtime_tier_when_data_is_prior_trade_day(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = "2026-06-04"
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 5))
    monkeypatch.setattr("aqsp.freshness.today_shanghai", lambda: date(2026, 6, 5))
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "sina"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("fallback", "fallback 到 sina", True),
    )
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda markdown: [])

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "- 数据完整度: 收盘后 / 增强行情 / 无需本地缓存" in report
    assert "- 数据时效: 最新交易日 2026-06-04 / 延迟 1 天" in report


def test_run_scheduled_surfaces_t1_blockers_in_report_and_notification(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "300750": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "300750",
                    "name": "宁德时代",
                    "open": 430.0,
                    "high": 435.0,
                    "low": 428.0,
                    "close": 432.0,
                    "volume": 1000,
                    "amount": 432000000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *args, **kwargs: ["600519", "300750"],
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            ),
            PickResult(
                symbol="300750",
                name="宁德时代",
                date=latest,
                close=432.0,
                score=39.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=432.0,
                stop_loss=420.0,
                take_profit=460.0,
                position="watch",
                strategies=("bowl_rebound",),
                reasons=("MACD 动能改善",),
                risks=("流动性过滤",),
            ),
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (
            [symbol for symbol in candidates if symbol != "600519"],
            ["600519"],
        ),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"300750": {"300750": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="600519,300750",
        csv="",
        source="auto",
        limit=2,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert seen
    assert "T+1 持仓约束：昨日已买标的今日不纳入纸面复核名单" in report
    assert "贵州茅台: T+1 持仓约束，昨日已买，今日仅保留观察" in report
    assert "T+1 限制：昨日已买 1 只（600519）仅保留观察" in report
    assert "300750 宁德时代" in seen[0]
    assert "600519 贵州茅台" in seen[0]
    assert "T+1 持仓约束，昨日已买，今日仅保留观察" in seen[0]
    assert "## 🔒" not in seen[0]


def test_run_scheduled_surfaces_snapshot_lifecycle_in_summary_and_notification(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "688981": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "688981",
                    "name": "中芯国际",
                    "open": 131.0,
                    "high": 133.0,
                    "low": 130.0,
                    "close": 131.79,
                    "volume": 1000,
                    "amount": 131790000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *args, **kwargs: ["688981"],
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="688981",
                name="中芯国际",
                date=latest,
                close=131.79,
                score=-9.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=131.79,
                stop_loss=128.08,
                take_profit=161.554,
                position="watch",
                strategies=(),
                reasons=("MA20 斜率向上",),
                risks=("收盘价低于 MA20",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"688981": {"688981": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots",
        lambda *args, **kwargs: SnapshotDiff(
            date_current=latest,
            date_previous="2026-06-04",
            new_picks=(
                PickSnapshot(
                    symbol="688981",
                    name="中芯国际",
                    score=-9.0,
                    rank=1,
                    adjusted_score=-9.0,
                    recommended_adjustment="keep",
                ),
            ),
            removed_picks=(
                PickSnapshot(
                    symbol="600036",
                    name="招商银行",
                    score=24.0,
                    rank=1,
                    adjusted_score=24.0,
                    recommended_adjustment="keep",
                ),
            ),
            rank_changes=(("300750", 4, 5),),
            score_changes=(),
        ),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda *args, **kwargs: type(
            "Composite",
            (),
            {"calculate_score": lambda self, data, regime="unknown": {}},
        )(),
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="688981",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert seen
    assert "🆕 **新晋候选**: 688981 中芯国际" in report
    assert "归档移出记录: 600036 招商银行" in report
    assert "排名记录变化: 300750 #4→#5↓" in report
    assert "- 变化: 新增 1 / 移出 1" in seen[0]
    assert "## 变化" in seen[0]
    assert "- 新晋候选: 688981 中芯国际" in seen[0]
    assert "排名记录变化: 300750 #4→#5↓" in seen[0]


def test_main_accepts_run_scheduled_alias(monkeypatch) -> None:
    from aqsp.cli import main
    import aqsp.cli as cli_mod

    def mock_run_scheduled(args):
        assert args.command == "run-scheduled"
        assert args.symbols == "600519"
        return 0

    monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)
    assert main(["run-scheduled", "--symbols", "600519"]) == 0


def test_run_scheduled_annotates_candidate_status_in_report_and_notify(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "688981": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "688981",
                    "name": "中芯国际",
                    "open": 131.0,
                    "high": 133.0,
                    "low": 130.0,
                    "close": 131.79,
                    "volume": 1000,
                    "amount": 131790000.0,
                    "suspended": False,
                    "limit_up": 145.0,
                    "limit_down": 118.0,
                }
            ]
        ),
        "000001": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000001",
                    "name": "平安银行",
                    "open": 10.7,
                    "high": 10.9,
                    "low": 10.6,
                    "close": 10.82,
                    "volume": 1000,
                    "amount": 108200000.0,
                    "suspended": False,
                    "limit_up": 11.9,
                    "limit_down": 9.74,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["688981", "000001"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="688981",
                name="中芯国际",
                date=latest,
                close=131.79,
                score=-9.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=131.79,
                stop_loss=128.08,
                take_profit=161.554,
                position="watch",
                strategies=("momentum",),
                reasons=("MA20 斜率向上",),
                risks=("收盘价低于 MA20",),
            ),
            PickResult(
                symbol="000001",
                name="平安银行",
                date=latest,
                close=10.82,
                score=-18.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=10.82,
                stop_loss=10.73,
                take_profit=11.731,
                position="watch",
                strategies=("value_defense",),
                reasons=("估值防守",),
                risks=("缺少量能确认",),
            ),
        ],
    )

    class FakeCompositeStrategy:
        def __init__(self, thresholds=None):
            self.thresholds = thresholds

        def calculate_score(self, data, regime="unknown"):
            return {}

    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        FakeCompositeStrategy,
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    ledger_picks: list[PickResult] = []
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda _path, picks, **_kwargs: ledger_picks.extend(picks),
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={
                "688981": {"688981": 1.0, "000001": 0.25},
                "000001": {"688981": 0.25, "000001": 1.0},
            },
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots",
        lambda *args, **kwargs: SnapshotDiff(
            date_current=latest,
            date_previous="2026-06-04",
            new_picks=(
                PickSnapshot(
                    symbol="688981",
                    name="中芯国际",
                    score=-9.0,
                    rank=1,
                    adjusted_score=-9.0,
                    recommended_adjustment="keep",
                ),
            ),
            removed_picks=(),
            rank_changes=(),
            score_changes=(),
        ),
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

    args = Namespace(
        mode="close",
        symbols="688981,000001",
        csv="",
        source="auto",
        limit=2,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert seen
    assert ledger_picks[0].metrics["candidate_status"] == "新晋"
    assert (
        ledger_picks[0].metrics["candidate_next_step"]
        == "等待量价继续走强后，再评估是否转入纸面复核名单"
    )
    assert ledger_picks[0].metrics["candidate_review_priority"] == "high"
    assert "## 候选" in seen[0]
    assert "| # | 标的 | 状态 | 分数 | 处理 | 关键点 |" not in seen[0]
    assert (
        "- 1. 688981 中芯国际 | 新晋 | -9 | 继续观察: 等待量价继续走强后，再评估是否转入纸面复核名单"
        in seen[0]
    )
    assert "- 暂无纸面复核主线，观察名单：" in seen[0]
    assert "- 688981 中芯国际" in seen[0]
    assert "复核: 高优先级 / 盘中走强后" in seen[0]
    assert "- 2. 000001 平安银行 | 继续观察 | -18 | 继续观察: 估值防守" in seen[0]
    assert "## 📋" not in seen[0]
    assert "- 重点 1: 688981 中芯国际 | 继续观察 | 新晋 | 评分 -9.0" in report
    assert "- 决策: 继续观察 | 新晋 | 评分 -9.0" in report
    assert "- 下一步: 等待量价继续走强后，再评估是否转入纸面复核名单" in report
    assert "- 再看优先级/时机: 高优先级 / 盘中走强后" in report


def test_run_scheduled_skips_gate_for_intraday_task(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("gate should be skipped")
        ),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "append_run_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 0
    )
    monkeypatch.setattr(cli_mod, "screen_universe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(high_corr_pairs=(), matrix={}),
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="eastmoney",
        limit=1,
        max_universe=1,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "intraday.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "高频任务跳过双门检查: task_id=intraday" in output
    assert "冷启动统计: ledger=" in output


def test_run_scheduled_intraday_uses_formal_ledger_for_runtime_stats_and_compact_report(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }
    formal_ledger = tmp_path / "predictions.jsonl"
    intraday_ledger = tmp_path / "intraday.jsonl"
    seen: dict[str, object] = {}

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_LEDGER", str(formal_ledger))
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("gate should be skipped")
        ),
    )

    class Validation:
        checked = 12
        wins = 8
        avg_return_pct = 1.2
        avg_excess_pct = 0.6
        skipped_not_executable = 0
        not_executable_reasons = {}
        strategy_not_executable_rates = {}

    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda ledger_path, *_args, **_kwargs: (
            seen.__setitem__("validation_ledger", ledger_path) or Validation()
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "_compute_real_pnl",
        lambda ledger_path, *_args, **_kwargs: (
            seen.__setitem__("pnl_ledger", ledger_path) or (0.0, 0.0, 0.0)
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "_count_independent_signal_days",
        lambda ledger_path: seen.__setitem__("cold_start_ledger", ledger_path) or 34,
    )
    monkeypatch.setattr(
        cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "stable_bull"
    )
    monkeypatch.setattr("aqsp.data.anomaly.detect_anomalies", lambda _frames: [])
    monkeypatch.setattr("aqsp.data.freshness.check_freshness", lambda _frames: [])
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(high_corr_pairs=(), matrix={}),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=88.0,
                rating="buy_candidate",
                entry_type="watch",
                ideal_buy=1500.0,
                stop_loss=1450.0,
                take_profit=1580.0,
                position="watch",
            )
        ],
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "append_run_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots",
        lambda *_args, **_kwargs: None,
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="eastmoney",
        limit=1,
        max_universe=1,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(intraday_ledger),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=False,
        notify=False,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    assert seen["validation_ledger"] == str(formal_ledger)
    assert seen["pnl_ledger"] == str(formal_ledger)
    assert seen["cold_start_ledger"] == str(formal_ledger)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "## 策略自检" not in report
    assert "冷启动期:" not in report
    assert "## 失败模式分析" not in report
    assert "## 1." not in report


def test_run_scheduled_intraday_writes_provisional_outputs_before_late_failure(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    latest = TEST_TRADE_DAY.isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }
    report_path = tmp_path / "intraday.md"
    csv_path = tmp_path / "intraday.csv"
    mirror_report_path = tmp_path / "public_intraday.md"
    mirror_csv_path = tmp_path / "public_intraday.csv"

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_LEDGER", str(tmp_path / "formal.jsonl"))
    monkeypatch.setenv("AQSP_PROVISIONAL_REPORT", str(mirror_report_path))
    monkeypatch.setenv("AQSP_PROVISIONAL_OUTPUT_CSV", str(mirror_csv_path))
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *args, **kwargs: (frames, "tencent"),
    )
    monkeypatch.setattr(
        cli_mod, "_compute_real_pnl", lambda *_args, **_kwargs: (0.0, 0.0, 0.0)
    )
    monkeypatch.setattr(cli_mod, "_count_independent_signal_days", lambda _path: 34)
    monkeypatch.setattr(
        cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "stable_bull"
    )
    monkeypatch.setattr(cli_mod, "_should_build_market_context", lambda _task_id: False)
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )

    class CompositeAfterSnapshot:
        def __init__(self, thresholds=None):
            assert report_path.exists()
            assert csv_path.exists()
            assert mirror_report_path.exists()
            assert mirror_csv_path.exists()

        def calculate_score(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        CompositeAfterSnapshot,
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=88.0,
                rating="buy_candidate",
                entry_type="watch",
                ideal_buy=1500.0,
                stop_loss=1450.0,
                take_profit=1580.0,
                position="watch",
            )
        ],
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: (_ for _ in ()).throw(RuntimeError("late diagnostics failed")),
    )

    args = Namespace(
        mode="open",
        symbols="600519",
        csv="",
        source="online_first",
        limit=1,
        max_universe=1,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        enable_online_factors=False,
        report=str(report_path),
        output_csv=str(csv_path),
        ledger=str(tmp_path / "intraday.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
    )

    with pytest.raises(RuntimeError, match="late diagnostics failed"):
        cli_mod.run_scheduled(args)

    assert report_path.exists()
    assert csv_path.exists()
    assert mirror_report_path.exists()
    assert mirror_csv_path.exists()
    assert "盘中快照" in report_path.read_text(encoding="utf-8")
    assert "600519" in csv_path.read_text(encoding="utf-8")
    assert "盘中快照" in mirror_report_path.read_text(encoding="utf-8")
    assert "600519" in mirror_csv_path.read_text(encoding="utf-8")
