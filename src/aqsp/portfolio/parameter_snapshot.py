"""Provenance of the immutable configuration used by a screening run."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from aqsp.strategies.thresholds import Thresholds


@dataclass(frozen=True)
class ParameterSnapshot:
    """Public identity only; numeric research parameters stay in runtime artifacts."""

    version: str
    effective_from: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("parameter snapshot requires a version")
        if not isinstance(self.effective_from, str):
            raise ValueError("parameter snapshot requires an effective date")
        if date.fromisoformat(self.effective_from).isoformat() != self.effective_from:
            raise ValueError("parameter snapshot requires an ISO effective date")
        if not isinstance(self.fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.fingerprint
        ):
            raise ValueError("parameter snapshot requires a SHA256 fingerprint")


def build_parameter_snapshot(
    thresholds: Thresholds, runtime_weights: Mapping[str, object]
) -> ParameterSnapshot | None:
    """Bind identity to the actual scoring inputs, never a later config reload.

    Legacy/test configurations without an effective date have no verified
    identity. A timestamp or a guessed expiry date must not fill that gap.
    """
    if not thresholds.version or not thresholds.effective_from:
        return None
    encoded = json.dumps(
        {"thresholds": thresholds.to_dict(), "runtime_weights": dict(runtime_weights)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return ParameterSnapshot(
        version=thresholds.version,
        effective_from=thresholds.effective_from,
        fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def parse_parameter_snapshot(payload: object) -> ParameterSnapshot | None:
    """Read an optional identity; reject malformed metadata instead of inventing it."""
    if payload is None:
        return None
    if not isinstance(payload, Mapping) or set(payload) != {
        "version",
        "effective_from",
        "fingerprint",
    }:
        raise ValueError("invalid parameter snapshot fields")
    return ParameterSnapshot(
        version=payload["version"],
        effective_from=payload["effective_from"],
        fingerprint=payload["fingerprint"],
    )


def parameter_snapshot_from_runtime_weights(
    payload: object,
) -> ParameterSnapshot | None:
    """Read ledger dictionaries and pandas' legacy dictionary CSV cells safely."""
    if isinstance(payload, str):
        if len(payload) > 65536:
            return None
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, RecursionError):
            try:
                payload = ast.literal_eval(payload)
            except (SyntaxError, ValueError, TypeError, RecursionError):
                return None
    if not isinstance(payload, Mapping):
        return None
    try:
        identity = parse_parameter_snapshot(payload.get("parameter_snapshot"))
        if identity is not None and identity.version != payload.get(
            "thresholds_version"
        ):
            return None
        return identity
    except (TypeError, ValueError):
        return None
