from __future__ import annotations

from argparse import Namespace
from datetime import date
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from unittest.mock import MagicMock

from aqsp.core.time import today_shanghai
from aqsp.core.types import PickResult
from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.debate import DebateResult
from aqsp.portfolio.manager import PortfolioDecisionSummary
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.portfolio.snapshot import PickSnapshot, SnapshotDiff


@pytest.fixture(autouse=True)
def _isolated_notify_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(tmp_path / "notify_state.json"))


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
    assert "继续观察名单" in line
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

    latest = today_shanghai().isoformat()
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
    assert seen[0].index("## 数据") < seen[0].index("## 结论")
    assert "auto -> eastmoney" in seen[0]
    assert "- 健康: fallback" in seen[0]
    assert "## 🧭" not in seen[0]


def test_run_scheduled_enriches_pick_name_from_symbol_map(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = today_shanghai().isoformat()
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
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )

    latest = today_shanghai().isoformat()
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
    assert "继续按日运行主链" in seen[0]
    report_text = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "未通过 walk-forward 双门验证" in report_text


def test_run_scheduled_uses_env_notify_when_cli_notify_is_false(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_NOTIFY", "true")
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )

    latest = today_shanghai().isoformat()
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


def test_run_monitor_notifies_warning_when_warning_notify_enabled(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

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
    """辩论结论回写到 pick 供 PM 使用，但不改写 runtime 原始评分与顺序。

    B 方案契约：
    - pick.score（runtime 原始分）与顺序保持不变，用于溯源。
    - pick.recommended_adjustment / adjusted_score 被辩论结论覆盖，PM 据此调整优先级。
    """
    import aqsp.cli as cli_mod

    latest = today_shanghai().isoformat()
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

    class DummyDebateCoordinator:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_debate(self, pick, _df, *, signal_date: str):
            adjusted_score = 95.0 if pick.symbol == "300750" else 10.0
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
                disagreement_score=0.8,
                adjustment_weight=0.7,
                adjusted_score=adjusted_score,
                recommended_adjustment="raise",
                adjustment_reason="测试：低分票被建议上调",
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

    exit_code = cli_mod.run_scheduled(args)
    debate_rows = [
        line
        for line in (tmp_path / "data" / "debate_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert exit_code == 0
    assert [pick.symbol for pick in captured] == ["600519", "300750"]
    # runtime 原始评分与顺序保持不变（溯源用）
    assert [pick.score for pick in captured] == [80.0, 40.0]
    # 辩论结论已回写，供 PM 调整优先级（DummyDebateCoordinator 对两只都给 raise）
    assert [pick.recommended_adjustment for pick in captured] == ["raise", "raise"]
    # adjusted_score 来自辩论桩：600519=10.0, 300750=95.0
    assert [pick.adjusted_score for pick in captured] == [10.0, 95.0]
    assert len(debate_rows) == 2


def test_run_scheduled_report_omits_low_signal_control_sections(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = today_shanghai().isoformat()
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

    latest = today_shanghai().isoformat()
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

    latest = today_shanghai().isoformat()
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
    assert "处理项：" in report
    assert "刷新 gate" in report
    assert "当前还差 27 天" in report
    assert "。；" not in report


def test_run_scheduled_falls_back_to_synthetic_regime_when_benchmark_missing(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    dates = pd.date_range("2026-03-01", periods=80, freq="B")

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
    assert "当前市况" in report
    assert "- 市场标签: stable_bull" in report
    assert "当前市况: 稳定上涨" in report
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

    latest = today_shanghai().isoformat()
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
    assert "- 继续观察名单: 300750 宁德时代、600519 贵州茅台" in seen[0]
    assert (
        "- 现在卡在哪: 600519 贵州茅台: T+1 持仓约束，昨日已买，今日仅保留观察"
        in seen[0]
    )
    assert "## 🔒" not in seen[0]


def test_run_scheduled_surfaces_snapshot_lifecycle_in_summary_and_notification(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = today_shanghai().isoformat()
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
    assert "- 候选变化: 新增 1 / 移出 1 / 排名异动 1" in seen[0]
    assert "## 变化" in seen[0]
    assert "- 新晋候选: 688981 中芯国际" in seen[0]
    assert "## 📈" not in seen[0]


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

    latest = today_shanghai().isoformat()
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
    assert (
        "先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入纸面复核名单（高优先级 / 盘中走强后）。"
        in seen[0]
    )
    assert "- 2. 000001 平安银行 | 继续观察 | -18 | 继续观察: 估值防守" in seen[0]
    assert "## 📋" not in seen[0]
    assert (
        "- 重点 1: 688981 中芯国际 | 继续观察名单 | 新晋 | 评分 -9.0 | 处理 维持原排序"
        in report
    )
    assert "- 决策: 继续观察名单 | 新晋 | 评分 -9.0" in report
    assert "- 接下来先看: 等待量价继续走强后，再评估是否转入纸面复核名单" in report
    assert "- 再看优先级/时机: 高优先级 / 盘中走强后" in report
