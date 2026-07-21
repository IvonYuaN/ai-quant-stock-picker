from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.ledger import ExecutionConfig, execution_config_from_thresholds, read_ledger
from aqsp.ledger.base import (
    _check_executable,
    _resolve_exit,
    is_ledger_row_paper_review_eligible,
)
from aqsp.ratings import is_tradable_rating
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

LOGGER = logging.getLogger("aqsp.paper")


PAPER_REVIEW = "paper_review"
NOT_EXECUTABLE = "not_executable"
BLOCKED_BY_CIRCUIT_BREAKER = "blocked_by_circuit_breaker"
HELD = "held"
PAPER_STATUSES = frozenset(
    {PAPER_REVIEW, NOT_EXECUTABLE, BLOCKED_BY_CIRCUIT_BREAKER, HELD}
)


@dataclass(frozen=True)
class PaperAccountConfig:
    """Optional account guard for paper tracking; it never places an order."""

    initial_cash: float = 100_000.0
    lot_size: int = 100
    enforce_cash: bool = False

    def __post_init__(self) -> None:
        if self.initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


@dataclass(frozen=True)
class PaperAccountSnapshot:
    initial_cash: float
    cash: float
    market_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    held_count: int


@dataclass(frozen=True)
class PaperSummary:
    opened: int = 0
    closed: int = 0
    open_positions: int = 0
    not_executable: int = 0
    pending_entry: int = 0
    skipped: int = 0
    paper_review: int = 0
    blocked_by_circuit_breaker: int = 0
    account: PaperAccountSnapshot | None = None


_PAPER_CONTEXT_FIELDS = (
    "portfolio_action",
    "quality_gate_status",
    "quality_gate_action",
    "quality_gate_reasons",
    "paper_review_eligible",
    "observation_only",
    "technical_evidence",
    "technical_evidence_count",
    "technical_quality_status",
    "data_quality_status",
    "data_quality_alerts",
    "candidate_status",
    "candidate_blocker",
    "candidate_next_step",
    "candidate_review_window",
    "candidate_review_priority",
    "strategies",
    "rating",
    "score",
    "thresholds_version",
    "regime_at_signal",
    "signal_day_group",
    "entry_type",
    "sub_strategy",
    "position",
    "benchmark_symbol",
    "limit_up_pct",
    "limit_down_pct",
)


def read_paper_trades(path: str | Path) -> list[dict]:
    trade_path = Path(path)
    if not trade_path.exists():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(
        trade_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "纸面账本 %s 第 %d 行 JSON 损坏，已跳过: %s",
                trade_path,
                lineno,
                exc,
            )
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_paper_trades(path: str | Path, rows: list[dict]) -> None:
    trade_path = Path(path)
    text = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows
    )
    atomic_write_text(trade_path, (text + "\n") if text else "")


