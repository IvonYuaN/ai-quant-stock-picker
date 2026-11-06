from __future__ import annotations

from datetime import date
import json

from aqsp.research.strategy_evidence import (
    build_strategy_oos_evidence,
    list_strategy_oos_evidence,
    save_strategy_oos_evidence,
)
from aqsp.walkforward_gate import build_walkforward_gate_payload


def _assumptions() -> dict[str, object]:
    return {
        "uses_raw_prices": True,
        "uses_point_in_time_data": True,
        "train_test_separated": True,
        "has_purge_window": True,
        "includes_transaction_costs": True,
        "includes_slippage": True,
        "excludes_not_executable": True,
        "cost_model": "fee=3bps; slippage=20bps",
        "price_mode": "raw",
        "future_data_used": False,
    }


def _gate(**overrides: object) -> dict[str, object]:
    payload = build_walkforward_gate_payload(
        dsr=1.7,
        pbo=0.2,
        run_date="2026-06-10",
        start="2023-01-01",
        end="2026-06-09",
        n_periods=12,
        n_variants=8,
        thresholds_version="1.1.21",
        metadata={"backtest_assumptions": _assumptions()},
    )
    payload.update(overrides)
    return payload


def _build(**overrides: object):
    values = {
        "strategy_id": "composite",
        "strategy_version": "1.1.21",
        "parameters": {"weights": {"momentum": 0.3}},
        "requested_data_source": "sqlite_db",
        "actual_data_source": "sqlite_db",
        "price_mode": "raw",
        "data_start": "2023-01-01",
        "data_end": "2026-06-09",
        "effective_symbols": 4200,
        "metrics": {"total_return": 0.12, "sharpe_ratio": 1.1},
        "oos_windows": [{"period": "2025-01-01/2025-06-30", "total_return": 0.04}],
        "assumptions": _assumptions(),
        "gate_payload": _gate(),
        "today": date(2026, 6, 14),
    }
    values.update(overrides)
    return build_strategy_oos_evidence(**values)


def test_strategy_oos_evidence_binds_exact_version_and_requires_human_review():
    evidence = _build()

    assert evidence.strategy_id == "composite"
    assert evidence.strategy_version == evidence.thresholds_version == "1.1.21"
    assert len(evidence.parameters_sha256) == 64
    assert len(evidence.artifact_id) == 64
    assert evidence.evidence_status == "validated_research_evidence"
    assert evidence.human_review_required is True
    assert evidence.promotion_authorized is False


def test_strategy_oos_evidence_blocks_stale_gate_and_missing_pit_assumption():
    assumptions = {**_assumptions(), "uses_point_in_time_data": False}
    evidence = _build(
        assumptions=assumptions,
        gate_payload=_gate(run_date="2025-01-01"),
    )

    assert evidence.evidence_status == "blocked"
    assert any("uses_point_in_time_data" in item for item in evidence.blockers)
    assert any("gate stale" in item for item in evidence.blockers)
    assert evidence.promotion_authorized is False


def test_strategy_oos_evidence_blocks_threshold_mismatch_and_adjusted_prices():
    evidence = _build(
        price_mode="qfq",
        gate_payload=_gate(thresholds_version="1.1.20"),
    )

    assert evidence.evidence_status == "blocked"
    assert "price_mode must be raw" in evidence.blockers
    assert "thresholds_version does not match strategy_version" in evidence.blockers


def test_strategy_oos_evidence_saves_immutable_content_addressed_artifact(tmp_path):
    evidence = _build()
    path = save_strategy_oos_evidence(evidence, root=tmp_path)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == f"{evidence.artifact_id}.json"
    assert stored["artifact_id"] == evidence.artifact_id
    assert stored["promotion_authorized"] is False
    assert save_strategy_oos_evidence(evidence, root=tmp_path) == path
    listed = list_strategy_oos_evidence(root=tmp_path, strategy_id="composite")
    assert [item["artifact_id"] for item in listed] == [evidence.artifact_id]


def test_strategy_oos_evidence_reader_fails_closed_on_tampered_artifact(tmp_path):
    evidence = _build()
    path = save_strategy_oos_evidence(evidence, root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["strategy_version"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        list_strategy_oos_evidence(root=tmp_path)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered strategy evidence must be rejected")

