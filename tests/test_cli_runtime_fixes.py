from __future__ import annotations

from argparse import Namespace
from datetime import date
from datetime import datetime
import json
import logging
from pathlib import Path
import inspect
from types import SimpleNamespace

import pandas as pd
import pytest
from aqsp.core.errors import DataError


@pytest.fixture(autouse=True)
def _force_runtime_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)


@pytest.fixture(autouse=True)
def _isolated_runtime_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AQSP_NOTIFY_STATE_PATH", str(tmp_path / "notify_state.json"))
    monkeypatch.setenv(
        "AQSP_GATE_NOTIFY_STATE_PATH", str(tmp_path / "gate_notify_state.json")
    )


def _fresh_frame(day: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [day],
            "symbol": ["600000"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [1000],
            "amount": [10200.0],
            "suspended": [False],
            "limit_up": [11.22],
            "limit_down": [9.18],
        }
    )


def test_intraday_debate_coordinator_requires_second_round() -> None:
    import aqsp.cli as cli_mod

    runtime = SimpleNamespace(
        enable_llm=False,
        max_rounds=1,
        thresholds_version="",
        language="zh-CN",
        roles=("bull", "bear"),
        role_runtime=(),
    )

    coordinator = cli_mod._build_debate_coordinator(
        runtime,
        thresholds_version="test",
        regime="intraday",
        data_source="test",
    )

    assert coordinator.max_rounds == 2


def test_data_quality_context_is_visible_without_blocking_candidate() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.data.anomaly import AnomalyAlert
    from aqsp.data.freshness import FreshnessReport

    pick = PickResult(
        symbol="600000",
        name="浦发银行",
        date="2026-07-10",
        close=10.0,
        score=72.0,
        rating="strong_buy_candidate",
        entry_type="pullback",
        ideal_buy=9.9,
        stop_loss=9.2,
        take_profit=11.0,
        position="medium",
    )

    enriched = cli_mod._annotate_data_quality_context(
        [pick],
        anomaly_alerts=[
            AnomalyAlert(
                symbol="600000",
                anomaly_type="price_gap",
                severity="critical",
                detail="开盘跳空: +12.00%",
                value=0.12,
                threshold=0.05,
            )
        ],
        freshness_reports=[
            FreshnessReport(
                symbol="600000",
                last_date="2026-07-06",
                delay_days=4,
                status="critical",
            )
        ],
    )

    result = enriched[0]
    assert result.rating == "strong_buy_candidate"
    assert result.metrics["data_quality_status"] == "critical"
    assert "candidate_blocker" not in result.metrics
    assert "先复核数据质量" in result.metrics["candidate_next_step"]
    assert any("数据质量" in risk for risk in result.risks)


def test_data_quality_context_preserves_existing_candidate_blocker() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.data.anomaly import AnomalyAlert

    pick = PickResult(
        symbol="600000",
        name="浦发银行",
        date="2026-07-10",
        close=10.0,
        score=72.0,
        rating="watch",
        entry_type="pullback",
        ideal_buy=9.9,
        stop_loss=9.2,
        take_profit=11.0,
        position="small",
        metrics={"candidate_blocker": "板块集中度过高"},
    )

    result = cli_mod._annotate_data_quality_context(
        [pick],
        anomaly_alerts=[
            AnomalyAlert(
                symbol="600000",
                anomaly_type="volume_spike",
                severity="warning",
                detail="成交量异常放大: 6.0x 20日均量",
                value=6.0,
                threshold=5.0,
            )
        ],
        freshness_reports=[],
    )[0]

    assert result.metrics["candidate_blocker"] == "板块集中度过高"
    assert result.metrics["data_quality_status"] == "watch"


def test_special_strategy_ledger_guard_blocks_non_trading_day(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)

    allowed, reason = cli_mod._special_strategy_ledger_write_allowed(
        {"600000": _fresh_frame("2026-06-22")},
        max_data_lag_days=1,
    )

    assert allowed is False
    assert "非交易日" in reason


def test_cli_debate_execution_enabled_does_not_bypass_goal_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  multi_agent_advisory_layer:
    enabled: false
    purpose: disable debate
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))

    enabled = cli_mod._debate_execution_enabled(
        Namespace(enable_debate=True),
        Namespace(enabled=False),
    )

    assert enabled is False


def test_special_strategy_ledger_guard_requires_fresh_data(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)
    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 26))

    allowed, reason = cli_mod._special_strategy_ledger_write_allowed(
        {"600000": _fresh_frame("2026-06-20")},
        max_data_lag_days=0,
    )

    assert allowed is False
    assert "数据新鲜度未通过" in reason


def test_run_morning_breakout_skips_non_trading_day_before_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("should not fetch on holiday")
        ),
    )

    args = Namespace(
        symbols="600000",
        source="auto",
        pool="all",
        max_universe=0,
        max_data_lag_days=1,
        benchmark_symbol="000300",
        top=5,
        notify=False,
        output="",
        report="",
        ledger="data/predictions.jsonl",
    )

    assert cli_mod.run_morning_breakout(args) == 0
    assert "今日非交易日，跳过早盘策略" in capsys.readouterr().out


def test_run_closing_premium_skips_non_trading_day_before_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("should not fetch on holiday")
        ),
    )

    args = Namespace(
        symbols="600000",
        source="auto",
        pool="all",
        max_universe=0,
        max_data_lag_days=1,
        benchmark_symbol="000300",
        top=5,
        notify=False,
        output="",
        report="",
        ledger="data/predictions.jsonl",
    )

    assert cli_mod.run_closing_premium(args) == 0
    assert "今日非交易日，跳过尾盘策略" in capsys.readouterr().out


def test_augment_summary_with_market_context_merges_candidate_and_global_overview() -> (
    None
):
    import aqsp.cli as cli_mod
    from aqsp.market_context import build_market_context_artifact
    from aqsp.news.catalysts import CatalystEvent, CatalystReport
    from aqsp.portfolio.manager import PortfolioDecisionSummary

    summary = PortfolioDecisionSummary(
        promote_count=1,
        downgrade_count=0,
        keep_count=0,
        top_focus=("688297 中无人机",),
        watchlist=(),
        allocations=(),
        cash_reserve=1.0,
        allocation_note="今日以观察为主",
        cross_market_overview="海外商业航天催化，重点看 688297 中无人机",
    )
    artifact = build_market_context_artifact(
        catalyst_report=CatalystReport(
            date="2026-06-30",
            generated_at="2026-06-30T14:35:00+08:00",
            source_status="ok",
            events=(
                CatalystEvent(
                    title="SpaceX 拟推进上市并加快低轨卫星部署",
                    source="财联社",
                    published_at="2026-06-30 10:02:00+08:00",
                    impact="positive",
                    category="资本运作",
                    inference="海外商业航天关注度抬升。",
                ),
            ),
            warnings=(),
        )
    )

    merged = cli_mod._augment_summary_with_market_context(
        summary,
        market_context=artifact,
    )

    assert merged is not None
    assert (
        merged.cross_market_overview
        == "海外商业航天催化，重点看 688297 中无人机；方向 商业航天、卫星互联网、军工电子"
    )


def test_runtime_market_context_does_not_restore_prelimit_ranking_hook() -> None:
    import aqsp.cli as cli_mod

    assert not hasattr(cli_mod, "_reprioritize_screened_picks_with_market_context")


def test_filter_catalyst_report_for_symbols_keeps_global_and_selected_events() -> None:
    import aqsp.cli as cli_mod
    from aqsp.news.catalysts import CatalystEvent, CatalystReport

    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
            CatalystEvent(
                title="宁德时代拿下新订单",
                source="公告",
                published_at="2026-06-30 10:20:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="positive",
                category="订单/需求验证",
                inference="电池链关注度抬升。",
            ),
            CatalystEvent(
                title="招商银行获长期资金增持",
                source="公告",
                published_at="2026-06-30 10:40:00+08:00",
                symbol="600036",
                name="招商银行",
                impact="positive",
                category="资本运作",
                inference="银行方向关注度抬升。",
            ),
        ),
        warnings=(),
        event_status="high_impact",
        raw_news_count=3,
        stale_news_count=1,
    )

    filtered = cli_mod._filter_catalyst_report_for_symbols(report, ("300750",))

    assert filtered is not None
    assert [event.symbol for event in filtered.events] == ["", "300750"]
    assert filtered.event_status == "high_impact"
    assert filtered.raw_news_count == 3
    assert filtered.stale_news_count == 1


def test_reprioritize_screened_picks_keeps_direct_news_context_only() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.market_context import build_market_context_artifact
    from aqsp.news.catalysts import CatalystEvent, CatalystReport

    _picks = [
        PickResult(
            symbol="300750",
            name="宁德时代",
            date="2026-07-07",
            close=180.0,
            score=70.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=180.0,
            stop_loss=170.0,
            take_profit=198.0,
            position="watch",
        ),
        PickResult(
            symbol="600036",
            name="招商银行",
            date="2026-07-07",
            close=36.0,
            score=69.9,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=36.0,
            stop_loss=34.0,
            take_profit=39.0,
            position="watch",
        ),
    ]
    artifact = build_market_context_artifact(
        catalyst_report=CatalystReport(
            date="2026-07-07",
            generated_at="2026-07-07T09:10:00+08:00",
            source_status="ok",
            events=(
                CatalystEvent(
                    title="宁德时代被监管问询",
                    source="交易所",
                    published_at="2026-07-07T08:40:00+08:00",
                    symbol="300750",
                    name="宁德时代",
                    impact="negative",
                    category="监管/合规风险",
                    confidence=0.82,
                    source_quality_label="高价值来源",
                    source_quality_score=4,
                    inference="宁德时代 风险抬升，短线回避监管/合规风险方向。",
                ),
            ),
            warnings=(),
        )
    )

    assert artifact is not None
    assert not hasattr(cli_mod, "_reprioritize_screened_picks_with_market_context")