def sync_paper_trades(
    *,
    signal_ledger: str | Path,
    paper_ledger: str | Path,
    frames: dict[str, pd.DataFrame],
    execution: ExecutionConfig | None = None,
    signal_dates: set[str] | None = None,
    account: PaperAccountConfig | None = None,
    circuit_breaker_triggered: bool = False,
    circuit_breaker_reason: str = "",
) -> PaperSummary:
    execution = execution or execution_config_from_thresholds()
    signals = read_ledger(signal_ledger)
    if signal_dates is not None:
        allowed_dates = {str(item) for item in signal_dates if str(item)}
        signals = [
            signal
            for signal in signals
            if str(signal.get("signal_date") or "") in allowed_dates
        ]
    with advisory_lock(paper_ledger):
        trades = read_paper_trades(paper_ledger)

        account = account or PaperAccountConfig()
        opened, not_executable = _update_pending_entry_trades(
            trades, frames, execution, account
        )
        closed = _update_open_trades(trades, frames)
        existing_signal_ids = {str(t.get("signal_id", "")) for t in trades}
        open_symbols = {
            str(t.get("symbol", ""))
            for t in trades
            if t.get("status") == "open" and t.get("symbol")
        }

        skipped = 0
        paper_review = 0
        blocked_by_circuit_breaker = 0
        now = now_shanghai().isoformat(timespec="seconds")
        for signal in signals:
            signal_id = str(signal.get("id", ""))
            symbol = str(signal.get("symbol", ""))
            if not signal_id or signal_id in existing_signal_ids:
                continue
            if signal.get("status") not in ("pending", "validated"):
                skipped += 1
                continue
            if not is_tradable_rating(signal.get("rating")):
                skipped += 1
                continue
            if not is_ledger_row_paper_review_eligible(signal):
                skipped += 1
                continue
            if symbol in open_symbols:
                skipped += 1
                continue

            if circuit_breaker_triggered:
                trades.append(
                    _blocked_by_circuit_breaker_from_signal(
                        signal,
                        execution,
                        now,
                        circuit_breaker_reason,
                    )
                )
                existing_signal_ids.add(signal_id)
                blocked_by_circuit_breaker += 1
                continue

            frame = frames.get(symbol)
            if frame is None or frame.empty:
                skipped += 1
                continue
            trade = _open_trade_from_signal(signal, frame, execution, now, account)
            if trade is None:
                trades.append(_pending_entry_from_signal(signal, execution, now))
                existing_signal_ids.add(signal_id)
                paper_review += 1
                continue
            if account.enforce_cash and not _cash_allows_trade(trades, trade, account):
                trade["paper_status"] = PAPER_REVIEW
                trade["paper_state"] = PAPER_REVIEW
                trade["cash_rejection_reason"] = "insufficient_cash"
                trade["status"] = "pending_entry"
                trades.append(trade)
                existing_signal_ids.add(signal_id)
                paper_review += 1
                continue
            trades.append(trade)
            existing_signal_ids.add(signal_id)
            if trade["status"] == "open":
                open_symbols.add(symbol)
                opened += 1
            elif trade["status"] == "not_executable":
                not_executable += 1
            else:
                skipped += 1

        closed += _update_open_trades(trades, frames)
        _refresh_cash_trace(trades, account)
        write_paper_trades(paper_ledger, trades)
        open_positions = sum(1 for trade in trades if trade.get("status") == "open")
        pending_entry = sum(
            1 for trade in trades if trade.get("status") == "pending_entry"
        )
        account_snapshot = summarize_paper_account(
            trades, frames, initial_cash=account.initial_cash
        )
    return PaperSummary(
        opened=opened,
        closed=closed,
        open_positions=open_positions,
        not_executable=not_executable,
        pending_entry=pending_entry,
        skipped=skipped,
        paper_review=paper_review,
        blocked_by_circuit_breaker=blocked_by_circuit_breaker,
        account=account_snapshot,
    )


def render_paper_report(summary: PaperSummary, trades: list[dict]) -> str:
    open_trades = [t for t in trades if t.get("status") == "open"]
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    blocked_trades = [t for t in trades if t.get("status") == "not_executable"]
    pending_trades = [t for t in trades if t.get("status") == "pending_entry"]
    lines = [
        "# AQSP 纸面跟踪状态",
        "",
        "> 本报告只同步纸面持有跟踪，不下单；只有 buy_candidate / strong_buy_candidate 会进入纸面跟踪。",
        "",
        f"- 新增纸面观察: {summary.opened}",
        f"- 已完成纸面退出: {summary.closed}",
        f"- 当前纸面持有: {len(open_trades)}",
        f"- 不可成交: {summary.not_executable or len(blocked_trades)}",
        f"- 待人工复核: {summary.paper_review or len(pending_trades)}",
        f"- 熔断阻塞: {summary.blocked_by_circuit_breaker}",
        f"- 等待入场数据: {summary.pending_entry or len(pending_trades)}",
        f"- 跳过信号: {summary.skipped}",
        "",
    ]
    if open_trades:
        lines.append("## 当前纸面持有")
        for trade in open_trades:
            lines.append(
                f"- {trade['symbol']} entry={trade['entry_price']} "
                f"stop={trade['stop_loss']} take={trade['take_profit']} "
                f"horizon={trade['horizon_days']}d"
            )
        lines.append("")
    else:
        lines.append("## 当前纸面持有")
        lines.append("无 open 纸面持有记录。")
        lines.append("")
    if closed_trades:
        lines.append("## 最近纸面退出")
        for trade in closed_trades[-5:]:
            lines.append(
                f"- {trade['symbol']} {trade['exit_reason']} "
                f"{trade['return_pct']}% ({trade['entry_date']} -> {trade['exit_date']})"
            )
        lines.append("")
    if blocked_trades:
        lines.append("## 不可成交")
        for trade in blocked_trades[-5:]:
            lines.append(
                f"- {trade['symbol']} {trade.get('not_executable_reason', '')} "
                f"({trade.get('signal_date', '')} -> {trade.get('entry_date', '')})"
            )
    if pending_trades:
        lines.append("")
        lines.append("## 等待入场")
        for trade in pending_trades[-10:]:
            lines.append(
                f"- {trade['symbol']} {trade.get('name', '')} "
                f"signal_date={trade.get('signal_date', '')}"
            )
    if summary.account is not None:
        lines.extend(
            [
                "",
                "## 纸面账户",
                f"- 现金: {summary.account.cash:.2f}",
                f"- 持仓市值: {summary.account.market_value:.2f}",
                f"- 账户权益: {summary.account.equity:.2f}",
                f"- 未实现盈亏: {summary.account.unrealized_pnl:.2f}",
            ]
        )
    return "\n".join(lines)


