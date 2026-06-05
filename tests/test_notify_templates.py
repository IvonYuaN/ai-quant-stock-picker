from __future__ import annotations

from aqsp.briefing import Briefing, BriefingSection
from aqsp.briefing.closing_review import DailyReview, WeeklySummary
from aqsp.briefing.debate import DebateResult
from aqsp.core.types import PickResult
from aqsp.monitor.checker import MonitorResult
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.portfolio.manager import PortfolioDecisionSummary, WatchlistReviewItem
from aqsp.portfolio.snapshot import PickSnapshot, SnapshotDiff
from aqsp.notify_templates import (
    build_briefing_notification,
    build_closing_premium_notification,
    build_closing_review_notification,
    build_monitor_notification,
    build_daily_run_notification,
    build_morning_breakout_notification,
)
from aqsp.strategies.closing_premium import PremiumSignal
from aqsp.strategies.morning_breakout import BreakoutSignal


def test_build_briefing_notification_includes_debate_summary_when_summary_mode() -> None:
    briefing = Briefing(
        date="2026-06-04",
        sections=[
            BriefingSection(title="主链总览", content="PM主裁决: 维持观察"),
            BriefingSection(title="明日重点", content="**000001 平安银行** 观察开盘强弱"),
        ],
        debate_results=[
            DebateResult(
                debate_id="d1",
                symbol="000001",
                name="平安银行",
                original_score=68.0,
                rating="watch",
                final_consensus="技术面偏强，但仓位不宜过大",
                disagreement_score=0.45,
                recommended_adjustment="keep",
            )
        ],
    )

    markdown = build_briefing_notification(briefing, mode="summary")

    assert "## 主链摘要" in markdown
    assert "## 多Agent辩论" in markdown
    assert "分歧度 45%" in markdown
    assert "# AI 量化选股日报" not in markdown


def test_build_briefing_notification_returns_full_markdown_when_full_mode() -> None:
    briefing = Briefing(
        date="2026-06-04",
        sections=[BriefingSection(title="主链总览", content="PM主裁决: 维持观察")],
    )

    markdown = build_briefing_notification(briefing, mode="full")

    assert "# AI 量化选股日报 - 2026-06-04" in markdown


def test_build_daily_run_notification_includes_allocation_guidance() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-04",
        tradable=[],
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=1,
            downgrade_count=0,
            keep_count=0,
            top_focus=("300750 宁德时代",),
            watchlist=(),
            allocations=(
                PortfolioAllocation(
                    symbol="300750",
                    name="宁德时代",
                    weight=0.2,
                    rationale=("主链评分 72.0",),
                ),
            ),
            cash_reserve=0.8,
            allocation_note="单票上限 20%；信号强度不足时提高现金留存",
            regime_label="稳定上涨",
            strategy_mix_name="进攻牛市",
            strategy_mix_description="稳定上涨期，重仓动量+涨停板",
            strategy_focus=("动量趋势", "涨停接力"),
            strategy_weights=(("momentum", 0.3), ("limit_up_ladder", 0.3)),
        ),
        debate_results=(
            DebateResult(
                debate_id="d1",
                symbol="300750",
                name="宁德时代",
                original_score=72.0,
                rating="buy_candidate",
                final_consensus="趋势强但仍需确认开盘承接",
                disagreement_score=0.42,
                recommended_adjustment="raise",
            ),
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "- 当前市况: 稳定上涨" in markdown
    assert "- 策略主配比: 进攻牛市 | 稳定上涨期，重仓动量+涨停板" in markdown
    assert "- 优先策略: 动量趋势、涨停接力" in markdown
    assert "- 配仓建议: 300750 20%" in markdown
    assert "- 现金留存: 80%" in markdown
    assert "## 配仓执行" in markdown
    assert "300750 宁德时代 20% | 主链评分 72.0" in markdown
    assert "## 多Agent辩论" in markdown
    assert "趋势强但仍需确认开盘承接" in markdown
    assert "先看 300750 宁德时代 的开盘强弱与流动性" in markdown


def test_build_daily_run_notification_surfaces_watchlist_blockers_when_no_allocations() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-04",
        tradable=[],
        candidates=(),
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=2,
            keep_count=1,
            top_focus=(),
            watchlist=("000021 深科技", "000338 潍柴动力"),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="单票上限 20%；今日不建议建立主仓",
            action_hotspots=("板块集中度过高，压低科技暴露",),
            execution_blockers=("000021 深科技: 板块集中度过高，压低科技暴露",),
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "- 观察池: 000021 深科技、000338 潍柴动力" in markdown
    assert "- 主链状态: 今日无可执行标的，转入观察池：000021 深科技、000338 潍柴动力" in markdown
    assert "- 裁决热点: 板块集中度过高，压低科技暴露" in markdown
    assert "- 执行阻塞: 000021 深科技: 板块集中度过高，压低科技暴露" in markdown
    assert "暂无可执行主仓，先盯观察池" in markdown
    assert "只有阻塞条件解除后再考虑转入执行名单" in markdown


def test_build_daily_run_notification_surfaces_watch_reviews_as_checklist() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-05",
        tradable=[],
        candidates=(),
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=2,
            keep_count=1,
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
                    next_step="等待量价继续走强后，再评估是否转入执行名单",
                    review_window="盘中走强后",
                    priority="high",
                ),
                WatchlistReviewItem(
                    symbol="000001",
                    name="平安银行",
                    blocker="高相关未解除",
                    next_step="等待高相关标的分化后，再重新评估执行顺位",
                    review_window="分化确认后",
                    priority="medium",
                ),
            ),
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "- 观察复核:" in markdown
    assert (
        "  - 688981 中芯国际 | 高优先级 / 盘中走强后 | 等待量价继续走强后，再评估是否转入执行名单"
        in markdown
    )
    assert (
        "1. 先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入执行名单（高优先级 / 盘中走强后）。"
        in markdown
    )


