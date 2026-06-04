from __future__ import annotations

from aqsp.briefing import Briefing, BriefingSection
from aqsp.briefing.closing_review import DailyReview, WeeklySummary
from aqsp.briefing.debate import DebateResult
from aqsp.monitor.checker import MonitorResult
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.portfolio.manager import PortfolioDecisionSummary
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
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "- 当前市况: 稳定上涨" in markdown
    assert "- 策略主配比: 进攻牛市 | 稳定上涨期，重仓动量+涨停板" in markdown
    assert "- 优先策略: 动量趋势、涨停接力" in markdown
    assert "- 配仓建议: 300750 20%" in markdown
    assert "- 现金留存: 80%" in markdown


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