def _pending_entry_from_signal(
    signal: dict,
    execution: ExecutionConfig,
    now: str,
) -> dict:
    row = _paper_row_common_from_signal(
        signal,
        execution,
        now,
        include_execution_prices=False,
    )
    row.update(
        {
            "status": "pending_entry",
            "paper_status": PAPER_REVIEW,
            "paper_state": PAPER_REVIEW,
            "paper_review_reason": "等待次日开盘数据确认可成交性",
        }
    )
    return row


def _paper_row_common_from_signal(
    signal: dict,
    execution: ExecutionConfig,
    now: str,
    *,
    include_execution_prices: bool = True,
) -> dict:
    row: dict[str, object] = {
        "id": uuid4().hex,
        "signal_id": signal.get("id"),
        "symbol": str(signal.get("symbol", "")),
        "name": signal.get("name", signal.get("symbol", "")),
        "signal_date": str(signal.get("signal_date", "")),
        "stop_loss": float(signal.get("stop_loss") or 0),
        "take_profit": float(signal.get("take_profit") or 0),
        "horizon_days": int(
            _value_or_default(signal, "horizon_days", execution.horizon_days)
        ),
        "fee_bps": float(_value_or_default(signal, "fee_bps", execution.fee_bps)),
        "slippage_bps": float(
            _value_or_default(signal, "slippage_bps", execution.slippage_bps)
        ),
        "score": float(signal.get("score") or 0),
        "rating": signal.get("rating", ""),
        "strategies": signal.get("strategies", []),
        "created_at": now,
        "updated_at": now,
        "quantity": int(signal.get("quantity") or signal.get("shares") or 0),
        "cash_required": 0.0,
        "cash_before": None,
        "cash_after": None,
        "t_plus_one_sellable_date": None,
        "not_executable_reason": "",
        "reject_reason": "",
    }
    if include_execution_prices:
        row["entry_price"] = 0.0
    row.update(_paper_context_from_signal(signal))
    return row


def _open_trade_from_signal(
    signal: dict,
    frame: pd.DataFrame | None,
    execution: ExecutionConfig,
    now: str,
    account: PaperAccountConfig | None = None,
) -> dict | None:
    if frame is None or frame.empty:
        return None
    symbol = str(signal.get("symbol", ""))
    signal_date = str(signal.get("signal_date", ""))
    frame = frame.sort_values("date").reset_index(drop=True)
    future = frame[frame["date"] > signal_date]
    if future.empty:
        return None
    entry_bar = future.iloc[0]
    prev_rows = frame[frame["date"] <= signal_date]
    prev_close = (
        float(prev_rows.iloc[-1]["close"])
        if not prev_rows.empty
        else float(signal.get("signal_close") or 0)
    )
    executable, reason = _check_executable(entry_bar, prev_close, signal)
    if not executable:
        row = _paper_row_common_from_signal(signal, execution, now)
        row.update(
            {
                "entry_date": str(entry_bar["date"]),
                "status": "not_executable",
                "paper_status": NOT_EXECUTABLE,
                "paper_state": NOT_EXECUTABLE,
                "not_executable_reason": reason,
                "reject_reason": reason,
            }
        )
        return row

    slippage_bps = _value_or_default(signal, "slippage_bps", execution.slippage_bps)
    fee_bps = _value_or_default(signal, "fee_bps", execution.fee_bps)
    horizon_days = _value_or_default(signal, "horizon_days", execution.horizon_days)
    slippage = float(slippage_bps) / 10000
    entry_price = float(entry_bar["open"]) * (1 + slippage)
    account = account or PaperAccountConfig()
    quantity = _paper_quantity(signal, account)
    cash_required = round(entry_price * quantity * (1 + float(fee_bps) / 10000), 4)
    entry_date = str(entry_bar["date"])
    sellable_date = _next_frame_date(frame, entry_date)
    row = {
        "id": uuid4().hex,
        "signal_id": signal.get("id"),
        "symbol": symbol,
        "name": signal.get("name", symbol),
        "signal_date": signal_date,
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "stop_loss": float(signal.get("stop_loss") or 0),
        "take_profit": float(signal.get("take_profit") or 0),
        "horizon_days": int(horizon_days),
        "fee_bps": float(fee_bps),
        "slippage_bps": float(slippage_bps),
        "score": float(signal.get("score") or 0),
        "rating": signal.get("rating", ""),
        "strategies": signal.get("strategies", []),
        "status": "open",
        "paper_status": HELD,
        "paper_state": HELD,
        "quantity": quantity,
        "cash_required": cash_required,
        "cash_before": None,
        "cash_after": None,
        "t_plus_one_sellable_date": sellable_date,
        "reject_reason": "",
        "created_at": now,
        "updated_at": now,
    }
    row.update(_paper_context_from_signal(signal))
    return row


