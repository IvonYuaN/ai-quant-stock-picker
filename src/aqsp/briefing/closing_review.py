from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class TradeReview:
    """交易复盘"""

    symbol: str
    name: str
    strategy_type: str
    signal_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    is_win: bool
    holding_days: int
    exit_reason: str
    lessons: tuple[str, ...]


@dataclass(frozen=True)
class DailyReview:
    """每日复盘"""

    date: str
    total_signals: int
    executed_signals: int
    win_count: int
    loss_count: int
    win_rate: float
    total_return: float
    max_single_win: float
    max_single_loss: float
    avg_holding_days: float
    strategy_breakdown: dict[str, dict]
    market_environment: str
    key_lessons: tuple[str, ...]
    improvement_suggestions: tuple[str, ...]


@dataclass(frozen=True)
class WeeklySummary:
    """周度总结"""

    week_start: str
    week_end: str
    total_trades: int
    win_rate: float
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    best_strategy: str
    worst_strategy: str
    market_trend: str
    next_week_outlook: str


class ClosingReviewer:
    """收盘复盘分析器

    核心功能：
    1. 验证早盘/尾盘预测结果
    2. 计算当日盈亏
    3. 评估策略效果
    4. 提取经验教训
    5. 生成复盘报告
    """

    def __init__(self, ledger_path: str = "data/predictions.jsonl") -> None:
        self.ledger_path = ledger_path

    def review_today(self, today: str | None = None) -> DailyReview:
        """复盘今日交易

        Args:
            today: 日期，格式YYYY-MM-DD，默认为今天

        Returns:
            每日复盘结果
        """
        if today is None:
            today = now_shanghai().strftime("%Y-%m-%d")

        predictions = self._load_predictions(today)

        if not predictions:
            return self._empty_review(today)

        reviews = [self._review_single_prediction(pred) for pred in predictions]

        total_signals = len(reviews)
        executed_signals = len([r for r in reviews if r.return_pct != 0])
        win_count = len([r for r in reviews if r.is_win])
        loss_count = executed_signals - win_count
        win_rate = win_count / executed_signals if executed_signals > 0 else 0
        total_return = sum(r.return_pct for r in reviews)

        returns = [r.return_pct for r in reviews if r.return_pct != 0]
        max_single_win = max(returns) if returns else 0
        max_single_loss = min(returns) if returns else 0
        avg_holding_days = (
            sum(r.holding_days for r in reviews) / len(reviews) if reviews else 0
        )

        strategy_breakdown = self._calculate_strategy_breakdown(reviews)
        market_environment = self._evaluate_market_environment(today)
        key_lessons = self._extract_key_lessons(reviews)
        improvement_suggestions = self._generate_improvement_suggestions(
            reviews, win_rate
        )

        return DailyReview(
            date=today,
            total_signals=total_signals,
            executed_signals=executed_signals,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            total_return=total_return,
            max_single_win=max_single_win,
            max_single_loss=max_single_loss,
            avg_holding_days=avg_holding_days,
            strategy_breakdown=strategy_breakdown,
            market_environment=market_environment,
            key_lessons=key_lessons,
            improvement_suggestions=improvement_suggestions,
        )

    def _load_predictions(self, date: str) -> list[dict]:
        """加载指定日期的预测记录"""
        predictions = []
        ledger_path = Path(self.ledger_path)

        if not ledger_path.exists():
            return predictions

        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pred = json.loads(line)
                    if pred.get("signal_date") == date:
                        predictions.append(pred)
                except json.JSONDecodeError:
                    continue

        return predictions

    def _review_single_prediction(self, pred: dict) -> TradeReview:
        """复盘单条预测"""
        symbol = pred.get("symbol", "")
        name = pred.get("name", "")
        strategy_type = pred.get("strategy_type", "未知")
        signal_date = pred.get("signal_date", "")
        entry_price = pred.get("entry_price", 0)

        current_price = pred.get("current_price", entry_price)
        return_pct = pred.get("return_pct", 0)

        is_win = return_pct > 0
        holding_days = pred.get("holding_days", 1)
        exit_reason = self._determine_exit_reason(return_pct)
        lessons = self._extract_lessons(pred, return_pct, is_win)

        return TradeReview(
            symbol=symbol,
            name=name,
            strategy_type=strategy_type,
            signal_date=signal_date,
            entry_price=entry_price,
            exit_price=current_price,
            return_pct=return_pct,
            is_win=is_win,
            holding_days=holding_days,
            exit_reason=exit_reason,
            lessons=lessons,
        )

    def _determine_exit_reason(self, return_pct: float) -> str:
        """确定退出原因"""
        if return_pct >= 5:
            return "止盈"
        if return_pct <= -3:
            return "止损"
        return "到期平仓"

    def _extract_lessons(
        self, pred: dict, return_pct: float, is_win: bool
    ) -> tuple[str, ...]:
        """提取经验教训"""
        lessons = []

        if is_win:
            if return_pct > 5:
                lessons.append("大赚：策略判断准确，可加大仓位")
            else:
                lessons.append("小赚：符合预期，继续执行")
        else:
            if return_pct < -3:
                lessons.append("大亏：需检查止损是否及时")
            else:
                lessons.append("小亏：正常波动，保持纪律")

        strategy_type = pred.get("strategy_type", "")
        if strategy_type == "早盘打板" and not is_win:
            lessons.append("打板失败：需关注市场整体环境")
        elif strategy_type == "尾盘溢价" and not is_win:
            lessons.append("溢价失败：需检查量价配合")

        return tuple(lessons)

    def _calculate_strategy_breakdown(
        self, reviews: list[TradeReview]
    ) -> dict[str, dict]:
        """按策略分类统计"""
        breakdown: dict[str, dict] = {}

        for review in reviews:
            strategy = review.strategy_type
            if strategy not in breakdown:
                breakdown[strategy] = {
                    "total": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_return": 0,
                    "win_rate": 0,
                }

            breakdown[strategy]["total"] += 1
            breakdown[strategy]["total_return"] += review.return_pct

            if review.is_win:
                breakdown[strategy]["wins"] += 1
            elif review.return_pct != 0:
                breakdown[strategy]["losses"] += 1

        for strategy in breakdown:
            total = breakdown[strategy]["total"]
            wins = breakdown[strategy]["wins"]
            breakdown[strategy]["win_rate"] = wins / total if total > 0 else 0

        return breakdown

    def _evaluate_market_environment(self, date: str) -> str:
        """评估市场环境"""
        return "震荡市"

    def _extract_key_lessons(self, reviews: list[TradeReview]) -> tuple[str, ...]:
        """提取关键经验教训"""
        lessons = []

        win_count = len([r for r in reviews if r.is_win])
        loss_count = len([r for r in reviews if not r.is_win and r.return_pct != 0])

        if loss_count > win_count:
            lessons.append("今日亏损较多，需反思策略是否适应当前市场")

        big_losses = [r for r in reviews if r.return_pct < -3]
        if big_losses:
            lessons.append("存在大亏交易，需严格执行止损")

        breakout_reviews = [r for r in reviews if r.strategy_type == "早盘打板"]
        if breakout_reviews:
            breakout_win_rate = len([r for r in breakout_reviews if r.is_win]) / len(
                breakout_reviews
            )
            if breakout_win_rate < 0.5:
                lessons.append("打板成功率偏低，需关注市场情绪")

        return tuple(lessons)

    def _generate_improvement_suggestions(
        self, reviews: list[TradeReview], win_rate: float
    ) -> tuple[str, ...]:
        """生成改进建议"""
        suggestions = []

        if win_rate < 0.5:
            suggestions.append("胜率偏低，建议减少交易频率，提高选股标准")

        recent_reviews = sorted(reviews, key=lambda x: x.signal_date)[-5:]
        recent_losses = len([r for r in recent_reviews if not r.is_win])
        if recent_losses >= 3:
            suggestions.append("连续亏损，建议暂停交易，观察市场")

        big_losses = [r for r in reviews if r.return_pct < -5]
        if big_losses:
            suggestions.append("存在大亏交易，建议降低单笔仓位")

        return tuple(suggestions)

    def _empty_review(self, date: str) -> DailyReview:
        """生成空复盘"""
        return DailyReview(
            date=date,
            total_signals=0,
            executed_signals=0,
            win_count=0,
            loss_count=0,
            win_rate=0,
            total_return=0,
            max_single_win=0,
            max_single_loss=0,
            avg_holding_days=0,
            strategy_breakdown={},
            market_environment="无数据",
            key_lessons=("今日无交易信号",),
            improvement_suggestions=("继续观察市场",),
        )

    def generate_weekly_summary(self, end_date: str | None = None) -> WeeklySummary:
        """生成周度总结

        Args:
            end_date: 结束日期，默认为今天

        Returns:
            周度总结
        """
        if end_date is None:
            end_date = now_shanghai().strftime("%Y-%m-%d")

        end = now_shanghai().strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=6)
        week_start = start.strftime("%Y-%m-%d")

        all_reviews: list[TradeReview] = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            predictions = self._load_predictions(date_str)
            for pred in predictions:
                all_reviews.append(self._review_single_prediction(pred))
            current += timedelta(days=1)

        total_trades = len(all_reviews)
        win_count = len([r for r in all_reviews if r.is_win])
        win_rate = win_count / total_trades if total_trades > 0 else 0
        total_return = sum(r.return_pct for r in all_reviews)

        returns = [r.return_pct for r in all_reviews]
        sharpe_ratio = self._calculate_sharpe_ratio(returns)
        max_drawdown = self._calculate_max_drawdown(returns)

        strategy_stats: dict[str, dict] = {}
        for review in all_reviews:
            strategy = review.strategy_type
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {"total_return": 0, "count": 0}
            strategy_stats[strategy]["total_return"] += review.return_pct
            strategy_stats[strategy]["count"] += 1

        best_strategy = ""
        worst_strategy = ""
        if strategy_stats:
            best_strategy = max(
                strategy_stats,
                key=lambda s: strategy_stats[s]["total_return"],
            )
            worst_strategy = min(
                strategy_stats,
                key=lambda s: strategy_stats[s]["total_return"],
            )

        return WeeklySummary(
            week_start=week_start,
            week_end=end_date,
            total_trades=total_trades,
            win_rate=win_rate,
            total_return=total_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            best_strategy=best_strategy,
            worst_strategy=worst_strategy,
            market_trend="震荡",
            next_week_outlook="观望为主",
        )

    def _calculate_sharpe_ratio(self, returns: list[float]) -> float:
        """计算夏普比率"""
        if not returns:
            return 0.0
        avg_return = sum(returns) / len(returns)
        if len(returns) < 2:
            return 0.0
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = variance**0.5
        if std_dev == 0:
            return 0.0
        return avg_return / std_dev

    def _calculate_max_drawdown(self, returns: list[float]) -> float:
        """计算最大回撤"""
        if not returns:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in returns:
            cumulative += r
            peak = max(peak, cumulative)
            drawdown = peak - cumulative
            max_dd = max(max_dd, drawdown)
        return max_dd


