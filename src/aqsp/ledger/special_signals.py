from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from aqsp.core.errors import DataError
from aqsp.core.types import RunMetadata
from aqsp.ledger.base import (
    FORMAL_LEDGER_WORKLOADS,
    ExecutionConfig,
    read_ledger,
    run_metadata_fields,
    write_ledger,
)
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
    workload: str = "",
    run_metadata: RunMetadata | None = None,
) -> None:
    _validate_special_run_metadata(run_metadata, workload=workload)
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
                **run_metadata_fields(run_metadata),
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


def _validate_special_run_metadata(
    metadata: RunMetadata | None,
    *,
    workload: str,
) -> None:
    """Reject incomplete provenance before a formal special signal is written."""
    requested_workload = workload.strip()
    metadata_workload = metadata.workload.strip() if metadata else ""
    if requested_workload and metadata is None:
        raise DataError(
            "特殊策略 ledger 声明 workload 时必须提供 run_metadata: "
            f"workload={requested_workload}"
        )
    if requested_workload and requested_workload != metadata_workload:
        raise DataError(
            "特殊策略 ledger 的 workload 与 run_metadata 不一致: "
            f"workload={requested_workload}, metadata={metadata_workload}"
        )
    effective_workload = requested_workload or metadata_workload
    if metadata is None or effective_workload not in FORMAL_LEDGER_WORKLOADS:
        return

    required = {
        "requested_source": metadata.requested_source,
        "actual_source": metadata.actual_source,
        "source_freshness_tier": metadata.source_freshness_tier,
        "source_coverage_tier": metadata.source_coverage_tier,
        "source_local_status": metadata.source_local_status,
        "source_health_label": metadata.source_health_label,
        "thresholds_version": metadata.thresholds_version,
        "data_latest_trade_date": metadata.data_latest_trade_date,
    }
    missing = tuple(name for name, value in required.items() if not str(value).strip())
    if missing:
        raise DataError(
            "正式特殊策略 ledger 缺少 provenance: "
            + ", ".join(missing)
            + f" (workload={effective_workload})"
        )
    if metadata.data_lag_days < 0:
        raise DataError("正式特殊策略 ledger 的 data_lag_days 不能为负数")
