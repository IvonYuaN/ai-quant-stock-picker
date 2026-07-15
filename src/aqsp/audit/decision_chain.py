"""Append-only hash-chain records for deterministic research decisions."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock


GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class DecisionAuditRecord:
    run_id: str
    created_at: str
    thresholds_version: str
    regime: str
    source: str
    candidates: tuple[Mapping[str, Any], ...]
    evidence_ids: tuple[str, ...] = ()
    advisory_ids: tuple[str, ...] = ()
    previous_hash: str = GENESIS_HASH
    record_hash: str = ""

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("record_hash", None)
        return payload

    def with_hash(self) -> DecisionAuditRecord:
        payload = self.payload()
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return DecisionAuditRecord(
            **payload, record_hash=hashlib.sha256(encoded.encode()).hexdigest()
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload(), "record_hash": self.record_hash}


@dataclass(frozen=True)
class DecisionChainVerification:
    ok: bool
    checked_records: int
    detail: str


def append_decision_record(
    path: str | Path, record: DecisionAuditRecord
) -> DecisionAuditRecord:
    """Append an immutable decision record, chaining it to the last valid row."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with advisory_lock(file_path):
        previous_hash = _last_record_hash(file_path)
        hashed = DecisionAuditRecord(
            **{**record.payload(), "previous_hash": previous_hash}
        ).with_hash()
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(hashed.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    return hashed


def new_decision_record(
    *,
    run_id: str,
    thresholds_version: str,
    regime: str,
    source: str,
    candidates: tuple[Mapping[str, Any], ...],
    evidence_ids: tuple[str, ...] = (),
    advisory_ids: tuple[str, ...] = (),
) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        run_id=run_id,
        created_at=now_shanghai().isoformat(timespec="seconds"),
        thresholds_version=thresholds_version,
        regime=regime,
        source=source,
        candidates=candidates,
        evidence_ids=evidence_ids,
        advisory_ids=advisory_ids,
    )


def verify_decision_chain(path: str | Path) -> DecisionChainVerification:
    file_path = Path(path)
    if not file_path.exists():
        return DecisionChainVerification(True, 0, "empty chain")
    previous_hash = GENESIS_HASH
    for index, line in enumerate(
        file_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            record_hash = str(payload.pop("record_hash"))
            record = DecisionAuditRecord(**payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return DecisionChainVerification(
                False, index - 1, f"invalid record {index}: {exc}"
            )
        if record.previous_hash != previous_hash:
            return DecisionChainVerification(
                False, index - 1, f"broken link at record {index}"
            )
        if record.with_hash().record_hash != record_hash:
            return DecisionChainVerification(
                False, index - 1, f"hash mismatch at record {index}"
            )
        previous_hash = record_hash
    return DecisionChainVerification(
        True, index if "index" in locals() else 0, "chain verified"
    )


def _last_record_hash(path: Path) -> str:
    if not path.exists():
        return GENESIS_HASH
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            return str(json.loads(line).get("record_hash") or GENESIS_HASH)
    return GENESIS_HASH