def _paper_context_from_signal(signal: dict) -> dict:
    context: dict[str, object] = {}
    for field in _PAPER_CONTEXT_FIELDS:
        value = signal.get(field)
        if value in (None, "", [], ()):
            continue
        if isinstance(value, (tuple, set)):
            context[field] = list(value)
            continue
        context[field] = value
    return context


def _value_or_default(row: dict, key: str, default: object) -> object:
    value = row.get(key)
    if value is None:
        return default
    return value


def _update_pending_entry_trades(
    trades: list[dict],
    frames: dict[str, pd.DataFrame],
    execution: ExecutionConfig,
    account: PaperAccountConfig | None = None,
) -> tuple[int, int]:
    opened = 0
    not_executable = 0
    now = now_shanghai().isoformat(timespec="seconds")
    for trade in trades:
        if trade.get("status") != "pending_entry":
            continue
        if not is_ledger_row_paper_review_eligible(trade):
            trade["status"] = "watch_only"
            trade["paper_status"] = PAPER_REVIEW
            trade["paper_state"] = PAPER_REVIEW
            trade["paper_review_reason"] = "质量门禁未通过"
            trade["updated_at"] = now
            continue
        symbol = str(trade.get("symbol", ""))
        frame = frames.get(symbol)
        converted = _open_trade_from_signal(trade, frame, execution, now, account)
        if converted is None:
            continue
        keep_id = trade.get("id")
        keep_signal_id = trade.get("signal_id")
        trade.clear()
        trade.update(converted)
        trade["id"] = keep_id or converted["id"]
        trade["signal_id"] = keep_signal_id or converted.get("signal_id")
        if (
            trade["status"] == "open"
            and account is not None
            and account.enforce_cash
            and not _cash_allows_trade(trades, trade, account)
        ):
            trade["status"] = "pending_entry"
            trade["paper_status"] = PAPER_REVIEW
            trade["paper_state"] = PAPER_REVIEW
            trade["cash_rejection_reason"] = "insufficient_cash"
        if trade["status"] == "open":
            opened += 1
        elif trade["status"] == "not_executable":
            not_executable += 1
    return opened, not_executable


def _paper_quantity(signal: dict, account: PaperAccountConfig) -> int:
    raw = signal.get("quantity", signal.get("shares"))
    if raw is None:
        return account.lot_size
    try:
        quantity = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("paper quantity must be an integer") from exc
    if quantity <= 0 or quantity % account.lot_size:
        raise ValueError("paper quantity must be a positive multiple of lot_size")
    return quantity


def _next_frame_date(frame: pd.DataFrame, current_date: str) -> str | None:
    dates = frame.loc[frame["date"].astype(str) > current_date, "date"]
    return str(dates.iloc[0]) if not dates.empty else None


def _cash_allows_trade(
    trades: list[dict], trade: dict, account: PaperAccountConfig
) -> bool:
    committed = sum(
        float(row.get("cash_required") or 0.0)
        for row in trades
        if row.get("paper_status") == HELD or row.get("status") == "open"
    )
    return committed + float(trade.get("cash_required") or 0.0) <= account.initial_cash


