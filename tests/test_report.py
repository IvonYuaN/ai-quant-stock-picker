from __future__ import annotations

from aqsp.core.types import RunMetadata
from aqsp.models import PickResult
from aqsp.portfolio.manager import PortfolioDecision, PortfolioDecisionSummary
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.report import to_markdown


def test_report_renders_run_metadata_when_provided() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )
    metadata = RunMetadata(
        requested_source="auto",
        actual_source="tdx_vipdoc",
        source_freshness_tier="end_of_day",
        source_coverage_tier="history_core",
        source_local_status="present",
        source_health_label="healthy",
        source_health_message="tdx_vipdoc 健康；源成功/失败 3/0",
        fallback_used=False,
        explicit_symbol_count=0,
        resolved_symbol_count=100,
        fetched_frame_count=101,
        screened_count=8,
        final_count=1,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000,
        online_factors_enabled=False,
        thresholds_version="1.0.0",
        data_latest_trade_date="2026-05-29",
        data_lag_days=0,
        regime="stable_bull",
        max_universe=100,
    )

    markdown = to_markdown([pick], metadata=metadata)

    assert "## 运行参数" in markdown
    assert "- 数据源: auto -> tdx_vipdoc" in markdown
    assert (
        "- 数据层级: fresh=end_of_day / cover=history_core / local=present" in markdown
    )
    assert "- 数据时效: latest=2026-05-29 / lag=0d" in markdown
    assert "- 数据健康: healthy / tdx_vipdoc 健康；源成功/失败 3/0" in markdown
    assert "显式 0 / 解析 100 / 取数 101 / 筛选前 8 / 最终 1" in markdown
    assert "- thresholds.version: 1.0.0" in markdown


def test_report_renders_portfolio_manager_decision_when_provided() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="promote",
                score_delta=4.0,
                reasons=("多Agent辩论支持上调优先级",),
            )
        ],
    )

    assert "### Portfolio Manager" in markdown
    assert "- 最终动作: 上调优先级" in markdown
    assert "- 分数调整: +4.0" in markdown


def test_report_hides_noop_portfolio_manager_decision() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="keep",
                score_delta=0.0,
                reasons=("保持原排序",),
            )
        ],
    )

    assert "### Portfolio Manager" not in markdown


def test_report_renders_final_decision_board_first() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
        reasons=("趋势保持", "量价配合"),
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="promote",
                score_delta=4.0,
                reasons=("多Agent辩论支持上调优先级",),
            )
        ],
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=1,
            downgrade_count=0,
            keep_count=0,
            top_focus=("600900 长江电力",),
            watchlist=(),
            allocations=(),
            cash_reserve=0.0,
            allocation_note="",
        ),
    )

    assert "## 最终决策看板" in markdown
    assert "- PM主裁决: 上调 1 / 降级 0 / 维持 0" in markdown
    assert "- 重点关注: 600900 长江电力" in markdown
    assert "- Top 1: 600900 长江电力 | 观察候选 | 评分 76 | PM 上调优先级" in markdown
    assert "PM依据: 多Agent辩论支持上调优先级" in markdown
    assert markdown.index("## 最终决策看板") < markdown.index("## 1. 600900 长江电力")


def test_report_renders_action_hotspots_and_execution_blockers() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="watch",
        reasons=("趋势保持", "量价配合"),
    )

    markdown = to_markdown(
        [pick],
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=1,
            keep_count=0,
            top_focus=(),
            watchlist=("600900 长江电力",),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="单票上限 20%；今日不建议建立主仓",
            action_hotspots=("板块集中度过高，压低公用事业暴露",),
            execution_blockers=("600900 长江电力: 板块集中度过高，压低公用事业暴露",),
        ),
    )

    assert "- 裁决热点: 板块集中度过高，压低公用事业暴露" in markdown
    assert "- 执行阻塞:" in markdown
    assert "600900 长江电力: 板块集中度过高，压低公用事业暴露" in markdown


def test_report_renders_allocation_guidance_when_summary_provided() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
        reasons=("趋势保持", "量价配合"),
    )

    markdown = to_markdown(
        [pick],
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=1,
            downgrade_count=0,
            keep_count=0,
            top_focus=("600900 长江电力",),
            watchlist=(),
            allocations=(
                PortfolioAllocation(
                    symbol="600900",
                    name="长江电力",
                    weight=0.2,
                    rationale=("主链评分 76.0", "PM 上调优先级"),
                ),
            ),
            cash_reserve=0.25,
            allocation_note="单票上限 20%；信号强度不足时提高现金留存",
            regime_label="稳定上涨",
            strategy_mix_name="进攻牛市",
            strategy_mix_description="稳定上涨期，重仓动量+涨停板",
            strategy_focus=("动量趋势", "涨停接力"),
            strategy_weights=(("momentum", 0.3), ("limit_up_ladder", 0.3)),
        ),
    )

    assert "- 当前市况: 稳定上涨" in markdown
    assert "- 策略主配比: 进攻牛市 | 稳定上涨期，重仓动量+涨停板" in markdown
    assert "- 优先策略: 动量趋势、涨停接力" in markdown
    assert "- 策略权重建议: momentum 30%、limit_up_ladder 30%" in markdown
    assert "长江电力: 20% | 主链评分 76.0；PM 上调优先级" in markdown
    assert "- 执行顺序: 先看 600900 长江电力" in markdown
    assert "- 现金留存: 25%" in markdown
    assert "- 配置说明: 单票上限 20%；信号强度不足时提高现金留存" in markdown


def test_report_renders_debate_score_change_when_available() -> None:
    from aqsp.briefing.debate import DebateResult

    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )

    markdown = to_markdown(
        [pick],
        debate_results=[
            DebateResult(
                debate_id="d1",
                symbol="600900",
                name="长江电力",
                original_score=76.0,
                adjusted_score=79.0,
                rating="buy_candidate",
                final_consensus="趋势延续，但仍需确认量能",
                disagreement_score=0.35,
                recommended_adjustment="raise",
            )
        ],
    )

    assert "- 评分变化: 76.0 → 79.0" in markdown
    assert "- 分歧度: 35%" in markdown


def test_report_hides_non_promote_portfolio_section_below_pick_detail() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="watch",
        reasons=("趋势保持", "量价配合"),
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="downgrade",
                score_delta=-6.0,
                reasons=("板块集中度过高，压低公用事业暴露",),
            )
        ],
    )

    assert "## 1. 600900 长江电力" in markdown
    assert "- Top 1: 600900 长江电力 | 候选观察池 | 评分 76 | PM 降级观察" in markdown
    detail_section = markdown.split("## 1. 600900 长江电力", maxsplit=1)[1]
    assert "### Portfolio Manager" in detail_section
    assert "- 最终动作: 降级观察" in detail_section
    assert "板块集中度过高，压低公用事业暴露" in detail_section


def test_report_avoids_repeating_symbol_as_name() -> None:
    pick = PickResult(
        symbol="600900",
        name="600900",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
        reasons=("趋势保持",),
    )

    markdown = to_markdown([pick])

    assert "## 1. 600900\n" in markdown
    assert "- 风险提示: 无" in markdown
    assert "600900 600900" not in markdown
