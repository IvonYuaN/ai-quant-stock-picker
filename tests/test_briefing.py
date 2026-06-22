from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import pandas as pd

from aqsp.briefing import (
    Briefing,
    BriefingGenerator,
    BriefingSection,
    enhance_briefing,
    send_briefing,
)
from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.debate import AShareDebateAgent
from aqsp.core.types import PickResult
from aqsp.portfolio.manager import PortfolioDecisionSummary, WatchlistReviewItem
from aqsp.utils.llm_safe import LlmResult


def _make_pick(**overrides) -> PickResult:
    defaults = dict(
        symbol="600519",
        name="贵州茅台",
        date="2026-05-27",
        close=1500.0,
        score=8.5,
        rating="A",
        entry_type="next_open",
        ideal_buy=1490.0,
        stop_loss=1450.0,
        take_profit=1600.0,
        position="half",
        strategies=("momentum", "value"),
        reasons=("动量突破MA20", "PE低估"),
        risks=("高位震荡风险",),
    )
    defaults.update(overrides)
    return PickResult(**defaults)


class TestBriefingSection:
    def test_frozen(self):
        section = BriefingSection(title="test", content="body")
        with pytest.raises(AttributeError):
            section.title = "new"


class TestBriefing:
    def test_to_markdown_basic(self):
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[BriefingSection(title="sec1", content="body1")],
        )
        md = briefing.to_markdown()
        assert "# 每日研究复盘-2026-05-27 10:00" in md
        assert "## sec1" in md
        assert "body1" in md
        assert "仅供研究" in md
        assert "不构成交易指令或投资建议" in md

    def test_to_markdown_multiple_sections(self):
        briefing = Briefing(
            date="2026-05-27",
            sections=[
                BriefingSection(title="A", content="a"),
                BriefingSection(title="B", content="b"),
            ],
        )
        md = briefing.to_markdown()
        assert md.index("## A") < md.index("## B")


