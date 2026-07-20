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
from aqsp.briefing.renderer import MarkdownRenderer
from aqsp.briefing.generator import _apply_debate_results_to_picks
from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.debate import (
    AShareDebateAgent,
    AShareDebateCoordinator,
    DebateResult,
)
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


def test_apply_debate_results_matches_same_day_symbol_by_candidate_fingerprint() -> (
    None
):
    first = _make_pick(
        score=82.0,
        metrics={"candidate_fingerprint": "candidate-first"},
    )
    second = _make_pick(
        score=61.0,
        metrics={"candidate_fingerprint": "candidate-second"},
    )
    first_result = DebateResult(
        debate_id="debate-first",
        symbol=first.symbol,
        name=first.name,
        original_score=first.score,
        rating=first.rating,
        related_signal_date=first.date,
        candidate_fingerprint="candidate-first",
        research_verdict="第一批讨论",
    )
    second_result = DebateResult(
        debate_id="debate-second",
        symbol=second.symbol,
        name=second.name,
        original_score=second.score,
        rating=second.rating,
        related_signal_date=second.date,
        candidate_fingerprint="candidate-second",
        research_verdict="第二批讨论",
    )

    enriched = _apply_debate_results_to_picks(
        [first, second], [second_result, first_result]
    )

    assert [pick.score for pick in enriched] == [82.0, 61.0]
    assert [pick.metrics["debate_research_verdict"] for pick in enriched] == [
        "第一批讨论",
        "第二批讨论",
    ]


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
        assert "当前主看" in main_chain_sec.content

    def test_main_chain_section_uses_score_sorted_lead_pick(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(symbol="600036", name="招商银行", score=-13.0, rating="watch"),
            _make_pick(symbol="300750", name="宁德时代", score=16.0, rating="watch"),
        ]
        briefing = gen.generate(picks=picks, frames={})
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert (
            "当前主看: 300750 宁德时代 | 继续观察 | 评分 16.0" in main_chain_sec.content
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
            "当前主看: 300750 宁德时代 | 继续观察 | 新晋 | 评分 16.0"
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
        assert "- 主看名单: 600036 招商银行" in main_chain_sec.content
        assert "- 观察名单: 601318 中国平安" in main_chain_sec.content
        assert "观察名单: 600036 招商银行" not in main_chain_sec.content
        assert "当前主看: 600036 招商银行" in main_chain_sec.content

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
        assert "- 后续关注:" in main_chain_sec.content
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
                "data_latest_trade_date": "2026-07-08",
                "data_lag_days": "0",
            },
        )
        source_sec = next(s for s in briefing.sections if s.title == "数据源状态")
        assert "auto -> eastmoney" in source_sec.content
        assert "盘中实时 / 多维行情" in source_sec.content
        assert "最新交易日 2026-07-08 / 延迟 0 天" in source_sec.content
        assert "已切换备用源" in source_sec.content
        assert "需人工复核" in source_sec.content

    def test_debate_coordinator_builds_round_summary_and_cross_opinions(self):
        from aqsp.briefing.debate import AShareDebateCoordinator, format_debate_result

        coordinator = AShareDebateCoordinator(
            enable_llm=False,
            max_rounds=2,
            roles=(
                AgentRole.BULL,
                AgentRole.BEAR,
                AgentRole.RISK_CONTROL,
                AgentRole.CROSS_MARKET,
            ),
        )
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="buy_candidate",
            strategies=("momentum",),
            reasons=("放量突破", "趋势延续"),
            risks=("追高波动",),
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "优先复核",
                "cross_market_priority_score": 3,
                "cross_market_observation_window": "2-5日",
            },
        )
        frame = pd.DataFrame(
            {
                "date": [
                    "2026-06-20",
                    "2026-06-23",
                    "2026-06-24",
                    "2026-06-25",
                    "2026-06-26",
                ],
                "open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "high": [101.0, 102.5, 103.0, 104.5, 105.0],
                "low": [99.5, 100.5, 101.5, 102.5, 103.5],
                "close": [100.8, 101.8, 102.6, 103.8, 104.9],
                "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0],
            }
        )

        result = coordinator.run_debate(
            pick,
            frame,
            market_context_lines=(
                "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
                "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
                "传导推演[强]: 海外物理AI叙事升温 -> A股机器人、传感器、丝杠、减速器、工控链；动作 优先复核；观察窗 2-5日；同向 2 条｜反向 1 条；优先看有订单、放量和产业催化验证的环节。｜证据: 置信 0.80 / 2 源共振 / 最新 95 分钟前。",
                "来源质量: 高价值 1 条｜多源/权威 1 条",
                "确认信号: 机器人龙头放量上攻且核心零部件同步走强",
                "失效条件: 只有海外叙事但A股机器人板块不共振",
                "证据堆栈: 同向 2 条｜反向 1 条",
            ),
        )

        assert len(result.rounds) == 2
        assert "看多" in result.rounds[0].summary
        assert "跨市焦点" in result.rounds[0].summary
        assert result.rounds[1].cross_opinions
        assert "risk_control" in result.rounds[1].cross_opinions
        assert result.support_points
        assert result.opposition_points
        assert result.watch_items
        assert result.research_verdict
        assert result.primary_risk_gate
        assert result.next_trigger
        assert result.role_reliability_lines
        assert result.role_selection_plan.startswith("围绕物理AI映射，")
        assert "倾向优先纸面复核" in result.research_verdict
        assert result.primary_risk_gate == "失效条件: 只有海外叙事但A股机器人板块不共振"
        assert result.next_trigger == "先确认 机器人龙头放量上攻且核心零部件同步走强。"
        assert any("技术多头:" in line for line in result.role_reliability_lines)
        assert any("来源质量较高" in item for item in result.support_points)
        assert any("跨市传导" in item for item in result.support_points)
        assert any("跨市传导" in item for item in result.opposition_points)
        assert any("验证重点" in item for item in result.support_points)
        assert any("失效条件" in item for item in result.opposition_points)
        assert any("先确认" in item for item in result.watch_items)
        assert any("跨市逻辑失效处理" in item for item in result.watch_items)
        assert any("同向证据" in item for item in result.support_points)
        assert any("反向证据" in item for item in result.opposition_points)
        assert any("跨市证据已出现反向分歧" in item for item in result.watch_items)

        formatted = format_debate_result(result)
        assert "- 研究口径: 结论已阻断" in formatted
        assert "## 讨论摘要" in formatted
        assert "## 支持观点" in formatted
        assert "## 待确认" in formatted
        assert "## 裁决压缩" in formatted
        assert "## 视角与分工" in formatted
        assert "- 第1轮:" in formatted
        assert "- 角色可信度:" in formatted
        assert formatted.index("## 裁决压缩") < formatted.index("## 市场上下文")
        assert "- 跨市判断:" not in formatted
        assert formatted.index("## 待确认") < formatted.index("## 视角与分工")
        assert "- 讨论视角:" not in formatted

    def test_format_debate_result_includes_historical_context_note(self):
        from aqsp.briefing.debate import DebateResult, format_debate_result

        result = DebateResult(
            debate_id="demo",
            symbol="300750",
            name="宁德时代",
            original_score=72.0,
            rating="watch",
            recommended_adjustment="keep",
            disagreement_score=0.2,
            historical_context_note="历史校验: 强证据 2/3 (67%)；冲突主导 1/3",
            role_reliability_lines=("技术多头: 近21天 7/10 (70%)｜当前权重 0.18",),
        )

        formatted = format_debate_result(result)

        assert "- 历史校验: 强证据 2/3 (67%)；冲突主导 1/3" in formatted
        assert "- 角色可信度: 技术多头: 近21天 7/10 (70%)｜当前权重 0.18" in formatted

    def test_research_section_summarizes_absorbed_backlog(self):
        from aqsp.research.summary import (
            ResearchFamilySummary,
            ResearchPipelineSummary,
            ResearchRepoBacklogItem,
            ResearchRepoLaneSummary,
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
                repo_intake_total=296,
                repo_substrate_candidate_count=165,
                repo_reject_boundary_count=63,
                repo_report_only_count=68,
                repo_lane_summaries=(
                    ResearchRepoLaneSummary(lane="backtest_validation", count=61),
                    ResearchRepoLaneSummary(lane="data_source", count=32),
                ),
                repo_backlog=(
                    ResearchRepoBacklogItem(
                        repo="OpenBB-finance/OpenBB",
                        lane="data_source",
                        priority="P1",
                        landing="config/data_sources.yaml + aqsp.data.source_catalog",
                        next_action="抽取字段/schema/freshness gate",
                        url="https://github.com/OpenBB-finance/OpenBB",
                    ),
                ),
            ),
        )
        research_sec = next(s for s in briefing.sections if s.title == "研究吸收")
        assert "研究结论落地情况" in research_sec.content
        assert "113" in research_sec.content
        assert "mpquant/Ashare" in research_sec.content
        assert "大盘择时 / 市场状态过滤（满足条件后启用）" in research_sec.content
        assert (
            "开源扫描池: 共 296 项 / 底座候选 165 / 执行红线 63 / 仅记录 68"
            in research_sec.content
        )
        assert (
            "扫描分类: backtest_validation 61、data_source 32" in research_sec.content
        )
        assert (
            "开源接入队列: OpenBB-finance/OpenBB [P1/data_source] -> config/data_sources.yaml + aqsp.data.source_catalog"
            in research_sec.content
        )

    def test_main_chain_section_includes_portfolio_risk_summary(self):
        gen = BriefingGenerator()
        pick = _make_pick(symbol="300750", name="宁德时代", rating="buy_candidate")
        portfolio_summary = PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=0,
            keep_count=1,
            top_focus=("300750 宁德时代",),
            watchlist=(),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="测试",
            portfolio_risk_lines=(
                "组合集中度 HHI 0.040，有效持仓 25.0",
                "最大单票 20.0%，现金留存 80.0%",
            ),
        )

        section = gen._build_main_chain_section([pick], portfolio_summary, [])

        assert (
            "- 组合风险: 组合集中度 HHI 0.040，有效持仓 25.0；最大单票 20.0%，现金留存 80.0%"
            in section.content
        )

    def test_evidence_section_shows_strategies(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(
                metrics={
                    "cross_market_primary_theme": "海外物理AI叙事升温",
                    "cross_market_linkage_basis": "产业映射",
                    "cross_market_action": "重点跟踪",
                    "cross_market_lead_window": "隔夜-3日",
                    "cross_market_observation_window": "2-5日",
                    "cross_market_validation_signals": (
                        "机器人龙头放量上攻且核心零部件同步走强",
                    ),
                    "cross_market_invalidation_signals": (
                        "只有海外叙事但A股机器人板块不共振",
                    ),
                }
            )
        ]
        briefing = gen.generate(picks=picks, frames={})
        evidence_sec = next(s for s in briefing.sections if s.title == "候选来龙去脉")
        assert "600519" in evidence_sec.content
        assert "momentum" in evidence_sec.content
        assert (
            "跨市场线索: 纸面复核｜海外物理AI叙事升温｜观察窗 2-5日"
            in evidence_sec.content
        )
        assert (
            "传导链条: 产业映射｜领先窗 隔夜-3日｜确认 机器人龙头放量上攻且核心零部件同步走强｜失效 只有海外叙事但A股机器人板块不共振"
            in evidence_sec.content
        )
        assert "动量突破MA20" in evidence_sec.content
        assert "风险提示: 高位震荡风险" in evidence_sec.content
        assert "- 风险:" not in evidence_sec.content

    def test_evidence_section_includes_price_path_context(self):
        gen = BriefingGenerator()
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2026-01-01", periods=20).strftime("%Y-%m-%d"),
                "close": [10.0 + index for index in range(20)],
                "high": [10.5 + index for index in range(20)],
                "low": [9.5 + index for index in range(20)],
                "volume": [1000.0 + index * 10 for index in range(20)],
            }
        )

        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", name="贵州茅台")],
            frames={"600519": frame},
        )

        evidence_sec = next(s for s in briefing.sections if s.title == "候选来龙去脉")
        assert "- 量价路径:" in evidence_sec.content
        assert "5日收益" in evidence_sec.content
        assert "20日收益" in evidence_sec.content

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
        assert "今日无纸面复核对象" in next_sec.content
        assert "观察名单" in next_sec.content
        assert "待阻塞解除后再考虑转入纸面复核名单" in next_sec.content
        assert "600519" in next_sec.content
        assert "avoid" not in next_sec.content

    def test_next_day_section_excludes_watch_picks(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", rating="watch")],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "今日无纸面复核对象" in next_sec.content
        assert "观察名单" in next_sec.content
        assert "待阻塞解除后再考虑转入纸面复核名单" in next_sec.content
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
        assert "观察名单: 600519 贵州茅台(观察阻塞)" in next_sec.content

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
        assert "阻塞: T+1 未解除" in next_sec.content
        assert "下一步: 明日解除 T+1 后，优先再看开盘承接与流动性" in next_sec.content
        assert "复核窗口: 高优先级 / 明日开盘前后" in next_sec.content

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
        assert "观察名单" in next_day
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
    def test_bear_role_uses_candidate_specific_short_term_metrics(self):
        agent = AShareDebateAgent(role=AgentRole.BEAR)
        frame = pd.DataFrame({"close": [10 + index * 0.01 for index in range(30)]})
        strong = _make_pick(
            symbol="AAA",
            score=72,
            metrics={"ret5_pct": 4.2, "bias20_pct": 2.0},
            risks=(),
        )
        weak = _make_pick(
            symbol="BBB",
            score=72,
            metrics={"ret5_pct": -4.2, "bias20_pct": 2.0},
            risks=(),
        )
        assert agent.generate_initial_opinion(strong, frame).stance == "neutral"
        assert agent.generate_initial_opinion(weak, frame).stance == "bearish"

    def test_shared_data_blocker_does_not_create_candidate_bear_vote(self):
        agent = AShareDebateAgent(role=AgentRole.BEAR)
        frame = pd.DataFrame({"close": [10 + index * 0.01 for index in range(30)]})
        common_blocker = _make_pick(
            symbol="AAA",
            score=72,
            metrics={"ret5_pct": 2.0, "ret20_pct": 5.0},
            risks=("盘中覆盖不完整，缺少: 000300",),
        )
        weak = _make_pick(
            symbol="BBB",
            score=72,
            metrics={"ret5_pct": 2.0, "ret20_pct": -2.5},
            risks=("盘中覆盖不完整，缺少: 000300",),
        )
        assert agent.generate_initial_opinion(common_blocker, frame).stance == "neutral"
        assert agent.generate_initial_opinion(weak, frame).stance == "bearish"

    def test_strong_buy_rating_is_not_misclassified_as_st_risk(self):
        agent = AShareDebateAgent(role=AgentRole.RISK_CONTROL)
        opinion = agent.generate_initial_opinion(
            _make_pick(
                symbol="000001",
                name="平安银行",
                score=75.5,
                rating="strong_buy_candidate",
                risks=(),
            ),
            pd.DataFrame({"close": [10 + index * 0.01 for index in range(30)]}),
        )

        rendered = " ".join((*opinion.arguments, *opinion.risk_factors))
        assert "ST股风险" not in rendered
        assert "ST股：存在退市风险" not in rendered

    def test_st_name_still_emits_explicit_st_risk(self):
        agent = AShareDebateAgent(role=AgentRole.RISK_CONTROL)
        opinion = agent.generate_initial_opinion(
            _make_pick(name="*ST示例", rating="watch"),
            pd.DataFrame({"close": [10 + index * 0.01 for index in range(30)]}),
        )

        rendered = " ".join((*opinion.arguments, *opinion.risk_factors))
        assert "ST股风险" in rendered
        assert "ST股：存在退市风险" in rendered

    def test_cross_market_agent_does_not_invent_overseas_claim_when_news_is_empty(self):
        agent = AShareDebateAgent(
            role=AgentRole.CROSS_MARKET,
            enable_llm=False,
            language="zh-CN",
        )
        opinion = agent.generate_initial_opinion(
            _make_pick(score=72),
            pd.DataFrame({"close": [10 + i for i in range(30)]}),
            market_context_lines=(
                "消息状态: 部分可用",
                "消息结果: 无可用新闻记录",
            ),
        )

        rendered = " ".join(
            (*opinion.arguments, *opinion.risk_factors, *opinion.opportunity_factors)
        )
        assert "海外叙事未必立刻传到A股" not in rendered
        assert "跨市场线索存在，但仍需确认是否形成A股主线接力" not in rendered
        assert "无可用消息或规则传导证据" in rendered

    def test_debate_watch_items_ignore_overseas_risk_without_cross_market_evidence(
        self,
    ):
        coordinator = AShareDebateCoordinator(roles=(AgentRole.CROSS_MARKET,))
        result = DebateResult(
            debate_id="d1",
            symbol="600519",
            name="贵州茅台",
            original_score=72.0,
            rating="A",
            market_context_lines=(
                "海外风险: 偏多（正面 1 / 负面 0）",
                "消息结果: 无可用新闻记录",
            ),
        )

        items = coordinator._build_watch_items([], result)

        assert "核对海外风险线索是否延续，避免隔夜外盘噪音误导。" not in items

    def test_cross_market_agent_uses_structured_transmission_chain(self):
        agent = AShareDebateAgent(
            role=AgentRole.CROSS_MARKET,
            enable_llm=False,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})

        opinion = agent.generate_initial_opinion(
            _make_pick(
                score=72,
                metrics={
                    "cross_market_primary_theme": "海外物理AI叙事升温",
                    "cross_market_linkage_basis": "产业映射",
                    "cross_market_action": "优先复核",
                    "cross_market_source_quality_label": "多源/权威媒体",
                    "cross_market_source_quality_score": 3,
                    "cross_market_lead_window": "隔夜-3日",
                    "cross_market_observation_window": "2-5日",
                    "cross_market_first_order_targets": (
                        "机器人整机",
                        "丝杠/减速器",
                        "传感器",
                    ),
                    "cross_market_second_order_targets": (
                        "工控",
                        "机器视觉",
                        "伺服",
                    ),
                    "cross_market_execution_watchpoints": ("机器人龙头放量强度",),
                    "cross_market_validation_signals": (
                        "机器人龙头放量上攻且核心零部件同步走强",
                    ),
                    "cross_market_invalidation_signals": (
                        "只有海外叙事但A股机器人板块不共振",
                    ),
                    "cross_market_support_event_count": 2,
                    "cross_market_conflict_event_count": 1,
                    "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
                },
            ),
            df,
        )

        assert opinion.stance == "bullish"
        assert "传导类型 产业映射，领先窗 隔夜-3日" in opinion.arguments
        assert "来源质量 多源/权威媒体，跨市主线可信度更高" in opinion.arguments
        assert "先看A股先手链条: 机器人整机、丝杠/减速器、传感器" in opinion.arguments
        assert "若扩散到 工控、机器视觉，持续性更强" in opinion.arguments
        assert "盘中先盯 机器人龙头放量强度" in opinion.arguments
        assert "跨市证据堆栈: 同向 2 条｜反向 1 条" in opinion.arguments
        assert "同向证据 2 条，海外主题不是单点脉冲。" in opinion.arguments
        assert "先看 机器人龙头放量上攻且核心零部件同步走强" in opinion.arguments
        assert "⚠️ 反向证据 1 条，跨市强化链条已出现分歧" in opinion.risk_factors
        assert "⚠️ 失效条件: 只有海外叙事但A股机器人板块不共振" in opinion.risk_factors
        assert "✅ 来源质量较高: 多源/权威媒体" in opinion.opportunity_factors
        assert "✅ 同向证据 2 条，海外主题连续强化" in opinion.opportunity_factors
        assert (
            "✅ 验证重点: 机器人龙头放量上攻且核心零部件同步走强"
            in opinion.opportunity_factors
        )

    def test_cross_market_agent_turns_bearish_when_conflicts_outweigh_support(self):
        agent = AShareDebateAgent(
            role=AgentRole.CROSS_MARKET,
            enable_llm=False,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})

        opinion = agent.generate_initial_opinion(
            _make_pick(
                score=72,
                metrics={
                    "cross_market_primary_theme": "海外物理AI叙事升温",
                    "cross_market_action": "重点跟踪",
                    "cross_market_priority_score": 2,
                    "cross_market_support_event_count": 1,
                    "cross_market_conflict_event_count": 2,
                    "cross_market_evidence_stack_summary": "同向 1 条｜反向 2 条",
                },
            ),
            df,
        )

        assert opinion.stance == "bearish"

    @pytest.mark.parametrize("role", [AgentRole.BEAR, AgentRole.RISK_CONTROL])
    def test_explicit_pick_risks_enter_risk_vote_and_risk_evidence(self, role):
        agent = AShareDebateAgent(role=role, enable_llm=False, language="zh-CN")
        risk = "PCB涨价压缩下游利润，短线需防高开回落"

        opinion = agent.generate_initial_opinion(
            _make_pick(score=72, risks=(risk,)),
            pd.DataFrame({"close": [10 + i for i in range(30)]}),
        )

        assert opinion.stance == "bearish"
        rendered = " ".join((*opinion.arguments, *opinion.risk_factors))
        assert risk in rendered
        if role == AgentRole.RISK_CONTROL:
            assert "候选明确风险:" in rendered
            assert "未提供额外风控证据" not in rendered

    def test_cross_market_metadata_without_evidence_stays_neutral_and_unpublished(
        self,
    ):
        agent = AShareDebateAgent(
            role=AgentRole.CROSS_MARKET,
            enable_llm=False,
            language="zh-CN",
        )
        pick = _make_pick(
            score=72,
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "优先复核",
                "cross_market_priority_score": 3,
                "cross_market_first_order_targets": ("机器人整机",),
                "cross_market_validation_signals": ("板块放量共振",),
            },
        )

        opinion = agent.generate_initial_opinion(
            pick,
            pd.DataFrame({"close": [10 + i for i in range(30)]}),
        )

        assert opinion.stance == "neutral"
        rendered = " ".join(
            (*opinion.arguments, *opinion.risk_factors, *opinion.opportunity_factors)
        )
        assert "无可用跨市消息或规则传导，不据此形成判断" in rendered
        assert "海外主线已映射到A股方向" not in rendered
        assert "传导动作 优先复核" not in rendered
        assert "验证重点: 板块放量共振" not in rendered

    def test_bear_agent_turns_bearish_for_high_score_when_invalidation_present(self):
        agent = AShareDebateAgent(
            role=AgentRole.BEAR,
            enable_llm=False,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})

        opinion = agent.generate_initial_opinion(
            _make_pick(
                score=72,
                metrics={
                    "cross_market_primary_theme": "海外芯片限制升级",
                    "cross_market_pressure_targets": ("苹果链", "出口代工"),
                    "cross_market_invalidation_signals": (
                        "只有消息刺激但半导体设备材料不扩散",
                    ),
                    "cross_market_conflict_event_count": 1,
                },
            ),
            df,
        )

        assert opinion.stance == "bearish"
        assert "失效条件已明确: 只有消息刺激但半导体设备材料不扩散" in opinion.arguments
        assert "当前承压方向包括 苹果链、出口代工" in opinion.arguments
        assert "⚠️ 失效条件: 只有消息刺激但半导体设备材料不扩散" in opinion.risk_factors

    def test_sector_and_risk_agents_consume_cross_market_validation_and_pressure(self):
        sector_agent = AShareDebateAgent(
            role=AgentRole.SECTOR_LEADER,
            enable_llm=False,
            language="zh-CN",
        )
        risk_agent = AShareDebateAgent(
            role=AgentRole.RISK_CONTROL,
            enable_llm=False,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})
        pick = _make_pick(
            score=72,
            metrics={
                "cross_market_primary_theme": "海外供给收缩映射",
                "cross_market_first_order_targets": (
                    "存储",
                    "半导体材料",
                    "先进封装",
                ),
                "cross_market_second_order_targets": ("PCB", "覆铜板", "面板"),
                "cross_market_pressure_targets": ("消费电子代工", "下游整机"),
                "cross_market_validation_signals": (
                    "存储与半导体材料同步放量而非单一环节独涨",
                ),
                "cross_market_invalidation_signals": ("只有消息刺激但存储材料不扩散",),
            },
        )

        sector_opinion = sector_agent.generate_initial_opinion(pick, df)
        risk_opinion = risk_agent.generate_initial_opinion(pick, df)

        assert (
            "先看 存储、半导体材料、先进封装 是否同步走强" in sector_opinion.arguments
        )
        assert "若扩散到 PCB、覆铜板，板块持续性更强" in sector_opinion.arguments
        assert (
            "同时观察 消费电子代工、下游整机 是否承压让位" in sector_opinion.arguments
        )
        assert "✅ 轮动观察: 消费电子代工" in sector_opinion.opportunity_factors
        assert risk_opinion.stance == "bearish"
        assert (
            "一旦出现 只有消息刺激但存储材料不扩散，应取消纸面复核"
            in risk_opinion.arguments
        )
        assert "⚠️ 承压方向: 消费电子代工、下游整机" in risk_opinion.risk_factors

    def test_default_debate_roles_use_relevant_context_instead_of_neutral_collapse(
        self,
    ):
        coordinator = AShareDebateCoordinator(enable_llm=False, max_rounds=2)
        pick = _make_pick(
            score=72,
            metrics={
                "cross_market_first_order_targets": ("机器人整机",),
                "cross_market_support_event_count": 2,
                "cross_market_conflict_event_count": 0,
            },
        )
        frame = pd.DataFrame({"close": [100.0 + i for i in range(20)]})

        result = coordinator.run_debate(
            pick,
            frame,
            market_context_lines=(
                "政策跟踪: 工信部支持机器人产业链落地。",
                "融资情绪: 杠杆拥挤，融资余额下降。",
                "北向资金: 净流出，外资风险偏好回落。",
                "全局雷达: 全市场偏空，情绪退潮。",
            ),
        )

        counts = {
            stance: sum(value == stance for value in result.final_vote.values())
            for stance in ("bullish", "bearish", "neutral")
        }
        assert counts["bullish"] >= 2
        assert counts["bearish"] >= 3
        assert counts["neutral"] < 8
        assert result.final_vote[AgentRole.POLICY_SENSITIVE] == "bullish"
        assert result.final_vote[AgentRole.MARGIN_TRADING] == "bearish"
        assert result.final_vote[AgentRole.NORTHBOUND] == "bearish"
        assert result.final_vote[AgentRole.RETAIL_MOOD] == "bearish"

    def test_context_only_roles_stay_neutral_without_role_specific_evidence(self):
        frame = pd.DataFrame({"close": [100.0 + i for i in range(20)]})
        pick = _make_pick(score=72, metrics={})
        for role in (
            AgentRole.POLICY_SENSITIVE,
            AgentRole.MARGIN_TRADING,
            AgentRole.NORTHBOUND,
            AgentRole.RETAIL_MOOD,
        ):
            opinion = AShareDebateAgent(
                role, enable_llm=False
            ).generate_initial_opinion(
                pick, frame, market_context_lines=("普通市场摘要: 暂无对应数据。",)
            )
            assert opinion.stance == "neutral"

    def test_sector_role_stays_neutral_when_targets_have_no_evidence(self):
        agent = AShareDebateAgent(AgentRole.SECTOR_LEADER, enable_llm=False)
        opinion = agent.generate_initial_opinion(
            _make_pick(
                score=72,
                metrics={
                    "cross_market_first_order_targets": ("机器人整机",),
                    "cross_market_second_order_targets": ("传感器",),
                },
            ),
            pd.DataFrame({"close": [100.0 + i for i in range(20)]}),
        )

        assert opinion.stance == "neutral"

    def test_two_sided_debate_preserves_real_bear_and_score_boundary(self):
        coordinator = AShareDebateCoordinator(
            enable_llm=False,
            max_rounds=2,
            roles=(AgentRole.BULL, AgentRole.BEAR),
        )
        pick = _make_pick(score=72, metrics={})
        result = coordinator.run_debate(
            pick,
            pd.DataFrame({"close": [100.0 + i for i in range(20)]}),
            signal_date=pick.date,
        )

        assert result.final_vote[AgentRole.BULL] == "bullish"
        assert result.final_vote[AgentRole.BEAR] == "bearish"
        assert any(
            record for opinion in result.rounds[-1].opinions
            for record in opinion.rebuttal_records
        )
        assert "missing_real_opposition" not in result.failure
        assert result.recommended_adjustment == "keep"
        assert result.adjustment_weight == 0.0
        assert result.adjusted_score == pick.score
        assert result.deterministic_score == pick.score
        assert result.deterministic_score_unchanged is True

    def test_one_sided_debate_blocks_verdict_and_preserves_reliability(self):
        coordinator = AShareDebateCoordinator(
            enable_llm=False,
            max_rounds=2,
            roles=(AgentRole.BULL, AgentRole.BEAR),
        )
        pick = _make_pick(score=72, risks=(), metrics={})
        result = coordinator.run_debate(
            pick,
            pd.DataFrame({"close": [100.0 + i for i in range(20)]}),
            signal_date=pick.date,
        )

        assert result.final_vote[AgentRole.BULL] == "bullish"
        assert result.final_vote[AgentRole.BEAR] == "neutral"
        assert "未形成真实正反方交锋" in result.research_verdict
        assert result.recommended_adjustment == "keep"
        assert result.adjustment_weight == 0.0
        assert result.adjusted_score == pick.score
        assert result.role_reliability_lines
        assert "missing_real_opposition" in result.failure

    def test_llm_enhancement_keeps_deterministic_points_and_records_advisory(self):
        agent = AShareDebateAgent(
            role=AgentRole.BULL,
            enable_llm=True,
            language="zh-CN",
        )
        deterministic_agent = AShareDebateAgent(
            role=AgentRole.BULL,
            enable_llm=False,
            language="zh-CN",
        )
        df = pd.DataFrame({"close": [10 + i for i in range(30)]})
        deterministic = deterministic_agent.generate_initial_opinion(
            _make_pick(score=72), df
        )

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
        assert opinion.arguments == deterministic.arguments
        assert opinion.risk_factors == deterministic.risk_factors
        assert opinion.opportunity_factors == deterministic.opportunity_factors
        assert opinion.llm_advisory_points == (
            "论点: 趋势主升结构仍在扩张",
            "论点: 放量后承接仍然健康",
            "风险: 高位分歧后波动会放大",
            "机会: 强趋势延续时容易走加速段",
        )

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

        deterministic = AShareDebateAgent(
            role=AgentRole.NORTHBOUND,
            enable_llm=False,
            language="zh-CN",
        ).generate_initial_opinion(_make_pick(score=66), df)
        assert opinion.arguments == deterministic.arguments
        assert opinion.risk_factors == deterministic.risk_factors
        assert opinion.opportunity_factors == deterministic.opportunity_factors
        assert opinion.llm_advisory_points == (
            "论点: 北向偏好延续",
            "机会: 外资增配可能持续",
        )
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
        mock_notify.assert_not_called()

    @patch("aqsp.briefing.notifier.dispatch_notification_once")
    def test_returns_default_notifier_results(self, mock_dispatch):
        briefing = Briefing(date="d", sections=[])
        mock_dispatch.return_value = [
            MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
        ]

        results = send_briefing(briefing)

        mock_dispatch.assert_called_once()
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
        assert "需人工复核" in body

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
        assert "有观察名单，今日无纸面复核对象" in summary
        assert "观察名单" in summary

    def test_next_day_section_mentions_watchlist_when_no_tradable_pick(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[_make_pick(symbol="600519", name="贵州茅台", rating="watch")],
            frames={},
        )
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert "观察名单" in next_sec.content
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

        assert "- 观察名单: 600519 贵州茅台" in summary
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

        assert "- 观察名单: 600519 贵州茅台(新晋)(8.5分)" in summary
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
            "- 下一步: 600519 贵州茅台 | 等待量价继续走强后，再评估是否转入纸面复核名单"
            in summary
        )
        assert "- 复核窗口: 600519 贵州茅台 | 高优先级 / 盘中走强后" in summary

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
            "- 下一步: 600519 贵州茅台 | 明日解除 T+1 后，优先再看开盘承接与流动性"
            in summary
        )
        assert "- 复核窗口: 600519 贵州茅台 | 高优先级 / 明日开盘前后" in summary

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
        assert "- 阻塞: 板块集中度过高，压低新能源暴露" in evidence_sec.content
        assert (
            "- 下一步: 等待板块暴露回落后，再重新评估纸面复核优先级"
            in evidence_sec.content
        )
        assert "- 复核窗口: 中优先级 / 板块分化时" in evidence_sec.content

    def test_core_items_render_structured_portfolio_summary(self):
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=1,
                downgrade_count=1,
                keep_count=0,
                top_focus=("600519 贵州茅台",),
                watchlist=("300750 宁德时代(观察名单)",),
                allocations=(),
                cash_reserve=0.2,
                allocation_note="单票上限 20%；信号强度不足时提高现金留存",
                regime_label="稳定上涨",
                strategy_mix_name="进攻牛市",
                strategy_mix_description="稳定上涨期，重仓动量+涨停板",
                strategy_focus=("动量趋势", "涨停接力"),
                strategy_weights=(("momentum", 0.3), ("limit_up_ladder", 0.3)),
                cross_market_overview="海外物理AI叙事升温，纸面复核 300750 宁德时代",
                cross_market_focus=("300750 宁德时代 | 海外物理AI叙事升温(纸面复核)",),
                debate_focus=("300750 宁德时代 | 倾向优先纸面复核，主因 技术面强势",),
                debate_support_points=("300750 宁德时代 | 量价共振且跨市主线仍在扩散",),
                debate_opposition_points=(
                    "300750 宁德时代 | 若高开过猛则追高回撤风险放大",
                ),
                debate_watch_items=("300750 宁德时代 | 先确认开盘承接与量价延续",),
                debate_risk_gates=("300750 宁德时代 | 追高回撤风险",),
                debate_next_triggers=("300750 宁德时代 | 先确认开盘承接与量价延续",),
                debate_priority_queue=(
                    "300750 宁德时代 | 倾向优先纸面复核，主因 技术面强势 | 先确认开盘承接与量价延续 | 卡点 追高回撤风险",
                ),
                action_hotspots=("板块集中度过高，压低新能源暴露",),
                execution_blockers=("300750 宁德时代: 板块集中度过高，压低新能源暴露",),
            ),
        )

        summary = briefing.generate_smart_summary()

        assert "今日结论: 上调 1 / 降级 1 / 维持 0" in summary
        assert "- 当前市况: 稳定上涨" in summary
        assert "- 跨市主线: 海外物理AI叙事升温，纸面复核 300750 宁德时代" in summary
        assert "- 策略偏向: 进攻牛市" in summary
        assert "- 主看名单: 600519 贵州茅台" in summary
        assert (
            "- 讨论顺序: 300750 宁德时代 | 倾向优先纸面复核，主因 技术面强势 | 先确认开盘承接与量价延续 | 卡点 追高回撤风险"
            in summary
        )
        assert "- 观察名单: 300750 宁德时代" in summary
        assert "- 跨市焦点: 300750 宁德时代 | 海外物理AI叙事升温(纸面复核)" in summary
        assert (
            "- 讨论焦点: 300750 宁德时代 | 倾向优先纸面复核，主因 技术面强势" in summary
        )
        assert "- 讨论支持: 300750 宁德时代 | 量价共振且跨市主线仍在扩散" in summary
        assert "- 讨论反对: 300750 宁德时代 | 若高开过猛则追高回撤风险放大" in summary
        assert "- 讨论待确认: 300750 宁德时代 | 先确认开盘承接与量价延续" in summary
        assert "- 讨论卡点: 300750 宁德时代 | 追高回撤风险" in summary
        assert "- 讨论触发: 300750 宁德时代 | 先确认开盘承接与量价延续" in summary
        assert "- 待确认: 板块集中度过高，压低新能源暴露" in summary
        assert "- 现金留存: 20%" in summary
        assert "- 阻塞: 300750 宁德时代: 板块集中度过高，压低新能源暴露" in summary

    def test_smart_summary_labels_strategy_values_as_score_multipliers(self):
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=0,
                downgrade_count=0,
                keep_count=1,
                top_focus=(),
                watchlist=(),
                allocations=(),
                cash_reserve=1.0,
                allocation_note="",
                strategy_weights=(
                    ("low_vol_trend", 0.97),
                    ("ma_pullback", 0.97),
                ),
            ),
        )

        summary = briefing.generate_smart_summary()

        assert "- 市况评分倍率: low_vol_trend ×0.97、ma_pullback ×0.97" in summary
        assert "97%" not in summary

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
        assert "仓位参考" in main_chain_sec.content
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
            "- 仓位参考: 300750 宁德时代 30% | 主链评分 72.0；强信号优先分配" in summary
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
        assert "策略偏向: 进攻牛市" in main_chain_sec.content
        assert "稳定上涨期，重仓动量+涨停板" in main_chain_sec.content
        assert (
            "更偏好这些方向: 低波趋势、均线回踩、N 字反弹、RPS 动量"
            in main_chain_sec.content
        )

    def test_main_chain_section_only_surfaces_runtime_market_context_summary(self):
        gen = BriefingGenerator()
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="strong_buy_candidate",
        )

        briefing = gen.generate(
            picks=[pick],
            frames={},
            regime="stable_bull",
            market_context_lines=(
                "运行判定: HMM 牛市 | 置信度 72% | 年化波动 15.9% | 映射 稳定上涨",
                "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
            ),
        )
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")

        assert (
            "运行判定: HMM 牛市 | 置信度 72% | 年化波动 15.9% | 映射 稳定上涨"
            in main_chain_sec.content
        )
        assert (
            "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。"
            not in main_chain_sec.content
        )

    def test_main_chain_section_frontloads_cross_market_priority_digest(self):
        from aqsp.briefing.debate import DebateResult

        gen = BriefingGenerator()
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="strong_buy_candidate",
        )
        portfolio_summary = PortfolioDecisionSummary(
            promote_count=1,
            downgrade_count=0,
            keep_count=0,
            top_focus=("300750 宁德时代",),
            watchlist=(),
            allocations=(),
            cash_reserve=0.2,
            allocation_note="单票上限 20%；信号强度不足时提高现金留存",
            cross_market_overview="海外物理AI叙事升温，纸面复核 300750 宁德时代",
            cross_market_focus=("300750 宁德时代 | 海外物理AI叙事升温(纸面复核)",),
        )
        debate_result = DebateResult(
            debate_id="d1",
            symbol="300750",
            name="宁德时代",
            original_score=72.0,
            rating="buy_candidate",
            recommended_adjustment="raise",
            disagreement_score=0.42,
            final_vote={
                AgentRole.BULL: "bullish",
                AgentRole.RISK_CONTROL: "neutral",
                AgentRole.CROSS_MARKET: "bullish",
            },
            market_context_lines=(
                "传导推演[强]: 海外物理AI叙事升温 -> A股机器人、传感器、丝杠、减速器、工控链；动作 优先复核；观察窗 2-5日；同向 2 条｜反向 1 条。",
                "确认信号: 机器人龙头放量上攻且核心零部件同步走强",
                "失效条件: 只有海外叙事但A股机器人板块不共振",
            ),
        )

        main_chain_sec = gen._build_main_chain_section(
            [pick],
            portfolio_summary,
            [debate_result],
        )

        assert "- 跨市判断:" not in main_chain_sec.content
        assert "- 委员会结论:" in main_chain_sec.content

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
        assert "委员会阻塞" in summary
        assert "结论已阻断：缺少可核验证据" in summary
        assert "下调" not in summary
        assert "6.8" not in summary
        assert "不改写系统评分" not in summary
        assert "多个观点说" not in summary
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
        assert "委员会阻塞" in summary
        assert "结论已阻断：缺少可核验证据" in summary
        assert "70%" not in summary

    def test_smart_summary_includes_debate_active_roles(self):
        from aqsp.briefing.debate import DebateResult

        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            debate_results=[
                DebateResult(
                    debate_id="test",
                    symbol="300750",
                    name="宁德时代",
                    original_score=72.0,
                    rating="buy_candidate",
                    recommended_adjustment="raise",
                    adjusted_score=78.0,
                    disagreement_score=0.42,
                    final_vote={
                        AgentRole.BULL: "bullish",
                        AgentRole.RISK_CONTROL: "neutral",
                        AgentRole.CROSS_MARKET: "bullish",
                    },
                ),
            ],
        )

        summary = briefing.generate_smart_summary()

        assert "委员会阻塞" in summary
        assert "- 讨论视角: 技术多头、风险控制、跨市传导" not in summary

    def test_briefing_schema_debate_points_include_active_roles(self):
        from aqsp.briefing.debate import DebateResult
        from aqsp.briefing.schema import BriefingData, RegimeInfo

        data = BriefingData(
            date="2026-05-27 10:00",
            picks=(),
            regime_info=RegimeInfo(
                regime="stable_bull",
                description="稳定上涨",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=None,
            debate_results=(
                DebateResult(
                    debate_id="test",
                    symbol="300750",
                    name="宁德时代",
                    original_score=72.0,
                    rating="buy_candidate",
                    recommended_adjustment="raise",
                    adjusted_score=78.0,
                    disagreement_score=0.42,
                    final_vote={
                        AgentRole.BULL: "bullish",
                        AgentRole.RISK_CONTROL: "neutral",
                        AgentRole.CROSS_MARKET: "bullish",
                    },
                ),
            ),
        )

        assert data.debate_points == [
            "委员会阻塞: 宁德时代(300750) 结论已阻断：缺少可核验证据"
        ]

    def test_briefing_to_markdown_uses_committee_heading_for_debate_section(self):
        from aqsp.briefing.debate import DebateResult

        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[],
            debate_results=[
                DebateResult(
                    debate_id="test",
                    symbol="300750",
                    name="宁德时代",
                    original_score=72.0,
                    rating="buy_candidate",
                    recommended_adjustment="raise",
                    adjusted_score=78.0,
                    disagreement_score=0.42,
                    final_consensus="趋势强但仍需确认开盘承接",
                    role_selection_summary="因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。",
                ),
            ],
        )

        markdown = briefing.to_markdown()

        assert "## 多 Agent 结论" in markdown
        assert "委员会结论摘要：" in markdown
        assert "结论已阻断：缺少可核验证据" in markdown
        assert "选角理由: 因海外传导、分歧校验" not in markdown
        assert "## 不同看法" not in markdown
        assert "不同看法结论：" not in markdown

    def test_renderer_main_chain_uses_human_debate_adjustment_labels(self):
        from aqsp.briefing.debate import DebateResult

        gen = BriefingGenerator()
        pick = _make_pick(symbol="300750", name="宁德时代", rating="watch")
        portfolio_summary = PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=0,
            keep_count=1,
            top_focus=("300750 宁德时代",),
            watchlist=(),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="测试",
        )
        debate_result = DebateResult(
            debate_id="test",
            symbol="300750",
            name="宁德时代",
            original_score=72.0,
            rating="buy_candidate",
            recommended_adjustment="raise",
            adjusted_score=78.0,
            disagreement_score=0.42,
            final_consensus="趋势强但仍需确认开盘承接",
        )

        section = gen._build_main_chain_section(
            [pick],
            portfolio_summary,
            [debate_result],
        )

        assert "- 委员会结论:" in section.content
        assert "300750 宁德时代: 结论已阻断：缺少可核验证据" in section.content

    def test_renderer_smart_summary_includes_debate_active_roles(self):
        from aqsp.briefing.debate import DebateResult
        from aqsp.briefing.schema import BriefingData, RegimeInfo

        renderer = MarkdownRenderer()
        data = BriefingData(
            date="2026-05-27 10:00",
            picks=(),
            regime_info=RegimeInfo(
                regime="stable_bull",
                description="稳定上涨",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=None,
            debate_results=(
                DebateResult(
                    debate_id="test",
                    symbol="300750",
                    name="宁德时代",
                    original_score=72.0,
                    rating="buy_candidate",
                    recommended_adjustment="raise",
                    adjusted_score=78.0,
                    disagreement_score=0.42,
                    final_vote={
                        AgentRole.BULL: "bullish",
                        AgentRole.RISK_CONTROL: "neutral",
                        AgentRole.CROSS_MARKET: "bullish",
                    },
                    research_verdict="倾向优先纸面复核，先确认开盘承接。",
                    role_selection_summary="因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。",
                    role_selection_plan="技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交；跨市传导看海外催化到A股映射。",
                ),
            ),
        )

        summary = renderer.generate_smart_summary(data)

        assert "- 讨论视角: 技术多头、风险控制、跨市传导" in summary
        assert (
            "- 选角理由: 因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。"
            in summary
        )
        assert (
            "- 角色分工: 技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交；跨市传导看海外催化到A股映射。"
            in summary
        )
        assert "- 委员会覆盖: 已分析 1 只重点候选" in summary
        assert summary.index("- 委员会覆盖: 已分析 1 只重点候选") < summary.index(
            "- 选角理由:"
        )

    def test_renderer_main_chain_includes_debate_active_roles(self):
        from aqsp.briefing.debate import DebateResult
        from aqsp.briefing.schema import BriefingData, Pick, RegimeInfo

        renderer = MarkdownRenderer()
        data = BriefingData(
            date="2026-05-27 10:00",
            picks=(
                Pick.from_pick_result(
                    _make_pick(symbol="300750", name="宁德时代", rating="watch")
                ),
            ),
            regime_info=RegimeInfo(
                regime="stable_bull",
                description="稳定上涨",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=0,
                downgrade_count=0,
                keep_count=1,
                top_focus=("300750 宁德时代",),
                watchlist=(),
                allocations=(),
                cash_reserve=1.0,
                allocation_note="测试",
            ),
            debate_results=(
                DebateResult(
                    debate_id="test",
                    symbol="300750",
                    name="宁德时代",
                    original_score=72.0,
                    rating="buy_candidate",
                    recommended_adjustment="raise",
                    adjusted_score=78.0,
                    disagreement_score=0.42,
                    final_vote={
                        AgentRole.BULL: "bullish",
                        AgentRole.RISK_CONTROL: "neutral",
                        AgentRole.CROSS_MARKET: "bullish",
                    },
                    research_verdict="倾向优先纸面复核，先确认开盘承接。",
                    role_selection_summary="因海外传导、分歧校验，本轮先看 技术多头、风险控制、跨市传导。",
                    role_selection_plan="技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交；跨市传导看海外催化到A股映射。",
                ),
            ),
        )

        markdown = renderer.render(data)

        assert "## 多 Agent 结论" in markdown
        assert "重点候选的委员会结论如下：" in markdown
        assert "# 多 Agent 结论 - 300750 宁德时代" in markdown
        assert "- 讨论视角: 技术多头、风险控制、跨市传导" in markdown

    def test_renderer_puts_committee_conclusion_before_structured_process(self):
        from aqsp.briefing.debate import AgentOpinion, DebateRound
        from aqsp.briefing.schema import BriefingData, RegimeInfo

        result = DebateResult(
            debate_id="test-readability",
            symbol="300750",
            name="宁德时代",
            original_score=72.0,
            rating="buy_candidate",
            related_signal_date="2026-07-14",
            research_verdict="优先纸面观察，先确认开盘承接。",
            final_vote={
                AgentRole.BULL: "bullish",
                AgentRole.RISK_CONTROL: "neutral",
            },
            support_points=("量价共振仍在",),
            opposition_points=("高开后可能回撤",),
            risk_warnings=["追高风险"],
            pending_confirmations=("失效条件: 开盘承接转弱",),
            rounds=[
                DebateRound(
                    round_num=1,
                    summary="技术与风控完成交叉校验",
                    opinions=[
                        AgentOpinion(
                            agent_id="bull",
                            role=AgentRole.BULL,
                            stance="bullish",
                            confidence=0.8,
                            llm_advisory_points=("不应出现在页面的原始话术",),
                        ),
                        AgentOpinion(
                            agent_id="risk",
                            role=AgentRole.RISK_CONTROL,
                            stance="neutral",
                            confidence=0.6,
                        ),
                    ],
                )
            ],
        )
        data = BriefingData(
            date="2026-07-14",
            picks=(),
            regime_info=RegimeInfo(
                regime="stable_bull",
                description="稳定上涨",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=None,
            debate_results=(result,),
        )

        with patch("aqsp.briefing.renderer.now_shanghai") as mocked_now:
            mocked_now.return_value.date.return_value.isoformat.return_value = (
                "2026-07-14"
            )
            markdown = MarkdownRenderer().render(data)

        assert "## 多 Agent 结论（今日 advisory-only）" in markdown
        assert "- 委员会置信度: **70%**" in markdown
        assert "- 支持理由: 量价共振仍在" in markdown
        assert "- 反对理由: 高开后可能回撤" in markdown
        assert "- 风险: 追高风险" in markdown
        assert "- 失效条件: 开盘承接转弱" in markdown
        assert "- 评分边界: 确定性评分保持不变" in markdown
        assert "## 结构化讨论过程" in markdown
        assert markdown.index("## 多 Agent 结论") < markdown.index("## 结构化讨论过程")
        assert "不应出现在页面的原始话术" not in markdown
        assert "- LLM advisory: 1 个角色有增强内容" in markdown

    def test_renderer_marks_historical_committee_as_archive_not_today_advice(self):
        from aqsp.briefing.schema import BriefingData, RegimeInfo

        result = DebateResult(
            debate_id="test-history",
            symbol="600519",
            name="贵州茅台",
            original_score=60.0,
            rating="watch",
            related_signal_date="2026-07-13",
            final_consensus="历史信号仅供复盘",
        )
        data = BriefingData(
            date="2026-07-13",
            picks=(),
            regime_info=RegimeInfo(
                regime="stable_sideways",
                description="稳定盘整",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=None,
            debate_results=(result,),
        )

        with patch("aqsp.briefing.renderer.now_shanghai") as mocked_now:
            mocked_now.return_value.date.return_value.isoformat.return_value = (
                "2026-07-14"
            )
            markdown = MarkdownRenderer().render(data)

        assert "## 多 Agent 结论（历史归档，非今日建议）" in markdown
        assert "信号日期: 2026-07-13（历史信号，非今日建议）" in markdown
        assert "## 历史后续观察（非今日建议）" in markdown
        assert "## 明日重点" not in markdown
        assert "不代表今日建议" in markdown

    def test_briefing_schema_builds_decision_context_and_artifact_metadata(self):
        from aqsp.briefing.schema import (
            ArtifactMetadata,
            BriefingData,
            Pick,
            RegimeInfo,
        )

        pick = Pick.from_pick_result(
            _make_pick(
                symbol="300750",
                name="宁德时代",
                metrics={
                    "news_catalyst_judgement": "supports",
                    "news_catalyst_lead": "300750 宁德时代 偏多｜订单/需求验证｜中标储能大单",
                    "cross_market_primary_theme": "海外供给收缩映射",
                    "cross_market_action": "重点跟踪",
                    "candidate_blocker": "等待开盘承接确认",
                    "candidate_next_step": "优先看竞价量能",
                    "artifact_ids": ("catalyst:2026-07-07",),
                },
            )
        )
        artifact_a = ArtifactMetadata.from_payload(
            artifact_id="catalyst:2026-07-07",
            artifact_type="catalyst_report",
            generated_at="2026-07-07T09:10:00+08:00",
            payload={"events": [{"title": "中标储能大单"}]},
            sources=("RSSHub-财经",),
            upstream_versions={"horizon": "3e21c04"},
        )
        artifact_b = ArtifactMetadata.from_payload(
            artifact_id="catalyst:2026-07-07-copy",
            artifact_type="catalyst_report",
            generated_at="2026-07-07T09:10:00+08:00",
            payload={"events": [{"title": "中标储能大单"}]},
        )
        data = BriefingData(
            date="2026-07-07",
            picks=(pick,),
            regime_info=RegimeInfo(
                regime="stable_bull",
                description="稳定上涨",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=None,
            artifacts=(artifact_a,),
        )

        assert artifact_a.input_hash == artifact_b.input_hash
        card = data.decision_context_cards[0]
        assert card.news_judgement.startswith("消息支持")
        assert card.cross_market == "重点跟踪｜海外供给收缩映射"
        assert card.artifact_ids == ("catalyst:2026-07-07",)

    def test_markdown_renderer_outputs_decision_context_and_artifacts(self):
        from aqsp.briefing.schema import (
            ArtifactMetadata,
            BriefingData,
            Pick,
            RegimeInfo,
        )

        renderer = MarkdownRenderer()
        pick = Pick.from_pick_result(
            _make_pick(
                symbol="300750",
                name="宁德时代",
                metrics={
                    "news_catalyst_judgement": "opposes",
                    "news_catalyst_lead": "300750 宁德时代 偏空｜监管/合规风险｜被监管问询",
                    "candidate_next_step": "等待问询风险消化",
                    "artifact_ids": ("catalyst:2026-07-07",),
                },
            )
        )
        data = BriefingData(
            date="2026-07-07",
            picks=(pick,),
            regime_info=RegimeInfo(
                regime="stable_bull",
                description="稳定上涨",
                circuit_breaker_triggered=False,
                circuit_breaker_reason="",
            ),
            source_status=None,
            research_summary=None,
            portfolio_summary=None,
            artifacts=(
                ArtifactMetadata(
                    artifact_id="catalyst:2026-07-07",
                    artifact_type="catalyst_report",
                    generated_at="2026-07-07T09:10:00+08:00",
                    sources=("RSSHub-财经",),
                    input_hash="abc123",
                    upstream_versions={"horizon": "3e21c04"},
                ),
            ),
        )

        markdown = renderer.render(data)

        assert "## 候选上下文" in markdown
        assert (
            "- 消息: 消息反对: 300750 宁德时代 偏空｜监管/合规风险｜被监管问询"
            in markdown
        )
        assert "- 证据: catalyst:2026-07-07" in markdown
        assert "## 产物追溯" in markdown
        assert "catalyst:2026-07-07 | catalyst_report" in markdown

    def test_briefing_generator_keeps_news_out_of_candidate_evidence_card(self):
        gen = BriefingGenerator()
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="300750",
                    name="宁德时代",
                    score=16.0,
                    rating="watch",
                    metrics={
                        "news_catalyst_judgement": "supports",
                        "news_catalyst_lead": "300750 宁德时代 偏多｜订单/需求验证｜中标储能大单",
                        "candidate_next_step": "优先看竞价量能",
                    },
                )
            ],
            frames={},
        )
        evidence = next(s for s in briefing.sections if s.title == "候选来龙去脉")

        assert "消息判断:" not in evidence.content
        assert "上下文卡: 消息 " not in evidence.content
        assert "下一步: 优先看竞价量能" in evidence.content

    def test_briefing_debate_receives_market_context_lines(self):
        import pandas as pd

        captured: dict[str, object] = {}

        class DummyCoordinator:
            def run_debate(
                self, pick, df, *, market_context_lines=(), signal_date: str = ""
            ):
                captured["symbol"] = pick.symbol
                captured["market_context_lines"] = tuple(market_context_lines)
                captured["signal_date"] = signal_date
                assert isinstance(df, pd.DataFrame)
                from aqsp.briefing.debate import DebateResult

                return DebateResult(
                    debate_id="demo",
                    symbol=pick.symbol,
                    name=pick.name,
                    original_score=pick.score,
                    rating=pick.rating,
                    market_context_lines=tuple(market_context_lines),
                    final_vote={
                        AgentRole.BULL: "bullish",
                        AgentRole.RISK_CONTROL: "neutral",
                        AgentRole.CROSS_MARKET: "bullish",
                    },
                )

        gen = BriefingGenerator(enable_debate=True)
        gen.debate_coordinator = DummyCoordinator()
        pick = _make_pick(symbol="300750", name="宁德时代", score=72.0)
        frame = pd.DataFrame(
            {
                "date": ["2026-06-30"],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000.0],
            }
        )

        briefing = gen.generate(
            picks=[pick],
            frames={pick.symbol: frame},
            market_context_lines=(
                "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
                "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
            ),
        )

        assert captured["symbol"] == "300750"
        assert captured["market_context_lines"] == (
            "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
            "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
        )
        assert len(briefing.debate_results) == 1
        assert briefing.debate_results[0].market_context_lines == (
            "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
            "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
        )
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert "结论已阻断：缺少可核验证据" in main_chain_sec.content
        assert "- 讨论视角: 技术多头、风险控制、跨市传导" not in main_chain_sec.content

    def test_generate_refreshes_portfolio_summary_with_internal_debate_result(self):
        import pandas as pd
        from aqsp.briefing.debate import DebateResult

        class DummyCoordinator:
            def run_debate(
                self, pick, df, *, market_context_lines=(), signal_date: str = ""
            ):
                del df, market_context_lines, signal_date
                return DebateResult(
                    debate_id="debate-300750",
                    symbol=pick.symbol,
                    name=pick.name,
                    original_score=pick.score,
                    rating=pick.rating,
                    recommended_adjustment="raise",
                    adjusted_score=pick.score + 8.0,
                    research_verdict="倾向优先纸面复核，主因 跨市主线扩散",
                    primary_risk_gate="追高回撤风险",
                    next_trigger="先确认机器人龙头放量上攻",
                    support_points=("海外物理AI叙事仍在扩散。",),
                    opposition_points=("若高开过猛，追高回撤风险会放大。",),
                    watch_items=("观察次日承接是否继续。",),
                    role_reliability_lines=("跨市场: 近21天 7/10 (70%)",),
                    cross_market_support_event_count=2,
                    cross_market_conflict_event_count=1,
                    cross_market_evidence_stack_summary="同向 2 条｜反向 1 条",
                )

        gen = BriefingGenerator(enable_debate=True)
        gen.debate_coordinator = DummyCoordinator()
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            score=72.0,
            rating="buy_candidate",
        )
        frame = pd.DataFrame(
            {
                "date": ["2026-06-30"],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000.0],
            }
        )

        briefing = gen.generate(picks=[pick], frames={pick.symbol: frame})
        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        smart_summary = briefing.generate_smart_summary()

        assert briefing.picks[0].score == 72.0
        assert (
            briefing.picks[0].metrics["debate_research_verdict"]
            == "倾向优先纸面复核，主因 跨市主线扩散"
        )
        assert briefing.portfolio_summary is not None
        assert briefing.portfolio_summary.debate_support_points == (
            "300750 宁德时代 | 海外物理AI叙事仍在扩散。",
        )
        assert "- 讨论支持: 300750 宁德时代 | 海外物理AI叙事仍在扩散。" in smart_summary
        assert "- 讨论卡点: 300750 宁德时代 | 追高回撤风险" in main_chain_sec.content
        assert (
            "- 讨论触发: 300750 宁德时代 | 先确认机器人龙头放量上攻"
            in main_chain_sec.content
        )
        assert "同向 2 条｜反向 1 条" in main_chain_sec.content

    def test_briefing_generator_uses_task_specific_role_preset_when_roles_not_explicit(
        self, monkeypatch
    ):
        monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
        monkeypatch.setenv("AQSP_RUN_TASK_ID", "briefing")

        gen = BriefingGenerator(enable_debate=True)

        assert tuple(role.value for role in gen.debate_coordinator.roles) == (
            "bull",
            "bear",
            "risk_control",
            "sector_leader",
            "cross_market",
            "policy_sensitive",
            "northbound",
        )

    def test_briefing_generator_respects_goal_switch_when_constructor_requests_debate(
        self, monkeypatch, tmp_path
    ):
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
        monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")

        gen = BriefingGenerator(enable_debate=True)

        assert gen.enable_debate is False

    def test_briefing_generator_main_chain_section_surfaces_runtime_switch_summary(
        self, monkeypatch, tmp_path
    ):
        goal_switch_path = tmp_path / "goal_switches.yaml"
        goal_switch_path.write_text(
            """
version: "test"
mode: short_term_realtime
switches:
  historical_validation_only:
    enabled: true
    purpose: history only
  realtime_fallback_chain:
    enabled: false
    purpose: fallback disabled
  domestic_market_intelligence:
    enabled: false
    purpose: domestic disabled
  global_market_intelligence:
    enabled: true
    purpose: global enabled
  pit_enrichment_runtime_required:
    enabled: false
    purpose: pit optional
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))

        gen = BriefingGenerator(enable_debate=False)
        briefing = gen.generate(
            picks=[
                _make_pick(
                    symbol="300750", name="宁德时代", score=72.0, rating="buy_candidate"
                )
            ],
            frames={},
            portfolio_summary=PortfolioDecisionSummary(
                promote_count=1,
                downgrade_count=0,
                keep_count=0,
                top_focus=("300750 宁德时代",),
                watchlist=(),
                allocations=(),
                cash_reserve=1.0,
                allocation_note="测试",
            ),
        )

        main_chain_sec = next(s for s in briefing.sections if s.title == "主链总览")
        assert (
            "- 运行边界: 历史验证专用 开 / 回退链 关 / 国内情报 关 / 海外情报 开 / PIT 可缺省。"
            in main_chain_sec.content
        )
        assert (
            "- 运行说明: 实时回退链已关闭；未降级不代表备用源可用。"
            in main_chain_sec.content
        )
        assert (
            "- 运行说明: 国内情报已关闭；题材/政策/资金空白不等于当天无催化。"
            in main_chain_sec.content
        )

    def test_briefing_generator_resolves_event_focused_roles(self, monkeypatch):
        monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
        monkeypatch.delenv("AQSP_DEBATE_FOCUS_ROLES", raising=False)
        monkeypatch.setenv("AQSP_RUN_TASK_ID", "briefing")

        gen = BriefingGenerator(enable_debate=True)
        pick = _make_pick(
            symbol="300750",
            name="宁德时代",
            metrics={
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "优先复核",
                "cross_market_priority_score": 3,
                "cross_market_support_event_count": 2,
                "cross_market_conflict_event_count": 1,
            },
        )

        roles = gen._resolve_pick_debate_roles(
            pick,
            market_context_lines=(
                "传导推演[海外物理AI叙事升温]: 动作 优先复核。",
                "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
                "政策跟踪: 工信部继续强调机器人产业链支持。",
            ),
        )

        assert roles == (
            "cross_market",
            "sector_leader",
            "bull",
            "policy_sensitive",
            "risk_control",
            "northbound",
            "bear",
        )

    def test_briefing_focus_roles_do_not_lock_context_role_expansion(self, monkeypatch):
        monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
        monkeypatch.setenv("AQSP_DEBATE_FOCUS_ROLES", "risk_control")
        monkeypatch.setenv("AQSP_RUN_TASK_ID", "briefing")

        gen = BriefingGenerator(enable_debate=True)
        pick = _make_pick(
            symbol="688297",
            name="中无人机",
            metrics={
                "cross_market_primary_theme": "海外商业航天催化",
                "cross_market_action": "重点跟踪",
                "cross_market_priority_score": 2,
            },
        )

        roles = gen._resolve_pick_debate_roles(
            pick,
            market_context_lines=(
                "传导推演[中]: 海外商业航天催化 -> A股商业航天、卫星互联网、军工电子；动作 重点跟踪",
            ),
        )

        assert roles[0] == "risk_control"
        assert "cross_market" in roles
        assert "sector_leader" in roles
        assert "retail_mood" in roles
        assert len(roles) == 8

    def test_briefing_generator_expands_debate_coverage_with_runtime_max_candidates(
        self, monkeypatch
    ):
        import pandas as pd

        monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
        monkeypatch.setenv("AQSP_DEBATE_MAX_CANDIDATES", "4")
        seen_symbols: list[str] = []

        class DummyCoordinator:
            def run_debate(
                self, pick, df, *, market_context_lines=(), signal_date: str = ""
            ):
                del df, market_context_lines, signal_date
                seen_symbols.append(pick.symbol)
                from aqsp.briefing.debate import DebateResult

                return DebateResult(
                    debate_id=f"debate-{pick.symbol}",
                    symbol=pick.symbol,
                    name=pick.name,
                    original_score=pick.score,
                    rating=pick.rating,
                )

        gen = BriefingGenerator(enable_debate=True)
        gen.debate_coordinator = DummyCoordinator()
        picks = [
            _make_pick(symbol=f"30075{i}", name=f"候选{i}", score=90.0 - i)
            for i in range(5)
        ]
        frames = {
            pick.symbol: pd.DataFrame(
                {
                    "date": ["2026-06-30"],
                    "open": [100.0],
                    "high": [101.0],
                    "low": [99.0],
                    "close": [100.5],
                    "volume": [1000.0],
                }
            )
            for pick in picks
        }

        briefing = gen.generate(
            picks=picks,
            frames=frames,
        )

        assert seen_symbols == ["300750", "300751", "300752", "300753"]
        assert len(briefing.debate_results) == 4

    def test_realtime_debate_marks_missing_frame_as_visible_failure(self, monkeypatch):
        monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
        monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")

        gen = BriefingGenerator(enable_debate=True)
        briefing = gen.generate(picks=[_make_pick(symbol="300750")], frames={})

        assert len(briefing.debate_results) == 1
        blocked = briefing.debate_results[0]
        assert blocked.data_status == "empty"
        assert blocked.research_verdict == "结论阻断：行情数据为空，仅记录待补行情。"
        assert "empty_market_data" in blocked.failure
        assert briefing.debate_failed_symbols == ("300750(缺少有效行情帧)",)
        assert "讨论失败或缺失: 300750(缺少有效行情帧)" in briefing.to_markdown()
        assert "结论已阻断：行情数据为空" in briefing.to_markdown()

    def test_briefing_does_not_repeat_same_candidate_debate_in_one_run(
        self, monkeypatch
    ):
        import pandas as pd
        from aqsp.briefing.debate import DebateResult

        monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
        seen_symbols: list[str] = []

        class DummyCoordinator:
            def run_debate(
                self, pick, df, *, market_context_lines=(), signal_date: str = ""
            ):
                del df, market_context_lines, signal_date
                seen_symbols.append(pick.symbol)
                return DebateResult(
                    debate_id=f"debate-{pick.symbol}",
                    symbol=pick.symbol,
                    name=pick.name,
                    original_score=pick.score,
                    rating=pick.rating,
                )

        gen = BriefingGenerator(enable_debate=True)
        gen.debate_coordinator = DummyCoordinator()
        picks = [
            _make_pick(symbol="300750", score=80.0),
            _make_pick(symbol="300750", score=79.0),
        ]
        frame = pd.DataFrame({"close": [100.0, 101.0]})

        briefing = gen.generate(
            picks=picks,
            frames={"300750": frame},
        )

        assert seen_symbols == ["300750"]
        assert briefing.debate_requested_symbols == ("300750",)

    def test_realtime_disabled_roles_are_not_readded_by_role_guard(self, monkeypatch):
        monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
        monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
        monkeypatch.setenv("AQSP_DEBATE_DISABLED_ROLES", "risk_control,cross_market")

        gen = BriefingGenerator(enable_debate=True)

        active_roles = {role.value for role in gen.debate_coordinator.roles}
        assert "risk_control" not in active_roles
        assert "cross_market" not in active_roles

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
        assert "需人工复核" in summary

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
    def test_sends_card(self, mock_send, tmp_path):
        from aqsp.briefing.notifier import send_smart_summary_card

        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        send_smart_summary_card(briefing, state_path=tmp_path / "notify_state.json")
        mock_send.assert_called_once()
        card = mock_send.call_args[0][0]
        assert card["msg_type"] == "interactive"
        assert "选股简报" in card["card"]["header"]["title"]["content"]

    @patch("aqsp.briefing.notifier.notify_feishu_card")
    def test_skips_empty_summary(self, mock_send, tmp_path):
        from aqsp.briefing.notifier import send_smart_summary_card

        briefing = Briefing(date="2026-05-27 10:00", sections=[])
        with patch.object(Briefing, "generate_smart_summary", return_value="   "):
            send_smart_summary_card(briefing, state_path=tmp_path / "notify_state.json")
        mock_send.assert_not_called()

    @patch("aqsp.briefing.notifier.notify_feishu_card")
    def test_smart_summary_card_dedupes_same_content(self, mock_send, tmp_path):
        from aqsp.briefing.notifier import send_smart_summary_card
        from aqsp.notifier import NotifyResult

        mock_send.return_value = [NotifyResult(channel="feishu", ok=True, detail="ok")]
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[BriefingSection(title="t", content="c")],
        )
        state_path = tmp_path / "notify_state.json"

        send_smart_summary_card(briefing, state_path=state_path)
        send_smart_summary_card(briefing, state_path=state_path)

        mock_send.assert_called_once()

    @patch("aqsp.briefing.notifier.send_smart_summary_card")
    @patch("aqsp.briefing.notifier.dispatch_notification_once")
    def test_send_briefing_calls_smart_summary_first(self, mock_dispatch, mock_card):
        briefing = Briefing(
            date="d", sections=[BriefingSection(title="t", content="c")]
        )
        send_briefing(briefing)
        mock_card.assert_called_once_with(briefing)
        mock_dispatch.assert_called_once()
