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


def test_build_briefing_notification_includes_debate_summary_when_summary_mode() -> (
    None
):
    briefing = Briefing(
        date="2026-06-04",
        sections=[
            BriefingSection(title="主链总览", content="PM主裁决: 维持观察"),
            BriefingSection(
                title="明日重点", content="**000001 平安银行** 观察开盘强弱"
            ),
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


def test_build_briefing_notification_includes_research_radar_when_summary_mode() -> (
    None
):
    briefing = Briefing(
        date="2026-06-04",
        sections=[
            BriefingSection(title="主链总览", content="PM主裁决: 维持观察"),
            BriefingSection(
                title="研究吸收",
                content="\n".join(
                    [
                        "- 研究发现落盘: **未落盘（按配置吸收队列展示）**",
                        "- 已吸收但未直接入分策略族: **4**",
                        "- 已部分实现策略族: **5**",
                        "- 下一接入重点: data_source/baostock [P1] - 补 fixture",
                        "- 当前前置缺口: data_source/tushare - needs_env (TUSHARE_TOKEN)",
                        "- 原则: 研究内容只做候选和解释，不直接覆盖 runtime 打分。",
                    ]
                ),
            ),
            BriefingSection(title="明日重点", content="观察开盘强弱"),
        ],
    )

    markdown = build_briefing_notification(briefing, mode="summary")

    assert "## 研究雷达" in markdown
    assert "未落盘（按配置吸收队列展示）" in markdown
    assert "已吸收但未直接入分策略族" in markdown
    assert "data_source/baostock" in markdown
    assert "TUSHARE_TOKEN" in markdown
    assert "不直接覆盖 runtime 打分" in markdown


def test_build_briefing_notification_returns_full_markdown_when_full_mode() -> None:
    briefing = Briefing(
        date="2026-06-04",
        sections=[BriefingSection(title="主链总览", content="PM主裁决: 维持观察")],
    )

    markdown = build_briefing_notification(briefing, mode="full")

    assert "# AI 量化选股日报 - 2026-06-04" in markdown


def test_build_briefing_notification_sanitizes_research_wording_in_both_modes() -> None:
    briefing = Briefing(
        date="2026-06-04",
        sections=[
            BriefingSection(
                title="主链总览",
                content="立即买入 600519，加入执行名单，等待下单。",
            ),
            BriefingSection(title="明日重点", content="执行开仓后看真实持仓。"),
        ],
    )

    summary_markdown = build_briefing_notification(briefing, mode="summary")
    full_markdown = build_briefing_notification(briefing, mode="full")
    combined = "\n".join((summary_markdown, full_markdown))

    assert "纸面重点观察" in combined
    assert "纸面复核名单" in combined
    assert "纸面记录" in combined
    assert "纸面持有" in combined
    assert "立即买入" not in combined
    assert "执行名单" not in combined
    assert "执行开仓" not in combined
    assert "真实持仓" not in combined
    assert "下单" not in combined


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
    assert "- 纸面配仓参考: 300750 20%" in markdown
    assert "- 现金留存: 80%" in markdown
    assert "## 📌 今日快照" in markdown
    assert "| 项目 | 结论 | 先看什么 |" in markdown
    assert "| 候选 | ⏸️ 暂无清晰候选 | 等待下一轮信号 |" in markdown
    assert "| 纸面现实 | 🧪 纸面配仓 20% | 300750 宁德时代 20% |" in markdown
    assert (
        "| 分歧 | 🗣️ 最高分歧 42% | 300750 宁德时代：趋势强但仍需确认开盘承接 |"
        in markdown
    )
    assert "## 🧭 阅读顺序" in markdown
    assert markdown.index("## 📌 今日快照") < markdown.index("## 🧭 阅读顺序")
    assert "1. 🧪 先看纸面配仓复核：300750 宁德时代，核对开盘承接和流动性。" in markdown
    assert "2. 🔍 再看候选简表：确认状态、分数、关键点是否一致。" in markdown
    assert "3. 🗣️ 最后看多 Agent 分歧：300750 宁德时代 分歧度 42%。" in markdown
    assert markdown.index("## 🧭 阅读顺序") < markdown.index("## 📋 候选简表")
    assert "## 纸面配仓参考" in markdown
    assert "300750 宁德时代 20% | 主链评分 72.0" in markdown
    assert "## 多Agent辩论" in markdown
    assert "趋势强但仍需确认开盘承接" in markdown
    assert "先看 300750 宁德时代 的开盘强弱与流动性" in markdown
    assert "参考仓位执行" not in markdown
    assert "可执行标的" not in markdown
    assert "首选标的" not in markdown
    assert "配仓建议" not in markdown
    assert "配仓执行" not in markdown
    assert "新开仓" not in markdown


def test_build_daily_run_notification_surfaces_watchlist_blockers_when_no_allocations() -> (
    None
):
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
    assert (
        "- 主链状态: 今日无纸面复核对象，转入观察池：000021 深科技、000338 潍柴动力"
        in markdown
    )
    assert "- 裁决热点: 板块集中度过高，压低科技暴露" in markdown
    assert "- 纸面阻塞: 000021 深科技: 板块集中度过高，压低科技暴露" in markdown
    assert "| 候选 | ⏸️ 暂无清晰候选 | 等待下一轮信号 |" in markdown
    assert "| 纸面现实 | 👀 观察池优先 | 000021 深科技、000338 潍柴动力 |" in markdown
    assert (
        "| 风险/阻塞 | 🔒 1 条阻塞 | 000021 深科技: 板块集中度过高，压低科技暴露 |"
        in markdown
    )
    assert "## 🧭 阅读顺序" in markdown
    assert "1. ⏸️ 先看空档：今日无清晰候选，不为了凑数量推进。" in markdown
    assert (
        "2. 🔒 再看风险/阻塞：000021 深科技: 板块集中度过高，压低科技暴露" in markdown
    )
    assert "3. 📌 最后看约束：单票上限 20%；今日不建议建立主仓。" in markdown
    assert "暂无可执行主仓，先盯观察池" not in markdown
    assert "暂无纸面复核主线，先盯观察池" in markdown
    assert "只有阻塞条件解除后再考虑转入纸面复核名单" in markdown


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
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "- 观察复核:" in markdown
    assert (
        "  - 688981 中芯国际 | 高优先级 / 盘中走强后 | 等待量价继续走强后，再评估是否转入纸面复核名单"
        in markdown
    )
    assert (
        "1. 先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入纸面复核名单（高优先级 / 盘中走强后）。"
        in markdown
    )


def test_build_daily_run_notification_lists_watch_candidates_when_not_tradable() -> (
    None
):
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
                    "candidate_next_step": "等待量价继续走强后，再评估是否转入纸面复核名单",
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
                    "candidate_next_step": "等待板块暴露回落后，再重新评估纸面复核优先级",
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

    assert "## 📋 候选简表" in markdown
    assert "| # | 标的 | 状态 | 分数 | 处理 | 关键点 |" in markdown
    assert (
        "| 1 | 688981 中芯国际 | 新晋 | -9 | 👀 观察 | 等待量价继续走强后，再评估是否转入纸面复核名单；复核 高优先级 / 盘中走强后 |"
        in markdown
    )
    assert (
        "| 2 | 000001 平安银行 | 观察阻塞 | -18 | ⛔ 等阻塞解除 | 板块集中度过高，压低银行暴露 |"
        in markdown
    )
    assert "| 候选 | 👀 观察 2 / 阻塞 1 | 688981 中芯国际 |" in markdown
    assert (
        "| 风险/阻塞 | 🔒 1 条阻塞 | 000001 平安银行：板块集中度过高，压低银行暴露 |"
        in markdown
    )
    assert (
        "1. 先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入纸面复核名单（高优先级 / 盘中走强后）。"
        in markdown
    )


def test_build_daily_run_notification_includes_candidate_status_for_tradable_pick() -> (
    None
):
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

    assert (
        "- 纸面重点: 300750 宁德时代 | 延续上升 | 73分 | 参考 220.5 / 防守 214.2 / 目标 238"
        in markdown
    )
    assert (
        "| 1 | 300750 宁德时代 | 延续上升 | 73 | 🎯 纸面复核 | 趋势延续 |" in markdown
    )
    assert "买 220.5" not in markdown


def test_build_daily_run_notification_surfaces_default_review_for_new_watch_pick() -> (
    None
):
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
                    "candidate_next_step": "等待量价继续走强后，再评估是否转入纸面复核名单",
                    "candidate_review_window": "盘中走强后",
                    "candidate_review_priority": "high",
                },
            ),
        ),
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="eastmoney 健康",
    )

    assert "复核 高优先级 / 盘中走强后" in markdown
    assert (
        "1. 先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入纸面复核名单（高优先级 / 盘中走强后）。"
        in markdown
    )


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
    assert "归档移出记录: 600036 招商银行" in markdown
    assert "排名记录变化: 300750 #4→#5↓" in markdown
    assert "❌ **移出候选**" not in markdown


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
    assert "## 处理清单" in markdown
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
    assert "纸面观察候选" in markdown
    assert "000001 平安银行" in markdown
    assert "量价齐升" in markdown
    assert "首选标的" not in markdown
    assert "默认轻仓" not in markdown