def test_load_runtime_market_context_catalyst_report_reuses_preview_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.news.catalysts import CatalystEvent, CatalystReport

    preview_report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
            CatalystEvent(
                title="宁德时代拿下新订单",
                source="公告",
                published_at="2026-06-30 10:20:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="positive",
                category="订单/需求验证",
                inference="电池链关注度抬升。",
            ),
            CatalystEvent(
                title="招商银行获长期资金增持",
                source="公告",
                published_at="2026-06-30 10:40:00+08:00",
                symbol="600036",
                name="招商银行",
                impact="positive",
                category="资本运作",
                inference="银行方向关注度抬升。",
            ),
        ),
        warnings=(),
    )
    picks = [
        PickResult(
            symbol="300750",
            name="宁德时代",
            date="2026-06-30",
            close=220.0,
            score=70.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=220.0,
            stop_loss=210.0,
            take_profit=240.0,
            position="watch",
        ),
        PickResult(
            symbol="600036",
            name="招商银行",
            date="2026-06-30",
            close=45.0,
            score=66.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=45.0,
            stop_loss=43.0,
            take_profit=49.0,
            position="watch",
        ),
    ]

    monkeypatch.setattr(
        cli_mod,
        "_build_runtime_catalyst_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should reuse preview report")
        ),
    )
    filtered = cli_mod._load_runtime_market_context_catalyst_report(
        preview_report=preview_report,
        preview_symbols=("300750", "600036", "688981"),
        picks=picks,
        task_id="intraday",
    )

    assert filtered is not None
    assert [event.symbol for event in filtered.events] == ["", "300750", "600036"]


def test_load_runtime_market_context_catalyst_report_refetches_when_preview_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    seen: dict[str, object] = {}

    def fake_build_runtime_catalyst_report(picks, *, task_id: str):
        seen["symbols"] = tuple(pick.symbol for pick in picks)
        seen["task_id"] = task_id
        return "rebuilt-report"

    monkeypatch.setattr(
        cli_mod,
        "_build_runtime_catalyst_report",
        fake_build_runtime_catalyst_report,
    )
    picks = [
        PickResult(
            symbol="300750",
            name="宁德时代",
            date="2026-06-30",
            close=220.0,
            score=70.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=220.0,
            stop_loss=210.0,
            take_profit=240.0,
            position="watch",
        ),
        PickResult(
            symbol="600036",
            name="招商银行",
            date="2026-06-30",
            close=45.0,
            score=66.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=45.0,
            stop_loss=43.0,
            take_profit=49.0,
            position="watch",
        ),
    ]

    report = cli_mod._load_runtime_market_context_catalyst_report(
        preview_report="preview-report",
        preview_symbols=("300750",),
        picks=picks,
        task_id="intraday",
    )

    assert report == "rebuilt-report"
    assert seen["symbols"] == ("300750", "600036")
    assert seen["task_id"] == "intraday"


def test_build_runtime_catalyst_report_enables_cache_for_intraday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    seen: dict[str, object] = {}

    def fake_build_catalyst_report(
        *, symbols=(), symbol_names=None, config=None, **_kwargs
    ):
        seen["symbols"] = symbols
        seen["symbol_names"] = symbol_names
        seen["cache_path"] = getattr(config, "cache_path", "")
        seen["cache_ttl_seconds"] = getattr(config, "cache_ttl_seconds", 0.0)
        seen["max_stale_cache_age_seconds"] = getattr(
            config,
            "max_stale_cache_age_seconds",
            0.0,
        )
        seen["max_news_age_days"] = getattr(config, "max_news_age_days", 0)
        seen["allow_stale_cache_on_failure"] = getattr(
            config, "allow_stale_cache_on_failure", False
        )
        seen["isolate_external_sources"] = getattr(
            config, "isolate_external_sources", False
        )
        return "ok"

    monkeypatch.setattr(
        "aqsp.news.catalysts.build_catalyst_report",
        fake_build_catalyst_report,
    )
    result = cli_mod._build_runtime_catalyst_report(
        [
            PickResult(
                symbol="300750",
                name="宁德时代",
                date="2026-06-30",
                close=220.0,
                score=70.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=220.0,
                stop_loss=210.0,
                take_profit=240.0,
                position="watch",
            )
        ],
        task_id="intraday",
    )

    assert result == "ok"
    assert seen["symbols"] == ("300750",)
    assert seen["symbol_names"] == {"300750": "宁德时代"}
    assert seen["cache_path"] == "data/runtime/catalyst_report_cache.json"
    assert seen["cache_ttl_seconds"] == 120.0
    assert seen["max_stale_cache_age_seconds"] == 30 * 60
    assert seen["max_news_age_days"] == 5
    assert seen["allow_stale_cache_on_failure"] is True
    assert seen["isolate_external_sources"] is True


def test_runtime_catalyst_thread_mode_is_explicit_and_high_frequency_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.delenv("AQSP_INTRADAY_CATALYST_FETCH_MODE", raising=False)
    assert cli_mod._runtime_catalyst_isolate_external_sources("intraday") is True

    monkeypatch.setenv("AQSP_INTRADAY_CATALYST_FETCH_MODE", "thread")
    assert cli_mod._runtime_catalyst_isolate_external_sources("intraday") is False
    assert cli_mod._runtime_catalyst_isolate_external_sources("midday") is False
    assert cli_mod._runtime_catalyst_isolate_external_sources("daily") is True


def test_market_context_preview_count_is_bounded_when_screen_limit_is_large() -> None:
    import aqsp.cli as cli_mod

    assert (
        cli_mod._market_context_preview_count(
            limit=10,
            total=20,
            task_id="intraday",
        )
        == 5
    )
    assert (
        cli_mod._market_context_preview_count(
            limit=2,
            total=20,
            task_id="midday",
        )
        == 3
    )
    assert (
        cli_mod._market_context_preview_count(
            limit=10,
            total=2,
            task_id="intraday",
        )
        == 2
    )
    assert (
        cli_mod._market_context_preview_count(limit=10, total=20, task_id="daily") == 20
    )


def test_apply_debate_results_to_picks_keeps_runtime_score_when_override_disabled(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.briefing.debate import DebateResult
    from aqsp.core.types import PickResult

    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  multi_agent_runtime_override:
    enabled: false
    purpose: no runtime override
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))
    picks = [
        PickResult(
            symbol="300750",
            name="宁德时代",
            date="2026-06-30",
            close=220.0,
            score=70.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=220.0,
            stop_loss=210.0,
            take_profit=240.0,
            position="watch",
        )
    ]
    debate_results = [
        DebateResult(
            debate_id="d1",
            symbol="300750",
            name="宁德时代",
            original_score=70.0,
            adjusted_score=76.0,
            rating="watch",
            final_consensus="倾向优先纸面复核",
            recommended_adjustment="raise",
            research_verdict="倾向优先纸面复核",
            support_points=("量价共振仍在延续。",),
        )
    ]

    updated, rewritten = cli_mod._apply_debate_results_to_picks(picks, debate_results)

    assert rewritten == 0
    assert updated[0].score == 70.0
    assert updated[0].recommended_adjustment == "keep"
    assert updated[0].adjusted_score == 0.0
    assert "debate_recommended_adjustment" not in updated[0].metrics
    assert "debate_adjusted_score" not in updated[0].metrics
    assert updated[0].metrics["debate_research_verdict"] == "倾向优先纸面复核"
    assert updated[0].metrics["support_points"] == ["量价共振仍在延续。"]


def test_apply_debate_results_to_picks_ignores_runtime_override_switch(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.briefing.debate import DebateResult
    from aqsp.core.types import PickResult

    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  multi_agent_runtime_override:
    enabled: true
            purpose: obsolete switch must not affect ordering
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))
    picks = [
        PickResult(
            symbol="300750",
            name="宁德时代",
            date="2026-06-30",
            close=220.0,
            score=70.0,
            rating="watch",
            entry_type="relative_strength",
            ideal_buy=220.0,
            stop_loss=210.0,
            take_profit=240.0,
            position="watch",
        )
    ]
    debate_results = [
        DebateResult(
            debate_id="d1",
            symbol="300750",
            name="宁德时代",
            original_score=70.0,
            adjusted_score=76.0,
            rating="watch",
            final_consensus="倾向优先纸面复核",
            recommended_adjustment="raise",
        )
    ]

    updated, rewritten = cli_mod._apply_debate_results_to_picks(picks, debate_results)

    assert rewritten == 0
    assert updated[0].recommended_adjustment == "keep"
    assert updated[0].adjusted_score == 0.0
    assert updated[0].debate_consensus == "倾向优先纸面复核"


def test_fetch_special_strategy_frames_keeps_daily_when_intraday_overlay_is_empty(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    frames = {
        "600000": _fresh_frame("2026-06-26"),
        "000300": _fresh_frame("2026-06-26"),
    }

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (frames, "eastmoney"),
    )

    class FakeIntradayService:
        def __init__(self, _source) -> None:
            pass

        def merge_intraday_bar_into_daily_with_coverage(self, *_args, **_kwargs):
            return SimpleNamespace(
                frames={},
                requested_symbols=("600000", "000300"),
                covered_symbols=(),
                missing_symbols=("600000", "000300"),
                complete=False,
            )

    monkeypatch.setattr(cli_mod, "IntradayService", FakeIntradayService)
    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: date(2026, 6, 26))

    result, actual_source = cli_mod._fetch_special_strategy_frames(
        "eastmoney",
        ["600000"],
        benchmark_symbol="000300",
    )

    assert set(result) == {"600000", "000300"}
    assert actual_source == "eastmoney"
    assert result["600000"].attrs["intraday_overlay_coverage"]["status"] == "partial"


