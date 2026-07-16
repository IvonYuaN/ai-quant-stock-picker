from __future__ import annotations

from aqsp.core.types import RunMetadata
from aqsp.models import PickResult
from aqsp.portfolio.manager import PortfolioDecision, PortfolioDecisionSummary
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.report import to_intraday_dataframe, to_markdown
from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.debate import AgentOpinion, DebateResult, DebateRound


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


def test_report_renders_market_context_lines_when_provided() -> None:
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
        metrics={
            "cross_market_primary_theme": "外盘风险偏好修复",
            "cross_market_linkage_basis": "风险偏好映射",
            "cross_market_action": "重点跟踪",
            "cross_market_lead_window": "次日竞价-1日",
            "cross_market_observation_window": "次日-3日",
            "cross_market_validation_signals": ("次日竞价高弹性方向明显强于防御方向",),
            "cross_market_invalidation_signals": ("美股强但A股竞价无明显风险偏好跟随",),
        },
    )
    metadata = RunMetadata(
        requested_source="online_first",
        actual_source="eastmoney",
        source_freshness_tier="realtime",
        source_coverage_tier="broad_runtime",
        source_local_status="not_required",
        source_health_label="healthy",
        source_health_message="eastmoney 健康；源成功/失败 8/1",
        fallback_used=False,
        explicit_symbol_count=0,
        resolved_symbol_count=100,
        fetched_frame_count=101,
        screened_count=8,
        final_count=1,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000,
        online_factors_enabled=True,
        thresholds_version="1.0.0",
        data_latest_trade_date="2026-05-29",
        data_lag_days=0,
        regime="stable_bull",
        max_universe=100,
        market_context_overview="外盘风险偏好修复，重点看 A股成长、高弹性、AI链",
        market_context_lines=(
            "个股催化: 600900 长江电力 偏多｜政策催化｜电改预期强化。",
            "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
        ),
    )

    markdown = to_markdown([pick], metadata=metadata)

    assert "## 市场上下文" in markdown
    assert "- 跨市主线: 外盘风险偏好修复，重点看 A股成长、高弹性、AI链" in markdown
    assert "- 个股催化: 600900 长江电力 偏多｜政策催化｜电改预期强化。" in markdown
    assert "- 北向资金: 偏强（5日 z=1.20），外资风险偏好改善。" in markdown
    assert "- 跨市场线索: 纸面复核｜外盘风险偏好修复｜观察窗 次日-3日" in markdown
    assert (
        "- 传导链条: 风险偏好映射｜领先窗 次日竞价-1日｜确认 次日竞价高弹性方向明显强于防御方向｜失效 美股强但A股竞价无明显风险偏好跟随"
        in markdown
    )