class TestBriefingGenerator:
    def test_generate_returns_briefing(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        assert isinstance(briefing, Briefing)
        assert briefing.date
        assert len(briefing.sections) == 7

    def test_generate_empty_picks(self):
        gen = BriefingGenerator()
        briefing = gen.generate(picks=[], frames={})
        assert isinstance(briefing, Briefing)
        assert len(briefing.sections) == 7
        md = briefing.to_markdown()
        assert "无候选标的" in md

    def test_main_chain_section_is_present(self):
        gen = BriefingGenerator()
        briefing = gen.generate(picks=[_make_pick()], frames={})
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "今日结论" in main_chain_sec.content
        assert "先看这个" in main_chain_sec.content

    def test_main_chain_section_uses_score_sorted_lead_pick(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(symbol="600036", name="招商银行", score=-13.0, rating="watch"),
            _make_pick(symbol="300750", name="宁德时代", score=16.0, rating="watch"),
        ]
        briefing = gen.generate(picks=picks, frames={})
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert (
            "先看这个: 300750 宁德时代 | 继续观察名单 | 评分 16.0"
            in main_chain_sec.content
        )

    def test_main_chain_section_includes_candidate_status_label(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="300750",
                    name="宁德时代",
                    score=16.0,
                    rating="watch",
                    metrics={"candidate_status": "新晋"},
                )
            ],
            frames={},
        )
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert (
            "先看这个: 300750 宁德时代 | 继续观察名单 | 新晋 | 评分 16.0"
            in main_chain_sec.content
        )

    def test_main_chain_section_dedupes_watchlist_against_top_focus(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(
                symbol="600036",
                name="招商银行",
                score=59.0,
                rating="watch",
            ),
            _make_pick(
                symbol="601318",
                name="中国平安",
                score=-28.0,
                rating="watch",
            ),
        ]
        briefing = gen.generate(
            picks=picks,
            frames={},
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=0,
                downgrade_count=1,
                keep_count=1,
                top_focus=("600036 招商银行",),
                watchlist=("600036 招商银行", "601318 中国平安"),
                allocations=(),
                cash_reserve=1.0,
                allocation_note="保留现金",
            ),
        )

        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "- 今日重点名单: 600036 招商银行" in main_chain_sec.content
        assert "- 继续观察名单: 601318 中国平安" in main_chain_sec.content
        assert "继续观察名单: 600036 招商银行" not in main_chain_sec.content
        assert "先看这个: 600036 招商银行" in main_chain_sec.content

    def test_main_chain_section_includes_watch_review_checklist(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="688981", name="中芯国际", score=-9.0, rating="watch"
                ),
                _make_pick(
                    symbol="000001", name="平安银行", score=-18.0, rating="watch"
                ),
            ],
            frames={},
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=0,
                downgrade_count=2,
                keep_count=0,
                top_focus=(),
                watchlist=("688981 中芯国际", "000001 平安银行"),
                allocations=(),
                cash_reserve=1.0,
                allocation_note="今日以观察为主",
                watch_reviews=(
                    WatchlistReviewItem(
                        symbol="688981",
                        name="中芯国际",
                        blocker="板块集中度过高",
                        next_step="等待量价继续走强后，再评估是否转入纸面复核名单",
                        review_window="盘中走强后",
                        priority="high",
                    ),
                    WatchlistReviewItem(
                        symbol="000001",
                        name="平安银行",
                        blocker="高相关未解除",
                        next_step="等待高相关标的分化后，再重新评估纸面复核优先级",
                        review_window="分化确认后",
                        priority="medium",
                    ),
                ),
            ),
        )
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "- 观察名单下一步:" in main_chain_sec.content
        assert (
            "  - 688981 中芯国际 | 高优先级 / 盘中走强后 | 等待量价继续走强后，再评估是否转入纸面复核名单"
            in main_chain_sec.content
        )

    def test_main_chain_section_does_not_repeat_symbol_as_name(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(symbol="600036", name="600036", score=-13.0, rating="watch")
            ],
            frames={},
        )
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "600036 600036" not in main_chain_sec.content

    def test_regime_section_with_regime(self):
        gen = BriefingGenerator()
        briefing = gen.generate(picks=[], frames={}, regime="stable_bull")
        regime_sec = next(s for s in briefing.sections if s.title == "市场态势")
        assert "稳定上涨" in regime_sec.content

    def test_regime_section_with_circuit_breaker(self):
        gen = BriefingGenerator()
        cb = MagicMock()
        cb.triggered = True
        cb.reason = "组合保护冷却期中"
        briefing = gen.generate(picks=[], frames={}, circuit_breaker_status=cb)
        regime_sec = next(s for s in briefing.sections if s.title == "市场态势")
        assert "组合保护中" in regime_sec.content

    def test_source_section_shows_runtime_health(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[],
            frames={},
            source_status={
                "requested_source": "auto",
                "actual_source": "eastmoney",
                "freshness_tier": "realtime",
                "coverage_tier": "multi_dimensional",
                "health_label": "fallback",
                "health_message": "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
                "fallback_used": True,
            },
        )
        source_sec = next(s for s in briefing.sections if s.title == "数据源状态")
        assert "auto -> eastmoney" in source_sec.content
        assert "盘中实时 / 多维行情" in source_sec.content
        assert "已切换备用源" in source_sec.content
        assert "降低信任度" in source_sec.content

    def test_research_section_summarizes_absorbed_backlog(self):
        from aqsp.research.summary import (
            ResearchFamilySummary,
            ResearchPipelineSummary,
            ResearchSummary,
        )

        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[],
            frames={},
            research_summary=ResearchSummary(
                generated_at="",
                total_findings=113,
                pipeline_summaries=(
                    ResearchPipelineSummary(
                        pipeline="data_source",
                        total=24,
                        p1=14,
                        top_repo="mpquant/Ashare",
                    ),
                    ResearchPipelineSummary(
                        pipeline="strategy",
                        total=22,
                        p1=10,
                        top_repo="sngyai/Sequoia-X",
                    ),
                ),
                absorbed_families=(
                    ResearchFamilySummary(
                        family_id="market_regime_timing_filter",
                        name="大盘择时 / 市场状态过滤",
                        status="research_absorbed",
                        runtime_stage="gated_runtime",
                        absorbed_from_count=4,
                        runtime_gate_count=4,
                    ),
                ),
                source_candidates=(),
                next_actions=(),
                prereq_items=(),
                implemented_family_count=5,
                report_only_family_count=0,
                gated_family_count=1,
            ),
        )
        research_sec = next(s for s in briefing.sections if s.title == "研究吸收")
        assert "研究结论落地情况" in research_sec.content
        assert "113" in research_sec.content
        assert "mpquant/Ashare" in research_sec.content
        assert "大盘择时 / 市场状态过滤（满足条件后启用）" in research_sec.content

    def test_evidence_section_shows_strategies(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        evidence_sec = next(s for s in briefing.sections if s.title == "候选来龙去脉")
        assert "600519" in evidence_sec.content
        assert "momentum" in evidence_sec.content
        assert "动量突破MA20" in evidence_sec.content
        assert "风险提示: 高位震荡风险" in evidence_sec.content
        assert "- 风险:" not in evidence_sec.content

    def test_theme_section_categorizes(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(symbol="000001", reasons=("放量突破", "动量强劲")),
            _make_pick(symbol="000002", reasons=("PE低估",)),
        ]
        briefing = gen.generate(picks=picks, frames={})
        theme_sec = next(s for s in briefing.sections if s.title == "题材热度")
        assert "量价" in theme_sec.content
        assert "动量" in theme_sec.content

    def test_next_day_section_shows_top5(self):
        gen = BriefingGenerator()
        picks = [_make_pick(symbol=f"sym{i:03d}") for i in range(10)]
        briefing = gen.generate(picks=picks, frames={})
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert next_sec.content.count("**") <= 10

    def test_next_day_section_excludes_avoid_picks(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", rating="avoid")],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "当前暂无纸面复核对象" in next_sec.content
        assert "继续观察名单" in next_sec.content
        assert "待阻塞条件解除后再考虑转入纸面复核名单" in next_sec.content
        assert "600519" in next_sec.content
        assert "avoid" not in next_sec.content

    def test_next_day_section_excludes_watch_picks(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", rating="watch")],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "当前暂无纸面复核对象" in next_sec.content
        assert "继续观察名单" in next_sec.content
        assert "待阻塞条件解除后再考虑转入纸面复核名单" in next_sec.content
        assert "600519" in next_sec.content

    def test_next_day_section_includes_candidate_status_for_watch_pick(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="600519",
                    name="贵州茅台",
                    rating="watch",
                    metrics={"candidate_status": "观察阻塞"},
                )
            ],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "继续观察名单: 600519 贵州茅台(观察阻塞)" in next_sec.content

    def test_next_day_section_includes_blocker_and_next_step_for_watch_pick(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="600519",
                    name="贵州茅台",
                    rating="watch",
                    metrics={
                        "candidate_status": "观察阻塞",
                        "candidate_blocker": "T+1 未解除",
                        "candidate_next_step": "明日解除 T+1 后，优先复核开盘承接与流动性",
                        "candidate_review_window": "明日开盘前后",
                        "candidate_review_priority": "high",
                    },
                )
            ],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "现在卡在哪: T+1 未解除" in next_sec.content
        assert (
            "接下来先看: 明日解除 T+1 后，优先再看开盘承接与流动性" in next_sec.content
        )
        assert "再看时间: 高优先级 / 明日开盘前后" in next_sec.content

    def test_render_template(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        rendered = gen.render_template(briefing, picks)
        assert "每日研究复盘" in rendered
        assert "600519" in rendered

    def test_render_template_keeps_avoid_out_of_next_day_section(self):
        gen = BriefingGenerator()
        picks = [_make_pick(symbol="600519", rating="avoid")]
        briefing = gen.generate(picks=picks, frames={})
        rendered = gen.render_template(briefing, picks)
        next_day = rendered.split("## 明日先看", maxsplit=1)[1]
        assert "继续观察名单" in next_day
        assert "600519" in next_day
        assert "avoid" not in next_day

    def test_markdown_and_template_sanitize_dynamic_fields(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(
                symbol="600519",
                name="贵州茅台<script>",
                rating="strong_buy_candidate",
                entry_type="执行开仓<script>",
                reasons=("立即买入后等待下单<script>alert(1)</script>",),
                risks=("真实持仓暴露过高<img onerror=alert(1)>",),
                metrics={
                    "candidate_blocker": "买入条件不足，下单阻塞",
                    "candidate_next_step": "执行开仓后看真实持仓",
                },
            )
        ]
        briefing = gen.generate(picks=picks, frames={})

        rendered = "\n".join(
            (briefing.to_markdown(), gen.render_template(briefing, picks))
        )

        for forbidden in (
            "<script>",
            "<img",
            "onerror",
            "立即买入",
            "下单",
            "执行开仓",
            "真实持仓",
        ):
            assert forbidden not in rendered
        assert "&lt;script&gt;" in rendered
        assert "纸面记录阻塞" in rendered
        assert "纸面持有" in rendered


class TestDebateAgent:
    def test_llm_enhancement_keeps_stance_but_rewrites_role_specific_points(self):
        agent = AShareDebateAgent(
            role=AgentRole.BULL,
            enable_llm=True,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})

        with patch(
            "aqsp.briefing.debate.llm_call_or_fallback",
            return_value=LlmResult(
                text="""
                {
                  "arguments": ["趋势主升结构仍在扩张", "放量后承接仍然健康"],
                  "risk_factors": ["高位分歧后波动会放大"],
                  "opportunity_factors": ["强趋势延续时容易走加速段"]
                }
                """,
                degraded=False,
            ),
        ) as mock_llm:
            opinion = agent.generate_initial_opinion(_make_pick(score=72), df)

        assert mock_llm.called is True
        assert opinion.stance == "bullish"
        assert opinion.arguments[0] == "趋势主升结构仍在扩张"
        assert opinion.risk_factors == ["高位分歧后波动会放大"]
        assert opinion.opportunity_factors == ["强趋势延续时容易走加速段"]

    def test_llm_enhancement_falls_back_when_payload_is_invalid(self):
        agent = AShareDebateAgent(
            role=AgentRole.RISK_CONTROL,
            enable_llm=True,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})

        with patch(
            "aqsp.briefing.debate.llm_call_or_fallback",
            return_value=LlmResult(text="not-json", degraded=True),
        ):
            opinion = agent.generate_initial_opinion(_make_pick(score=68), df)

        assert opinion.stance in {"neutral", "bearish"}
        assert opinion.arguments
        assert opinion.risk_factors

    def test_llm_enhancement_uses_role_specific_provider_and_model(self):
        agent = AShareDebateAgent(
            role=AgentRole.NORTHBOUND,
            enable_llm=True,
            language="zh-CN",
            llm_provider="agnes",
            llm_model="agnes-2.0-flash",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})

        with patch.dict(os.environ, {"LLM_PROVIDER": "glm"}, clear=False):
            with patch(
                "aqsp.briefing.debate.llm_call_or_fallback",
                return_value=LlmResult(
                    text='{"arguments":["北向偏好延续"],"risk_factors":[],"opportunity_factors":["外资增配可能持续"]}',
                    degraded=False,
                    model="agnes-2.0-flash",
                ),
            ) as mock_llm:
                opinion = agent.generate_initial_opinion(_make_pick(score=66), df)
                assert os.getenv("LLM_PROVIDER") == "glm"

        assert opinion.arguments == ["北向偏好延续"]
        assert os.getenv("LLM_PROVIDER") != "agnes"
        assert mock_llm.call_args.kwargs["model"] == "agnes-2.0-flash"


