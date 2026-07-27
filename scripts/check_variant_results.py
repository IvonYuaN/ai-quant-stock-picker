#!/usr/bin/env python3
"""Validate the isolated variant_results.json runtime artifact."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MAX_TOP_HOLDING_DUPLICATES = 1
TOP_DIVERSITY_WINDOW = 20
REQUIRED_TECHNICAL_KEYS = ("macd_hist", "kdj_j", "volume_ratio", "atr_pct")


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
        if not _has_structured_technical_evidence(item):
            raise ValueError(f"{variant_id} technical evidence missing")
    if len(strategy_signatures) < min_variants:
        raise ValueError("unique strategy signatures below minimum")
    if len(holding_signatures) <= 1:
        raise ValueError("holding signatures are not diversified")
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


def _has_structured_technical_evidence(item: dict[str, Any]) -> bool:
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
        if all(_is_finite_number(evidence.get(key)) for key in REQUIRED_TECHNICAL_KEYS):
            return True
    return False


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