def test_intraday_actual_source_uses_current_overlay_provenance() -> None:
    import aqsp.cli as cli_mod

    eastmoney = _fresh_frame("2026-06-26")
    eastmoney.attrs["source_name"] = "eastmoney"
    tencent = _fresh_frame("2026-06-26")
    tencent.attrs["source_name"] = "tencent"

    assert cli_mod._intraday_actual_source({"600000": eastmoney}, "sina") == "eastmoney"
    assert (
        cli_mod._intraday_actual_source(
            {"600000": eastmoney, "000001": tencent}, "sina"
        )
        == "multi"
    )
    assert cli_mod._intraday_actual_source({}, "sina") == "sina"


def test_fetch_special_strategy_frames_blocks_historical_only_live_short_source(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not fetch historical-only live_short source")
        ),
    )

    with pytest.raises(DataError, match="sqlite_db 不适合 live_short"):
        cli_mod._fetch_special_strategy_frames(
            "sqlite_db",
            ["600000"],
            benchmark_symbol="000300",
        )


def test_live_short_rejects_candidate_actual_source() -> None:
    import aqsp.cli as cli_mod

    allowed, reason = cli_mod._runtime_actual_source_workload_allowed(
        "auto",
        "akshare",
        workload="live_short",
    )

    assert allowed is False
    assert "仅可作为 observation 层" in reason


def test_force_intraday_observation_keeps_score_but_blocks_review() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    pick = PickResult(
        symbol="600000",
        name="测试标的",
        date="2026-06-26",
        close=10.0,
        score=88.0,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=10.0,
        stop_loss=9.4,
        take_profit=11.0,
        position="10%-30%",
        strategies=("volume_breakout",),
        reasons=("放量",),
        risks=(),
    )

    observed = cli_mod._force_intraday_observation(
        [pick],
        missing_symbols=("000300",),
    )

    assert observed[0].score == 88.0
    assert observed[0].rating == "buy_candidate"
    assert observed[0].metrics["observation_only"] is True
    assert observed[0].metrics["intraday_missing_symbols"] == ("000300",)
    assert observed[0].metrics["candidate_review_priority"] == "low"
    assert observed[0].metrics["portfolio_action"] == "observation_only"


def test_force_intraday_observation_only_downgrades_missing_candidate() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    picks = [
        PickResult(
            symbol=symbol,
            name=symbol,
            date="2026-06-26",
            close=10.0,
            score=80.0,
            rating="buy_candidate",
            entry_type="next_open",
            ideal_buy=10.0,
            stop_loss=9.4,
            take_profit=11.0,
            position="10%-30%",
        )
        for symbol in ("600000", "600001")
    ]

    observed = cli_mod._force_intraday_observation(
        picks,
        missing_symbols=("600001",),
    )

    assert observed[0].metrics.get("observation_only", False) is False
    assert observed[1].metrics["observation_only"] is True


def test_cross_market_context_assigns_display_priority_without_changing_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-10",
        close=180.0,
        score=73.5,
        rating="watch",
        entry_type="next_open",
        ideal_buy=180.0,
        stop_loss=170.0,
        take_profit=198.0,
        position="watch",
    )
    context = SimpleNamespace()
    # Isolate the provider so this test covers only the post-screening contract.
    monkeypatch.setattr(
        "aqsp.market_context.market_context_metrics_for_pick",
        lambda _pick, _context: {
            "news_catalyst_judgement": "supports",
            "news_catalyst_priority_score": 3,
            "news_catalyst_support_count": 1,
            "cross_market_action": "优先复核",
            "cross_market_priority_score": 3,
            "cross_market_rule_ids": ("physical_ai",),
        },
    )
    enriched = cli_mod._annotate_cross_market_context([pick], market_context=context)[0]

    assert enriched.score == 73.5
    assert enriched.metrics["candidate_review_priority"] == "优先复核"
    assert "正向消息" in enriched.metrics["candidate_review_priority_reason"]


def test_cross_market_context_maps_shared_rule_to_current_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    picks = [
        PickResult(
            symbol="300750",
            name="宁德时代",
            date="2026-07-10",
            close=180.0,
            score=73.5,
            rating="watch",
            entry_type="next_open",
            ideal_buy=180.0,
            stop_loss=170.0,
            take_profit=198.0,
            position="watch",
        ),
        PickResult(
            symbol="688981",
            name="中芯国际",
            date="2026-07-10",
            close=80.0,
            score=71.0,
            rating="watch",
            entry_type="next_open",
            ideal_buy=80.0,
            stop_loss=75.0,
            take_profit=88.0,
            position="watch",
        ),
    ]

    monkeypatch.setattr(
        "aqsp.market_context.market_context_metrics_for_pick",
        lambda _pick, _context: {
            "cross_market_rule_ids": ("physical_ai",),
            "cross_market_action": "优先复核",
            "cross_market_priority_score": 3,
        },
    )
    enriched = cli_mod._annotate_cross_market_context(
        picks, market_context=SimpleNamespace()
    )

    assert enriched[0].score == picks[0].score
    assert enriched[0].metrics["cross_market_candidate_symbols"] == ("688981",)
    assert enriched[1].metrics["cross_market_candidate_symbols"] == ("300750",)


def test_cross_market_watch_candidate_stays_observation_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    formal = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-10",
        close=180.0,
        score=73.5,
        rating="watch",
        entry_type="next_open",
        ideal_buy=180.0,
        stop_loss=170.0,
        take_profit=198.0,
        position="watch",
    )
    screened = PickResult(
        symbol="688981",
        name="中芯国际",
        date="2026-07-10",
        close=80.0,
        score=41.0,
        rating="watch",
        entry_type="next_open",
        ideal_buy=80.0,
        stop_loss=75.0,
        take_profit=88.0,
        position="watch",
    )
    monkeypatch.setattr(
        "aqsp.market_context.market_context_metrics_for_pick",
        lambda _pick, _context: {
            "cross_market_rule_ids": ("physical_ai",),
            "cross_market_priority_score": 2,
            "cross_market_validation_signals": ("板块同步放量",),
        },
    )

    result = cli_mod._append_cross_market_watch_candidates(
        [formal],
        [formal, screened],
        market_context=SimpleNamespace(),
        max_candidates=0,
    )

    assert [item.symbol for item in result] == ["300750", "688981"]
    assert result[1].score == 41.0
    assert result[1].metrics["observation_only"] is True
    assert result[1].metrics["portfolio_action"] == "observation_only"


def test_news_watch_candidate_limit_zero_means_uncapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_NEWS_WATCH_MAX_CANDIDATES", "0")
    assert cli_mod._news_watch_candidate_limit() == 0


def test_cross_market_context_marks_negative_evidence_as_risk_review() -> None:
    import aqsp.cli as cli_mod

    metrics = {
        "news_catalyst_judgement": "opposes",
        "news_catalyst_oppose_count": 1,
        "cross_market_action": "风险复核",
    }
    assert cli_mod._market_context_review_priority(metrics) == (
        "风险复核",
        "存在负向或冲突证据，先做风险复核",
    )


def test_snapshot_round_trip_keeps_candidate_review_priority(tmp_path: Path) -> None:
    from aqsp.core.types import PickResult
    from aqsp.portfolio.snapshot import load_snapshot, save_snapshot

    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-10",
        close=180.0,
        score=73.5,
        rating="watch",
        entry_type="next_open",
        ideal_buy=180.0,
        stop_loss=170.0,
        take_profit=198.0,
        position="watch",
        metrics={"candidate_review_priority": "优先复核"},
    )
    path = tmp_path / "snapshots.jsonl"
    save_snapshot([pick], snapshot_path=str(path), date="2026-07-10")

    loaded = load_snapshot("2026-07-10", snapshot_path=str(path))
    assert loaded is not None
    assert loaded[0].candidate_review_priority == "优先复核"


def test_relevant_intraday_missing_symbols_ignores_unrelated_pool_gaps() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    pick = PickResult(
        symbol="688981",
        name="中芯国际",
        date="2026-07-14",
        close=100.0,
        score=80.0,
        rating="buy_candidate",
        entry_type="intraday",
        ideal_buy=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position="medium",
    )

    assert cli_mod._relevant_intraday_missing_symbols(
        [pick],
        missing_symbols=("000004", "688981", "000300"),
        benchmark_symbol="000300",
    ) == ("688981", "000300")


def test_fetch_special_strategy_frames_blocks_history_actual_source_after_fallback(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (
            {"600000": _fresh_frame("2026-06-26")},
            "tdx_vipdoc",
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "_get_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not build intraday service for historical fallback")
        ),
    )

    with pytest.raises(DataError, match="请求源 auto 实际落到 tdx_vipdoc"):
        cli_mod._fetch_special_strategy_frames(
            "auto",
            ["600000"],
            benchmark_symbol="",
        )


