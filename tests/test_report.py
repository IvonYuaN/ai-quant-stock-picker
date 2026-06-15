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

    assert "## 数据与规则" in markdown
    assert "- 数据来源: auto -> tdx_vipdoc" in markdown
    assert "- 数据完整度: 收盘后 / 核心历史 / 本地缓存可用" in markdown
    assert "- 数据时效: 最新交易日 2026-05-29 / 延迟 0 天" in markdown
    assert "- 数据状态: 正常 / tdx_vipdoc 健康；数据源成功/失败 3/0" in markdown
    assert "显式 0 / 解析 100 / 取数 101 / 筛选前 8 / 最终 1" in markdown
    assert "- 规则版本: 1.0.0" in markdown
    assert "不构成交易指令或投资建议" in markdown


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

    assert "### 排序调整" in markdown
    assert "- 今日处理: 上调优先级" in markdown
    assert "- 分数调整: +4.0" in markdown
    assert "最终动作" not in markdown


def test_report_sanitizes_dynamic_markdown_fields() -> None:
    pick = PickResult(
        symbol="600519",
        name="贵州茅台<script>",
        date="2026-06-09",
        close=1500,
        score=82,
        rating="strong_buy_candidate",
        entry_type="执行开仓<script>",
        ideal_buy=1490,
        stop_loss=1450,
        take_profit=1600,
        position="half",
        strategies=("执行名单",),
        reasons=("立即买入后等待下单<script>alert(1)</script>",),
        risks=("真实持仓暴露过高<img onerror=alert(1)>",),
        metrics={
            "candidate_blocker": "买入条件不足，下单阻塞",
            "candidate_next_step": "执行开仓后看真实持仓",
        },
    )

    markdown = to_markdown([pick])

    for forbidden in (
        "<script>",
        "<img",
        "onerror",
        "立即买入",
        "下单",
        "执行开仓",
        "真实持仓",
    ):
        assert forbidden not in markdown
    assert "&lt;script&gt;" in markdown
    assert "纸面记录阻塞" in markdown
    assert "纸面持有" in markdown


def test_report_renders_watch_position_for_downgraded_candidate() -> None:
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
        metrics={
            "candidate_status": "延续上升",
            "candidate_blocker": "板块集中度过高，压低银行暴露",
        },
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="000001",
                action="downgrade",
                score_delta=-4.0,
                reasons=("板块集中度过高，压低银行暴露",),
            )
        ],
    )

    assert "- 比例参考: watch" in markdown
    assert "仓位建议" not in markdown


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

    assert "### 排序调整" not in markdown


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
        metrics={"candidate_status": "新晋"},
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

    assert "## 今日重点看板" in markdown
    assert "- 今日结论: 上调 1 / 降级 0 / 维持 0" in markdown
    assert "- 观察重点: 600900 长江电力" in markdown
    assert "- 重点关注: 600900 长江电力" not in markdown
    assert (
        "- 重点 1: 600900 长江电力 | 继续观察 | 新晋 | 评分 76 | 处理 上调优先级"
        in markdown
    )
    assert "判断原因: 不同看法支持上调优先级" in markdown
    assert "- 决策: 继续观察 | 新晋 | 评分 76.0" in markdown
    assert markdown.index("## 今日重点看板") < markdown.index("## 1. 600900 长江电力")


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

    assert "- 需要重点确认: 板块集中度过高，压低公用事业暴露" in markdown
    assert "- 现在卡在哪:" in markdown
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
    assert "- 现在偏向: 进攻牛市 | 稳定上涨期，重仓动量+涨停板" in markdown
    assert "- 更偏好这些方向: 动量趋势、涨停接力" in markdown
    assert "- 方向占比参考: momentum 30%、limit_up_ladder 30%" in markdown
    assert "长江电力: 20% | 主链评分 76.0；PM 上调优先级" in markdown
    assert "- 再看顺序: 先看 600900 长江电力" in markdown
    assert "- 现金留存: 25%" in markdown
    assert "- 配置说明: 单票上限 20%；信号强度不足时提高现金留存" in markdown


def test_report_keeps_actionable_focus_label_when_allocations_exist() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
        reasons=("趋势保持",),
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
                    rationale=("主链评分 76.0",),
                ),
            ),
            cash_reserve=0.8,
            allocation_note="",
        ),
    )

    assert "- 纸面复核: 600900 长江电力" in markdown
    assert "- 重点关注: 600900 长江电力" not in markdown
    assert "- 观察重点: 600900 长江电力" not in markdown