class TestEnhanceBriefing:
    def test_noop_when_disabled(self):
        briefing = Briefing(date="d", sections=[])
        result = enhance_briefing(briefing, enable_llm=False)
        assert result is briefing

    def test_noop_when_env_not_set(self):
        briefing = Briefing(date="d", sections=[])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_LLM_BRIEFING", None)
            result = enhance_briefing(briefing, enable_llm=True)
            assert result is briefing

    def test_noop_when_env_false(self):
        briefing = Briefing(date="d", sections=[])
        with patch.dict(os.environ, {"ENABLE_LLM_BRIEFING": "false"}):
            result = enhance_briefing(briefing, enable_llm=True)
            assert result is briefing

    def test_keep_original_briefing_when_llm_succeeds(self):
        briefing = Briefing(
            date="d",
            sections=[BriefingSection(title="结论", content="原始事实内容")],
        )
        with patch.dict(os.environ, {"ENABLE_LLM_BRIEFING": "true"}):
            with patch(
                "aqsp.briefing.llm.llm_call_or_fallback",
                return_value=LlmResult(
                    text="被优化后的整篇内容",
                    degraded=False,
                    model="demo",
                ),
            ) as mock_llm:
                result = enhance_briefing(briefing, enable_llm=True)

        assert result is briefing
        assert result.to_markdown() == briefing.to_markdown()
        assert mock_llm.called is True