def test_special_strategy_runtime_ready_requires_enabled_and_regime(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    class FakeThresholdConfig:
        enabled = True

    class FakeStrategy:
        thresholds = object()
        cfg = FakeThresholdConfig()
        regime_required = ("stable_bull",)

    monkeypatch.setattr(
        cli_mod,
        "_detect_runtime_regime",
        lambda *_args, **_kwargs: "stable_bear",
    )

    allowed, regime, reason = cli_mod._special_strategy_runtime_ready(
        strategy=FakeStrategy(),
        frames={"000300": _fresh_frame("2026-06-26")},
        benchmark_symbol="000300",
    )

    assert allowed is False
    assert regime == "stable_bear"
    assert "市场状态不匹配" in reason


def test_special_strategy_runtime_ready_blocks_disabled_threshold(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    class FakeThresholdConfig:
        enabled = False

    class FakeStrategy:
        thresholds = object()
        mb = FakeThresholdConfig()
        regime_required = ()

    monkeypatch.setattr(
        cli_mod,
        "_detect_runtime_regime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not detect regime")
        ),
    )

    allowed, regime, reason = cli_mod._special_strategy_runtime_ready(
        strategy=FakeStrategy(),
        frames={"000300": _fresh_frame("2026-06-26")},
        benchmark_symbol="000300",
    )

    assert allowed is False
    assert regime == ""
    assert reason == "策略已禁用"


def test_run_evolve_uses_full_runtime_universe_when_auto_resolving_symbols(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        seen["max_universe"] = kwargs["max_universe"]
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        max_universe=0,
        apply=False,
        output="",
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert seen["pool_name"] == ""
    assert seen["symbols"] == ""
    assert seen["max_universe"] == 0
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_scheduled_intraday_blocks_historical_only_source_before_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not resolve symbols for blocked source")
        ),
    )

    args = Namespace(
        mode="open",
        symbols="",
        source="sqlite_db",
        pool="all",
        limit=10,
        max_universe=0,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        enable_online_factors=False,
        csv="",
        benchmark_symbol="000300",
        ledger="data/intraday_predictions.jsonl",
        report="",
        output_csv="",
        skip_validation=True,
        notify=False,
        fee_bps=None,
        slippage_bps=None,
        skip_pit_financials=False,
    )

    assert cli_mod._run_scheduled_legacy(args) == 1
    assert "sqlite_db 不适合 live_short" in capsys.readouterr().out


def test_run_scheduled_intraday_merges_today_intraday_before_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    merged_frames = {"600519": _fresh_frame("2026-07-09")}
    seen: dict[str, object] = {}

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (_ for _ in ()).throw(
            AssertionError("intraday run must use merged intraday frames")
        ),
    )

    def fake_fetch_special(source, symbols, **kwargs):
        seen["source"] = source
        seen["symbols"] = symbols
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        return merged_frames, "eastmoney"

    def fake_assert_fresh_data(frames, max_data_lag_days, **kwargs):
        seen["frames"] = frames
        seen["max_data_lag_days"] = max_data_lag_days
        seen["workload"] = kwargs.get("workload")
        raise RuntimeError("stop after merged intraday freshness")

    monkeypatch.setattr(cli_mod, "_fetch_special_strategy_frames", fake_fetch_special)
    monkeypatch.setattr(cli_mod, "assert_fresh_data", fake_assert_fresh_data)

    args = Namespace(
        mode="open",
        symbols="",
        source="eastmoney",
        pool="all",
        limit=10,
        max_universe=0,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        enable_online_factors=False,
        csv="",
        benchmark_symbol="000300",
        ledger="data/intraday_predictions.jsonl",
        report="",
        output_csv="",
        skip_validation=True,
        notify=False,
        fee_bps=None,
        slippage_bps=None,
        skip_pit_financials=False,
        as_of="",
    )

    with pytest.raises(RuntimeError, match="merged intraday freshness"):
        cli_mod._run_scheduled_legacy(args)

    assert seen["source"] == "eastmoney"
    assert seen["symbols"] == ["600519"]
    assert seen["benchmark_symbol"] == "000300"
    assert seen["frames"] is merged_frames
    assert seen["max_data_lag_days"] == 1
    assert seen["workload"] == "live_short"


def test_run_scheduled_live_short_task_requires_intraday_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    merged_frames = {"600519": _fresh_frame("2026-07-09")}
    seen: dict[str, object] = {}

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "live_short")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (_ for _ in ()).throw(
            AssertionError("live_short task must use intraday overlay")
        ),
    )

    def fake_fetch_special(source, symbols, **kwargs):
        seen["source"] = source
        seen["symbols"] = symbols
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        return merged_frames, "eastmoney"

    def fake_assert_fresh_data(frames, max_data_lag_days, **kwargs):
        seen["frames"] = frames
        seen["workload"] = kwargs.get("workload")
        raise RuntimeError("stop after live_short overlay")

    monkeypatch.setattr(cli_mod, "_fetch_special_strategy_frames", fake_fetch_special)
    monkeypatch.setattr(cli_mod, "assert_fresh_data", fake_assert_fresh_data)

    args = Namespace(
        mode="open",
        symbols="600519",
        source="eastmoney",
        pool="",
        limit=10,
        max_universe=0,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        enable_online_factors=False,
        csv="",
        benchmark_symbol="000300",
        ledger="data/intraday_predictions.jsonl",
        report="",
        output_csv="",
        skip_validation=True,
        notify=False,
        fee_bps=None,
        slippage_bps=None,
        skip_pit_financials=False,
        as_of="",
        enable_debate=False,
    )

    with pytest.raises(RuntimeError, match="stop after live_short overlay"):
        cli_mod._run_scheduled_legacy(args)

    assert seen["source"] == "eastmoney"
    assert seen["symbols"] == ["600519"]
    assert seen["benchmark_symbol"] == "000300"
    assert seen["frames"] is merged_frames
    assert seen["workload"] == "live_short"


def test_run_scheduled_daily_blocks_history_only_source_without_as_of(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.delenv("AQSP_RUN_TASK_ID", raising=False)
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not resolve symbols for blocked source")
        ),
    )

    args = Namespace(
        mode="close",
        symbols="",
        csv="",
        source="sqlite_db",
        pool="all",
        limit=10,
        max_universe=0,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        enable_online_factors=False,
        benchmark_symbol="000300",
        ledger="data/predictions.jsonl",
        report="",
        output_csv="",
        skip_validation=True,
        notify=False,
        fee_bps=None,
        slippage_bps=None,
        skip_pit_financials=False,
        as_of="",
    )

    assert cli_mod._run_scheduled_legacy(args) == 1
    assert "sqlite_db 不适合 live_short" in capsys.readouterr().out


def test_run_scheduled_daily_blocks_history_actual_source_after_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.delenv("AQSP_RUN_TASK_ID", raising=False)
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (
            {"600519": _fresh_frame("2026-06-26")},
            "tdx_vipdoc",
        ),
    )

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        pool="all",
        limit=10,
        max_universe=0,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        enable_online_factors=False,
        benchmark_symbol="",
        ledger="data/predictions.jsonl",
        report="",
        output_csv="",
        skip_validation=True,
        notify=False,
        fee_bps=None,
        slippage_bps=None,
        skip_pit_financials=False,
        as_of="",
    )

    assert cli_mod._run_scheduled_legacy(args) == 1
    assert "请求源 auto 实际落到 tdx_vipdoc" in capsys.readouterr().out


def test_run_scheduled_keeps_learning_weights_proposal_only() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)

    assert "strategy_weights_from_ledger(args.ledger)" not in source
    assert "learner.compute_weights(ledger_df)" in source
    assert "_runtime_strategy_weights(thresholds, regime)" in source
    assert "未应用到本次筛选" in source


def test_run_screen_uses_freshness_guard_for_non_csv_sources() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod.run_screen)

    assert "assert_fresh_data(" in source
    assert "latest_trade_date(frames)" in source
    assert "if args.csv" in source
    assert "_runtime_strategy_weights(thresholds, regime)" in source


def test_run_screen_blocks_history_only_source_before_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not fetch frames for blocked source")
        ),
    )

    args = Namespace(
        csv="",
        source="sqlite_db",
        symbols="600519",
        benchmark_symbol="000300",
        pool="",
        min_avg_amount=50_000_000,
        mode="close",
        limit=1,
        max_data_lag_days=0,
        report="",
        output_csv="",
        enable_online_factors=False,
    )

    assert cli_mod.run_screen(args) == 1
    assert "sqlite_db 不适合 live_short" in capsys.readouterr().out


def test_run_screen_rejects_csv_as_live_short_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "load_csv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("historical CSV must be rejected before loading")
        ),
    )
    args = Namespace(
        csv="history.csv",
        source="auto",
        symbols="",
        benchmark_symbol="000300",
        pool="",
        min_avg_amount=50_000_000,
        mode="close",
        limit=1,
        max_data_lag_days=0,
        report="",
        output_csv="",
        enable_online_factors=False,
    )

    assert cli_mod.run_screen(args) == 1
    assert "不能形成 live_short 候选" in capsys.readouterr().out


def test_scheduled_rejects_csv_even_with_as_of(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: True)
    args = Namespace(
        csv="history.csv",
        source="auto",
        symbols="",
        benchmark_symbol="000300",
        pool="",
        mode="close",
        limit=1,
        max_universe=0,
        min_avg_amount=50_000_000,
        max_data_lag_days=0,
        enable_online_factors=False,
        as_of="2026-06-22",
        skip_validation=True,
        ledger="data/predictions.jsonl",
        report="",
        output_csv="",
        notify=False,
    )

    assert cli_mod._run_scheduled_legacy(args) == 1
    assert "scheduled --csv" in capsys.readouterr().out


def test_run_screen_blocks_history_actual_source_after_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_args, **_kwargs: (
            {"600519": _fresh_frame("2026-06-26")},
            "tdx_vipdoc",
        ),
    )

    args = Namespace(
        csv="",
        source="auto",
        symbols="600519",
        benchmark_symbol="",
        pool="",
        min_avg_amount=50_000_000,
        mode="close",
        limit=1,
        max_data_lag_days=0,
        report="",
        output_csv="",
        enable_online_factors=False,
    )

    assert cli_mod.run_screen(args) == 1
    assert "请求源 auto 实际落到 tdx_vipdoc" in capsys.readouterr().out


