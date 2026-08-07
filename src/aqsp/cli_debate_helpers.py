"""Debate record I/O helpers extracted from ``cli.py``.

These functions manage the JSONL persistence layer for multi-agent debate
results: reading retained debates, merging updates, writing sorted output,
and computing stable record keys / candidate fingerprints.

They are pure utilities with no dependency on ``cli.py`` internals, which
keeps the extraction acyclic and testable in isolation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aqsp.models import PickResult
from aqsp.utils.jsonl_io import atomic_write_text


def _debate_record_key(data: dict[str, Any]) -> str:
    """Build a stable composite key for deduplicating debate records."""
    symbol = str(data.get("symbol", "") or "")
    debate_date = str(
        data.get("related_signal_date", "") or data.get("debate_date", "")
    )
    task_id = str(data.get("task_id", "") or "")
    fingerprint = str(data.get("candidate_fingerprint", "") or "")
    if task_id or fingerprint:
        return "|".join((symbol, debate_date, task_id, fingerprint))
    return f"{symbol}_{debate_date}"


def _candidate_debate_fingerprint(pick: PickResult) -> str:
    """Return a short hash that uniquely identifies a pick for debate dedup."""
    payload = {
        "symbol": pick.symbol,
        "date": pick.date,
        "score": round(float(pick.score or 0.0), 4),
        "rating": pick.rating,
        "strategies": list(pick.strategies),
        "reasons": list(pick.reasons),
        "risks": list(pick.risks),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _read_retained_debates(debate_file: Path, cutoff_date: str) -> dict[str, dict]:
    """Read debate records on or after *cutoff_date*, deduplicated by key."""
    retained: dict[str, dict] = {}
    if not debate_file.exists():
        return retained
    for line in debate_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            debate_date = str(
                data.get("related_signal_date", "") or data.get("debate_date", "")
            )
            if debate_date < cutoff_date:
                continue
            key = _debate_record_key(data)
            if key not in retained or retained[key].get("created_at", "") < data.get(
                "created_at", ""
            ):
                retained[key] = data
        except (json.JSONDecodeError, KeyError):
            pass
    return retained


def _merge_debate_records(target: dict[str, dict], updates: dict[str, dict]) -> None:
    """Merge *updates* into *target*, keeping the newest record per key."""
    for data in updates.values():
        debate_date = str(
            data.get("related_signal_date", "") or data.get("debate_date", "")
        )
        symbol = str(data.get("symbol", ""))
        if not symbol or not debate_date:
            continue
        key = _debate_record_key(data)
        if key not in target or target[key].get("created_at", "") < data.get(
            "created_at", ""
        ):
            target[key] = data


def _write_debate_records(debate_file: Path, records: dict[str, dict]) -> None:
    """Write debate records as sorted JSONL using an atomic write."""
    text = "".join(
        json.dumps(data, ensure_ascii=False) + "\n"
        for data in sorted(
            records.values(),
            key=lambda item: (
                str(item.get("related_signal_date", "") or item.get("debate_date", "")),
                str(item.get("symbol", "")),
                str(item.get("task_id", "")),
                str(item.get("candidate_fingerprint", "")),
                str(item.get("created_at", "")),
            ),
        )
    )
    atomic_write_text(debate_file, text)