class TestSendBriefing:
    def test_calls_notifier(self):
        briefing = Briefing(
            date="d", sections=[BriefingSection(title="t", content="c")]
        )
        mock_notifier = MagicMock()
        send_briefing(briefing, notifier=mock_notifier)
        mock_notifier.assert_called_once()
        assert "t" in mock_notifier.call_args[0][0]
        assert "## 结论" in mock_notifier.call_args[0][0]
        assert "阅读方式" not in mock_notifier.call_args[0][0]
        assert "# AI 量化选股日报" not in mock_notifier.call_args[0][0]

    @patch("aqsp.notifier.notify_markdown")
    def test_calls_default_notifier(self, mock_notify):
        briefing = Briefing(date="d", sections=[])
        mock_notify.return_value = []
        send_briefing(briefing)
        mock_notify.assert_called_once()

    @patch("aqsp.notifier.notify_markdown")
    def test_returns_default_notifier_results(self, mock_notify):
        briefing = Briefing(date="d", sections=[])
        mock_notify.return_value = [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")]

        results = send_briefing(briefing)

        assert len(results) == 1
        assert results[0].channel == "serverchan"

    def test_send_briefing_prepends_source_status_banner(self):
        briefing = Briefing(
            date="d", sections=[BriefingSection(title="t", content="c")]
        )
        mock_notifier = MagicMock()
        send_briefing(
            briefing,
            notifier=mock_notifier,
            source_status={
                "requested_source": "auto",
                "actual_source": "eastmoney",
                "health_label": "fallback",
                "health_message": "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
            },
        )
        body = mock_notifier.call_args[0][0]
        assert body.index("## 数据") < body.index("## 结论")
        assert "## 结论" in body
        assert "auto -> eastmoney" in body
        assert "降低信任度" in body

    def test_send_briefing_returns_custom_notifier_results(self):
        briefing = Briefing(
            date="d", sections=[BriefingSection(title="t", content="c")]
        )
        result = send_briefing(
            briefing,
            notifier=lambda _markdown: [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ],
        )

        assert len(result) == 1
        assert result[0].channel == "serverchan"


class TestGenerateSmartSummary:
    def test_empty_briefing(self):
        briefing = Briefing(date="2026-05-27 10:00", sections=[])
        summary = briefing.generate_smart_summary()
        assert "今日无候选标的，保持观望" in summary

    def test_one_liner_with_candidates_and_regime(self):
        gen = BriefingGenerator()
        picks = [_make_pick(symbol="600519", name="贵州茅台", score=8.5)]
        briefing = gen.generate(picks=picks, frames={}, regime="stable_bull")
        summary = briefing.generate_smart_summary()
        assert "稳定上涨" in summary
        assert "筛出1只候选" in summary

    def test_risk_alerts_prioritized_first(self):
        gen = BriefingGenerator()
        cb = MagicMock()
        cb.triggered = True
        cb.reason = "组合保护冷却期中"
        picks = [_make_pick()]
        briefing = gen.generate(
            picks=picks, frames={}, regime="stable_bull", circuit_breaker_status=cb
        )
        summary = briefing.generate_smart_summary()
        lines = [line for line in summary.split("\n") if line.startswith("⚠️")]
        assert len(lines) >= 1
        assert "组合保护" in lines[0]

    def test_actionable_picks_included(self):
        gen = BriefingGenerator()
        picks = [_make_pick(symbol="600519", name="贵州茅台", rating="buy_candidate")]
        briefing = gen.generate(picks=picks, frames={})
        summary = briefing.generate_smart_summary()
        assert "纸面复核对象" in summary
        assert "600519" in summary

    def test_no_actionable_picks_excluded(self):
        gen = BriefingGenerator()
        picks = [_make_pick(symbol="600519", name="贵州茅台", rating="avoid")]
        briefing = gen.generate(picks=picks, frames={})
        summary = briefing.generate_smart_summary()
        assert "有继续观察名单，当前暂无纸面复核对象" in summary
        assert "继续观察名单" in summary

    def test_next_day_section_mentions_watchlist_when_no_tradable_pick(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", name="贵州茅台", rating="watch")],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "继续观察名单" in next_sec.content
        assert "600519 贵州茅台" in next_sec.content

    def test_action_plan_mentions_watchlist_when_candidates_exist_but_not_tradable(
        self,
    ):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", name="贵州茅台", rating="watch")],
            frames={},
        )

        summary = briefing.generate_smart_summary()

        assert "- 继续观察名单: 600519 贵州茅台" in summary
        assert "- 重点观察: 600519 贵州茅台" in summary

    def test_action_plan_mentions_candidate_status_when_candidates_exist_but_not_tradable(
        self,
    ):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="600519",
                    name="贵州茅台",
                    rating="watch",
                    metrics={"candidate_status": "新晋"},
                )
            ],
            frames={},
        )

        summary = briefing.generate_smart_summary()

        assert "- 继续观察名单: 600519 贵州茅台(新晋)(8.5分)" in summary
        assert "- 重点观察: 600519 贵州茅台(新晋)(8.5分)" in summary

    def test_action_plan_mentions_default_review_for_new_watch_pick(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="600519",
                    name="贵州茅台",
                    rating="watch",
                    metrics={
                        "candidate_status": "新晋",
                        "candidate_next_step": "等待量价继续走强后，再评估是否转入纸面复核名单",
                        "candidate_review_window": "盘中走强后",
                        "candidate_review_priority": "high",
                    },
                )
            ],
            frames={},
        )

        summary = briefing.generate_smart_summary()

        assert (
            "- 解锁后先看: 600519 贵州茅台 | 等待量价继续走强后，再评估是否转入纸面复核名单"
            in summary
        )
        assert "- 再看时间: 600519 贵州茅台 | 高优先级 / 盘中走强后" in summary

    def test_action_plan_mentions_unlock_hint_when_watch_pick_is_blocked(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="600519",
                    name="贵州茅台",
                    rating="watch",
                    metrics={
                        "candidate_status": "观察阻塞",
                        "candidate_blocker": "T+1 未解除",
                        "candidate_next_step": "明日解除 T+1 后，优先复核开盘承接与流动性",
                        "candidate_review_window": "明日开盘前后",
                        "candidate_review_priority": "high",
                    },
                )
            ],
            frames={},
        )

        summary = briefing.generate_smart_summary()

        assert (
            "- 解锁后先看: 600519 贵州茅台 | 明日解除 T+1 后，优先再看开盘承接与流动性"
            in summary
        )
        assert "- 再看时间: 600519 贵州茅台 | 高优先级 / 明日开盘前后" in summary

    def test_evidence_section_includes_candidate_status_label(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="300750",
                    name="宁德时代",
                    score=72.0,
                    rating="buy_candidate",
                    metrics={"candidate_status": "延续上升"},
                )
            ],
            frames={},
        )
        evidence_sec = next(s for s in briefing.sections if s.title == "候选来龙去脉")
        assert "状态: 延续上升" in evidence_sec.content

    def test_evidence_section_includes_candidate_blocker_and_next_step(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="300750",
                    name="宁德时代",
                    score=72.0,
                    rating="watch",
                    metrics={
                        "candidate_status": "观察阻塞",
                        "candidate_blocker": "板块集中度过高，压低新能源暴露",
                        "candidate_next_step": "等待板块暴露回落后，再重新评估纸面复核优先级",
                        "candidate_review_window": "板块分化时",
                        "candidate_review_priority": "medium",
                    },
                )
            ],
            frames={},
        )
        evidence_sec = next(s for s in briefing.sections if s.title == "候选来龙去脉")
        assert "- 现在卡在哪: 板块集中度过高，压低新能源暴露" in evidence_sec.content
        assert (
            "- 接下来先看: 等待板块暴露回落后，再重新评估纸面复核优先级"
            in evidence_sec.content
        )
        assert "- 再看优先级/时机: 中优先级 / 板块分化时" in evidence_sec.content

    def test_core_items_render_structured_portfolio_summary(self):
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=1,
                downgrade_count=1,
                keep_count=0,
                top_focus=("600519 贵州茅台",),
                watchlist=("300750 宁德时代(继续观察名单)",),
                allocations=(),
                cash_reserve=0.2,
                allocation_note="单票上限 20%；信号强度不足时提高现金留存",
                regime_label="稳定上涨",
                strategy_mix_name="进攻牛市",
                strategy_mix_description="稳定上涨期，重仓动量+涨停板",
                strategy_focus=("动量趋势", "涨停接力"),
                strategy_weights=(("momentum", 0.3), ("limit_up_ladder", 0.3)),
                action_hotspots=("板块集中度过高，压低新能源暴露",),
                execution_blockers=("300750 宁德时代: 板块集中度过高，压低新能源暴露",),
            ),
        )

        summary = briefing.generate_smart_summary()

        assert "今日结论: 上调 1 / 降级 1 / 维持 0" in summary
        assert "- 当前市况: 稳定上涨" in summary
        assert "- 现在偏向: 进攻牛市" in summary
        assert "- 今日重点名单: 600519 贵州茅台" in summary
        assert "- 继续观察名单: 300750 宁德时代" in summary
        assert "- 需要重点确认: 板块集中度过高，压低新能源暴露" in summary
        assert "- 现金留存: 20%" in summary
        assert (
            "- 现在卡在哪: 300750 宁德时代: 板块集中度过高，压低新能源暴露" in summary
        )

    def test_main_chain_section_renders_allocation_guidance(self):
        gen = BriefingGenerator()
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="strong_buy_candidate",
        )
        briefing = gen.generate(picks=[pick], frames={})
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "比例参考" in main_chain_sec.content
        assert "300750 宁德时代: 30%" in main_chain_sec.content
        assert "强信号优先分配" in main_chain_sec.content
        assert "现金留存" in main_chain_sec.content

    def test_action_plan_renders_first_allocation_rationale(self):
        gen = BriefingGenerator()
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="strong_buy_candidate",
        )
        briefing = gen.generate(picks=[pick], frames={})

        summary = briefing.generate_smart_summary()

        assert (
            "- 首个纸面理由: 300750 宁德时代 | 主链评分 72.0；强信号优先分配" in summary
        )
        assert (
            "- 比例参考: 300750 宁德时代 30% | 主链评分 72.0；强信号优先分配" in summary
        )
        assert "- 跟踪约束: 单票上限 30%" in summary

    def test_main_chain_section_renders_strategy_mix_guidance(self):
        gen = BriefingGenerator()
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="strong_buy_candidate",
        )
        briefing = gen.generate(picks=[pick], frames={}, regime="stable_bull")
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "当前市况: 稳定上涨" in main_chain_sec.content
        assert "现在偏向: 配置化策略权重" in main_chain_sec.content
        assert "更偏好这些方向: 动量趋势、质量稳健" in main_chain_sec.content

    def test_debate_results_lower_adjustment(self):
        from aqsp.briefing.debate import DebateResult

        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            debate_results=[
                DebateResult(
                    debate_id="test",
                    symbol="600519",
                    name="贵州茅台",
                    original_score=8.5,
                    rating="buy_candidate",
                    recommended_adjustment="lower",
                    adjusted_score=6.8,
                    disagreement_score=0.3,
                ),
            ],
        )
        summary = briefing.generate_smart_summary()
        assert "辩论共识" in summary
        assert "下调" in summary
        assert "6.8" in summary
        assert "不改写系统评分" in summary
        assert "建议下调评分至" not in summary

    def test_debate_results_high_disagreement(self):
        from aqsp.briefing.debate import DebateResult

        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            debate_results=[
                DebateResult(
                    debate_id="test",
                    symbol="600519",
                    name="贵州茅台",
                    original_score=8.5,
                    rating="buy_candidate",
                    recommended_adjustment="keep",
                    adjusted_score=8.5,
                    disagreement_score=0.7,
                ),
            ],
        )
        summary = briefing.generate_smart_summary()
        assert "讨论分歧" in summary
        assert "70%" in summary

    def test_degraded_source_health(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick()],
            frames={},
            source_status={
                "requested_source": "auto",
                "actual_source": "eastmoney",
                "freshness_tier": "realtime",
                "coverage_tier": "multi_dimensional",
                "health_label": "fallback",
                "health_message": "fallback 到 eastmoney",
                "fallback_used": True,
            },
        )
        summary = briefing.generate_smart_summary()
        assert "数据源降级" in summary
        assert "降低信任度" in summary

    def test_bearish_regime_warning(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={}, regime="stable_bear")
        summary = briefing.generate_smart_summary()
        assert "控制纸面暴露" in summary
        assert "注意控制仓位" not in summary

    def test_sideways_regime_info(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={}, regime="volatile_sideways")
        summary = briefing.generate_smart_summary()
        assert "关注突破方向" in summary

    def test_max_five_points(self):
        gen = BriefingGenerator()
        cb = MagicMock()
        cb.triggered = True
        cb.reason = "test"
        picks = [
            _make_pick(symbol=f"sym{i:03d}", rating="buy_candidate") for i in range(10)
        ]
        briefing = gen.generate(
            picks=picks,
            frames={},
            regime="stable_bear",
            circuit_breaker_status=cb,
            source_status={
                "requested_source": "auto",
                "actual_source": "eastmoney",
                "health_label": "fallback",
                "health_message": "test",
                "fallback_used": True,
            },
        )
        summary = briefing.generate_smart_summary()
        bullet_lines = [
            line
            for line in summary.split("\n")
            if line.startswith(("⚠️", "🤖", "📊", "🎯", "📉", "📈"))
        ]
        assert len(bullet_lines) <= 5

    def test_risk_from_evidence_section(self):
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[
                BriefingSection(
                    title="候选来龙去脉",
                    content="### 600519 贵州茅台 (评分: 8.5)\n风险提示: 高位震荡风险\n",
                ),
                BriefingSection(
                    title="明日重点",
                    content="- **600519 贵州茅台**: 参考买点 1490 / 止损 1450",
                ),
                BriefingSection(
                    title="市场态势",
                    content="当前市场态势: **稳定上涨：低波动 + 正趋势**",
                ),
            ],
        )
        summary = briefing.generate_smart_summary()
        assert "风险提示" in summary
        assert "高位震荡风险" in summary