def test_run_scheduled_runtime_weights_exclude_learner_proposals() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)
    proposal_at = source.index("weight_proposals = learner.compute_weights(ledger_df)")
    runtime_weight_at = source.index(
        "weights = _runtime_strategy_weights(thresholds, regime)"
    )
    snapshot_attach_at = source.index("_attach_runtime_weight_snapshot(")
    snapshot_at = source.index("strategy_weights=weights", snapshot_attach_at)

    assert proposal_at < runtime_weight_at < snapshot_attach_at < snapshot_at
    between = source[proposal_at:runtime_weight_at]
    assert "weights.update(weight_proposals)" not in between
    assert "weight_proposals[" not in source[runtime_weight_at:snapshot_at]


def test_run_scheduled_executability_feedback_does_not_change_runtime_weights() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)
    runtime_weight_at = source.index(
        "weights = _runtime_strategy_weights(thresholds, regime)"
    )
    config_at = source.index("config = ScreeningConfig(")

    assert "strategy_executability_weight_adjustments" not in source
    assert "不可成交反馈降权:" not in source
    assert "weights[strategy_id]" not in source[runtime_weight_at:config_at]


def test_runtime_strategy_weights_are_resolved_from_strategy_mix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.regime.strategy_mixer import RuntimeStrategyMix
    from aqsp.strategies.thresholds import Thresholds

    expected = {"rps_momentum": 1.23, "n_rebound": 0.77}
    mix = RuntimeStrategyMix(
        regime="stable_bull",
        regime_label="稳定上涨",
        strategy_weights=tuple(expected.items()),
    )

    monkeypatch.setattr(
        cli_mod,
        "build_runtime_strategy_mix",
        lambda regime, thresholds: mix,
    )
    assert cli_mod._runtime_strategy_weights(Thresholds(), "stable_bull") == expected


def test_runtime_weight_snapshot_is_attached_without_changing_score() -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.strategies.thresholds import Thresholds

    pick = PickResult(
        symbol="600000",
        name="测试标的",
        date="2026-07-13",
        close=10.0,
        score=72.5,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        position="10%-30%",
    )
    updated = cli_mod._attach_runtime_weight_snapshot(
        [pick],
        thresholds=Thresholds(),
        regime="stable_bull",
        strategy_weights={"rps_momentum": 1.06},
        strategy_weight_reasons={},
    )[0]

    assert updated.score == 72.5
    assert updated.metrics["strategy_weight_snapshot"] == {
        "source": "runtime_strategy_mix",
        "regime": "stable_bull",
        "strategy_weights": {"rps_momentum": 1.06},
        "strategy_weight_reasons": {},
        "base_blend_weight": 0.7,
        "regime_blend_weight": 0.3,
        "thresholds_version": Thresholds().version,
    }


def test_formal_runtime_ledger_path_uses_formal_ledger_for_intraday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setenv("AQSP_LEDGER", "data/predictions.jsonl")

    assert (
        cli_mod._formal_runtime_ledger_path(
            "data/intraday_predictions.jsonl",
            task_id="intraday",
        )
        == "data/predictions.jsonl"
    )
    assert (
        cli_mod._formal_runtime_ledger_path(
            "data/midday_predictions.jsonl",
            task_id="midday",
        )
        == "data/predictions.jsonl"
    )
    assert (
        cli_mod._formal_runtime_ledger_path(
            "data/predictions.jsonl",
            task_id="daily",
        )
        == "data/predictions.jsonl"
    )


def test_should_build_market_context_allows_intraday_when_live_short_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  live_short_runtime:
    enabled: true
    purpose: allow realtime intraday context
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))

    assert cli_mod._should_build_market_context("intraday") is True
    assert cli_mod._should_build_market_context("midday") is True


def test_should_build_market_context_blocks_intraday_when_live_short_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  live_short_runtime:
    enabled: false
    purpose: disable realtime intraday context
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))

    assert cli_mod._should_build_market_context("intraday") is False
    assert cli_mod._should_build_market_context("daily") is True


def test_market_context_source_timeout_seconds_shrinks_for_intraday() -> None:
    import aqsp.cli as cli_mod

    assert cli_mod._market_context_source_timeout_seconds("intraday") == 1.0
    assert cli_mod._market_context_source_timeout_seconds("daily") == 4.0


def test_runtime_catalyst_max_news_age_days_shrinks_for_intraday() -> None:
    import aqsp.cli as cli_mod

    assert cli_mod._runtime_catalyst_max_news_age_days("intraday") == 5
    assert cli_mod._runtime_catalyst_max_news_age_days("daily") == 30


def test_run_scheduled_routes_market_context_through_shared_task_gate() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)

    assert (
        "if screened_picks and _should_build_market_context(normalized_task_id):"
        in source
    )
    assert "if _should_build_market_context(normalized_task_id):" in source


def test_run_scheduled_skips_runtime_chain_on_non_trading_day(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.setattr(cli_mod, "today_shanghai", lambda: datetime(2026, 6, 19).date())
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _day: False)
    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-trading day must not resolve universe")
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-trading day must not write ledger")
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("non-trading day must not notify")
        ),
    )

    args = Namespace(
        mode="close",
        symbols="",
        csv="",
        source="auto",
        limit=5,
        max_universe=0,
        min_avg_amount=10_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=3.0,
        slippage_bps=20.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=True,
        enable_debate=False,
        pool="",
    )

    assert cli_mod.run_scheduled(args) == 0


def test_run_scheduled_dispatches_through_service_boundary(monkeypatch) -> None:
    import aqsp.cli as cli_mod
    import aqsp.services.scheduled as scheduled_service

    seen: dict[str, object] = {}

    def fake_service(args, *, legacy_runner):
        seen["args"] = args
        seen["legacy_runner"] = legacy_runner
        return 7

    monkeypatch.setattr(scheduled_service, "run_scheduled_service", fake_service)
    args = Namespace()

    assert cli_mod.run_scheduled(args) == 7
    assert seen["args"] is args
    assert seen["legacy_runner"] is cli_mod._run_scheduled_legacy


def test_run_scheduled_validates_ledger_before_circuit_breaker_pnl() -> None:
    import aqsp.cli as cli_mod

    source = inspect.getsource(cli_mod._run_scheduled_legacy)

    assert source.index(
        "validate_predictions(formal_ledger_path, frames)"
    ) < source.index("_compute_real_pnl(")


def test_disabled_circuit_breaker_status_is_not_triggered() -> None:
    from aqsp.cli import _disabled_circuit_breaker_status

    status = _disabled_circuit_breaker_status(
        daily_pnl_pct=-99.0,
        weekly_pnl_pct=-99.0,
        monthly_pnl_pct=-99.0,
    )

    assert status.triggered is False
    assert status.level == "disabled"
    assert "AQSP_DISABLE_CIRCUIT_BREAKER" in status.reason


def test_intraday_circuit_breaker_disable_requires_explicit_intraday_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    monkeypatch.delenv("AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER", raising=False)
    monkeypatch.setenv("AQSP_DISABLE_CIRCUIT_BREAKER", "true")
    assert cli_mod._circuit_breaker_disabled(task_id="intraday") is False

    monkeypatch.setenv("AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER", "true")
    assert cli_mod._circuit_breaker_disabled(task_id="intraday") is True
    status = cli_mod._disabled_circuit_breaker_status(
        daily_pnl_pct=-1.0,
        weekly_pnl_pct=-2.0,
        monthly_pnl_pct=-3.0,
        switch_name=cli_mod._circuit_breaker_disable_switch_name("intraday"),
    )
    assert "AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER" in status.reason


def test_circuit_breaker_only_restricts_paper_actions_not_research() -> None:
    from aqsp.cli import _allow_observation_during_circuit_breaker

    assert _allow_observation_during_circuit_breaker("daily") is True
    assert _allow_observation_during_circuit_breaker("intraday") is True


def test_run_scheduled_as_of_controls_universe_and_fetch_end_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(_source, _symbols, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return ["600519"]

    def fake_fetch_frames_for_cli_with_metadata(_source, _symbols, **kwargs):
        seen["end_date"] = kwargs["end_date"]
        raise RuntimeError("stop after date wiring")

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        fake_fetch_frames_for_cli_with_metadata,
    )

    args = Namespace(
        mode="close",
        symbols="",
        csv="",
        source="sqlite_db",
        limit=5,
        max_universe=3000,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger="data/predictions.jsonl",
        horizon_days=3,
        fee_bps=None,
        slippage_bps=None,
        benchmark_symbol="",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
        as_of="2026-07-06",
    )

    with pytest.raises(RuntimeError, match="stop after date wiring"):
        cli_mod._run_scheduled_legacy(args)

    assert seen == {
        "as_of": date(2026, 7, 6),
        "end_date": date(2026, 7, 6),
    }


def test_run_scheduled_live_short_caps_env_lag_before_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}
    frames = {"600519": _fresh_frame("2026-07-08")}

    monkeypatch.delenv("AQSP_RUN_TASK_ID", raising=False)
    monkeypatch.setenv("AQSP_MAX_DATA_LAG_DAYS", "3")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "sina"),
    )

    def fake_assert_fresh_data(_frames, max_data_lag_days, **kwargs):
        seen["max_data_lag_days"] = max_data_lag_days
        seen["workload"] = kwargs.get("workload")
        raise RuntimeError("stop after freshness")

    monkeypatch.setattr(cli_mod, "assert_fresh_data", fake_assert_fresh_data)

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=5,
        max_universe=3000,
        min_avg_amount=50_000_000,
        max_data_lag_days=0,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger="data/predictions.jsonl",
        horizon_days=3,
        fee_bps=None,
        slippage_bps=None,
        benchmark_symbol="",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
        as_of="",
    )

    with pytest.raises(RuntimeError, match="stop after freshness"):
        cli_mod._run_scheduled_legacy(args)

    assert seen["max_data_lag_days"] == 1
    assert seen["workload"] == "live_short"


