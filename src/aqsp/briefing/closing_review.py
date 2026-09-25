from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aqsp.core.time import now_shanghai
from aqsp.ledger.base import is_ledger_row_paper_review_eligible
from aqsp.paper import read_paper_trades
from aqsp.presentation import format_symbol_name
from aqsp.ratings import is_tradable_rating, rating_label


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """把账本里的 reasons/risks（list[str] 或单个字符串）规整为 tuple[str, ...]。

    None/缺失/空串兜底为空 tuple，非列表非字符串的脏数据同样安全回落。
    """
    if value is None:
        return ()
    if isinstance(value, str):
        value = value.strip()
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return tuple(items)
    return ()


def _as_float(value: object) -> float:
    """把账本数值字段安全转为 float；None/缺失/非法值兜底为 0.0。"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stop_distance_pct(stop_loss: float, signal_close: float) -> float:
    """止损幅度（百分点，正数）：|stop_loss/signal_close - 1| * 100。

    stop_loss/signal_close 任一缺失或非正时返回 0.0 表示无法评估。
    """
    if stop_loss <= 0 or signal_close <= 0:
        return 0.0
    return abs(stop_loss / signal_close - 1) * 100


@dataclass(frozen=True)
class TradeReview:
    """纸面验证复盘"""

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
    # 以下为归因字段（来自 predictions 行的 reasons/risks/score/entry_type
    # 与 paper 行的 stop_loss/take_profit），供数据驱动复盘使用；
    # 缺失时兜底为空值，不阻塞原有展示。
    reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    stop_loss: float = 0.0
    take_profit: float = 0.0
    entry_type: str = ""
    score: float = 0.0
    # 信号日收盘价，用于止损幅度归一化（|stop_loss/signal_close-1|）
    signal_close: float = 0.0


@dataclass(frozen=True)
class MarketEnvironmentBands:
    """市场环境分档阈值（复盘展示口径，不参与打分/筛选决策）。

    超额收益与收益单位均为百分点；胜率单位为比例（0~1）。
    此处仅用于生成复盘报告中的描述性文字，任何策略阈值仍以
    thresholds.yaml 为准，不得引用本 dataclass。
    """

    strong_outperform: float = 2.0
    mild_underperform: float = -2.0
    strong_win_rate: float = 0.6
    weak_win_rate: float = 0.4
    max_drawdown_limit: float = 5.0


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
    # 以下为账本直连口径（来自 predictions.jsonl 中已验证行），
    # 与上方纸面虚拟盘口径相互独立，便于对照复盘。
    ledger_validated_count: int = 0
    ledger_win_rate: float = 0.0
    ledger_avg_return: float = 0.0
    ledger_avg_excess_return: float = 0.0
    # 复盘聚合口径说明（如「近60日平仓 N 笔」/「最近 N 笔历史平仓（兜底）」），
    # 用于报告透明标注，避免因选股管道暂停导致复盘展示陈旧战绩却无说明。
    review_scope: str = ""
    # 单笔透视（按 |return_pct| 降序前 5 笔的预格式化行），供报告直出；
    # 空tuple表示无平仓样本，报告不渲染该节。
    trade_highlights: tuple[str, ...] = ()


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
        review_window_days: int = 60,
        max_review_trades: int = 30,
    ) -> None:
        self.ledger_path = ledger_path
        self.paper_ledger_path = paper_ledger_path
        # 收盘复盘按「平仓日期」回看窗口；窗口内无平仓时兜底取最近 N 笔历史战绩，
        # 避免选股管道暂停期间复盘真空（见 2026-09-25 根因修复）。
        self.review_window_days = review_window_days
        self.max_review_trades = max_review_trades

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
        # 收盘复盘应聚合「近期平仓」的纸面交易，而非按 signal_date==today 过滤：
        # 一笔交易今天平仓时其 signal_date 在数日前，原逻辑永远漏掉 → 复盘常年空壳。
        # 见 2026-09-25 根因修复。
        all_paper = self._load_paper_rows()
        closed_rows, review_scope = self._select_review_closed_trades(all_paper, today)
        pending_rows = [
            row for row in paper_rows if row.get("status") == "pending_entry"
        ]
        blocked_rows = [
            row for row in paper_rows if row.get("status") == "not_executable"
        ]

        # 注意：按平仓日期聚合的复盘不再依赖 signal_date==today 的纸面行，
        # 故「是否为空」判定须看全量纸面账本 all_paper，而非仅 today 信号行。
        if not predictions and not all_paper:
            return self._empty_review(today)

        # 归因匹配池：closed 交易的 signal_date 多数早于今日，
        # 仅用当日 predictions 会导致 reasons/risks/score/signal_close 全部落空。
        # 这里把匹配池扩展到覆盖全部 closed 行 signal_date 的区间（含今日），
        # 主链总览仍用当日 predictions，口径互不影响。
        closed_signal_dates = [
            d
            for d in (
                str(row.get("signal_date", "") or "").strip() for row in closed_rows
            )
            if d
        ]
        if closed_signal_dates:
            match_predictions = self._load_predictions_between(
                min(closed_signal_dates), today
            )
        else:
            match_predictions = list(predictions)
        predictions_by_id, predictions_by_symbol = self._prediction_indexes(
            match_predictions
        )
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
        validated_rows = [
            p for p in predictions if str(p.get("status", "")).strip() == "validated"
        ]
        ledger_returns = [
            float(p["return_pct"])
            for p in validated_rows
            if p.get("return_pct") is not None
        ]
        ledger_excess = [
            float(p["excess_return_pct"])
            for p in validated_rows
            if p.get("excess_return_pct") is not None
        ]
        ledger_validated_count = len(validated_rows)
        ledger_win_rate = (
            sum(1 for p in validated_rows if p.get("win") is True)
            / ledger_validated_count
            if ledger_validated_count
            else 0.0
        )
        ledger_avg_return = (
            sum(ledger_returns) / len(ledger_returns) if ledger_returns else 0.0
        )
        ledger_avg_excess_return = (
            sum(ledger_excess) / len(ledger_excess) if ledger_excess else 0.0
        )

        if ledger_validated_count > 0:
            market_environment = self._evaluate_market_environment(
                ledger_avg_excess_return=ledger_avg_excess_return
            )
        else:
            market_environment = self._evaluate_market_environment(
                win_rate=win_rate,
                avg_return=total_return,
                executed=executed_signals,
            )
        key_lessons = self._extract_key_lessons(
            reviews,
            blocked_count=len(blocked_rows),
            pending_count=len(pending_rows),
            total_signals=total_signals,
        )
        main_chain_summary = self._build_main_chain_summary(predictions)
        if not main_chain_summary:
            main_chain_summary = self._build_main_chain_summary_from_paper(paper_rows)
        improvement_suggestions = self._generate_improvement_suggestions(
            reviews,
            win_rate,
            pending_count=len(pending_rows),
            blocked_count=len(blocked_rows),
        )
        trade_highlights = self._build_trade_highlights(reviews)

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
            ledger_validated_count=ledger_validated_count,
            ledger_win_rate=ledger_win_rate,
            ledger_avg_return=ledger_avg_return,
            ledger_avg_excess_return=ledger_avg_excess_return,
            review_scope=review_scope,
            trade_highlights=trade_highlights,
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
            if is_tradable_rating(rating) and is_ledger_row_paper_review_eligible(pred):
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
            lines.append("主看名单: " + "、".join(tradable[:3]))
        if watchlist:
            lines.append("观察名单: " + "、".join(watchlist[:5]))
        if blockers:
            lines.append("阻塞: " + "；".join(blockers[:2]))
        for item in review_items[:2]:
            lines.append("后续关注: " + item)
        return tuple(lines)

    def _build_main_chain_summary_from_paper(
        self, paper_rows: list[dict]
    ) -> tuple[str, ...]:
        if not paper_rows:
            return ()

        promoted = sum(
            1
            for row in paper_rows
            if str(row.get("portfolio_action", "")).strip() == "promote"
        )
        downgraded = sum(
            1
            for row in paper_rows
            if str(row.get("portfolio_action", "")).strip() == "downgrade"
        )
        kept = sum(
            1
            for row in paper_rows
            if str(row.get("portfolio_action", "")).strip() == "keep"
        )

        tradable: list[str] = []
        watchlist: list[str] = []
        blockers: list[str] = []
        review_items: list[str] = []
        for row in paper_rows:
            symbol = str(row.get("symbol", "")).strip()
            name = str(row.get("name", "")).strip()
            display = format_symbol_name(symbol, name)
            rating = str(row.get("rating", "")).strip()
            action = str(row.get("portfolio_action", "")).strip()
            if is_ledger_row_paper_review_eligible(row) and (
                is_tradable_rating(rating) or action == "promote"
            ):
                tradable.append(display)
            else:
                watchlist.append(display)
            blocker = str(row.get("candidate_blocker", "")).strip()
            next_step = str(row.get("candidate_next_step", "")).strip()
            review_meta = " / ".join(
                part
                for part in (
                    self._review_priority_label(
                        str(row.get("candidate_review_priority", "")).strip()
                    ),
                    str(row.get("candidate_review_window", "")).strip(),
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
            lines.append("主看名单: " + "、".join(tradable[:3]))
        if watchlist:
            lines.append("观察名单: " + "、".join(watchlist[:5]))
        if blockers:
            lines.append("阻塞: " + "；".join(blockers[:2]))
        for item in review_items[:2]:
            lines.append("后续关注: " + item)
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

    def _select_review_closed_trades(
        self, all_paper: list[dict], today: str
    ) -> tuple[list[dict], str]:
        """按「平仓日期」聚合近期已平仓的纸面交易。

        - 优先取 exit_date 落在 [today - review_window_days, today] 窗口内的 closed 行；
        - 若窗口内无平仓（选股管道暂停期间常见），兜底取最近 max_review_trades 笔
          历史 closed 行，并在 scope 文案中透明标注，避免复盘真空却无说明。
        返回 (closed_rows, scope_text)。
        """
        try:
            today_dt = datetime.strptime(today, "%Y-%m-%d")
        except ValueError:
            # 非标准日期串兜底：必须用项目时钟 now_shanghai，禁用 datetime.now()
            # （红线守卫 test_runtime_redline_guard 静态扫描禁止裸 datetime.now）。
            # replace(tzinfo=None) 去掉时区，使其与 exit_date 解析出的 naive 日期可比。
            today_dt = now_shanghai().replace(tzinfo=None)

        closed = [row for row in all_paper if row.get("status") == "closed"]

        def _exit_dt(row: dict):
            exit_date = str(row.get("exit_date", "") or "").strip()
            if not exit_date:
                return None
            try:
                return datetime.strptime(exit_date, "%Y-%m-%d")
            except ValueError:
                return None

        window_lo = today_dt - timedelta(days=self.review_window_days)
        windowed = [
            row
            for row in closed
            if (ed := _exit_dt(row)) is not None and window_lo <= ed <= today_dt
        ]
        if windowed:
            windowed.sort(
                key=lambda r: str(r.get("exit_date", "") or ""),
                reverse=True,
            )
            scope = f"近{self.review_window_days}日平仓 {len(windowed)} 笔"
            return windowed, scope

        # 兜底：窗口内无平仓，取最近 N 笔历史平仓（按 exit_date 降序）
        dated = [row for row in closed if _exit_dt(row) is not None]
        dated.sort(key=lambda r: str(r.get("exit_date", "") or ""), reverse=True)
        fallback = dated[: self.max_review_trades]
        if fallback:
            latest_exit = str(fallback[0].get("exit_date", "") or "")
            scope = (
                f"最近 {len(fallback)} 笔历史平仓（兜底；最近平仓 {latest_exit}，"
                f"其后选股管道无新平仓）"
            )
            return fallback, scope

        return [], "近期无已平仓记录"

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
            merged_row.get("entry_price") or matched_prediction.get("entry_price") or 0
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
        reasons = _as_str_tuple(merged_row.get("reasons"))
        risks = _as_str_tuple(merged_row.get("risks"))
        stop_loss = _as_float(merged_row.get("stop_loss"))
        take_profit = _as_float(merged_row.get("take_profit"))
        entry_type = str(merged_row.get("entry_type", "") or "").strip()
        score = _as_float(merged_row.get("score"))
        signal_close = _as_float(merged_row.get("signal_close"))

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
            reasons=reasons,
            risks=risks,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_type=entry_type,
            score=score,
            signal_close=signal_close,
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
            "watch": "观察名单",
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
        """提取单笔经验教训（数据驱动）。

        引用该笔交易自身的入场理由/风险提示/止损关系，替代原收益分桶模板；
        归因字段缺失时回落为简短的中性描述，不编造结论。
        """
        symbol = str(pred.get("symbol", "") or "")
        reasons = _as_str_tuple(pred.get("reasons"))
        risks = _as_str_tuple(pred.get("risks"))
        stop_loss = _as_float(pred.get("stop_loss"))
        signal_close = _as_float(pred.get("signal_close"))
        stop_pct = _stop_distance_pct(stop_loss, signal_close)

        lessons: list[str] = []
        reason_text = reasons[0] if reasons else ""
        if is_win:
            head = f"{symbol} 盈利 {return_pct:+.2f}%".strip()
            if reason_text:
                head += f"：入场理由「{reason_text}」有效"
            lessons.append(head)
            if risks:
                lessons.append(f"留意既有风险提示「{risks[0]}」是否仍在累积")
        else:
            head = f"{symbol} 亏损 {return_pct:.2f}%".strip()
            if reason_text:
                head += f"：入场理由「{reason_text}」未兑现"
            lessons.append(head)
            if stop_pct > 0:
                if return_pct <= -stop_pct:
                    lessons.append(
                        f"亏损已达止损幅度 -{stop_pct:.1f}%（stop_loss={stop_loss:.3f}），按规则退出"
                    )
                else:
                    lessons.append(
                        f"未达止损幅度 -{stop_pct:.1f}%，属非止损退出，需复盘退出时机"
                    )

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

    def _evaluate_market_environment(
        self,
        date: str | None = None,
        *,
        win_rate: float = 0.0,
        avg_return: float = 0.0,
        executed: int = 0,
        ledger_avg_excess_return: float | None = None,
        bands: MarketEnvironmentBands | None = None,
    ) -> str:
        """评估市场环境。

        优先采用账本口径（相对基准的超额收益，来自已验证的
        predictions.jsonl 行）；无账本验证样本时回退纸面口径。
        样本不足时明确标注"暂不评估"，绝不返回未经数据支撑的假设值。
        """
        bands = bands or MarketEnvironmentBands()

        if ledger_avg_excess_return is not None:
            excess = ledger_avg_excess_return
            if excess >= bands.strong_outperform:
                return f"显著跑赢大盘（账本平均超额 {excess:+.2f}%）"
            if excess > 0:
                return f"温和跑赢大盘（账本平均超额 {excess:+.2f}%）"
            if excess <= bands.mild_underperform:
                return f"显著跑输大盘（账本平均超额 {excess:+.2f}%）"
            return f"小幅跑输大盘（账本平均超额 {excess:+.2f}%）"

        if executed <= 0:
            return "样本不足，暂不评估市场环境"
        if win_rate >= bands.strong_win_rate and avg_return > 0:
            return f"纸面偏强（胜率 {win_rate:.1%}，平均收益 {avg_return:+.2f}%）"
        if win_rate <= bands.weak_win_rate:
            return f"纸面偏弱（胜率 {win_rate:.1%}，平均收益 {avg_return:+.2f}%）"
        return f"纸面震荡（胜率 {win_rate:.1%}，平均收益 {avg_return:+.2f}%）"

    def _evaluate_weekly_trend(
        self,
        *,
        win_rate: float,
        total_return: float,
        total_trades: int,
        bands: MarketEnvironmentBands | None = None,
    ) -> str:
        """周度趋势（数据驱动，替代原先写死的"震荡"）。"""
        bands = bands or MarketEnvironmentBands()
        if total_trades <= 0:
            return "本周无成交样本，趋势不明"
        if total_return > 0 and win_rate >= bands.strong_win_rate:
            return f"上行（周收益 {total_return:+.2f}%，胜率 {win_rate:.1%}）"
        if total_return < 0 and win_rate <= bands.weak_win_rate:
            return f"下行（周收益 {total_return:+.2f}%，胜率 {win_rate:.1%}）"
        return f"震荡（周收益 {total_return:+.2f}%，胜率 {win_rate:.1%}）"

    def _evaluate_next_week_outlook(
        self,
        *,
        win_rate: float,
        max_drawdown: float,
        total_trades: int,
        bands: MarketEnvironmentBands | None = None,
    ) -> str:
        """下周展望（数据驱动，替代原先写死的"观望为主"）。"""
        bands = bands or MarketEnvironmentBands()
        if total_trades <= 0:
            return "样本不足，暂不给出展望"
        if max_drawdown > bands.max_drawdown_limit:
            return f"回撤偏大（{max_drawdown:.2f}%），建议收紧参与"
        if win_rate >= bands.strong_win_rate:
            return "可维持现有节奏，正常参与"
        if win_rate <= bands.weak_win_rate:
            return "胜率偏低，建议减量观察"
        return "维持观望，等待更明确信号"

    def _extract_key_lessons(
        self,
        reviews: list[TradeReview],
        *,
        blocked_count: int = 0,
        pending_count: int = 0,
        total_signals: int = 0,
    ) -> tuple[str, ...]:
        """提取关键经验教训（数据驱动）。

        每条 lesson 引用具体交易与数字：最大亏损/盈利笔、止损体检、最大连亏；
        reviews 为空时保持原有空数据路径（仅上下文提示，不编造归因）。
        """
        lessons = []

        if total_signals > 0 and not reviews:
            lessons.append("今日信号仍在跟踪，暂无 closed 虚拟盘结果。")

        if blocked_count > 0:
            lessons.append("存在不可成交样本，已按阻塞处理，不计入胜率。")

        if pending_count > 0:
            lessons.append("部分信号仍在等待纸面入场或纸面结束，后续需继续复核。")

        if not reviews:
            return tuple(lessons)

        loss_reviews = [r for r in reviews if not r.is_win and r.return_pct != 0]
        win_reviews = [r for r in reviews if r.is_win]

        # 最大亏损笔：symbol + return_pct + 入场理由
        if loss_reviews:
            worst = min(loss_reviews, key=lambda r: r.return_pct)
            reason = worst.reasons[0] if worst.reasons else "无入场理由记录"
            lessons.append(
                f"最大亏损 {worst.symbol} {worst.return_pct:.2f}%："
                f"入场理由「{reason}」失效"
            )

        # 最大盈利笔
        if win_reviews:
            best = max(win_reviews, key=lambda r: r.return_pct)
            reason = best.reasons[0] if best.reasons else "无入场理由记录"
            lessons.append(
                f"最大盈利 {best.symbol} {best.return_pct:+.2f}%："
                f"入场理由「{reason}」兑现"
            )

        # 止损体检：亏损幅度达到止损幅度的笔数 vs 非止损退出笔数
        stop_checked = [
            (r, _stop_distance_pct(r.stop_loss, _as_float(r.signal_close)))
            for r in reviews
        ]
        stop_checked = [(r, pct) for r, pct in stop_checked if pct > 0]
        if stop_checked:
            triggered = [
                (r, pct) for r, pct in stop_checked if r.return_pct <= -pct
            ]
            non_stop = [(r, pct) for r, pct in stop_checked if r.return_pct > -pct]
            if triggered:
                worst_stop = min(triggered, key=lambda x: x[0].return_pct)[0]
                lessons.append(
                    f"止损体检：{len(triggered)}/{len(stop_checked)} 笔亏损已达止损幅度"
                    f"（最深 {worst_stop.symbol} {worst_stop.return_pct:.2f}%），"
                    "确认是否按规则退出"
                )
            elif loss_reviews:
                lessons.append(
                    f"止损体检：{len(non_stop)}/{len(stop_checked)} 笔亏损"
                    "均未达止损幅度，属非止损退出，需复盘退出时机"
                )

        # 最大连亏笔数（按 signal_date 排序）
        ordered = sorted(reviews, key=lambda r: r.signal_date)
        max_streak = 0
        streak = 0
        for r in ordered:
            if not r.is_win and r.return_pct != 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        if max_streak >= 3:
            lessons.append(f"最大连亏 {max_streak} 笔，注意策略是否与当前市场失配")

        return tuple(lessons)

    def _generate_improvement_suggestions(
        self,
        reviews: list[TradeReview],
        win_rate: float,
        *,
        pending_count: int = 0,
        blocked_count: int = 0,
    ) -> tuple[str, ...]:
        """生成改进建议（数据驱动）。

        基于本次复盘样本统计：亏损笔的入场类型占比、连亏长度、
        blocked/pending 占比，每条建议必须引用具体数字；
        无 reviews 时回落到原有的 blocked/pending 提示文案。
        """
        suggestions = []

        if pending_count > 0:
            suggestions.append("对未完成验证的样本保留跟踪，避免过早下结论。")

        if blocked_count > 0:
            suggestions.append("复核不可成交原因，确认是否属于流动性或涨停限制。")

        if not reviews:
            return tuple(suggestions)

        total = len(reviews)
        win_count = len([r for r in reviews if r.is_win])
        loss_reviews = [r for r in reviews if not r.is_win and r.return_pct != 0]

        if win_rate < 0.5:
            suggestions.append(
                f"胜率 {win_rate:.1%}（{win_count}/{total}）偏低，"
                "建议提高选股标准、减少低分样本参与"
            )

        # 亏损笔中各 entry_type 占比
        if loss_reviews:
            entry_type_counts: dict[str, int] = {}
            for r in loss_reviews:
                label = r.entry_type.strip() or "未记录入场类型"
                entry_type_counts[label] = entry_type_counts.get(label, 0) + 1
            top_types = sorted(
                entry_type_counts.items(), key=lambda kv: kv[1], reverse=True
            )
            parts = [f"{label} {count}笔" for label, count in top_types[:2]]
            suggestions.append(
                f"亏损集中于 {len(loss_reviews)}/{total} 笔："
                + "、".join(parts)
                + "，优先复核该入场类型的有效性"
            )

        # 连亏（按 signal_date 排序）
        ordered = sorted(reviews, key=lambda r: r.signal_date)
        max_streak = 0
        streak = 0
        for r in ordered:
            if not r.is_win and r.return_pct != 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        if max_streak >= 3:
            suggestions.append(
                f"已连亏 {max_streak} 笔，建议暂停新增纸面验证，观察市场后再恢复"
            )

        # blocked/pending 占比（相对本次复盘全部样本）
        denom = total + pending_count + blocked_count
        if denom > 0 and (pending_count + blocked_count) > 0:
            share = (pending_count + blocked_count) / denom * 100
            suggestions.append(
                f"阻塞/待验证 {pending_count + blocked_count} 笔（占样本 {share:.0f}%），"
                "确认执行链路是否顺畅"
            )

        return tuple(suggestions)

    def _build_trade_highlights(
        self, reviews: list[TradeReview]
    ) -> tuple[str, ...]:
        """单笔透视：按 |return_pct| 降序前 5 笔的预格式化明细行。

        每行含：symbol 名字 / 收益% / 持有天数 / 入场理由（截断40字）/
        止损状态（亏损但未达止损幅度时标注「非止损退出」）。
        """
        if not reviews:
            return ()

        def _reason_text(review: TradeReview) -> str:
            text = review.reasons[0] if review.reasons else ""
            return text[:40] + ("…" if len(text) > 40 else "")

        def _stop_status(review: TradeReview) -> str:
            stop_pct = _stop_distance_pct(
                review.stop_loss, _as_float(review.signal_close)
            )
            if stop_pct <= 0:
                return ""
            if review.return_pct <= -stop_pct:
                return f"触发止损（幅度-{stop_pct:.1f}%）"
            if not review.is_win and review.return_pct > -stop_pct:
                return f"非止损退出（止损幅度-{stop_pct:.1f}%）"
            return ""

        top = sorted(reviews, key=lambda r: abs(r.return_pct), reverse=True)[:5]
        lines = []
        for r in top:
            display = format_symbol_name(r.symbol, r.name)
            parts = [
                display,
                f"{r.return_pct:+.2f}%",
                f"持有{r.holding_days}天",
            ]
            reason_text = _reason_text(r)
            if reason_text:
                parts.append(f"入场理由: {reason_text}")
            stop_status = _stop_status(r)
            if stop_status:
                parts.append(stop_status)
            lines.append(" | ".join(parts))
        return tuple(lines)

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
        # 周度总结同样按「平仓日期」聚合（exit_date 落在当周窗口内），
        # 与 review_today 口径一致；原逻辑按 signal_date 过滤会漏掉当周平仓、
        # 但 signal_date 在更早的交易。见 2026-09-25 根因修复。
        closed_rows = [
            row
            for row in self._load_paper_rows()
            if row.get("status") == "closed"
            and week_start <= str(row.get("exit_date", "") or "").strip() <= end_date
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
            market_trend=self._evaluate_weekly_trend(
                win_rate=win_rate,
                total_return=total_return,
                total_trades=total_trades,
            ),
            next_week_outlook=self._evaluate_next_week_outlook(
                win_rate=win_rate,
                max_drawdown=max_drawdown,
                total_trades=total_trades,
            ),
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
    report.append("📊 每日纸面验证复盘")
    report.append("=" * 60)
    report.append(f"📅 日期: {review.date}")
    if review.review_scope:
        report.append(f"🔎 复盘口径: {review.review_scope}")
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
    report.append(f"  纸面验证数: {review.executed_signals}")
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
            report.append(f"    纸面样本数: {stats['total']}")
            report.append(f"    胜率: {stats['win_rate']:.1%}")
            report.append(f"    总收益: {stats['total_return']:.2f}%")
        report.append("")

    report.append("🌍 市场环境")
    report.append("-" * 40)
    report.append(f"  {review.market_environment}")
    report.append("")

    if review.trade_highlights:
        report.append("🔍 单笔透视")
        report.append("-" * 40)
        for line in review.trade_highlights:
            report.append(f"  · {line}")
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
    report.append("📊 周度纸面验证总结")
    report.append("=" * 60)
    report.append(f"📅 周期: {summary.week_start} 至 {summary.week_end}")
    report.append("")

    report.append("📈 周度统计")
    report.append("-" * 40)
    report.append(f"  总纸面样本数: {summary.total_trades}")
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
