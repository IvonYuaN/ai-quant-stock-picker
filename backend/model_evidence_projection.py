"""Pure, lossless references for duplicate facts in model-generation input only."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


def _same_fact(left: Any, right: Any) -> bool:
    """Keep source types as well as values; equality must not merge bool and int."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_fact(value, right[key]) for key, value in left.items())
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same_fact(a, b) for a, b in zip(left, right))
    return bool(left == right)


def model_evidence_projection(evidence: dict[str, Any], prices: dict[str, Any]) -> dict[str, Any]:
    """Reference exact repeated facts without changing the authoritative evidence.

    Only use this copy when generating model requests, never for validation,
    adoption, persistence or audit. ``evidence_ref`` resolves to the named
    top-level evidence; ``price_ref`` resolves to the supplied ``price_refs``.
    Source values, timestamps and unknown fields remain untouched. Historical
    namespaces are not traversed, even when their values equal current facts.
    """
    projected = deepcopy(evidence)
    route = projected.get("route_constraints")
    nested = route.get("evidence") if isinstance(route, dict) else None
    if isinstance(nested, dict):
        # These are the current direction namespaces in PositionRouteReview.
        # A future or unknown namespace must retain its original contract.
        for key in ("market", "sector", "stock"):
            fact = evidence.get(key)
            if isinstance(fact, dict) and fact and key in nested and _same_fact(nested[key], fact):
                nested[key] = {"evidence_ref": key}
    for key, fact in evidence.items():
        if (re.fullmatch(r"price_[0-9]+", key) and isinstance(fact, dict) and fact
                and key in prices and _same_fact(fact, prices[key])):
            projected[key] = {"price_ref": key}
    return projected