def test_run_scheduled_as_of_keeps_configured_lag_for_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, int] = {}
    frames = {"600519": _fresh_frame("2026-07-06")}

    monkeypatch.setenv("AQSP_MAX_DATA_LAG_DAYS", "3")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "sqlite_db"),
    )

    def fake_assert_fresh_data(_frames, max_data_lag_days, **kwargs):
        seen["max_data_lag_days"] = max_data_lag_days
        seen["workload"] = kwargs.get("workload")
        raise RuntimeError("stop after freshness")

    monkeypatch.setattr(cli_mod, "assert_fresh_data", fake_assert_fresh_data)

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="sqlite_db",
        limit=5,
        max_universe=3000,
        min_avg_amount=50_000_000,
        max_data_lag_days=0,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger="data/predictions.jsonl",
        horizon_days=3,
        fee_bps=None,
        slippage_bps=None,
        benchmark_symbol="",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
        as_of="2026-07-06",
    )

    with pytest.raises(RuntimeError, match="stop after freshness"):
        cli_mod._run_scheduled_legacy(args)

    assert seen["max_data_lag_days"] == 3
    assert seen["workload"] is None


def test_run_screen_live_short_caps_env_lag_before_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}
    frames = {"600519": _fresh_frame("2026-07-08")}

    monkeypatch.setenv("AQSP_MAX_DATA_LAG_DAYS", "3")
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "sina"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *_, **__: (frames, "sina"),
    )

    def fake_assert_fresh_data(_frames, max_data_lag_days, **kwargs):
        seen["max_data_lag_days"] = max_data_lag_days
        seen["workload"] = kwargs.get("workload")
        raise RuntimeError("stop after freshness")

    monkeypatch.setattr(cli_mod, "assert_fresh_data", fake_assert_fresh_data)

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=5,
        min_avg_amount=50_000_000,
        max_data_lag_days=0,
        report="",
        output_csv="",
        benchmark_symbol="",
        pool="",
    )

    with pytest.raises(RuntimeError, match="stop after freshness"):
        cli_mod.run_screen(args)

    assert seen["max_data_lag_days"] == 1
    assert seen["workload"] == "live_short"


def test_run_screen_open_live_short_requires_intraday_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aqsp.cli as cli_mod

    merged_frames = {"600519": _fresh_frame("2026-07-09")}
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (_ for _ in ()).throw(
            AssertionError("open live_short screen must use intraday overlay")
        ),
    )

    def fake_fetch_special(source, symbols, **kwargs):
        seen["source"] = source
        seen["symbols"] = symbols
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        return merged_frames, "sina"

    def fake_assert_fresh_data(frames, max_data_lag_days, **kwargs):
        seen["frames"] = frames
        seen["workload"] = kwargs.get("workload")
        raise RuntimeError("stop after screen overlay")

    monkeypatch.setattr(cli_mod, "_fetch_special_strategy_frames", fake_fetch_special)
    monkeypatch.setattr(cli_mod, "assert_fresh_data", fake_assert_fresh_data)

    args = Namespace(
        mode="open",
        symbols="600519",
        csv="",
        source="auto",
        limit=5,
        min_avg_amount=50_000_000,
        max_data_lag_days=1,
        report="",
        output_csv="",
        benchmark_symbol="000300",
        pool="",
    )

    with pytest.raises(RuntimeError, match="stop after screen overlay"):
        cli_mod.run_screen(args)

    assert seen["source"] == "auto"
    assert seen["symbols"] == ["600519"]
    assert seen["benchmark_symbol"] == "000300"
    assert seen["frames"] is merged_frames
    assert seen["workload"] == "live_short"


def test_run_screen_injects_threshold_screening_config(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult
    from aqsp.strategies.thresholds import (
        RiskThresholds,
        ScoringThresholds,
        Thresholds,
    )

    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": "2026-06-22",
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
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *_args, **_kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *_args, **_kwargs: ["600519"]
    )
    monkeypatch.setattr(cli_mod, "latest_trade_date", lambda *_args: "2026-06-22")
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: date(2026, 6, 22),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(
        cli_mod,
        "_detect_runtime_regime",
        lambda *_args, **_kwargs: "stable_bull",
    )
    monkeypatch.setattr(
        cli_mod,
        "_runtime_regime_market_context_lines",
        lambda *_args, **_kwargs: ("运行判定: HMM stable_bull",),
    )
    monkeypatch.setattr(
        cli_mod,
        "load_thresholds",
        lambda: Thresholds(
            scoring=ScoringThresholds(max_bias20=9.0),
            risk=RiskThresholds(soft_stop_loss_pct=0.07, max_position_pct=0.12),
        ),
    )

    def fake_screen_universe(_frames, config):
        captured["config"] = config
        return [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date="2026-06-22",
                close=1505.0,
                score=60.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-12%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=(),
            )
        ]

    monkeypatch.setattr(cli_mod, "screen_universe", fake_screen_universe)
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "to_dataframe", lambda picks: pd.DataFrame())

    report_path = tmp_path / "screen.md"
    args = Namespace(
        csv="",
        source="auto",
        symbols="600519",
        benchmark_symbol="000300",
        pool="",
        min_avg_amount=50_000_000,
        mode="close",
        limit=1,
        max_data_lag_days=999,
        report=str(report_path),
        output_csv="",
        enable_online_factors=False,
    )

    assert cli_mod.run_screen(args) == 0
    config = captured["config"]
    assert config.max_bias20 == 9.0
    assert config.stop_loss_buffer == 0.07
    assert config.max_position_pct == 0.12
    report = report_path.read_text(encoding="utf-8")
    assert "- 市场标签: stable_bull" in report
    assert "- 运行判定: HMM stable_bull" in report
    assert "- 市场标签: unknown" not in report


def test_run_scheduled_composite_rescore_updates_frozen_pick_results(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    latest = "2026-06-15"
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
        "000001": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000001",
                    "name": "平安银行",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000,
                    "amount": 10100000.0,
                    "suspended": False,
                    "limit_up": 11.11,
                    "limit_down": 9.09,
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
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519", "000001"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "stable_bull"
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: type(
            "R",
            (),
            {"matrix": {}, "high_corr_pairs": ()},
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.format_snapshot_diff", lambda *_args, **_kwargs: ""
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())

    base_picks = [
        PickResult(
            symbol="600519",
            name="贵州茅台",
            date=latest,
            close=1505.0,
            score=40.0,
            rating="watch",
            entry_type="next_open",
            ideal_buy=1505.0,
            stop_loss=1450.0,
            take_profit=1600.0,
            position="watch",
            strategies=("ma_pullback",),
            reasons=("趋势回踩",),
            risks=(),
        ),
        PickResult(
            symbol="000001",
            name="平安银行",
            date=latest,
            close=10.1,
            score=80.0,
            rating="buy_candidate",
            entry_type="next_open",
            ideal_buy=10.1,
            stop_loss=9.7,
            take_profit=11.0,
            position="10%-30%",
            strategies=("ma_pullback",),
            reasons=("趋势回踩",),
            risks=(),
        ),
    ]
    monkeypatch.setattr(
        cli_mod, "screen_universe", lambda *_args, **_kwargs: list(base_picks)
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )

    class FakeCompositeStrategy:
        def __init__(self, thresholds=None):
            self.thresholds = thresholds

        def calculate_score(self, data, regime="unknown"):
            return {"600519": 0.9, "000001": 0.3}

    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy", FakeCompositeStrategy
    )

    captured: dict[str, list[PickResult]] = {}

    def fake_to_dataframe(picks):
        captured["picks"] = list(picks)
        return pd.DataFrame(
            [
                {
                    "symbol": pick.symbol,
                    "score": pick.score,
                    "regime_score": pick.regime_score,
                }
                for pick in picks
            ]
        )

    monkeypatch.setattr(cli_mod, "to_dataframe", fake_to_dataframe)
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")

    args = Namespace(
        mode="close",
        symbols="600519,000001",
        csv="",
        source="auto",
        limit=2,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report="",
        output_csv="",
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    runtime_picks = captured["picks"]
    by_symbol = {pick.symbol: pick for pick in runtime_picks}
    assert by_symbol["600519"].regime_score == 0.0
    assert by_symbol["600519"].score == 40.0
    assert by_symbol["600519"].rating == "watch"
    assert by_symbol["600519"].position == "watch"
    assert by_symbol["000001"].regime_score == 0.0
    assert by_symbol["000001"].score == 80.0
    assert by_symbol["000001"].rating == "buy_candidate"


def test_run_scheduled_skips_formal_ledger_writes_when_circuit_breaker_triggers(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod

    latest = "2026-06-15"
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

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *_, **__: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    order: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda *_args, **_kwargs: order.append("validate"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_compute_real_pnl",
        lambda *_args, **_kwargs: order.append("pnl") or (-4.0, 0.0, 0.0),
    )
    # Portfolio protection must not block fresh research generation.
    monkeypatch.setattr(cli_mod, "_count_independent_signal_days", lambda *_, **__: 0)
    monkeypatch.setattr(cli_mod, "_detect_runtime_regime", lambda *_, **__: "")
    monkeypatch.setattr("aqsp.data.anomaly.detect_anomalies", lambda *_, **__: [])
    monkeypatch.setattr("aqsp.data.freshness.check_freshness", lambda *_, **__: [])
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_, **__: type("R", (), {"matrix": {}, "high_corr_pairs": ()})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: type(
            "C", (), {"calculate_score": lambda self, *_args, **_kwargs: {}}
        )(),
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(cli_mod, "to_dataframe", lambda picks: pd.DataFrame())
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda *_args, **_kwargs: None,
    )

    class TriggeredBreaker:
        def check(self, **_kwargs):
            return type(
                "Status", (), {"triggered": True, "reason": "单日组合亏损触发"}
            )()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: TriggeredBreaker())

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
        report="",
        output_csv="",
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=False,
        notify=False,
    )

    assert cli_mod.run_scheduled(args) == 0
    assert order == ["validate", "pnl"]


