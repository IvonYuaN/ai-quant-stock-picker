from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aqsp.core.time import now_shanghai
from aqsp.paper import read_paper_trades
from aqsp.presentation import format_symbol_name
from aqsp.ratings import is_tradable_rating, rating_label


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
    main_chain_summary: tuple[str, ...]
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

    def __init__(
        self,
        ledger_path: str = "data/predictions.jsonl",
        paper_ledger_path: str = "data/paper_trades.jsonl",
    ) -> None:
        self.ledger_path = ledger_path
        self.paper_ledger_path = paper_ledger_path

    def review_today(self, today: str | None = None) -> DailyReview:
        """复盘今日交易

        Args:
            today: 日期，格式YYYY-MM-DD，默认为今天

        Returns:
            每日复盘结果
        """
        if today is None:
            today = self._latest_review_date() or now_shanghai().strftime("%Y-%m-%d")

        predictions = self._load_predictions(today)
        paper_rows = self._load_paper_rows(signal_date=today)
        closed_rows = [row for row in paper_rows if row.get("status") == "closed"]
        pending_rows = [row for row in paper_rows if row.get("status") == "pending_entry"]
        blocked_rows = [
            row for row in paper_rows if row.get("status") == "not_executable"
        ]

        if not predictions and not paper_rows:
            return self._empty_review(today)

        predictions_by_id, predictions_by_symbol = self._prediction_indexes(predictions)
        reviews = [
            self._review_single_trade(
                paper_row=row,
                matched_prediction=self._matching_prediction(
                    paper_row=row,
                    predictions_by_id=predictions_by_id,
                    predictions_by_symbol=predictions_by_symbol,
                ),
            )
            for row in closed_rows
        ]

        total_signals = len(predictions) if predictions else len(paper_rows)
        executed_signals = len(reviews)
        win_count = len([r for r in reviews if r.is_win])
        loss_count = len([r for r in reviews if not r.is_win])
        win_rate = win_count / executed_signals if executed_signals > 0 else 0
        total_return = sum(r.return_pct for r in reviews)

        returns = [r.return_pct for r in reviews]
        max_single_win = max(returns) if returns else 0
        max_single_loss = min(returns) if returns else 0
        avg_holding_days = (
            sum(r.holding_days for r in reviews) / len(reviews) if reviews else 0
        )

        strategy_breakdown = self._calculate_strategy_breakdown(reviews)
        market_environment = self._evaluate_market_environment(today)
        key_lessons = self._extract_key_lessons(
            reviews,
            blocked_count=len(blocked_rows),
            pending_count=len(pending_rows),
            total_signals=total_signals,
        )
        main_chain_summary = self._build_main_chain_summary(predictions)
        improvement_suggestions = self._generate_improvement_suggestions(
            reviews,
            win_rate,
            pending_count=len(pending_rows),
            blocked_count=len(blocked_rows),
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
            main_chain_summary=main_chain_summary,
            key_lessons=key_lessons,
            improvement_suggestions=improvement_suggestions,
        )

    def _latest_review_date(self) -> str:
        return max(
            self._latest_signal_date(),
            self._latest_paper_signal_date(),
        )

    def _latest_signal_date(self) -> str:
        ledger_path = Path(self.ledger_path)
        if not ledger_path.exists():
            return ""

        latest = ""
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                signal_date = str(row.get("signal_date", "")).strip()
                if signal_date and signal_date > latest:
                    latest = signal_date
        return latest

    def _latest_paper_signal_date(self) -> str:
        latest = ""
        for row in self._load_paper_rows():
            signal_date = str(row.get("signal_date", "")).strip()
            if signal_date and signal_date > latest:
                latest = signal_date
        return latest

    def _candidate_status(self, pred: dict) -> str:
        return str(pred.get("candidate_status", "") or "").strip()

    def _candidate_blocker(self, pred: dict) -> str:
        return str(pred.get("candidate_blocker", "") or "").strip()

    def _candidate_next_step(self, pred: dict) -> str:
        return str(pred.get("candidate_next_step", "") or "").strip()

    def _candidate_review_window(self, pred: dict) -> str:
        return str(pred.get("candidate_review_window", "") or "").strip()

    def _candidate_review_priority(self, pred: dict) -> str:
        return str(pred.get("candidate_review_priority", "") or "").strip()

    def _review_priority_label(self, priority: str) -> str:
        labels = {"high": "高优先级", "medium": "中优先级", "low": "低优先级"}
        return labels.get(priority, priority or "")

    def _build_main_chain_summary(self, predictions: list[dict]) -> tuple[str, ...]:
        if not predictions:
            return ()

        promoted = sum(
            1
            for pred in predictions
            if str(pred.get("portfolio_action", "")).strip() == "promote"
        )
        downgraded = sum(
            1
            for pred in predictions
            if str(pred.get("portfolio_action", "")).strip() == "downgrade"
        )
        kept = sum(
            1
            for pred in predictions
            if str(pred.get("portfolio_action", "")).strip() == "keep"
        )

        tradable: list[str] = []
        watchlist: list[str] = []
        blockers: list[str] = []
        review_items: list[str] = []
        for pred in predictions:
            symbol = str(pred.get("symbol", "")).strip()
            name = str(pred.get("name", "")).strip()
            display = format_symbol_name(symbol, name)
            rating = str(pred.get("rating", "")).strip()
            if is_tradable_rating(rating):
                tradable.append(display)
            else:
                watchlist.append(display)
            blocker = self._candidate_blocker(pred)
            next_step = self._candidate_next_step(pred)
            review_meta = " / ".join(
                part
                for part in (
                    self._review_priority_label(self._candidate_review_priority(pred)),
                    self._candidate_review_window(pred),
                )
                if part
            )
            if blocker:
                blockers.append(f"{display}: {blocker}")
            if next_step or review_meta:
                line = display
                if review_meta:
                    line += f" | {review_meta}"
                if next_step:
                    line += f" | {next_step}"
                review_items.append(line)

        lines = [f"PM主裁决: 上调 {promoted} / 降级 {downgraded} / 维持 {kept}"]
        if tradable:
            lines.append("可执行主链: " + "、".join(tradable[:3]))
        if watchlist:
            lines.append("候选观察池: " + "、".join(watchlist[:5]))
        if blockers:
            lines.append("执行阻塞: " + "；".join(blockers[:2]))
        for item in review_items[:2]:
            lines.append("观察复核: " + item)
        return tuple(lines)

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

    def _load_predictions_between(self, start_date: str, end_date: str) -> list[dict]:
        predictions: list[dict] = []
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
                except json.JSONDecodeError:
                    continue
                signal_date = str(pred.get("signal_date", "")).strip()
                if start_date <= signal_date <= end_date:
                    predictions.append(pred)

        return predictions

    def _load_paper_rows(
        self,
        *,
        signal_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict]:
        rows = read_paper_trades(self.paper_ledger_path)
        if signal_date:
            return [
                row
                for row in rows
                if str(row.get("signal_date", "")).strip() == signal_date
            ]
        if start_date or end_date:
            low = start_date or ""
            high = end_date or "9999-12-31"
            return [
                row
                for row in rows
                if low <= str(row.get("signal_date", "")).strip() <= high
            ]
        return rows

    def _prediction_indexes(
        self,
        predictions: list[dict],
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        predictions_by_id: dict[str, dict] = {}
        predictions_by_symbol: dict[str, dict] = {}
        for prediction in predictions:
            signal_id = str(prediction.get("id", "") or "").strip()
            if signal_id:
                predictions_by_id[signal_id] = prediction
            symbol = str(prediction.get("symbol", "") or "").strip()
            if symbol and symbol not in predictions_by_symbol:
                predictions_by_symbol[symbol] = prediction
        return predictions_by_id, predictions_by_symbol

    def _matching_prediction(
        self,
        *,
        paper_row: dict,
        predictions_by_id: dict[str, dict],
        predictions_by_symbol: dict[str, dict],
    ) -> dict:
        signal_id = str(paper_row.get("signal_id", "") or "").strip()
        if signal_id and signal_id in predictions_by_id:
            return predictions_by_id[signal_id]
        symbol = str(paper_row.get("symbol", "") or "").strip()
        return predictions_by_symbol.get(symbol, {})

    def _review_single_prediction(self, pred: dict) -> TradeReview:
        """复盘单条预测"""
        symbol = pred.get("symbol", "")
        name = pred.get("name", "")
        strategy_type = self._resolve_strategy_type(pred)
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

    def _review_single_trade(
        self,
        *,
        paper_row: dict,
        matched_prediction: dict,
    ) -> TradeReview:
        merged_row = {**matched_prediction, **paper_row}
        symbol = str(merged_row.get("symbol", "") or "")
        name = str(merged_row.get("name", "") or "")
        strategy_type = self._resolve_strategy_type(merged_row)
        signal_date = str(merged_row.get("signal_date", "") or "")
        entry_price = float(
            merged_row.get("entry_price")
            or matched_prediction.get("entry_price")
            or 0
        )
        exit_price = float(
            merged_row.get("exit_price")
            or merged_row.get("current_price")
            or matched_prediction.get("current_price")
            or entry_price
        )
        return_pct = float(merged_row.get("return_pct") or 0)
        is_win = return_pct > 0
        holding_days = self._resolve_holding_days(merged_row)
        exit_reason = str(merged_row.get("exit_reason", "") or "").strip()
        if not exit_reason:
            exit_reason = self._determine_exit_reason(return_pct)
        lessons = self._extract_lessons(merged_row, return_pct, is_win)

        return TradeReview(
            symbol=symbol,
            name=name,
            strategy_type=strategy_type,
            signal_date=signal_date,
            entry_price=entry_price,
            exit_price=exit_price,
            return_pct=return_pct,
            is_win=is_win,
            holding_days=holding_days,
            exit_reason=exit_reason,
            lessons=lessons,
        )

    def _resolve_holding_days(self, row: dict) -> int:
        explicit_days = row.get("holding_days")
        if explicit_days not in (None, ""):
            try:
                return max(int(explicit_days), 1)
            except (TypeError, ValueError):
                pass

        entry_date = str(row.get("entry_date", "") or "").strip()
        exit_date = str(row.get("exit_date", "") or "").strip()
        if entry_date and exit_date:
            try:
                entry = datetime.strptime(entry_date, "%Y-%m-%d")
                exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
                return max((exit_dt - entry).days + 1, 1)
            except ValueError:
                pass
        return 1

    def _resolve_strategy_type(self, pred: dict) -> str:
        explicit = str(pred.get("strategy_type", "")).strip()
        if explicit:
            return explicit

        strategy_ids = pred.get("strategies") or []
        if isinstance(strategy_ids, str):
            strategy_ids = [strategy_ids]
        strategy_ids = [str(item).strip() for item in strategy_ids if str(item).strip()]

        strategy_map = {
            "morning_breakout": "早盘打板",
            "closing_premium": "尾盘溢价",
            "volume_breakout": "放量突破",
            "ma_pullback": "均线回踩",
            "bowl_rebound": "碗口反弹",
            "n_rebound": "N字反弹",
            "low_vol_trend": "低波趋势",
            "rps_momentum": "RPS动量",
            "rps_relative_strength": "RPS强势",
            "relative_strength": "相对强度",
            "multi_factor_rotation": "多因子轮动",
        }
        entry_type_map = {
            "volume_breakout": "放量突破",
            "trend_pullback": "均线回踩",
            "reversal_watch": "反转观察",
            "relative_strength": "相对强度",
            "watch": "候选观察池",
            "close": "收盘候选",
            "open": "盘前候选",
            "next_open": "次日开盘",
        }

        base_label = ""
        if strategy_ids:
            base_label = strategy_map.get(
                strategy_ids[0], strategy_ids[0].replace("_", " ")
            )
        if not base_label:
            entry_type = str(pred.get("entry_type", "")).strip()
            if entry_type:
                base_label = entry_type_map.get(
                    entry_type, entry_type.replace("_", " ")
                )
        if not base_label:
            rating = str(pred.get("rating", "")).strip()
            if rating:
                base_label = rating_label(rating)

        sub_strategy = str(pred.get("sub_strategy", "")).strip()
        if base_label and sub_strategy:
            if sub_strategy in {
                "涨停打板",
                "弱转强",
                "放量突破",
                "底部反转",
                "量价突破",
            }:
                return f"{base_label}·{sub_strategy}"
            return base_label
        if base_label:
            return base_label
        if sub_strategy:
            return sub_strategy
        return "未知"

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

        strategy_type = self._resolve_strategy_type(pred)
        if strategy_type.startswith("早盘打板") and not is_win:
            lessons.append("打板失败：需关注市场整体环境")
        elif strategy_type.startswith("尾盘溢价") and not is_win:
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

    def _extract_key_lessons(
        self,
        reviews: list[TradeReview],
        *,
        blocked_count: int = 0,
        pending_count: int = 0,
        total_signals: int = 0,
    ) -> tuple[str, ...]:
        """提取关键经验教训"""
        lessons = []

        if total_signals > 0 and not reviews:
            lessons.append("今日信号仍在跟踪，暂无 closed 虚拟盘结果。")

        if blocked_count > 0:
            lessons.append("存在不可成交样本，已按阻塞处理，不计入胜率。")

        if pending_count > 0:
            lessons.append("部分信号仍在等待入场或平仓，后续需继续复核。")

        win_count = len([r for r in reviews if r.is_win])
        loss_count = len([r for r in reviews if not r.is_win and r.return_pct != 0])

        if loss_count > win_count:
            lessons.append("今日亏损较多，需反思策略是否适应当前市场")

        big_losses = [r for r in reviews if r.return_pct < -3]
        if big_losses:
            lessons.append("存在大亏交易，需严格执行止损")

        breakout_reviews = [
            r for r in reviews if r.strategy_type.startswith("早盘打板")
        ]
        if breakout_reviews:
            breakout_win_rate = len([r for r in breakout_reviews if r.is_win]) / len(
                breakout_reviews
            )
            if breakout_win_rate < 0.5:
                lessons.append("打板成功率偏低，需关注市场情绪")

        return tuple(lessons)

    def _generate_improvement_suggestions(
        self,
        reviews: list[TradeReview],
        win_rate: float,
        *,
        pending_count: int = 0,
        blocked_count: int = 0,
    ) -> tuple[str, ...]:
        """生成改进建议"""
        suggestions = []

        if pending_count > 0:
            suggestions.append("对未完成验证的样本保留跟踪，避免过早下结论。")

        if blocked_count > 0:
            suggestions.append("复核不可成交原因，确认是否属于流动性或涨停限制。")

        if reviews and win_rate < 0.5:
            suggestions.append("胜率偏低，建议减少交易频率，提高选股标准")

        recent_reviews = sorted(reviews, key=lambda x: x.signal_date)[-5:]
        recent_losses = len([r for r in recent_reviews if not r.is_win])
        if recent_reviews and recent_losses >= 3:
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
            main_chain_summary=(),
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
            end_date = self._latest_review_date() or now_shanghai().strftime("%Y-%m-%d")

        end = datetime.strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=6)
        week_start = start.strftime("%Y-%m-%d")

        predictions = self._load_predictions_between(week_start, end_date)
        predictions_by_id, predictions_by_symbol = self._prediction_indexes(predictions)
        closed_rows = [
            row
            for row in self._load_paper_rows(start_date=week_start, end_date=end_date)
            if row.get("status") == "closed"
        ]
        all_reviews = [
            self._review_single_trade(
                paper_row=row,
                matched_prediction=self._matching_prediction(
                    paper_row=row,
                    predictions_by_id=predictions_by_id,
                    predictions_by_symbol=predictions_by_symbol,
                ),
            )
            for row in closed_rows
        ]

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

    if review.main_chain_summary:
        report.append("🎯 主链总览")
        report.append("-" * 40)
        for item in review.main_chain_summary:
            report.append(f"  {item}")
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
        report.append("关键经验教训")
        report.append("-" * 40)
        for i, lesson in enumerate(review.key_lessons, 1):
            report.append(f"  {i}. {lesson}")
        report.append("")

    if review.improvement_suggestions:
        report.append("改进建议")
        report.append("-" * 40)
        for i, suggestion in enumerate(review.improvement_suggestions, 1):
            report.append(f"  {i}. {suggestion}")
        report.append("")

    if review.executed_signals == 0 and review.total_signals == 0:
        report.append("📝 今日无新信号，继续等待下一轮主链机会。")
    elif review.executed_signals == 0:
        report.append("🧭 今日以观察为主，等待右侧确认后再行动。")
    elif review.win_rate >= 0.6:
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
