from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from aqsp.ledger.base import ExecutionConfig, read_ledger, write_ledger
from aqsp.utils.jsonl_io import advisory_lock


@dataclass(frozen=True)
class SpecialSignalLedgerRow:
    symbol: str
    name: str
    signal_close: float
    score: float
    strategy_id: str
    sub_strategy: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    stop_loss: float
    confidence: float
    take_profit: float = 0.0
    position: str = "watch"
    entry_type: str = "next_open"
    ideal_buy: float = 0.0


def append_special_strategy_signals(
    path: str | Path,
    signals: Iterable[SpecialSignalLedgerRow],
    *,
    signal_date: str,
    created_at: str,
    thresholds_version: str,
    regime: str = "",
    execution: ExecutionConfig | None = None,
) -> None:
    execution = execution or ExecutionConfig()
    with advisory_lock(path):
        rows = read_ledger(path)
        row_index_by_key = {
            _special_signal_key(row): idx for idx, row in enumerate(rows)
        }

        for signal in signals:
            row_key = (
                signal_date,
                signal.symbol,
                thresholds_version,
                regime,
                signal.entry_type,
                signal.strategy_id,
                signal.sub_strategy,
            )
            signal_day_group = f"{signal_date}_{signal.strategy_id}"
            fields = {
                "signal_date": signal_date,
                "symbol": signal.symbol,
                "name": signal.name,
                "signal_close": signal.signal_close,
                "intended_entry": signal.entry_type,
                "score": signal.score,
                "rating": "buy_candidate",
                "position": signal.position,
                "entry_type": signal.entry_type,
                "ideal_buy": signal.ideal_buy or signal.signal_close,
                "strategies": [signal.strategy_id],
                "sub_strategy": signal.sub_strategy,
                "reasons": list(signal.reasons),
                "risks": list(signal.risks),
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "confidence": signal.confidence,
                "horizon_days": execution.horizon_days,
                "fee_bps": execution.fee_bps,
                "slippage_bps": execution.slippage_bps,
                "benchmark_symbol": execution.benchmark_symbol,
                "limit_up_pct": execution.limit_up_pct,
                "limit_down_pct": execution.limit_down_pct,
                "thresholds_version": thresholds_version,
                "regime_at_signal": regime,
                "signal_day_group": signal_day_group,
            }

            existing_idx = row_index_by_key.get(row_key)
            if existing_idx is not None:
                existing_row = rows[existing_idx]
                rows[existing_idx] = {
                    **existing_row,
                    **fields,
                    "status": str(existing_row.get("status", "") or "pending"),
                }
                continue

            rows.append(
                {
                    "id": uuid4().hex,
                    "created_at": created_at,
                    **fields,
                    "status": "pending",
                }
            )
            row_index_by_key[row_key] = len(rows) - 1

        write_ledger(path, rows)


def _special_signal_key(row: dict) -> tuple[str, str, str, str, str, str, str]:
    strategies = row.get("strategies", [])
    strategy_id = (
        str(strategies[0]) if isinstance(strategies, list) and strategies else ""
    )
    return (
        str(row.get("signal_date", "")),
        str(row.get("symbol", "")),
        str(row.get("thresholds_version", "")),
        str(row.get("regime_at_signal", "")),
        str(row.get("intended_entry", "next_open")),
        strategy_id,
        str(row.get("sub_strategy", "")),
    )
