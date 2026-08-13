"""Position tracking service bridging paper ledger and PositionTracker.

Reads open paper positions from the paper ledger, reconstructs them into
a :class:`PositionTracker` with T+1 freeze/unfreeze logic applied, and
produces a status report for the daily CLI output.

Advisory only — does not place orders or modify the ledger.

Dependency chain:
    position_service → portfolio.position_tracker (PositionTracker, Position)
                     → paper (read_paper_trades)
                     → core.time
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aqsp.core.time import today_shanghai
from aqsp.paper import read_paper_trades
from aqsp.portfolio.position_tracker import PositionTracker

logger = logging.getLogger(__name__)

# Paper ledger tracks signals, not actual lots.  The shares count only
# affects the displayed quantity, not the T+1 logic, so a fixed placeholder
# is sufficient.
_DEFAULT_PAPER_SHARES = 100

# Paper ledger statuses that represent an active position.
_OPEN_STATUSES = frozenset({"open", "pending_entry"})


@dataclass(frozen=True)
class PositionStatus:
    """Single position status for reporting."""

    symbol: str
    name: str
    total_shares: int
    available_shares: int
    frozen_shares: int
    cost_basis: float
    entry_date: str
    is_t1_frozen: bool


@dataclass(frozen=True)
class PositionReport:
    """Aggregated position status report for all open paper positions."""

    positions: tuple[PositionStatus, ...] = ()
    total_positions: int = 0
    t1_frozen_count: int = 0
    fully_sellable_count: int = 0
    skipped: tuple[str, ...] = ()

    @property
    def has_positions(self) -> bool:
        return len(self.positions) > 0


def _parse_entry_date(raw: object) -> date | None:
    """Parse a date string from the paper ledger (YYYY-MM-DD prefix)."""
    text = str(raw or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_tracker_from_ledger(
    paper_ledger_path: str | Path,
    *,
    today: date | None = None,
) -> PositionTracker:
    """Build a PositionTracker from paper ledger open positions.

    Reads ``open`` and ``pending_entry`` records, registers each as a
    buy in the tracker, then applies T+1 unfreeze for *today*.

    Args:
        paper_ledger_path: Path to the paper trades JSONL file.
        today: Current date for T+1 unfreeze.  Defaults to today (Shanghai).

    Returns:
        A PositionTracker with all open paper positions loaded.
    """
    today = today or today_shanghai()
    tracker = PositionTracker()
    trades = read_paper_trades(paper_ledger_path)

    for trade in trades:
        status = str(trade.get("status") or "").strip()
        if status not in _OPEN_STATUSES:
            continue

        symbol = str(trade.get("symbol") or "")
        if not symbol:
            continue

        # pending_entry records have no entry_price yet; skip them.
        entry_price = float(trade.get("entry_price") or 0)
        if entry_price <= 0:
            continue

        entry_date = _parse_entry_date(trade.get("entry_date"))
        if entry_date is None:
            # Fall back to signal_date for pending_entry without entry_date.
            entry_date = _parse_entry_date(trade.get("signal_date"))
        if entry_date is None:
            continue

        tracker.add_buy(
            symbol=symbol,
            shares=_DEFAULT_PAPER_SHARES,
            price=entry_price,
            trade_date=entry_date,
        )

    # Apply T+1 unfreeze: shares bought before today become sellable.
    tracker.update_available_shares(today)
    return tracker


def get_position_report(
    paper_ledger_path: str | Path,
    *,
    today: date | None = None,
) -> PositionReport:
    """Generate a position status report from the paper ledger.

    Advisory only — does not place orders or modify the ledger.

    Args:
        paper_ledger_path: Path to the paper trades JSONL file.
        today: Current date for T+1 unfreeze.  Defaults to today (Shanghai).

    Returns:
        A PositionReport with status for all open paper positions.
    """
    today = today or today_shanghai()
    tracker = build_tracker_from_ledger(paper_ledger_path, today=today)

    if not tracker.positions:
        return PositionReport()

    # Build a name lookup from the ledger for display.
    trades = read_paper_trades(paper_ledger_path)
    name_map: dict[str, str] = {}
    for trade in trades:
        sym = str(trade.get("symbol") or "")
        name = str(trade.get("name") or "")
        if sym and name:
            name_map[sym] = name

    statuses: list[PositionStatus] = []
    skipped: list[str] = []
    t1_frozen = 0
    fully_sellable = 0

    for symbol, pos in tracker.positions.items():
        # Retrieve entry_date from buy_history (last entry).
        entry_date_str = ""
        if pos.buy_history:
            entry_date_str = pos.buy_history[-1][0].isoformat()

        is_frozen = pos.frozen_shares > 0
        if is_frozen:
            t1_frozen += 1
        elif pos.is_fully_sellable:
            fully_sellable += 1

        statuses.append(
            PositionStatus(
                symbol=symbol,
                name=name_map.get(symbol, symbol),
                total_shares=pos.total_shares,
                available_shares=pos.available_shares,
                frozen_shares=pos.frozen_shares,
                cost_basis=pos.cost_basis,
                entry_date=entry_date_str,
                is_t1_frozen=is_frozen,
            )
        )

    # Sort: frozen first (higher risk), then by symbol.
    statuses.sort(key=lambda s: (not s.is_t1_frozen, s.symbol))

    return PositionReport(
        positions=tuple(statuses),
        total_positions=len(statuses),
        t1_frozen_count=t1_frozen,
        fully_sellable_count=fully_sellable,
        skipped=tuple(skipped),
    )


def format_position_report(report: PositionReport) -> list[str]:
    """Format a PositionReport as human-readable lines for CLI output.

    Output is intentionally compact — one summary line plus per-position
    detail only when there are T+1 frozen positions.
    """
    lines: list[str] = []
    if not report.has_positions:
        return lines

    lines.append(
        f"持仓概览: {report.total_positions} 只纸面持仓"
        f"（可卖 {report.fully_sellable_count}，T+1 冻结 {report.t1_frozen_count}）"
    )

    frozen = [p for p in report.positions if p.is_t1_frozen]
    if frozen:
        lines.append(f"  T+1 冻结（次日解冻）{len(frozen)} 只:")
        for pos in frozen[:5]:
            lines.append(
                f"    {pos.name}({pos.symbol}) "
                f"入场 {pos.cost_basis:.2f} | 买入日 {pos.entry_date}"
            )
        if len(frozen) > 5:
            lines.append(f"    ...及其他 {len(frozen) - 5} 只")

    return lines