def format_daily_review(review: DailyReview) -> str:
    """格式化每日复盘为报告"""
    report = []
    report.append("📊 每日交易复盘")
    report.append("=" * 60)
    report.append(f"📅 日期: {review.date}")
    report.append("")

    report.append("📈 总体统计")
    report.append("-" * 40)
    report.append(f"  总信号数: {review.total_signals}")
    report.append(f"  执行交易: {review.executed_signals}")
    report.append(f"  盈利笔数: {review.win_count}")
    report.append(f"  亏损笔数: {review.loss_count}")
    report.append(f"  胜率: {review.win_rate:.1%}")
    report.append(f"  总收益: {review.total_return:.2f}%")
    report.append(f"  最大单笔盈利: {review.max_single_win:.2f}%")
    report.append(f"  最大单笔亏损: {review.max_single_loss:.2f}%")
    report.append(f"  平均持有天数: {review.avg_holding_days:.1f}天")
    report.append("")

    if review.strategy_breakdown:
        report.append("📊 策略分类统计")
        report.append("-" * 40)
        for strategy, stats in review.strategy_breakdown.items():
            report.append(f"  【{strategy}】")
            report.append(f"    交易次数: {stats['total']}")
            report.append(f"    胜率: {stats['win_rate']:.1%}")
            report.append(f"    总收益: {stats['total_return']:.2f}%")
        report.append("")

    report.append("🌍 市场环境")
    report.append("-" * 40)
    report.append(f"  {review.market_environment}")
    report.append("")

    if review.key_lessons:
        report.append("💡 关键经验教训")
        report.append("-" * 40)
        for i, lesson in enumerate(review.key_lessons, 1):
            report.append(f"  {i}. {lesson}")
        report.append("")

    if review.improvement_suggestions:
        report.append("🔧 改进建议")
        report.append("-" * 40)
        for i, suggestion in enumerate(review.improvement_suggestions, 1):
            report.append(f"  {i}. {suggestion}")
        report.append("")

    if review.win_rate >= 0.6:
        report.append("🎉 今日表现优秀，继续保持！")
    elif review.win_rate >= 0.4:
        report.append("💪 今日表现一般，明日继续努力！")
    else:
        report.append("🤔 今日表现不佳，需反思改进！")

    return "\n".join(report)


def format_weekly_summary(summary: WeeklySummary) -> str:
    """格式化周度总结为报告"""
    report = []
    report.append("📊 周度交易总结")
    report.append("=" * 60)
    report.append(f"📅 周期: {summary.week_start} 至 {summary.week_end}")
    report.append("")

    report.append("📈 周度统计")
    report.append("-" * 40)
    report.append(f"  总交易次数: {summary.total_trades}")
    report.append(f"  胜率: {summary.win_rate:.1%}")
    report.append(f"  总收益: {summary.total_return:.2f}%")
    report.append(f"  夏普比率: {summary.sharpe_ratio:.2f}")
    report.append(f"  最大回撤: {summary.max_drawdown:.2f}%")
    report.append("")

    report.append("🏆 策略表现")
    report.append("-" * 40)
    report.append(f"  最佳策略: {summary.best_strategy}")
    report.append(f"  最差策略: {summary.worst_strategy}")
    report.append("")

    report.append("🌍 市场趋势")
    report.append("-" * 40)
    report.append(f"  {summary.market_trend}")
    report.append("")

    report.append("🔮 下周展望")
    report.append("-" * 40)
    report.append(f"  {summary.next_week_outlook}")
    report.append("")

    return "\n".join(report)