def test_build_morning_breakout_notification_full_mode_keeps_research_wording() -> None:
    signals = [
        BreakoutSignal(
            symbol="000001",
            name="平安银行",
            signal_type="强势打板",
            score=78.0,
            current_price=12.34,
            target_price=13.2,
            stop_loss=11.8,
            reasons=("量价齐升",),
            risks=("高波动",),
            confidence=0.72,
            entry_time="09:30 开盘瞬间",
            position_pct=0.2,
        )
    ]

    markdown = build_morning_breakout_notification(signals, mode="full", top_n=3)

    assert "纸面观察候选" in markdown
    assert "观察目标" in markdown
    assert "策略推荐" not in markdown
    assert "推荐 Top" not in markdown
    assert "建议仓位" not in markdown
    assert "入场时间" not in markdown


def test_build_closing_premium_notification_lists_observation_space() -> None:
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
    assert "纸面观察候选" in markdown
    assert "观察空间 5.42%" in markdown
    assert "尾盘资金流入" in markdown
    assert "首选标的" not in markdown
    assert "入场" not in markdown
    assert "预期 5.42%" not in markdown


def test_build_closing_premium_notification_full_mode_keeps_research_wording() -> None:
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

    markdown = build_closing_premium_notification(signals, mode="full", top_n=3)

    assert "纸面观察候选" in markdown
    assert "观察空间 5.42%" in markdown
    assert "策略推荐" not in markdown
    assert "推荐 Top" not in markdown
    assert "建议入场" not in markdown
    assert "操作建议" not in markdown
    assert "尾盘入场" not in markdown


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
            "早盘打板": {
                "total": 2,
                "wins": 1,
                "losses": 1,
                "total_return": 1.8,
                "win_rate": 0.5,
            }
        },
        market_environment="震荡市",
        main_chain_summary=(
            "PM主裁决: 上调 1 / 降级 2 / 维持 1",
            "纸面阻塞: 688981 中芯国际: 板块集中度过高",
            "观察复核: 688981 中芯国际 | 高优先级 / 盘中走强后 | 等待量价继续走强后，再评估是否转入纸面复核名单",
        ),
        key_lessons=("止损执行尚可，但入场分散度不足",),
        improvement_suggestions=("减少同类信号堆叠，优先保留最强票",),
    )

    markdown = build_closing_review_notification(review=review, mode="summary")

    assert "# 收盘复盘" in markdown
    assert "PM主裁决: 上调 1 / 降级 2 / 维持 1" in markdown
    assert "纸面阻塞: 688981 中芯国际: 板块集中度过高" in markdown
    assert "观察复核: 688981 中芯国际 | 高优先级 / 盘中走强后" in markdown
    assert "减少同类信号堆叠" in markdown
    assert "优先复核 688981 中芯国际 | 高优先级 / 盘中走强后" in markdown