def test_run_scheduled_intraday_keeps_observation_output_during_circuit_breaker(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    latest = "2026-06-15"
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
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", lambda *_, **__: ["600519"])
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *_, **__: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_special_strategy_frames",
        lambda *_, **__: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_mod, "_compute_real_pnl", lambda *_args, **_kwargs: (-4.0, 0.0, 0.0)
    )
    monkeypatch.setattr(cli_mod, "_count_independent_signal_days", lambda *_, **__: 35)
    monkeypatch.setattr(cli_mod, "_detect_runtime_regime", lambda *_, **__: "")
    monkeypatch.setattr("aqsp.data.anomaly.detect_anomalies", lambda *_, **__: [])
    monkeypatch.setattr("aqsp.data.freshness.check_freshness", lambda *_, **__: [])
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_, **__: type("R", (), {"matrix": {}, "high_corr_pairs": ()})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_, **__: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: type(
            "C", (), {"calculate_score": lambda self, *_args, **_kwargs: {}}
        )(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_screen_universe_with_thresholds",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=72.0,
                rating="watch",
                entry_type="observe",
                ideal_buy=1498.0,
                stop_loss=1470.0,
                take_profit=1535.0,
                position="observe",
                strategies=("observation",),
                reasons=("观察候选",),
            )
        ],
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_mod,
        "to_dataframe",
        lambda picks: pd.DataFrame([{"symbol": p.symbol} for p in picks]),
    )
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    saved_snapshots: list[object] = []
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot",
        lambda picks, **_kwargs: saved_snapshots.append(picks),
    )
    appended_events: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "append_run_event",
        lambda *_args, **kwargs: appended_events.append(str(kwargs.get("status"))),
    )
    monkeypatch.setattr(
        cli_mod,
        "append_predictions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("formal ledger should stay disabled during circuit breaker")
        ),
    )

    class TriggeredBreaker:
        def check(self, **_kwargs):
            return type(
                "Status",
                (),
                {"triggered": True, "reason": "组合保护冷却期中，至 2026-07-01 解除"},
            )()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: TriggeredBreaker())

    args = Namespace(
        mode="open",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "intraday.md"),
        output_csv=str(tmp_path / "intraday.csv"),
        ledger=str(tmp_path / "intraday_predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=False,
        notify=False,
    )

    assert cli_mod.run_scheduled(args) == 0
    assert appended_events == ["blocked_by_circuit_breaker"]
    assert len(saved_snapshots) == 1
    report_text = (tmp_path / "intraday.md").read_text(encoding="utf-8")
    assert report_text.startswith("# report")
    assert "## 组合保护" in report_text


def test_run_scheduled_logs_learning_proposal_failure(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.core.types import PickResult

    latest = "2026-06-15"
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
    }

    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod,
        "assert_fresh_data",
        lambda *_args, **_kwargs: datetime.fromisoformat(
            "2026-06-15T15:00:00+08:00"
        ).date(),
    )
    monkeypatch.setattr(cli_mod, "_runtime_data_lag_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        cli_mod,
        "_source_runtime_metadata",
        lambda *_args, **_kwargs: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "ok", False),
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod, "_detect_runtime_regime", lambda *_args, **_kwargs: "stable_bull"
    )
    monkeypatch.setattr(
        cli_mod, "_compute_real_pnl", lambda *_args, **_kwargs: (0.0, 0.0, 0.0)
    )
    monkeypatch.setattr(
        "aqsp.ledger.base.read_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ledger boom")),
    )
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli_mod, "_log_run_decisions", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_mod, "_annotate_candidate_status", lambda picks, **_kwargs: picks
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "LethalFilterPipeline",
        lambda: type("P", (), {"run": lambda self, *_args, **_kwargs: (True, "")})(),
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_sector_concentration_with_runtime_hints",
        lambda *_args, **_kwargs: type("C", (), {"warnings": (), "sectors": {}})(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: type(
            "R", (), {"matrix": {}, "high_corr_pairs": ()}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.format_correlation", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.format_concentration", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.risk.dynamic_stop.compute_dynamic_stop",
        lambda *_args, **_kwargs: type(
            "S", (), {"recommended_stop": 0.0, "method": "none"}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.manager.apply_portfolio_manager",
        lambda picks, **_kwargs: type(
            "B", (), {"picks": picks, "decisions": (), "summary": None}
        )(),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.save_snapshot", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.compare_snapshots", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "aqsp.portfolio.snapshot.format_snapshot_diff", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        "aqsp.strategies.composite.CompositeStrategy",
        lambda thresholds=None: type(
            "C", (), {"calculate_score": lambda self, *_args, **_kwargs: {}}
        )(),
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
                score=60.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=(),
            )
        ],
    )
    monkeypatch.setattr(
        cli_mod, "_enrich_pick_names", lambda picks, *_args, **_kwargs: picks
    )
    monkeypatch.setattr(
        cli_mod, "to_dataframe", lambda picks: pd.DataFrame([{"symbol": "600519"}])
    )
    monkeypatch.setattr(cli_mod, "to_markdown", lambda *_args, **_kwargs: "# report")

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())

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
        report="",
        output_csv="",
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="",
        skip_validation=True,
        notify=False,
    )

    with caplog.at_level(logging.WARNING):
        exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    assert "学习权重提案计算失败，按无提案继续: ledger boom" in caplog.text


def test_run_mine_factors_uses_full_runtime_universe_when_auto_resolving_symbols(
    monkeypatch,
) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["pool_name"] = kwargs["pool_name"]
        seen["symbols"] = symbols
        return ["600519"]

    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeMiner:
        def __init__(self, min_ic: float, min_ir: float):
            self.min_ic = min_ic
            self.min_ir = min_ir

        def mine_factors(self, frames):
            return []

    class FakeLibrary:
        def load(self) -> None:
            return None

        def add_factor(self, factor) -> bool:
            return False

        def save(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.AutoFactorMiner",
        FakeMiner,
    )
    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.FactorLibrary",
        FakeLibrary,
    )

    args = Namespace(
        source="eastmoney",
        min_ic=0.03,
        min_ir=0.5,
        output="",
        report="",
    )

    exit_code = cli_mod.run_mine_factors(args)

    assert exit_code == 0
    assert seen["pool_name"] == ""
    assert seen["symbols"] == ""
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_mine_factors_stores_results_as_inactive_research_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod

    added: list[dict] = []

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeMiner:
        def __init__(self, min_ic: float, min_ir: float):
            self.min_ic = min_ic
            self.min_ir = min_ir

        def mine_factors(self, frames):
            return [
                {
                    "name": "demo_factor",
                    "category": "price",
                    "formula": "close / open",
                    "lookback_period": 5,
                    "params": {},
                    "evaluation": {
                        "ic_mean": 0.04,
                        "ic_ir": 0.8,
                        "sample_size": 120,
                    },
                }
            ]

    class FakeLibrary:
        def load(self) -> None:
            return None

        def add_factor(self, factor) -> bool:
            added.append(factor)
            return True

        def save(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.AutoFactorMiner",
        FakeMiner,
    )
    monkeypatch.setattr(
        "aqsp.strategies.auto_factor_mining.FactorLibrary",
        FakeLibrary,
    )

    output = tmp_path / "factors.json"
    args = Namespace(
        source="eastmoney",
        min_ic=0.03,
        min_ir=0.5,
        output=str(output),
        report="",
    )

    exit_code = cli_mod.run_mine_factors(args)

    assert exit_code == 0
    assert added[0]["name"] == "demo_factor"
    assert added[0]["is_active"] is False
    assert added[0]["status"] == "research_candidate"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["is_active"] is False


def test_factor_library_treats_missing_active_flag_as_inactive(tmp_path: Path) -> None:
    from aqsp.strategies.auto_factor_mining import FactorLibrary

    library = FactorLibrary(str(tmp_path / "factor_library.json"))
    library.factors = [
        {"name": "legacy_missing_flag"},
        {"name": "disabled_factor", "is_active": False},
        {"name": "approved_factor", "is_active": True},
    ]

    assert [factor["name"] for factor in library.get_active_factors()] == [
        "approved_factor"
    ]

    assert library.add_factor({"name": "new_research_factor"}) is True
    assert library.factors[-1]["is_active"] is False
    assert library.factors[-1]["status"] == "research_candidate"