def test_report_labels_no_allocation_focus_as_observation() -> None:
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
        reasons=("短中期均线多头",),
    )

    markdown = to_markdown(
        [pick],
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=1,
            keep_count=0,
            top_focus=("000001 平安银行",),
            watchlist=("000001 平安银行",),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="今日无纸面复核主线，建议保留现金等待下一轮信号。",
        ),
    )

    assert "- 观察重点: 000001 平安银行" in markdown
    assert "- 重点关注: 000001 平安银行" not in markdown


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

    assert "- 讨论倾向: raise（附件观点，不改写系统评分）" in markdown
    assert "- 参考分歧: 系统原始评分 76.0；附件参考分 79.0" in markdown
    assert "评分变化" not in markdown
    assert "- 分歧: 35%" in markdown


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
    assert "- 重点 1: 600900 长江电力 | 仅观察 | 评分 76 | 处理 降级观察" in markdown
    detail_section = markdown.split("## 1. 600900 长江电力", maxsplit=1)[1]
    assert "### 排序调整" in detail_section
    assert "- 今日处理: 降级观察" in detail_section
    assert "板块集中度过高，压低公用事业暴露" in detail_section


def test_report_downgraded_pick_uses_observation_label_even_if_original_rating_buy() -> (
    None
):
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
        metrics={"candidate_status": "延续上升"},
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="000001",
                action="downgrade",
                score_delta=-4.0,
                reasons=("板块集中度过高，压低银行暴露",),
            )
        ],
    )

    assert "- 重点 1: 000001 平安银行 | 仅观察 | 延续上升" in markdown
    assert "- 决策: 仅观察 | 延续上升 | 评分 85.0" in markdown
    assert "重点关注 | 延续上升" not in markdown


def test_report_formats_string_portfolio_reasons_with_separator() -> None:
    pick = PickResult(
        symbol="600036",
        name="招商银行",
        date="2026-06-09",
        close=38.55,
        score=56,
        rating="avoid",
        entry_type="trend_pullback",
        ideal_buy=38.55,
        stop_loss=37.55,
        take_profit=42.04,
        position="watch",
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600036",
                action="downgrade",
                score_delta=-7.0,
                reasons=(
                    "板块集中度过高，压低银行暴露与前序候选高相关，降低组合拥挤风险"
                ),
            )
        ],
    )

    assert "板块集中度过高，压低银行暴露；与前序候选高相关" in markdown


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


def test_report_preserves_full_reason_text_without_truncating_chinese() -> None:
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
        reasons=("MA5/10/20/60 多头排列", "强趋势缩量回踩均线"),
        risks=(),
    )

    markdown = to_markdown([pick])

    assert "参考: MA5/10/20/60 多头排列；强趋势缩量回踩均线" in markdown
    assert "- 理由: MA5/10/20/60 多头排列；强趋势缩量回踩均线" in markdown
    assert "- 由:" not in markdown
    assert "强趋势缩量回踩均；" not in markdown


def test_report_renders_candidate_blocker_and_next_step_when_present() -> None:
    pick = PickResult(
        symbol="000001",
        name="平安银行",
        date="2026-06-05",
        close=10.82,
        score=58,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=10.82,
        stop_loss=10.73,
        take_profit=11.73,
        position="watch",
        reasons=("估值防守", "等待量能确认"),
        metrics={
            "candidate_status": "观察阻塞",
            "candidate_blocker": "板块集中度过高，压低银行暴露",
            "candidate_next_step": "等待板块暴露回落后，再重新评估纸面复核优先级",
            "candidate_review_window": "板块分化时",
            "candidate_review_priority": "medium",
        },
    )

    markdown = to_markdown([pick])

    assert "现在卡在哪: 板块集中度过高，压低银行暴露" in markdown
    assert "下一步: 等待板块暴露回落后，再重新评估纸面复核优先级" in markdown
    assert "- 接下来先看: 等待板块暴露回落后，再重新评估纸面复核优先级" in markdown
    assert "再看时间: 中优先级 / 板块分化时" in markdown
    assert "- 再看优先级/时机: 中优先级 / 板块分化时" in markdown
