#!/usr/bin/env python3
"""Validate the isolated variant_results.json runtime artifact."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date as CalendarDate
from pathlib import Path
from typing import Any


MAX_TOP_HOLDING_DUPLICATES = 1
TOP_DIVERSITY_WINDOW = 20
MIN_UNIQUE_HOLDING_SIGNATURE_RATIO = 0.8
REQUIRED_TECHNICAL_KEYS = ("macd_hist", "kdj_j", "volume_ratio", "atr_pct")
TECHNICAL_REASON_MARKERS = ("MACD", "KDJ", "量比", "ATR")


@dataclass(frozen=True)
class VariantResultsCheck:
    path: str
    schema_version: str
    end_date: str
    variants: int
    selected_symbols: int
    supported_symbols: int


def validate_variant_results(
    path: Path,
    *,
    expected_end: str = "",
    min_variants: int = 100,
    min_symbols: int = 121,
) -> VariantResultsCheck:
    payload = _object(json.loads(path.read_text(encoding="utf-8")), "variant_results")
    return validate_variant_payload(
        payload,
        path=str(path),
        expected_end=expected_end,
        min_variants=min_variants,
        min_symbols=min_symbols,
    )


def validate_variant_payload(
    payload: object,
    *,
    path: str = "",
    expected_end: str = "",
    min_variants: int = 100,
    min_symbols: int = 121,
) -> VariantResultsCheck:
    payload = _object(payload, "variant_results")
    schema_version = _text(payload.get("schema_version"), "schema_version")
    if schema_version != "variant-suite-v2":
        raise ValueError(f"schema_version must be variant-suite-v2: {schema_version}")
    end_date = _text(payload.get("end_date"), "end_date")
    try:
        CalendarDate.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError(f"end_date invalid: {end_date}") from exc
    if expected_end and end_date != expected_end:
        raise ValueError(f"end_date mismatch: {end_date} != {expected_end}")
    if float(payload.get("initial_cash") or 0.0) != 100_000.0:
        raise ValueError("initial_cash must be 100000")
    variants = _list(payload.get("variants"), "variants")
    if len(variants) < min_variants:
        raise ValueError(f"variant count too small: {len(variants)} < {min_variants}")
    universe = _object(payload.get("universe"), "universe")
    selected_symbols = int(universe.get("selected_symbols") or 0)
    supported_symbols = int(universe.get("supported_symbols") or 0)
    if selected_symbols < min_symbols:
        raise ValueError(
            f"selected_symbols too small: {selected_symbols} < {min_symbols}"
        )
    seen_ids: set[str] = set()
    strategy_signatures: set[str] = set()
    holding_signatures: set[str] = set()
    top_holding_signatures: list[str] = []
    for index, value in enumerate(variants):
        item = _object(value, f"variants[{index}]")
        variant_id = _text(item.get("variant_id"), f"variants[{index}].variant_id")
        if variant_id in seen_ids:
            raise ValueError(f"duplicate variant_id: {variant_id}")
        seen_ids.add(variant_id)
        strategy_signatures.add(
            _text(
                item.get("strategy_signature"), f"variants[{index}].strategy_signature"
            )
        )
        holding_signature = _text(
            item.get("holdings_signature"), f"variants[{index}].holdings_signature"
        )
        holding_signatures.add(holding_signature)
        if index < TOP_DIVERSITY_WINDOW:
            top_holding_signatures.append(holding_signature)
        if (
            _text(item.get("holdings_date"), f"variants[{index}].holdings_date")
            != end_date
        ):
            raise ValueError(f"{variant_id} holdings_date mismatch")
        previous_date = _text(
            item.get("previous_holdings_date"),
            f"variants[{index}].previous_holdings_date",
        )
        if not previous_date or previous_date >= end_date:
            raise ValueError(f"{variant_id} previous_holdings_date invalid")
        for key in ("holdings", "previous_holdings", "recent_actions", "adjustments"):
            _list(item.get(key), f"variants[{index}].{key}")
        if not _list(item.get("adjustments"), f"variants[{index}].adjustments"):
            raise ValueError(f"{variant_id} adjustments missing")
        if not _has_holding_change_explanation(item):
            raise ValueError(f"{variant_id} holding change explanation incomplete")
        if not _has_structured_technical_evidence(item, end_date=end_date):
            raise ValueError(f"{variant_id} technical evidence missing")
        if not _has_named_holdings(item, variant_id=variant_id):
            raise ValueError(f"{variant_id} holding name missing")
        if not _has_current_holding_technical_evidence(item, end_date=end_date):
            raise ValueError(f"{variant_id} current holding technical evidence missing")
    if len(strategy_signatures) < min_variants:
        raise ValueError("unique strategy signatures below minimum")
    minimum_unique_holdings = math.ceil(
        len(variants) * MIN_UNIQUE_HOLDING_SIGNATURE_RATIO
    )
    if len(holding_signatures) < minimum_unique_holdings:
        raise ValueError(
            "unique holding signatures below minimum: "
            f"{len(holding_signatures)} < {minimum_unique_holdings}"
        )
    if top_holding_signatures:
        duplicate, count = Counter(top_holding_signatures).most_common(1)[0]
        if count > MAX_TOP_HOLDING_DUPLICATES:
            raise ValueError(
                "top holding signatures too repetitive: "
                f"{duplicate} appears {count} times in top {len(top_holding_signatures)}"
            )
    return VariantResultsCheck(
        path=path,
        schema_version=schema_version,
        end_date=end_date,
        variants=len(variants),
        selected_symbols=selected_symbols,
        supported_symbols=supported_symbols,
    )


def _has_structured_technical_evidence(item: dict[str, Any], *, end_date: str) -> bool:
    evidence_sources: list[Any] = []
    evidence_sources.extend(
        _list(item.get("technical_evidence", []), "technical_evidence")
    )
    evidence_sources.extend(
        action.get("evidence")
        for action in _list(item.get("recent_actions", []), "recent_actions")
        if isinstance(action, dict)
    )
    evidence_sources.extend(
        holding.get("entry_evidence")
        for holding in _list(item.get("holdings", []), "holdings")
        if isinstance(holding, dict)
    )
    for evidence in evidence_sources:
        if not isinstance(evidence, dict):
            continue
        if all(
            _is_finite_number(evidence.get(key)) for key in REQUIRED_TECHNICAL_KEYS
        ) and _evidence_available_by(evidence, end_date):
            return True
    return False


def _has_named_holdings(item: dict[str, Any], *, variant_id: str) -> bool:
    for field in ("holdings", "previous_holdings"):
        for index, holding in enumerate(_list(item.get(field, []), field)):
            value = _object(holding, f"{variant_id}.{field}[{index}]")
            if not _text(value.get("symbol"), f"{variant_id}.{field}[{index}].symbol"):
                return False
            if not _text(value.get("name"), f"{variant_id}.{field}[{index}].name"):
                return False
    return True


def _has_current_holding_technical_evidence(
    item: dict[str, Any], *, end_date: str
) -> bool:
    holdings = _list(item.get("holdings", []), "holdings")
    if not holdings:
        return True
    evidence_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(item.get("technical_evidence", []), "technical_evidence"):
        if not isinstance(raw, dict):
            continue
        symbol = raw.get("symbol")
        if isinstance(symbol, str) and symbol:
            evidence_by_symbol.setdefault(symbol, []).append(raw)
    for holding in holdings:
        if not isinstance(holding, dict):
            return False
        symbol = holding.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            return False
        if not any(
            all(_is_finite_number(evidence.get(key)) for key in REQUIRED_TECHNICAL_KEYS)
            and _evidence_date_is(evidence, end_date)
            for evidence in evidence_by_symbol.get(symbol, [])
        ):
            return False
    return True


def _has_holding_change_explanation(item: dict[str, Any]) -> bool:
    """Require every position change to name the affected stock in the explanation."""
    quantities_by_field: dict[str, dict[str, int]] = {}
    for field in ("holdings", "previous_holdings"):
        quantities: dict[str, int] = {}
        for holding in _list(item.get(field, []), field):
            if not isinstance(holding, dict):
                return False
            symbol = holding.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                return False
            quantities[symbol] = int(holding.get("quantity") or 0)
        quantities_by_field[field] = quantities
    current = quantities_by_field["holdings"]
    previous = quantities_by_field["previous_holdings"]
    changed_symbols = {
        symbol
        for symbol in set(current) | set(previous)
        if current.get(symbol, 0) != previous.get(symbol, 0)
    }
    if not changed_symbols:
        return True
    explanation_lines = tuple(
        value
        for value in _list(item.get("adjustments", []), "adjustments")
        if isinstance(value, str)
    )
    return bool(explanation_lines) and all(
        any(
            symbol in line
            and any(marker in line for marker in TECHNICAL_REASON_MARKERS)
            for line in explanation_lines
        )
        for symbol in changed_symbols
    )


def _evidence_available_by(evidence: dict[str, Any], end_date: str) -> bool:
    value = evidence.get("execution_date") or evidence.get("date")
    if not isinstance(value, str):
        return False
    try:
        return CalendarDate.fromisoformat(value[:10]) <= CalendarDate.fromisoformat(
            end_date
        )
    except ValueError:
        return False


def _evidence_date_is(evidence: dict[str, Any], expected_date: str) -> bool:
    value = evidence.get("execution_date") or evidence.get("date")
    return isinstance(value, str) and value[:10] == expected_date


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _text(value: Any, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    return value.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="check_variant_results")
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-end", default="")
    parser.add_argument("--min-variants", type=int, default=100)
    parser.add_argument("--min-symbols", type=int, default=121)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_variant_results(
        args.path,
        expected_end=args.expected_end,
        min_variants=args.min_variants,
        min_symbols=args.min_symbols,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    else:
        print(
            "PASS variant_results "
            f"schema={result.schema_version} variants={result.variants} "
            f"symbols={result.selected_symbols} end={result.end_date}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
