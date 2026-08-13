"""Stop-loss checking service bridging paper ledger and StopLossManager.

Reads open paper positions, maps them to ``StopLossManager`` Position
objects, and runs the percentage-based stop-loss check against current
prices from OHLCV frames.  Results are advisory only — they flag risk
in the daily output, they do not place orders or modify the ledger.

Dependency chain:
    stop_loss_service → risk.stop_loss (StopLossManager, Position)
                     → paper (read_paper_trades)
                     → core.time
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aqsp.paper import read_paper_trades
from aqsp.risk.stop_loss import Position, StopLossManager

logger = logging.getLogger(__name__)

# Paper ledger tracks signals, not actual lots.  The shares field only
# affects the absolute ``loss_amount`` in the check result, not the
# trigger logic, so a fixed placeholder is sufficient.
_DEFAULT_PAPER_SHARES = 100


@dataclass(frozen=True)
class StopLossAlert:
    """Single stop-loss alert for an open paper position."""

    symbol: str
    name: str
    entry_price: float
    current_price: float
    pnl_pct: float
    reason: str
    stop_loss_price: float


@dataclass(frozen=True)
class StopLossReport:
    """Aggregated stop-loss check result for all open paper positions."""

    alerts: tuple[StopLossAlert, ...] = ()
    total_open_positions: int = 0
    checked_positions: int = 0
    skipped: tuple[str, ...] = ()

    @property
    def triggered(self) -> bool:
        return len(self.alerts) > 0

    @property
    def triggered_symbols(self) -> tuple[str, ...]:
        return tuple(alert.symbol for alert in self.alerts)


def _latest_close(frame: pd.DataFrame | None) -> float | None:
    """Extract the latest valid close price from an OHLCV frame."""
    if frame is None or frame.empty:
        return None
    sorted_frame = frame.sort_values("date")
    close = sorted_frame.iloc[-1].get("close")
    try:
        value = float(close)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def check_open_position_stop_losses(
    paper_ledger_path: str | Path,
    frames: dict[str, pd.DataFrame],
    *,
    manager: StopLossManager | None = None,
) -> StopLossReport:
    """Check all open paper positions against StopLossManager thresholds.

    Advisory only — does not place orders or modify the ledger.

    Args:
        paper_ledger_path: Path to the paper trades JSONL file.
        frames: Symbol → OHLCV DataFrame mapping (used for current prices).
        manager: Optional pre-configured StopLossManager.  A default
            instance loaded from ``thresholds.yaml`` is used when omitted.

    Returns:
        A StopLossReport with alerts for triggered positions.
    """
    manager = manager or StopLossManager()
    trades = read_paper_trades(paper_ledger_path)
    open_trades = [t for t in trades if t.get("status") == "open"]

    if not open_trades:
        return StopLossReport()

    alerts: list[StopLossAlert] = []
    skipped: list[str] = []

    for trade in open_trades:
        symbol = str(trade.get("symbol") or "")
        if not symbol:
            continue

        entry_price = float(trade.get("entry_price") or 0)
        if entry_price <= 0:
            skipped.append(symbol)
            continue

        current_price = _latest_close(frames.get(symbol))
        if current_price is None:
            skipped.append(symbol)
            continue

        position = Position(
            symbol=symbol,
            shares=_DEFAULT_PAPER_SHARES,
            cost_basis=entry_price,
        )
        result = manager.check_single_stock_stop(position, current_price)

        if result.triggered:
            stop_loss_price = float(trade.get("stop_loss") or 0)
            alerts.append(
                StopLossAlert(
                    symbol=symbol,
                    name=str(trade.get("name") or symbol),
                    entry_price=entry_price,
                    current_price=current_price,
                    pnl_pct=result.pnl_pct,
                    reason=result.reason,
                    stop_loss_price=stop_loss_price,
                )
            )

    return StopLossReport(
        alerts=tuple(alerts),
        total_open_positions=len(open_trades),
        checked_positions=len(open_trades) - len(skipped),
        skipped=tuple(skipped),
    )


def format_stop_loss_report(report: StopLossReport) -> list[str]:
    """Format a StopLossReport as human-readable lines for CLI output."""
    lines: list[str] = []
    if report.total_open_positions == 0:
        return lines

    lines.append(
        f"止损检查: {report.checked_positions}/{report.total_open_positions}"
        " 纸面持仓已检查"
    )
    if report.skipped:
        lines.append(f"  跳过(无价格数据): {', '.join(report.skipped)}")
    if report.alerts:
        lines.append(f"  ⚠️ 触发止损 {len(report.alerts)} 只:")
        for alert in report.alerts:
            lines.append(
                f"    {alert.name}({alert.symbol}) "
                f"入场 {alert.entry_price:.2f} → 现价 {alert.current_price:.2f} "
                f"({alert.pnl_pct:.2%})"
            )
    else:
        lines.append("  ✅ 无止损触发")
    return lines