def test_report_intraday_dataframe_includes_runtime_context_row() -> None:
    metadata = RunMetadata(
        requested_source="online_first",
        actual_source="tencent",
        source_freshness_tier="realtime",
        source_coverage_tier="broad_runtime",
        source_local_status="not_required",
        source_health_label="healthy",
        source_health_message="实时源正常",
        fallback_used=True,
        explicit_symbol_count=0,
        resolved_symbol_count=80,
        fetched_frame_count=80,
        screened_count=10,
        final_count=1,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000,
        online_factors_enabled=True,
        thresholds_version="1.0.0",
        data_latest_trade_date="2026-07-10",
        data_lag_days=0,
        regime="stable_bull",
        max_universe=80,
        task_id="intraday",
        market_context_overview="海外科技风险偏好改善",
        market_context_lines=("纳指期货走强", "A股算力链观察承接"),
    )

    frame = to_intraday_dataframe([], metadata=metadata)

    assert frame.iloc[0]["symbol"] == "__RUN__"
    assert frame.iloc[0]["run_task_id"] == "intraday"
    assert frame.iloc[0]["run_market_context_overview"] == "海外科技风险偏好改善"
    assert (
        frame.iloc[0]["run_market_context_lines"] == "纳指期货走强；A股算力链观察承接"
    )


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

    assert "### 排序变化" in markdown
    assert "- 本次变化: 优先级上调" in markdown
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

    assert "- 仓位参考: watch" in markdown
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

    assert "### 排序变化" not in markdown


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
    assert "- 结果概览: 上调 1 / 降级 0 / 维持 0" in markdown
    assert "- 观察重点: 600900 长江电力" in markdown
    assert "- 重点关注: 600900 长江电力" not in markdown
    assert "- 重点 1: 600900 长江电力 | 继续观察 | 新晋 | 评分 76" in markdown
    assert "原因: 分歧支持提高优先级" in markdown
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
            cross_market_overview="外盘风险偏好修复，纸面复核 600900 长江电力",
            cross_market_focus=("600900 长江电力 | 外盘风险偏好修复(纸面复核)",),
            debate_focus=("600900 长江电力 | 倾向优先纸面复核，主因 技术面强势",),
            debate_support_points=("600900 长江电力 | 技术面强势且量价配合",),
            debate_opposition_points=("600900 长江电力 | 若高开过猛则回撤风险放大",),
            debate_watch_items=("600900 长江电力 | 先确认开盘承接是否持续",),
            debate_risk_gates=("600900 长江电力 | 追高回撤风险",),
            debate_next_triggers=("600900 长江电力 | 先确认开盘承接",),
            debate_priority_queue=(
                "600900 长江电力 | 倾向优先纸面复核，主因 技术面强势 | 先确认开盘承接 | 卡点 追高回撤风险",
            ),
            action_hotspots=("板块集中度过高，压低公用事业暴露",),
            execution_blockers=("600900 长江电力: 板块集中度过高，压低公用事业暴露",),
        ),
    )

    assert "- 跨市主线: 外盘风险偏好修复，纸面复核 600900 长江电力" in markdown
    assert "- 跨市焦点: 600900 长江电力 | 外盘风险偏好修复(纸面复核)" in markdown
    assert "- 讨论焦点: 600900 长江电力 | 倾向优先纸面复核，主因 技术面强势" in markdown
    assert "- 讨论支持: 600900 长江电力 | 技术面强势且量价配合" in markdown
    assert "- 讨论反对: 600900 长江电力 | 若高开过猛则回撤风险放大" in markdown
    assert "- 讨论待确认: 600900 长江电力 | 先确认开盘承接是否持续" in markdown
    assert "- 讨论卡点: 600900 长江电力 | 追高回撤风险" in markdown
    assert "- 讨论触发: 600900 长江电力 | 先确认开盘承接" in markdown
    assert (
        "- 讨论顺序: 600900 长江电力 | 倾向优先纸面复核，主因 技术面强势 | 先确认开盘承接 | 卡点 追高回撤风险"
        in markdown
    )
    assert "- 待确认: 板块集中度过高，压低公用事业暴露" in markdown
    assert "- 阻塞:" in markdown
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
    assert "- 市况评分倍率: momentum ×0.30、limit_up_ladder ×0.30" in markdown
    assert "长江电力: 20% | 主链评分 76.0；优先级上调" in markdown
    assert "- 先看顺序: 600900 长江电力" in markdown
    assert "- 现金留存: 25%" in markdown
    assert "- 仓位约束: 单票上限 20%；信号强度不足时提高现金留存" in markdown


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


def test_report_downgrades_allocations_to_observation_when_circuit_breaker_triggered() -> (
    None
):
    pick = PickResult(
        symbol="688981",
        name="中芯国际",
        date="2026-07-10",
        close=171.0,
        score=92.31,
        rating="strong_buy_candidate",
        entry_type="volume_breakout",
        ideal_buy=171.0,
        stop_loss=163.84,
        take_profit=222.27,
        position="30%-30%",
        reasons=("MA5/10/20/60 多头排列",),
    )
    metadata = RunMetadata(
        requested_source="online_first",
        actual_source="sina",
        source_freshness_tier="intraday_realtime",
        source_coverage_tier="quote_enhanced",
        source_local_status="not_required",
        source_health_label="fallback",
        source_health_message="已切换到备用数据源 sina",
        fallback_used=True,
        explicit_symbol_count=0,
        resolved_symbol_count=3,
        fetched_frame_count=3,
        screened_count=3,
        final_count=3,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000.0,
        online_factors_enabled=False,
        thresholds_version="1.1.13",
        data_latest_trade_date="2026-07-10",
        data_lag_days=0,
        task_id="intraday",
        circuit_breaker_triggered=True,
        circuit_breaker_reason="组合保护冷却期中，至 2026-07-15 解除",
    )

    markdown = to_markdown(
        [pick],
        metadata=metadata,
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=0,
            keep_count=1,
            top_focus=("688981 中芯国际",),
            watchlist=(),
            allocations=(
                PortfolioAllocation(
                    symbol="688981",
                    name="中芯国际",
                    weight=0.3,
                    rationale=("主链评分 92.3",),
                ),
            ),
            cash_reserve=0.4,
            allocation_note="单票上限 30%",
        ),
    )

    assert "- 观察重点: 688981 中芯国际" in markdown
    assert "- 观察顺序:" in markdown
    assert "- 重点 1: 688981 中芯国际 | 盘中观察 | 评分 92.31" in markdown
    assert "- 决策: 盘中观察 | 评分 92.3" in markdown
    assert "纸面复核" not in markdown
    assert "仓位参考" not in markdown


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
                final_vote={
                    AgentRole.BULL: "bullish",
                    AgentRole.RISK_CONTROL: "neutral",
                },
                market_context_lines=(
                    "确认信号: 次日竞价高弹性方向明显强于防御方向",
                    "失效条件: 外盘强但A股竞价无明显风险偏好跟随",
                ),
                support_points=("技术多头: ✅ 技术面强势",),
                opposition_points=("风险控制: ⚠️ 接近涨停：流动性风险",),
                watch_items=("分歧不大，但仍需确认开盘承接。",),
                research_verdict="倾向优先纸面复核，主因 技术多头: ✅ 技术面强势",
                primary_risk_gate="风险控制: ⚠️ 接近涨停：流动性风险",
                next_trigger="分歧不大，但仍需确认开盘承接。",
                historical_context_note="历史校验: 强证据 2/3 (67%)；冲突主导 1/3",
                role_reliability_lines=(
                    "技术多头: 近21天 7/10 (70%)｜当前权重 0.18",
                    "风险控制: 近21天 8/10 (80%)｜当前权重 0.12",
                ),
                role_selection_summary="因海外传导、分歧校验，本轮先看 技术多头、风险控制。",
                role_selection_plan="技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交。",
            )
        ],
    )

    assert "- 委员会结论: 偏积极（仅作补充，不改写系统评分）" in markdown
    assert "- 参考分歧: 系统原始评分 76.0；附件参考分 79.0" in markdown
    assert "评分变化" not in markdown
    assert "- 分歧: 35%" in markdown
    assert "- 研究口径: 倾向优先纸面复核，主因 技术多头: ✅ 技术面强势" in markdown
    assert (
        "- 跨市判断: 先看 600900 长江电力 | 确认 次日竞价高弹性方向明显强于防御方向 | 失效 外盘强但A股竞价无明显风险偏好跟随"
        in markdown
    )
    assert "- 核心卡点: 风险控制: ⚠️ 接近涨停：流动性风险" in markdown
    assert "- 下一触发: 分歧不大，但仍需确认开盘承接。" in markdown
    assert "- 历史校验: 强证据 2/3 (67%)；冲突主导 1/3" in markdown