def test_build_closing_review_notification_sanitizes_full_and_summary_modes() -> None:
    review = DailyReview(
        date="2026-06-04",
        total_signals=1,
        executed_signals=1,
        win_count=1,
        loss_count=0,
        win_rate=1.0,
        total_return=2.0,
        max_single_win=2.0,
        max_single_loss=0.0,
        avg_holding_days=1.0,
        strategy_breakdown={},
        market_environment="震荡市",
        main_chain_summary=(
            "执行阻塞: 600519 需要执行开仓后下单",
            "观察复核: 600519 立即买入，查看真实持仓",
        ),
        key_lessons=("买入后表现较强",),
        improvement_suggestions=("下周不再首选下单。",),
    )

    summary_markdown = build_closing_review_notification(review=review, mode="summary")
    full_markdown = build_closing_review_notification(review=review, mode="full")
    combined = "\n".join((summary_markdown, full_markdown))

    assert "纸面阻塞" in combined
    assert "纸面推进" in combined
    assert "纸面记录" in combined
    assert "纸面重点观察" in combined
    assert "纸面入场记录后表现较强" in combined
    assert "执行阻塞" not in combined
    assert "执行开仓" not in combined
    assert "立即买入" not in combined
    assert "真实持仓" not in combined
    assert "首选下单" not in combined
    assert "下单" not in combined


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
