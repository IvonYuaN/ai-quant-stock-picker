"""Persistent, atomic batch cursor for live intraday universe coverage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import atomic_write_text


@dataclass(frozen=True)
class IntradayBatch:
    trade_date: str
    universe_version: str
    cycle_id: int
    offset: int
    symbols: tuple[str, ...]
    universe_count: int
    batch_size: int

    @property
    def batch_id(self) -> str:
        return f"{self.trade_date}:{self.cycle_id}:{self.offset}"

    @property
    def coverage_pct(self) -> float:
        if self.universe_count <= 0:
            return 0.0
        return round(
            min(1.0, (self.offset + len(self.symbols)) / self.universe_count), 6
        )


class IntradayUniverseCursor:
    """Select and commit one deterministic batch per successful live run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def select(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        trade_date: date,
        batch_size: int,
    ) -> IntradayBatch:
        # Live liquidity rankings can reorder between refreshes. Cursor state
        # must track the current live membership, not transient ranking order.
        normalized = tuple(
            sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})
        )
        if not normalized:
            raise ValueError("intraday universe must not be empty")
        if batch_size <= 0:
            raise ValueError("intraday batch_size must be positive")
        version = _universe_version(normalized)
        current = self._read()
        date_text = trade_date.isoformat()
        if (
            current.get("trade_date") != date_text
            or current.get("universe_version") != version
            or int(current.get("universe_count") or 0) != len(normalized)
        ):
            offset = 0
            cycle_id = 1
        else:
            offset = int(current.get("next_offset") or 0) % len(normalized)
            cycle_id = int(current.get("cycle_id") or 1)
        selected = tuple(normalized[offset : offset + batch_size])
        if len(selected) < batch_size and offset > 0:
            selected += tuple(normalized[: batch_size - len(selected)])
        batch = IntradayBatch(
            trade_date=date_text,
            universe_version=version,
            cycle_id=cycle_id,
            offset=offset,
            symbols=selected,
            universe_count=len(normalized),
            batch_size=batch_size,
        )
        self._write(
            {
                **current,
                "trade_date": batch.trade_date,
                "universe_version": batch.universe_version,
                "universe_count": batch.universe_count,
                "batch_size": batch.batch_size,
                "next_offset": batch.offset,
                "cycle_id": batch.cycle_id,
                "active_offset": batch.offset,
                "active_symbols": list(batch.symbols),
                "active_batch_id": batch.batch_id,
            }
        )
        return batch

    def commit_current(self, *, scanned_count: int) -> None:
        current = self._read()
        batch = self._batch_from_state(current)
        self.commit(batch, scanned_count=scanned_count)

    def fail_current(self, error: str) -> None:
        current = self._read()
        self.fail(self._batch_from_state(current), error)

    def commit(self, batch: IntradayBatch, *, scanned_count: int) -> None:
        if scanned_count < 0:
            raise ValueError("scanned_count must not be negative")
        next_offset = batch.offset + len(batch.symbols)
        cycle_id = batch.cycle_id
        if next_offset >= batch.universe_count:
            next_offset = 0
            cycle_id += 1
        self._write(
            {
                "trade_date": batch.trade_date,
                "universe_version": batch.universe_version,
                "universe_count": batch.universe_count,
                "batch_size": batch.batch_size,
                "next_offset": next_offset,
                "cycle_id": cycle_id,
                "last_successful_offset": batch.offset,
                "last_batch_id": batch.batch_id,
                "scanned_count": scanned_count,
                "coverage_pct": batch.coverage_pct,
                "last_batch_finished_at": now_shanghai().isoformat(timespec="seconds"),
                "last_error": "",
            }
        )

    def fail(self, batch: IntradayBatch, error: str) -> None:
        self._write(
            {
                "trade_date": batch.trade_date,
                "universe_version": batch.universe_version,
                "universe_count": batch.universe_count,
                "batch_size": batch.batch_size,
                "next_offset": batch.offset,
                "cycle_id": batch.cycle_id,
                "last_successful_offset": int(
                    self._read().get("last_successful_offset") or 0
                ),
                "scanned_count": 0,
                "coverage_pct": round(batch.offset / batch.universe_count, 6),
                "last_batch_finished_at": "",
                "last_error": str(error or "batch failed")[:500],
            }
        )

    def _read(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _batch_from_state(payload: dict[str, object]) -> IntradayBatch:
        symbols = tuple(
            str(item) for item in payload.get("active_symbols", ()) if str(item)
        )
        if not symbols:
            raise ValueError("no active intraday batch")
        return IntradayBatch(
            trade_date=str(payload.get("trade_date") or ""),
            universe_version=str(payload.get("universe_version") or ""),
            cycle_id=int(payload.get("cycle_id") or 1),
            offset=int(
                payload.get("active_offset", payload.get("next_offset", 0)) or 0
            ),
            symbols=symbols,
            universe_count=int(payload.get("universe_count") or 0),
            batch_size=int(payload.get("batch_size") or len(symbols)),
        )

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        )


def _universe_version(symbols: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\n".join(symbols).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