def test_build_daily_run_notification_lists_watch_candidates_when_not_tradable() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-05",
        tradable=[],
        candidates=(
            PickResult(
                symbol="688981",
                name="中芯国际",
                date="2026-06-05",
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
                metrics={
                    "candidate_status": "新晋",
                    "candidate_next_step": "等待量价继续走强后，再评估是否转入执行名单",
                    "candidate_review_window": "盘中走强后",
                    "candidate_review_priority": "high",
                },
            ),
            PickResult(
                symbol="000001",
                name="平安银行",
                date="2026-06-05",
                close=10.82,
                score=-18.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=10.82,
                stop_loss=10.73,
                take_profit=11.731,
                position="watch",
                strategies=(),
                reasons=("估值防守",),
                risks=("缺少量能确认",),
                metrics={
                    "candidate_status": "观察阻塞",
                    "candidate_blocker": "板块集中度过高，压低银行暴露",
                    "candidate_next_step": "等待板块暴露回落后，再重新评估执行顺位",
                },
            ),
        ),
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=1,
            keep_count=1,
            top_focus=(),
            watchlist=("688981 中芯国际", "000001 平安银行"),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="今日以观察为主",
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "## Top 候选" in markdown
    assert "1. 688981 中芯国际 | 新晋 | -9分 | 观察 | MA20 斜率向上" in markdown
    assert "复核: 高优先级 / 盘中走强后" in markdown
    assert (
        "2. 000001 平安银行 | 观察阻塞 | -18分 | 观察 | 估值防守 | 阻塞: 板块集中度过高，压低银行暴露"
        in markdown
    )
    assert (
        "1. 先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入执行名单（高优先级 / 盘中走强后）。"
        in markdown
    )


