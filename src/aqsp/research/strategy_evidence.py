"""Immutable, content-addressed strategy walk-forward evidence artifacts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from aqsp.backtest.audit import audit_backtest_assumptions
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text
from aqsp.walkforward_gate import (
    MAX_PBO,
    MAX_GATE_AGE_DAYS,
    MIN_CSCV_VARIANTS,
    MIN_DSR,
    validate_walkforward_gate_payload,
)


@dataclass(frozen=True)
class StrategyOosEvidence:
    schema_version: int
    artifact_id: str
    run_id: str
    generated_at: str
    strategy_id: str
    strategy_version: str
    thresholds_version: str
    parameters_sha256: str
    parameters: Mapping[str, Any]
    requested_data_source: str
    actual_data_source: str | None
    price_mode: str
    data_start: str
    data_end: str
    effective_symbols: int
    metrics: Mapping[str, Any]
    oos_windows: tuple[Mapping[str, Any], ...]
    grid_results: tuple[Mapping[str, Any], ...]
    assumptions: Mapping[str, object]
    gate: Mapping[str, Any]
    evidence_status: str
    blockers: tuple[str, ...]
    human_review_required: bool
    promotion_authorized: bool


def build_strategy_oos_evidence(
    *,
    strategy_id: str,
    strategy_version: str,
    parameters: Mapping[str, Any],
    requested_data_source: str,
    actual_data_source: str | None,
    price_mode: str,
    data_start: str,
    data_end: str,
    effective_symbols: int,
    metrics: Mapping[str, Any],
    oos_windows: Sequence[Mapping[str, Any]],
    assumptions: Mapping[str, object],
    gate_payload: Mapping[str, object],
    grid_results: Sequence[Mapping[str, Any]] = (),
    today: date | None = None,
) -> StrategyOosEvidence:
    """Bind an exact strategy configuration to its walk-forward result and gate."""
    normalized_parameters = json.loads(_canonical_json(parameters))
    parameters_sha256 = hashlib.sha256(_canonical_json(normalized_parameters).encode()).hexdigest()
    clean_windows = tuple(json.loads(_canonical_json(item)) for item in oos_windows)
    clean_grid_results = tuple(json.loads(_canonical_json(item)) for item in grid_results)
    clean_metrics = json.loads(_canonical_json(metrics))
    clean_assumptions = json.loads(_canonical_json(assumptions))
    blockers: list[str] = []

    if not strategy_id.strip():
        blockers.append("strategy_id missing")
    if not strategy_version.strip():
        blockers.append("strategy_version missing")
    if not requested_data_source.strip():
        blockers.append("requested_data_source missing")
    if not actual_data_source or not actual_data_source.strip():
        blockers.append("actual_data_source missing")
    thresholds_version = str(gate_payload.get("thresholds_version", "") or "").strip()
    if not thresholds_version:
        blockers.append("thresholds_version missing")
    elif thresholds_version != strategy_version:
        blockers.append("thresholds_version does not match strategy_version")
    if price_mode.strip().lower() != "raw":
        blockers.append("price_mode must be raw")
    if effective_symbols <= 0:
        blockers.append("effective_symbols must be positive")
    if not clean_windows:
        blockers.append("oos_windows missing")
    try:
        start_date = date.fromisoformat(data_start)
        end_date = date.fromisoformat(data_end)
    except (TypeError, ValueError):
        start_date = end_date = None
    if start_date is None or end_date is None or end_date < start_date:
        blockers.append("data range missing or invalid")

    assumption_audit = audit_backtest_assumptions(clean_assumptions)
    blockers.extend(f"assumption_audit: {item}" for item in assumption_audit.blockers)
    gate_validation = validate_walkforward_gate_payload(
        gate_payload,
        today=today or now_shanghai().date(),
        expected_thresholds_version=strategy_version or None,
        require_assumption_audit=True,
    )
    blockers.extend(f"walkforward_gate: {item}" for item in gate_validation.blockers)

    _validate_numeric_tree(clean_metrics, "metrics", blockers)
    for index, window in enumerate(clean_windows):
        if not str(window.get("period", "")).strip():
            blockers.append(f"oos_windows[{index}].period missing")
        _validate_numeric_tree(window, f"oos_windows[{index}]", blockers)

    generated_at = now_shanghai().isoformat()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "generated_at": generated_at,
        "strategy_id": strategy_id.strip(),
        "strategy_version": strategy_version.strip(),
        "thresholds_version": thresholds_version,
        "parameters_sha256": parameters_sha256,
        "parameters": normalized_parameters,
        "requested_data_source": requested_data_source.strip(),
        "actual_data_source": actual_data_source.strip() if actual_data_source else None,
        "price_mode": price_mode.strip().lower(),
        "data_start": data_start,
        "data_end": data_end,
        "effective_symbols": effective_symbols,
        "metrics": clean_metrics,
        "oos_windows": list(clean_windows),
        "grid_results": list(clean_grid_results),
        "assumptions": clean_assumptions,
        "gate": {
            "status": "pass" if gate_validation.ok else "blocked",
            "dsr": gate_validation.dsr,
            "pbo": gate_validation.pbo,
            "n_periods": gate_validation.n_periods,
            "n_variants": gate_validation.n_variants,
            "run_date": gate_validation.run_date.isoformat() if gate_validation.run_date else None,
            "data_end": gate_validation.data_end.isoformat() if gate_validation.data_end else None,
            "blockers": list(gate_validation.blockers),
        },
        "evidence_status": "validated_research_evidence" if not blockers else "blocked",
        "blockers": sorted(set(blockers)),
        "human_review_required": True,
        "promotion_authorized": False,
    }
    artifact_id = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return StrategyOosEvidence(
        schema_version=1,
        artifact_id=artifact_id,
        run_id=payload["run_id"],
        generated_at=generated_at,
        strategy_id=payload["strategy_id"],
        strategy_version=payload["strategy_version"],
        thresholds_version=thresholds_version,
        parameters_sha256=parameters_sha256,
        parameters=normalized_parameters,
        requested_data_source=payload["requested_data_source"],
        actual_data_source=payload["actual_data_source"],
        price_mode=payload["price_mode"],
        data_start=data_start,
        data_end=data_end,
        effective_symbols=effective_symbols,
        metrics=clean_metrics,
        oos_windows=clean_windows,
        grid_results=clean_grid_results,
        assumptions=clean_assumptions,
        gate=payload["gate"],
        evidence_status=payload["evidence_status"],
        blockers=tuple(payload["blockers"]),
        human_review_required=True,
        promotion_authorized=False,
    )


def save_strategy_oos_evidence(
    evidence: StrategyOosEvidence,
    *,
    root: str | Path,
) -> Path:
    """Persist an immutable content-addressed artifact under the runtime data root."""
    payload = asdict(evidence)
    identity_payload = {key: value for key, value in payload.items() if key != "artifact_id"}
    expected_artifact_id = hashlib.sha256(_canonical_json(identity_payload).encode("utf-8")).hexdigest()
    if expected_artifact_id != evidence.artifact_id:
        raise ValueError("strategy evidence content does not match artifact_id")
    path = Path(root) / "strategy_oos_evidence" / evidence.strategy_id / f"{evidence.artifact_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with advisory_lock(path):
        if path.exists():
            if path.read_text(encoding="utf-8") != serialized:
                raise ValueError("strategy evidence hash collision or existing artifact corruption")
            return path
        atomic_write_text(path, serialized)
    return path


def list_strategy_oos_evidence(
    *,
    root: str | Path,
    strategy_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read and verify persisted evidence; any corrupt artifact fails closed."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    evidence_root = Path(root) / "strategy_oos_evidence"
    if strategy_id is not None and (
        not strategy_id
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in strategy_id)
    ):
        raise ValueError("strategy_id contains invalid path characters")
    strategy_roots = [evidence_root / strategy_id] if strategy_id else sorted(evidence_root.iterdir()) if evidence_root.exists() else []
    rows: list[dict[str, Any]] = []
    for strategy_root in strategy_roots:
        if not strategy_root.exists():
            continue
        for path in strategy_root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"strategy evidence artifact unreadable: {path.name}") from exc
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ValueError(f"strategy evidence artifact schema invalid: {path.name}")
            artifact_id = str(payload.get("artifact_id", ""))
            identity_payload = {key: value for key, value in payload.items() if key != "artifact_id"}
            expected = hashlib.sha256(_canonical_json(identity_payload).encode("utf-8")).hexdigest()
            if artifact_id != expected or path.stem != artifact_id:
                raise ValueError(f"strategy evidence artifact hash mismatch: {path.name}")
            payload["current_assessment"] = _assess_current_gate(payload)
            rows.append(payload)
    rows.sort(key=lambda row: str(row.get("generated_at", "")), reverse=True)
    return rows[:limit]


def _assess_current_gate(payload: Mapping[str, Any]) -> dict[str, Any]:
    gate = payload.get("gate")
    assumptions = payload.get("assumptions")
    if not isinstance(gate, Mapping) or not isinstance(assumptions, Mapping):
        raise ValueError("strategy evidence gate/assumptions missing")
    dsr = gate.get("dsr")
    pbo = gate.get("pbo")
    n_variants = gate.get("n_variants")
    dsr_pass = isinstance(dsr, (int, float)) and not isinstance(dsr, bool) and dsr > MIN_DSR
    pbo_valid = isinstance(pbo, (int, float)) and not isinstance(pbo, bool) and pbo > 0
    pbo_pass = pbo_valid and pbo < MAX_PBO
    cscv_reliable = isinstance(n_variants, int) and not isinstance(n_variants, bool) and n_variants >= MIN_CSCV_VARIANTS
    validation = validate_walkforward_gate_payload(
        {
            "run_date": gate.get("run_date"),
            "deflated_sharpe": dsr,
            "pbo": pbo,
            "pbo_valid": pbo_valid,
            "dsr_pass": dsr_pass,
            "pbo_pass": pbo_pass,
            "both_pass": dsr_pass and pbo_pass and cscv_reliable,
            "n_periods": gate.get("n_periods"),
            "n_variants": n_variants,
            "cscv_reliable": cscv_reliable,
            "data_start": payload.get("data_start"),
            "data_end": payload.get("data_end"),
            "thresholds_version": payload.get("thresholds_version"),
            "backtest_assumptions": assumptions,
        },
        today=now_shanghai().date(),
        expected_thresholds_version=str(payload.get("strategy_version") or "") or None,
        require_assumption_audit=True,
    )
    return {
        "status": "pass" if validation.ok else "blocked",
        "age_days": validation.age_days,
        "max_age_days": MAX_GATE_AGE_DAYS,
        "blockers": list(validation.blockers),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_numeric_tree(value: Any, path: str, blockers: list[str]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        blockers.append(f"{path} contains non-finite number")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_numeric_tree(item, f"{path}.{key}", blockers)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_numeric_tree(item, f"{path}[{index}]", blockers)