def test_run_discover_marks_output_as_research_only(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.optimizer.pattern_discovery import DiscoveredPattern

    monkeypatch.setattr(
        "aqsp.ledger.base.read_ledger",
        lambda _path: [{"symbol": "600519", "signal_date": "2026-06-01"}],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeEngine:
        def __init__(self, min_sample_size: int, min_win_rate: float) -> None:
            assert min_sample_size == 12
            assert min_win_rate == 0.58

        def discover(self, ledger_df, frames):
            assert not ledger_df.empty
            assert "600519" in frames
            return [
                DiscoveredPattern(
                    pattern_id="pat_demo001",
                    pattern_type="breakout",
                    description="突破后延续",
                    conditions={"lookback_days": 60},
                    historical_win_rate=0.62,
                    historical_avg_return=3.4,
                    sample_size=28,
                    confidence=0.74,
                    first_seen="2026-01-01",
                    last_seen="2026-06-01",
                )
            ]

    monkeypatch.setattr(
        "aqsp.optimizer.pattern_discovery.PatternDiscoveryEngine", FakeEngine
    )

    output_path = tmp_path / "patterns.json"
    report_path = tmp_path / "patterns.md"
    args = Namespace(
        ledger="data/predictions.jsonl",
        source="eastmoney",
        min_sample=12,
        min_winrate=0.58,
        output=str(output_path),
        report=str(report_path),
    )

    exit_code = cli_mod.run_discover(args)

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload[0]["status"] == "research_candidate"
    assert payload[0]["proposal_only"] is True
    assert payload[0]["applied"] is False
    assert payload[0]["uses_forward_returns"] is True

    report_text = report_path.read_text(encoding="utf-8")
    assert "研究形态发现报告" in report_text
    assert "仅供研究复核" in report_text
    assert "自动写入主链" in report_text


def test_run_evolve_prefers_aqsp_symbols_when_configured(monkeypatch) -> None:
    import aqsp.cli as cli_mod

    seen: dict[str, object] = {}

    def fake_resolve_run_symbols(source, symbols, **kwargs):
        seen["symbols"] = symbols
        seen["pool_name"] = kwargs["pool_name"]
        return ["600519", "300750"]

    monkeypatch.setenv("AQSP_SYMBOLS", "600519,300750")
    monkeypatch.setattr(cli_mod, "_resolve_run_symbols", fake_resolve_run_symbols)

    def fake_fetch_frames_for_cli(*_args, **kwargs):
        seen["benchmark_symbol"] = kwargs.get("benchmark_symbol")
        seen["days"] = kwargs.get("days")
        return {"600519": object(), "300750": object()}

    monkeypatch.setattr(cli_mod, "_fetch_frames_for_cli", fake_fetch_frames_for_cli)

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return None

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        max_universe=0,
        apply=False,
        output="",
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert seen["symbols"] == "600519,300750"
    assert seen["pool_name"] == ""
    assert seen["benchmark_symbol"] is None
    assert seen["days"] == 250


def test_run_evolve_writes_result_when_evolution_succeeds(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.auto_evolution import EvolutionResult

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return EvolutionResult(
                strategy_name=strategy_name,
                old_params={"momentum_weight": 0.3},
                new_params={"momentum_weight": 0.4},
                performance_improvement=0.12,
                confidence=0.85,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                reason="performance_improvement",
            )

        def _apply_evolution(self, _result) -> None:
            raise AssertionError("should not apply when apply=False")

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    output = tmp_path / "evolution_result.json"
    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=False,
        output=str(output),
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["strategy_name"] == "composite"
    assert payload["new_params"]["momentum_weight"] == 0.4
    assert payload["performance_improvement"] == 0.12
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False


def test_run_evolve_apply_is_proposal_only(monkeypatch, tmp_path: Path) -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.auto_evolution import EvolutionResult

    applied = False

    monkeypatch.setattr(
        cli_mod,
        "_resolve_run_symbols",
        lambda *_args, **_kwargs: ["600519"],
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {"600519": object()},
    )

    class FakeConfig:
        enabled = True
        confidence_threshold = 0.8

    class FakeEvolution:
        def __init__(self, config_path: str):
            self.config_path = config_path
            self.config = FakeConfig()

        def evolve_parameters(self, strategy_name: str, frames):
            return EvolutionResult(
                strategy_name=strategy_name,
                old_params={"momentum_weight": 0.3},
                new_params={"momentum_weight": 0.4},
                performance_improvement=0.12,
                confidence=0.95,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                reason="performance_improvement",
            )

        def _apply_evolution(self, _result) -> None:
            nonlocal applied
            applied = True

    monkeypatch.setattr(
        "aqsp.strategies.auto_evolution.AutoEvolution",
        FakeEvolution,
    )

    output = tmp_path / "evolution_result.json"
    args = Namespace(
        source="eastmoney",
        config="config/evolution_config.yaml",
        apply=True,
        output=str(output),
    )

    exit_code = cli_mod.run_evolve(args)

    assert exit_code == 0
    assert applied is False
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert payload["status"] == "proposal_only"


def test_run_optimize_apply_writes_proposal_without_touching_thresholds(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod
    from aqsp.optimizer.param_optimizer import OptimizationResult
    from aqsp.strategies.thresholds import Thresholds

    applied: list[dict[str, float]] = []

    monkeypatch.setattr(cli_mod, "load_thresholds", lambda: Thresholds(version="test"))
    monkeypatch.setattr(cli_mod, "_get_hs300_symbols", lambda _as_of=None: ["600519"])
    monkeypatch.setattr(cli_mod, "_walkforward_fetch_days", lambda *_args: 120)
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {
            "600519": object(),
            "000001": object(),
            "000002": object(),
            "000003": object(),
            "000004": object(),
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "_apply_best_params",
        lambda params: applied.append(params),
    )

    class FakeFrame:
        empty = False

        def __getitem__(self, _key):
            return self

        def astype(self, _type):
            return self

        def __ge__(self, _other):
            return self

        def __le__(self, _other):
            return self

        def __and__(self, _other):
            return self

        @property
        def loc(self):
            return self

        def __len__(self):
            return 120

        def copy(self):
            return self

    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli",
        lambda *_args, **_kwargs: {
            symbol: FakeFrame()
            for symbol in ["600519", "000001", "000002", "000003", "000004"]
        },
    )

    monkeypatch.setattr(
        "aqsp.optimizer.param_optimizer.create_walkforward_evaluator",
        lambda **_kwargs: lambda _params: 1.0,
    )

    class FakeOptimizer:
        def __init__(self, *_args, **_kwargs):
            return None

        def optimize(self, *_args, **_kwargs):
            return OptimizationResult(
                best_params={"composite.momentum_weight": 0.4},
                best_score=1.23,
                all_results=[],
                n_trials=1,
                method="grid",
            )

    monkeypatch.setattr(
        "aqsp.optimizer.param_optimizer.GridSearchOptimizer",
        FakeOptimizer,
    )

    output = tmp_path / "optimization_result.json"
    args = Namespace(
        method="grid",
        trials=1,
        symbols="600519,000001,000002,000003,000004",
        start="2026-01-01",
        end="2026-06-01",
        source="sqlite_db",
        engine="builtin",
        output=str(output),
        apply=True,
    )

    exit_code = cli_mod.run_optimize(args)

    assert exit_code == 0
    assert applied == []
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["best_params"] == {"composite.momentum_weight": 0.4}
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False


def test_legacy_apply_best_params_rejects_threshold_writeback() -> None:
    import aqsp.cli as cli_mod

    with pytest.raises(RuntimeError, match="自动写回 thresholds.yaml 已禁用"):
        cli_mod._apply_best_params({"composite.momentum_weight": 0.4})


def test_auto_evolution_apply_writes_proposal_without_touching_thresholds(
    tmp_path: Path,
) -> None:
    from aqsp.strategies.auto_evolution import AutoEvolution, EvolutionResult

    thresholds_path = tmp_path / "thresholds.yaml"
    thresholds_path.write_text(
        "version: test\nstrategies:\n  composite:\n    momentum_weight: 0.3\n",
        encoding="utf-8",
    )
    original = thresholds_path.read_text(encoding="utf-8")
    evolution = AutoEvolution(
        thresholds_path=str(thresholds_path),
        data_dir=str(tmp_path / "evolution"),
    )
    result = EvolutionResult(
        strategy_name="composite",
        old_params={"momentum_weight": 0.3},
        new_params={"momentum_weight": 0.4},
        performance_improvement=0.12,
        confidence=0.9,
        timestamp=datetime(2026, 6, 3, 12, 0, 0),
        reason="test",
        sample_count=30,
        gate_evidence={"status": "pass"},
    )

    evolution._apply_evolution(result)

    assert thresholds_path.read_text(encoding="utf-8") == original
    proposal_path = tmp_path / "evolution" / "threshold_proposals.jsonl"
    payload = json.loads(proposal_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["status"] == "proposal_only"
    assert payload["applied"] is False
    assert payload["new_params"] == {"momentum_weight": 0.4}


def test_direct_walkforward_defaults_match_threshold_costs() -> None:
    from aqsp.backtest.walk_forward import WalkForwardTester
    from aqsp.research_engine import WalkForwardEngineConfig

    config = WalkForwardEngineConfig(
        train_days=120, test_days=30, purge_days=5, horizon_days=3
    )
    assert config.fee_bps == 3.0
    assert config.slippage_bps == 20.0
    tester = WalkForwardTester(strategy=object())
    assert tester.fee_bps == 3.0
    assert tester.slippage_bps == 20.0


def test_execution_cost_defaults_are_loaded_from_thresholds() -> None:
    import aqsp.cli as cli_mod
    from aqsp.strategies.thresholds import Thresholds

    thresholds = Thresholds()

    assert tuple(
        round(value, 4)
        for value in cli_mod._resolve_execution_cost_bps(
            thresholds,
            fee_bps=None,
            slippage_bps=None,
        )
    ) == (3.0, 20.0)
    assert cli_mod._resolve_execution_cost_bps(
        thresholds,
        fee_bps=8.0,
        slippage_bps=5.0,
    ) == (8.0, 5.0)