def test_report_blocks_new_debate_chain_when_rounds_are_not_interactive() -> None:
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
    result = DebateResult(
        debate_id="new-chain-boundary",
        symbol="600900",
        name="长江电力",
        original_score=76.0,
        adjusted_score=82.0,
        rating="buy_candidate",
        rounds=[
            DebateRound(
                round_num=1,
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="bullish",
                        confidence=0.7,
                        arguments=["量价共振"],
                    ),
                    AgentOpinion(
                        agent_id="risk-1",
                        role=AgentRole.RISK_CONTROL,
                        stance="neutral",
                        confidence=0.6,
                        arguments=["仍需确认流动性"],
                    ),
                ],
            ),
            DebateRound(
                round_num=2,
                opinions=[
                    AgentOpinion(
                        agent_id="bull-1",
                        role=AgentRole.BULL,
                        stance="bullish",
                        confidence=0.7,
                        arguments=["趋势仍强"],
                    ),
                    AgentOpinion(
                        agent_id="risk-1",
                        role=AgentRole.RISK_CONTROL,
                        stance="neutral",
                        confidence=0.6,
                        arguments=["风险仍需确认"],
                    ),
                ],
            ),
        ],
        final_consensus="趋势延续，但仍需确认量能",
        final_vote={
            AgentRole.BULL: "bullish",
            AgentRole.RISK_CONTROL: "neutral",
        },
        support_points=("量价共振",),
        opposition_points=("流动性仍需复核",),
        research_verdict="倾向优先纸面复核",
        next_trigger="确认开盘量能",
        deterministic_score=76.0,
    )

    markdown = to_markdown([pick], debate_results=[result])

    assert "- 研究口径: 结论已阻断：多轮讨论未形成有效交锋" in markdown
    assert "- 决策: 继续观察 | 评分 76.0" in markdown
    assert "附件参考分 82.0" in markdown
    assert "- 讨论轮次:" not in markdown
    assert "- 讨论视角:" not in markdown
    assert "- 选角理由:" not in markdown
    assert "- 角色分工:" not in markdown
    assert "- 角色可信度:" not in markdown
    assert "- 支持观点:" not in markdown
    assert "- 反对观点:" not in markdown
    assert "- 待确认:" not in markdown


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
    assert "- 重点 1: 600900 长江电力 | 仅观察 | 评分 76" in markdown
    detail_section = markdown.split("## 1. 600900 长江电力", maxsplit=1)[1]
    assert "### 排序变化" in detail_section
    assert "- 本次变化: 优先级下调" in detail_section
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

    assert "阻塞: 板块集中度过高，压低银行暴露" in markdown
    assert "下一步: 等待板块暴露回落后，再重新评估纸面复核优先级" in markdown
    assert "- 下一步: 等待板块暴露回落后，再重新评估纸面复核优先级" in markdown
    assert "复核窗口: 中优先级 / 板块分化时" in markdown
    assert "- 再看优先级/时机: 中优先级 / 板块分化时" in markdown