class TestBuildSmartSummaryCard:
    def test_card_structure(self):
        from aqsp.notifier import _build_smart_summary_card

        card = _build_smart_summary_card("测试标题", "**内容**")
        assert card["msg_type"] == "interactive"
        assert card["card"]["header"]["title"]["content"] == "测试标题"
        assert card["card"]["header"]["template"] == "turquoise"
        assert card["card"]["elements"][0]["tag"] == "markdown"
        assert card["card"]["elements"][0]["content"] == "**内容**"

    def test_card_truncates_long_content(self):
        from aqsp.notifier import _build_smart_summary_card

        long_content = "x" * 5000
        card = _build_smart_summary_card("title", long_content)
        assert len(card["card"]["elements"][0]["content"]) == 3800


class TestSendSmartSummaryCard:
    @patch("aqsp.briefing.notifier.notify_feishu_card")
    def test_sends_card(self, mock_send):
        from aqsp.briefing.notifier import send_smart_summary_card

        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        send_smart_summary_card(briefing)
        mock_send.assert_called_once()
        card = mock_send.call_args[0][0]
        assert card["msg_type"] == "interactive"
        assert "选股快报" in card["card"]["header"]["title"]["content"]

    @patch("aqsp.briefing.notifier.notify_feishu_card")
    def test_skips_empty_summary(self, mock_send):
        from aqsp.briefing.notifier import send_smart_summary_card

        briefing = Briefing(date="2026-05-27 10:00", sections=[])
        with patch.object(Briefing, "generate_smart_summary", return_value="   "):
            send_smart_summary_card(briefing)
        mock_send.assert_not_called()

    @patch("aqsp.briefing.notifier.send_smart_summary_card")
    @patch("aqsp.notifier.notify_markdown")
    def test_send_briefing_calls_smart_summary_first(self, mock_notify, mock_card):
        briefing = Briefing(
            date="d", sections=[BriefingSection(title="t", content="c")]
        )
        send_briefing(briefing)
        mock_card.assert_called_once_with(briefing)
        mock_notify.assert_called_once()
