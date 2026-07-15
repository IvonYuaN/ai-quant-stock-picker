from __future__ import annotations

import json
from pathlib import Path

from aqsp.audit.decision_chain import (
    GENESIS_HASH,
    append_decision_record,
    new_decision_record,
    verify_decision_chain,
)


def test_decision_chain_verifies_append_only_records(tmp_path: Path) -> None:
    path = tmp_path / "decision-audit.jsonl"
    first = append_decision_record(
        path,
        new_decision_record(
            run_id="close-1",
            thresholds_version="2026.07.11",
            regime="stable_bull",
            source="eastmoney",
            candidates=({"symbol": "600519", "score": 71.0},),
            evidence_ids=("news:1",),
            advisory_ids=("debate:1",),
        ),
    )
    second = append_decision_record(
        path,
        new_decision_record(
            run_id="close-2",
            thresholds_version="2026.07.11",
            regime="stable_bull",
            source="eastmoney",
            candidates=({"symbol": "300750", "score": 66.0},),
        ),
    )

    assert first.previous_hash == GENESIS_HASH
    assert second.previous_hash == first.record_hash
    assert verify_decision_chain(path).ok is True


def test_decision_chain_detects_tampered_score(tmp_path: Path) -> None:
    path = tmp_path / "decision-audit.jsonl"
    append_decision_record(
        path,
        new_decision_record(
            run_id="close-1",
            thresholds_version="2026.07.11",
            regime="stable_bull",
            source="eastmoney",
            candidates=({"symbol": "600519", "score": 71.0},),
        ),
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    row["candidates"][0]["score"] = 99.0
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    result = verify_decision_chain(path)

    assert result.ok is False
    assert result.detail == "hash mismatch at record 1"