def _refresh_cash_trace(trades: list[dict], account: PaperAccountConfig) -> None:
    """Persist the cash committed by each current paper state."""
    cash = float(account.initial_cash)
    for trade in trades:
        status = str(trade.get("paper_status", "") or "")
        if status == HELD or trade.get("status") == "open":
            required = float(trade.get("cash_required") or 0.0)
            trade["cash_before"] = round(cash, 4)
            cash -= required
            trade["cash_after"] = round(cash, 4)
        elif status in PAPER_STATUSES:
            trade["cash_before"] = round(cash, 4)
            trade["cash_after"] = round(cash, 4)


def _blocked_by_circuit_breaker_from_signal(
    signal: dict,
    execution: ExecutionConfig,
    now: str,
    reason: str,
) -> dict:
    row = _paper_row_common_from_signal(signal, execution, now)
    message = reason.strip() or "组合保护冷却期中，暂停新增纸面动作"
    row.update(
        {
            "status": BLOCKED_BY_CIRCUIT_BREAKER,
            "paper_status": BLOCKED_BY_CIRCUIT_BREAKER,
            "paper_state": BLOCKED_BY_CIRCUIT_BREAKER,
            "blocked_reason": message,
            "reject_reason": message,
        }
    )
    return row


def summarize_paper_account(
    trades: list[dict],
    frames: dict[str, pd.DataFrame],
    *,
    initial_cash: float = 100_000.0,
) -> PaperAccountSnapshot:
    """Calculate a deterministic paper-account view from the paper ledger."""
    cash = float(initial_cash)
    realized = 0.0
    market_value = 0.0
    unrealized = 0.0
    held_count = 0
    for trade in trades:
        entry = float(trade.get("entry_price") or 0.0)
        notional = float(trade.get("cash_required") or 0.0)
        if trade.get("paper_status") == HELD or trade.get("status") == "open":
            held_count += 1
            cash -= notional
            quantity = int(trade.get("quantity") or 0)
            frame = frames.get(str(trade.get("symbol", "")))
            current = entry
            if frame is not None and not frame.empty and "close" in frame:
                current = float(frame.sort_values("date").iloc[-1]["close"])
            value = current * quantity
            market_value += value
            unrealized += value - entry * quantity
        elif trade.get("status") == "closed":
            return_pct = float(trade.get("return_pct") or 0.0)
            proceeds = notional * (1 + return_pct / 100)
            cash += proceeds
            realized += proceeds - notional
    return PaperAccountSnapshot(
        initial_cash=float(initial_cash),
        cash=round(cash, 4),
        market_value=round(market_value, 4),
        equity=round(cash + market_value, 4),
        realized_pnl=round(realized, 4),
        unrealized_pnl=round(unrealized, 4),
        held_count=held_count,
    )


def _update_open_trades(trades: list[dict], frames: dict[str, pd.DataFrame]) -> int:
    closed = 0
    now = now_shanghai().isoformat(timespec="seconds")
    for trade in trades:
        if trade.get("status") != "open":
            continue
        symbol = str(trade.get("symbol", ""))
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        frame = frame.sort_values("date").reset_index(drop=True)
        future = frame[frame["date"] >= str(trade.get("entry_date", ""))]
        horizon = int(trade.get("horizon_days") or 1)
        # A-share T+1: the entry bar is never an exit bar.  Keep the existing
        # horizon convention by requiring the entry bar plus ``horizon - 1``
        # subsequent bars, which preserves historical ledger results.
        if len(future) < horizon or len(future) <= 1:
            continue
        window = future.iloc[1:horizon]
        if window.empty:
            continue
        row = {
            "stop_loss": trade.get("stop_loss"),
            "take_profit": trade.get("take_profit"),
            "slippage_bps": trade.get("slippage_bps"),
        }
        exit_bar, exit_price, exit_reason = _resolve_exit(window, row)
        fee_pct = float(trade.get("fee_bps") or 0) / 100
        entry_price = float(trade.get("entry_price") or 0)
        if entry_price <= 0:
            continue
        ret = (exit_price - entry_price) / entry_price * 100 - fee_pct
        trade["status"] = "closed"
        trade["paper_status"] = "closed"
        trade["paper_state"] = "closed"
        trade["exit_date"] = str(exit_bar["date"])
        trade["exit_price"] = round(exit_price, 4)
        trade["exit_reason"] = exit_reason
        trade["return_pct"] = round(ret, 4)
        trade["updated_at"] = now
        closed += 1
    return closed