def test_build_daily_run_notification_includes_candidate_status_for_tradable_pick() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-05",
        tradable=(
            PickResult(
                symbol="300750",
                name="宁德时代",
                date="2026-06-05",
                close=220.5,
                score=73.0,
                rating="buy_candidate",
                entry_type="relative_strength",
                ideal_buy=220.5,
                stop_loss=214.2,
                take_profit=238.0,
                position="10%-30%",
                strategies=(),
                reasons=("趋势延续",),
                risks=("高开回落",),
                metrics={"candidate_status": "延续上升"},
            ),
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "- 首选标的: 300750 宁德时代 | 延续上升 | 73分 | 买 220.5 / 损 214.2 / 盈 238" in markdown
    assert "1. 300750 宁德时代 | 延续上升 | 73分 | 买 220.5 / 损 214.2 / 盈 238" in markdown


def test_build_daily_run_notification_surfaces_default_review_for_new_watch_pick() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-05",
        tradable=[],
        candidates=(
            PickResult(
                symbol="688981",
                name="中芯国际",
                date="2026-06-05",
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
                metrics={
                    "candidate_status": "新晋",
                    "candidate_next_step": "等待量价继续走强后，再评估是否转入执行名单",
                    "candidate_review_window": "盘中走强后",
                    "candidate_review_priority": "high",
                },
            ),
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "复核: 高优先级 / 盘中走强后" in markdown
    assert "1. 先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入执行名单（高优先级 / 盘中走强后）。" in markdown


def test_build_daily_run_notification_surfaces_snapshot_diff_highlights() -> None:
    markdown = build_daily_run_notification(
        run_date="2026-06-05",
        tradable=[],
        candidates=(),
        portfolio_summary=PortfolioDecisionSummary(
            promote_count=0,
            downgrade_count=1,
            keep_count=1,
            top_focus=(),
            watchlist=("688981 中芯国际",),
            allocations=(),
            cash_reserve=1.0,
            allocation_note="今日以观察为主",
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
        snapshot_diff=SnapshotDiff(
            date_current="2026-06-05",
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

    assert "- 候选变化: 新增 1 / 移出 1 / 排名异动 1" in markdown
    assert "## 候选变化" in markdown
    assert "🆕 **新晋候选**: 688981 中芯国际" in markdown
    assert "❌ **移出候选**: 600036 招商银行" in markdown
    assert "📈 **排名异动**: 300750 #4→#5↓" in markdown


def test_build_monitor_notification_summary_mode_is_action_oriented() -> None:
    markdown = build_monitor_notification(
        [
            MonitorResult(
                name="stale_data",
                triggered=True,
                severity="critical",
                message="数据缓存文件不存在",
                details={"cache_path": "data/cache.db"},
            )
        ],
        mode="summary",
    )

    assert "## 核心结论" in markdown
    assert "## 行动建议" in markdown
    assert "stale_data" in markdown


def test_build_morning_breakout_notification_lists_top_candidates() -> None:
    signals = [
        BreakoutSignal(
            symbol="000001",
            name="平安银行",
            signal_type="强势打板",
            score=78.0,
            current_price=12.34,
            target_price=13.2,
            stop_loss=11.8,
            reasons=("量价齐升", "均线多头排列"),
            risks=("高波动",),
            confidence=0.72,
            entry_time="09:30 开盘瞬间",
            position_pct=0.2,
        )
    ]

    markdown = build_morning_breakout_notification(signals, mode="summary", top_n=3)

    assert "# 早盘打板策略" in markdown
    assert "000001 平安银行" in markdown
    assert "量价齐升" in markdown


def test_build_closing_premium_notification_lists_expected_return() -> None:
    signals = [
        PremiumSignal(
            symbol="600000",
            name="浦发银行",
            signal_type="量价突破",
            score=81.0,
            current_price=10.2,
            entry_price=10.15,
            stop_loss=9.8,
            take_profit_1=10.7,
            take_profit_2=11.1,
            reasons=("尾盘资金流入",),
            risks=("高开风险较大",),
            confidence=0.75,
            holding_days=2,
            expected_return=5.42,
        )
    ]

    markdown = build_closing_premium_notification(signals, mode="summary", top_n=3)

    assert "# 尾盘溢价策略" in markdown
    assert "预期 5.42%" in markdown
    assert "尾盘资金流入" in markdown


def test_build_closing_review_notification_summary_mode_highlights_main_chain() -> None:
    review = DailyReview(
        date="2026-06-04",
        total_signals=4,
        executed_signals=2,
        win_count=1,
        loss_count=1,
        win_rate=0.5,
        total_return=1.8,
        max_single_win=3.0,
        max_single_loss=-1.2,
        avg_holding_days=1.5,
        strategy_breakdown={
            "早盘打板": {"total": 2, "wins": 1, "losses": 1, "total_return": 1.8, "win_rate": 0.5}
        },
        market_environment="震荡市",
        main_chain_summary=("PM主裁决: 上调 1 / 降级 2 / 维持 1",),
        key_lessons=("止损执行尚可，但入场分散度不足",),
        improvement_suggestions=("减少同类信号堆叠，优先保留最强票",),
    )

    markdown = build_closing_review_notification(review=review, mode="summary")

    assert "# 收盘复盘" in markdown
    assert "PM主裁决: 上调 1 / 降级 2 / 维持 1" in markdown
    assert "减少同类信号堆叠" in markdown


def test_build_closing_review_notification_weekly_mode_supports_summary() -> None:
    weekly = WeeklySummary(
        week_start="2026-06-01",
        week_end="2026-06-05",
        total_trades=8,
        win_rate=0.625,
        total_return=6.5,
        sharpe_ratio=1.2,
        max_drawdown=2.1,
        best_strategy="尾盘溢价",
        worst_strategy="早盘打板",
        market_trend="震荡偏强",
        next_week_outlook="延续轻仓试错，优先做主链共识标的",
    )

    markdown = build_closing_review_notification(weekly_summary=weekly, mode="summary")

    assert "# 周度复盘" in markdown
    assert "尾盘溢价" in markdown
    assert "延续轻仓试错" in markdown
