"""Debate helpers extracted from ``cli.py``.

This module houses two groups of functions:

1. **Record I/O** — JSONL persistence for multi-agent debate results:
   reading retained debates, merging updates, writing sorted output,
   and computing stable record keys / candidate fingerprints.
2. **Coordinator / pick wiring** — building the debate coordinator,
   resolving per-pick roles, gating execution, and applying debate
   results back onto picks.

All functions are pure utilities with no dependency on ``cli.py`` internals,
which keeps the extraction acyclic and testable in isolation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from aqsp.briefing.debate import (
    AShareDebateCoordinator,
    DebateResult,
    debate_active_role_summary,
    debate_active_roles,
    parse_agent_roles,
)
from aqsp.goal_switches import goal_switch_enabled
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


# ---------------------------------------------------------------------------
# Coordinator / pick wiring
# ---------------------------------------------------------------------------


def serialize_debate_result(result: DebateResult) -> dict:
    """将辩论结果序列化为可JSON化的字典"""
    return result.to_dict()


def _build_debate_coordinator(
    debate_runtime: Any,
    *,
    thresholds_version: str,
    regime: str,
    data_source: str,
    roles_override: tuple[str, ...] | None = None,
) -> AShareDebateCoordinator:
    """Construct the multi-agent debate coordinator from runtime config."""
    active_roles = parse_agent_roles(roles_override or debate_runtime.roles)
    active_role_names = {role.value for role in active_roles}
    role_runtime = tuple(
        item for item in debate_runtime.role_runtime if item.role in active_role_names
    )
    return AShareDebateCoordinator(
        enable_llm=debate_runtime.enable_llm,
        # 实时盘中讨论必须至少完成一轮反驳，避免只产出单轮观点。
        max_rounds=(
            max(2, debate_runtime.max_rounds)
            if str(regime).strip().lower() == "intraday"
            else debate_runtime.max_rounds
        ),
        thresholds_version=thresholds_version,
        regime=regime,
        data_source=data_source,
        language=debate_runtime.language,
        roles=active_roles,
        role_runtime=role_runtime,
    )


def _resolve_pick_debate_roles(
    debate_runtime: Any,
    *,
    pick: PickResult,
    market_context_lines: tuple[str, ...],
) -> tuple[str, ...]:
    """Return the per-pick debate role list, possibly context-inferred."""
    if getattr(debate_runtime, "context_roles_locked", False):
        return tuple(debate_runtime.roles)

    from aqsp.briefing.agent_roles import infer_context_agent_roles

    return tuple(
        role.value
        for role in infer_context_agent_roles(
            pick,
            base_roles=debate_runtime.roles,
            market_context_lines=market_context_lines,
            disabled_roles=getattr(debate_runtime, "disabled_roles", ()),
        )
    )


def _debate_execution_enabled(args: Any, debate_runtime: Any) -> bool:
    """Gate: multi-agent advisory layer enabled AND user/config opts in."""
    return goal_switch_enabled("multi_agent_advisory_layer", default=True) and (
        getattr(args, "enable_debate", False) or debate_runtime.enabled
    )


def _apply_debate_results_to_picks(
    picks: list[PickResult],
    debate_results: list[DebateResult],
) -> tuple[list[PickResult], int]:
    """Merge debate results onto picks, returning updated picks and rewrite count."""
    debate_by_symbol = {result.symbol: result for result in debate_results}
    if not debate_by_symbol:
        return picks, 0

    rewritten = 0
    updated_picks: list[PickResult] = []
    for pick in picks:
        result = debate_by_symbol.get(pick.symbol)
        if result is None:
            updated_picks.append(pick)
            continue

        metrics = dict(pick.metrics)
        deterministic_baseline = (
            result.deterministic_score
            if result.deterministic_score
            else result.original_score
        )
        metrics["deterministic_score"] = float(pick.score)
        metrics["deterministic_score_unchanged"] = bool(
            result.deterministic_score_unchanged
            and deterministic_baseline == result.original_score == pick.score
        )
        metrics["advisory_only"] = bool(result.advisory_only)
        metrics["debate_id"] = result.debate_id
        metrics["debate_disagreement_score"] = result.disagreement_score
        metrics["debate_final_vote"] = {
            role.value: stance for role, stance in result.final_vote.items()
        }
        metrics["debate_active_roles"] = [
            role.value for role in debate_active_roles(result)
        ]
        active_role_summary = debate_active_role_summary(result)
        if active_role_summary:
            metrics["debate_active_role_summary"] = active_role_summary
        if result.role_selection_summary:
            metrics["debate_role_selection_summary"] = result.role_selection_summary
        if result.role_selection_plan:
            metrics["debate_role_selection_plan"] = result.role_selection_plan
        if result.research_verdict:
            metrics["debate_research_verdict"] = result.research_verdict
        if result.primary_risk_gate:
            metrics["debate_primary_risk_gate"] = result.primary_risk_gate
        if result.next_trigger:
            metrics["debate_next_trigger"] = result.next_trigger
        if result.support_points:
            metrics["support_points"] = list(result.support_points)
        if result.opposition_points:
            metrics["opposition_points"] = list(result.opposition_points)
        if result.watch_items:
            metrics["watch_items"] = list(result.watch_items)
        if result.role_reliability_lines:
            metrics["role_reliability_lines"] = list(result.role_reliability_lines)
        if result.historical_context_note:
            metrics["debate_historical_context_note"] = result.historical_context_note
        if result.historical_context_bucket:
            metrics["debate_historical_context_bucket"] = (
                result.historical_context_bucket
            )
        if result.historical_context_sample_count > 0:
            metrics["debate_historical_context_sample_count"] = (
                result.historical_context_sample_count
            )
            metrics["debate_historical_context_accuracy"] = (
                result.historical_context_accuracy
            )
        elif result.historical_context_accuracy > 0:
            metrics["debate_historical_context_accuracy"] = (
                result.historical_context_accuracy
            )
        if result.cross_market_support_event_count > 0:
            metrics["cross_market_support_event_count"] = (
                result.cross_market_support_event_count
            )
        if result.cross_market_conflict_event_count > 0:
            metrics["cross_market_conflict_event_count"] = (
                result.cross_market_conflict_event_count
            )
        if result.cross_market_evidence_stack_summary:
            metrics["cross_market_evidence_stack_summary"] = (
                result.cross_market_evidence_stack_summary
            )

        pick = replace(
            pick,
            metrics=metrics,
            debate_consensus=result.final_consensus,
        )
        updated_picks.append(pick)
    return updated_picks, rewritten
