from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.ledger import ExecutionConfig, execution_config_from_thresholds, read_ledger
from aqsp.ledger.base import _check_executable, _resolve_exit
from aqsp.ratings import is_tradable_rating


@dataclass(frozen=True)
class PaperSummary:
    opened: int = 0
    closed: int = 0
    open_positions: int = 0
    not_executable: int = 0
    pending_entry: int = 0
    skipped: int = 0


_PAPER_CONTEXT_FIELDS = (
    "portfolio_action",
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
    for line in trade_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_paper_trades(path: str | Path, rows: list[dict]) -> None:
    trade_path = Path(path)
    trade_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows
    )
    trade_path.write_text((text + "\n") if text else "", encoding="utf-8")


def sync_paper_trades(
    *,
    signal_ledger: str | Path,
    paper_ledger: str | Path,
    frames: dict[str, pd.DataFrame],
    execution: ExecutionConfig | None = None,
) -> PaperSummary:
    execution = execution or execution_config_from_thresholds()
    signals = read_ledger(signal_ledger)
    trades = read_paper_trades(paper_ledger)

    opened, not_executable = _update_pending_entry_trades(trades, frames, execution)
    closed = _update_open_trades(trades, frames)
    existing_signal_ids = {str(t.get("signal_id", "")) for t in trades}
    open_symbols = {
        str(t.get("symbol", ""))
        for t in trades
        if t.get("status") == "open" and t.get("symbol")
    }

    skipped = 0
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
        if symbol in open_symbols:
            skipped += 1
            continue

        frame = frames.get(symbol)
        if frame is None or frame.empty:
            skipped += 1
            continue
        trade = _open_trade_from_signal(signal, frame, execution, now)
        if trade is None:
            trades.append(_pending_entry_from_signal(signal, execution, now))
            existing_signal_ids.add(signal_id)
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
    write_paper_trades(paper_ledger, trades)
    open_positions = sum(1 for trade in trades if trade.get("status") == "open")
    pending_entry = sum(1 for trade in trades if trade.get("status") == "pending_entry")
    return PaperSummary(
        opened=opened,
        closed=closed,
        open_positions=open_positions,
        not_executable=not_executable,
        pending_entry=pending_entry,
        skipped=skipped,
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
                "not_executable_reason": reason,
            }
        )
        return row

    slippage_bps = _value_or_default(signal, "slippage_bps", execution.slippage_bps)
    fee_bps = _value_or_default(signal, "fee_bps", execution.fee_bps)
    horizon_days = _value_or_default(signal, "horizon_days", execution.horizon_days)
    slippage = float(slippage_bps) / 10000
    entry_price = float(entry_bar["open"]) * (1 + slippage)
    row = {
        "id": uuid4().hex,
        "signal_id": signal.get("id"),
        "symbol": symbol,
        "name": signal.get("name", symbol),
        "signal_date": signal_date,
        "entry_date": str(entry_bar["date"]),
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
        if field == "strategies":
            if isinstance(value, tuple):
                context[field] = list(value)
            else:
                context[field] = value
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
) -> tuple[int, int]:
    opened = 0
    not_executable = 0
    now = now_shanghai().isoformat(timespec="seconds")
    for trade in trades:
        if trade.get("status") != "pending_entry":
            continue
        symbol = str(trade.get("symbol", ""))
        frame = frames.get(symbol)
        converted = _open_trade_from_signal(trade, frame, execution, now)
        if converted is None:
            continue
        keep_id = trade.get("id")
        keep_signal_id = trade.get("signal_id")
        trade.clear()
        trade.update(converted)
        trade["id"] = keep_id or converted["id"]
        trade["signal_id"] = keep_signal_id or converted.get("signal_id")
        if trade["status"] == "open":
            opened += 1
        elif trade["status"] == "not_executable":
            not_executable += 1
    return opened, not_executable


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
        if len(future) < horizon:
            continue
        window = future.iloc[:horizon]
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
        trade["exit_date"] = str(exit_bar["date"])
        trade["exit_price"] = round(exit_price, 4)
        trade["exit_reason"] = exit_reason
        trade["return_pct"] = round(ret, 4)
        trade["updated_at"] = now
        closed += 1
    return closed
