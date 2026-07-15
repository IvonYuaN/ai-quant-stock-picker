"""Typed, read-only runtime snapshot for dashboards and research agents."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from aqsp.briefing.debate_tracker import audit_debate_quality
from aqsp.core.time import now_shanghai


SNAPSHOT_SCHEMA_VERSION = "v1"


class RuntimeSnapshotProvider(Protocol):
    """Minimal provider contract so snapshot construction stays UI-agnostic."""

    def default_task_id(self) -> str: ...

    def home_digest_payload(self, task_id: str, signal_date: str = "") -> Any: ...

    def runtime_overview(self, signal_date: str = "") -> Any: ...


@dataclass(frozen=True)
class RuntimeSnapshotCandidate:
    symbol: str
    display_name: str
    score: float
    rank: str
    research_status: str
    next_step: str
    blocker: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    news_context: str
    cross_market_context: str
    decision_note: str
    debate_round_count: int = 0
    debate_roles: tuple[str, ...] = ()
    debate_status: str = "not_requested"
    candidate_fingerprint: str = ""


@dataclass(frozen=True)
class RuntimeSnapshotDebate:
    symbol: str
    display_name: str
    conclusion: str
    recommended_adjustment: str
    disagreement_score: float
    primary_risk_gate: str
    next_trigger: str
    active_roles: tuple[str, ...]
    support_points: tuple[str, ...]
    opposition_points: tuple[str, ...]
    watch_items: tuple[str, ...]
    rounds: tuple["RuntimeSnapshotRound", ...] = ()
    process_recorded: bool = False
    conclusion_recorded: bool = False
    advisory_only: bool = True
    deterministic_score: float = 0.0
    deterministic_score_unchanged: bool = True
    failure: str = ""
    round_count: int = 0
    final_vote: dict[str, str] | None = None
    quality_issues: tuple[str, ...] = ()
    evidence_sufficient: bool = False
    advisory_boundary_ok: bool = True
    candidate_fingerprint: str = ""


@dataclass(frozen=True)
class RuntimeSnapshotOpinion:
    agent_id: str
    role: str
    stance: str
    confidence: float
    arguments: tuple[str, ...]
    counterarguments: tuple[str, ...]
    counterargument_roles: tuple[str, ...]
    peer_reviewed_roles: tuple[str, ...]
    risk_factors: tuple[str, ...]
    opportunity_factors: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeSnapshotRound:
    round_num: int
    summary: str
    opinions: tuple[RuntimeSnapshotOpinion, ...]


@dataclass(frozen=True)
class RuntimeSnapshotSource:
    requested: str
    effective: str
    health_reason: str
    latest_trade_date: str
    lag_days: str
    market_context: str


@dataclass(frozen=True)
class RuntimeResearchSnapshot:
    schema_version: str
    generated_at: str
    signal_date: str
    task_id: str
    task_label: str
    conclusion: str
    source: RuntimeSnapshotSource
    candidate_counts: dict[str, int]
    candidates: tuple[RuntimeSnapshotCandidate, ...]
    debates: tuple[RuntimeSnapshotDebate, ...]
    guardrails: tuple[str, ...]
    debate_failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-safe shape for agent tool calls."""
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True
        )


def build_runtime_research_snapshot(
    provider: RuntimeSnapshotProvider,
    *,
    signal_date: str = "",
    task_id: str = "",
) -> RuntimeResearchSnapshot:
    """Build one bounded research view without reading files or calling networks here."""
    selected_task_id = task_id.strip() or provider.default_task_id()
    payload = provider.home_digest_payload(selected_task_id, signal_date=signal_date)
    task_view = _get(payload, "task_view", None)
    resolved_date = str(
        _get(task_view, "selected_date", "")
        or _get(task_view, "latest_date", "")
        or signal_date
    ).strip()
    runtime = provider.runtime_overview(resolved_date)
    overview = _get(payload, "overview", None)

    debates = _snapshot_debates(_get(payload, "debates", ()))
    debate_failures = tuple(
        dict.fromkeys(
            (
                *_text_tuple(_get(payload, "debate_failed_symbols", ())),
                *(
                    f"{item.symbol}({item.failure})"
                    for item in debates
                    if item.symbol and item.failure
                ),
            )
        )
    )
    return RuntimeResearchSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        generated_at=now_shanghai().isoformat(),
        signal_date=resolved_date,
        task_id=str(_get(task_view, "task_id", "") or selected_task_id).strip(),
        task_label=str(_get(task_view, "task_label", "") or "").strip(),
        conclusion=str(_get(runtime, "conclusion", "") or "").strip(),
        source=RuntimeSnapshotSource(
            requested=str(_get(runtime, "requested_source", "") or "").strip(),
            effective=str(_get(runtime, "effective_source", "") or "").strip(),
            health_reason=str(_get(runtime, "source_reason", "") or "").strip(),
            latest_trade_date=str(
                _get(runtime, "data_latest_trade_date", "") or ""
            ).strip(),
            lag_days=str(_get(runtime, "lag_days", "") or "").strip(),
            market_context=str(
                _get(runtime, "market_context_runtime_line", "") or ""
            ).strip(),
        ),
        candidate_counts={
            "actionable": int(_get(overview, "actionable_total", 0) or 0),
            "watch": int(_get(overview, "watch_total", 0) or 0),
            "blocked": int(_get(overview, "blocked_total", 0) or 0),
        },
        candidates=_snapshot_candidates(payload, debates),
        debates=debates,
        guardrails=_snapshot_guardrails(runtime),
        debate_failures=debate_failures,
    )


def _snapshot_candidates(
    payload: Any,
    debates: tuple[RuntimeSnapshotDebate, ...] = (),
) -> tuple[RuntimeSnapshotCandidate, ...]:
    task_view = _get(payload, "task_view", None)
    cards = list(_get(task_view, "detail_cards", ()) or ())
    known_symbols = {_candidate_symbol(card) for card in cards}
    cards.extend(
        spotlight
        for spotlight in (_get(payload, "spotlights", ()) or ())
        if _candidate_symbol(spotlight) not in known_symbols
    )
    debate_by_key = {
        (item.symbol, item.candidate_fingerprint): item
        for item in debates
        if item.symbol and item.candidate_fingerprint
    }
    debates_by_symbol = {}
    for item in debates:
        if item.symbol:
            debates_by_symbol.setdefault(item.symbol, []).append(item)
    return tuple(
        _snapshot_candidate(
            card,
            _debate_for_candidate(
                card,
                debate_by_key=debate_by_key,
                debates_by_symbol=debates_by_symbol,
            ),
        )
        for card in cards
        if _candidate_symbol(card)
    )


def _snapshot_candidate(
    candidate: Any,
    debate: RuntimeSnapshotDebate | None = None,
) -> RuntimeSnapshotCandidate:
    return RuntimeSnapshotCandidate(
        symbol=_candidate_symbol(candidate),
        display_name=str(_get(candidate, "display_name", "") or "").strip(),
        score=float(_get(candidate, "score", 0.0) or 0.0),
        rank=str(_get(candidate, "rank_label", "") or "").strip(),
        research_status=_first_nonempty(
            _get(candidate, "action_label", ""),
            _get(candidate, "status_label", ""),
        ),
        next_step=str(_get(candidate, "next_step", "") or "").strip(),
        blocker=str(_get(candidate, "blocker", "") or "").strip(),
        reasons=_text_tuple(_get(candidate, "reasons", ())),
        risks=_text_tuple(_get(candidate, "risks", ())),
        news_context=str(_get(candidate, "news_catalyst_summary", "") or "").strip(),
        cross_market_context=str(
            _get(candidate, "cross_market_summary", "") or ""
        ).strip(),
        decision_note=str(_get(candidate, "decision_note", "") or "").strip(),
        debate_round_count=(
            0
            if debate is None
            else max(
                len(debate.rounds),
                int(getattr(debate, "round_count", 0) or 0),
            )
        ),
        debate_roles=() if debate is None else debate.active_roles,
        debate_status=(
            "not_requested"
            if debate is None
            else (
                "failed"
                if debate.failure
                else ("recorded" if debate.process_recorded else "incomplete")
            )
        ),
        candidate_fingerprint=_candidate_fingerprint(candidate),
    )


def _snapshot_debates(debates: Any) -> tuple[RuntimeSnapshotDebate, ...]:
    snapshots: list[RuntimeSnapshotDebate] = []
    for item in debates or ():
        rounds = _snapshot_rounds(_get(item, "rounds", ()))
        active_roles = _snapshot_active_roles(item)
        final_vote = _snapshot_final_vote(_get(item, "final_vote", {}))
        normalized = RuntimeSnapshotDebate(
            symbol=str(_get(item, "symbol", "") or "").strip(),
            display_name=str(
                _first_nonempty(
                    _get(item, "display_name", ""),
                    _get(item, "name", ""),
                )
            ).strip(),
            conclusion=_first_nonempty(
                _get(item, "research_verdict", ""),
                _get(item, "consensus", ""),
            ),
            recommended_adjustment=str(
                _get(item, "recommended_adjustment", "") or ""
            ).strip(),
            disagreement_score=float(_get(item, "disagreement_score", 0.0) or 0.0),
            primary_risk_gate=str(_get(item, "primary_risk_gate", "") or "").strip(),
            next_trigger=str(_get(item, "next_trigger", "") or "").strip(),
            active_roles=active_roles,
            support_points=_text_tuple(_get(item, "support_points", ())),
            opposition_points=_text_tuple(_get(item, "opposition_points", ())),
            watch_items=_text_tuple(_get(item, "watch_items", ())),
            rounds=rounds,
            process_recorded=False,
            conclusion_recorded=False,
            advisory_only=_strict_bool(_get(item, "advisory_only", True), True),
            deterministic_score=float(
                _get(item, "deterministic_score", _get(item, "original_score", 0.0))
                or 0.0
            ),
            deterministic_score_unchanged=_strict_bool(
                _get(item, "deterministic_score_unchanged", True), True
            ),
            failure=str(_get(item, "failure", "") or "").strip(),
            round_count=max(len(rounds), int(_get(item, "round_count", 0) or 0)),
            final_vote=final_vote,
            candidate_fingerprint=str(
                _get(item, "candidate_fingerprint", "")
                or _get(item, "debate_candidate_fingerprint", "")
                or ""
            ).strip(),
        )
        audit = audit_debate_quality(
            normalized,
            expected_roles=active_roles,
        )
        failure = normalized.failure
        if not audit.passed:
            failure = failure or "讨论链路未通过审计: " + "、".join(audit.issues)
        conclusion = normalized.conclusion
        if not audit.passed:
            conclusion = _snapshot_quality_failure(audit.issues)
        snapshots.append(
            RuntimeSnapshotDebate(
                **{
                    **asdict(normalized),
                    "conclusion": conclusion,
                    "process_recorded": audit.process_recorded,
                    "conclusion_recorded": audit.conclusion_recorded,
                    "failure": failure,
                    "quality_issues": audit.issues,
                    "evidence_sufficient": audit.evidence_sufficient,
                    "advisory_boundary_ok": audit.advisory_boundary_ok,
                }
            )
        )
    return tuple(snapshots)


def _snapshot_final_vote(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, str] = {}
    for role, stance in value.items():
        role_text = str(getattr(role, "value", role) or "").strip()
        stance_text = str(stance or "").strip()
        if role_text and stance_text:
            result[role_text] = stance_text
    return result or None


def _strict_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _snapshot_rounds(rounds: Any) -> tuple[RuntimeSnapshotRound, ...]:
    result: list[RuntimeSnapshotRound] = []
    for item in rounds or ():
        opinions = tuple(
            RuntimeSnapshotOpinion(
                agent_id=str(_get(opinion, "agent_id", "") or "").strip(),
                role=str(_get(opinion, "role", "") or "").strip(),
                stance=str(_get(opinion, "stance", "") or "").strip(),
                confidence=float(_get(opinion, "confidence", 0.0) or 0.0),
                arguments=_text_tuple(_get(opinion, "arguments", ())),
                counterarguments=_text_tuple(_get(opinion, "counterarguments", ())),
                counterargument_roles=_text_tuple(
                    _get(opinion, "counterargument_roles", ())
                ),
                peer_reviewed_roles=_text_tuple(
                    _get(opinion, "peer_reviewed_roles", ())
                ),
                risk_factors=_text_tuple(_get(opinion, "risk_factors", ())),
                opportunity_factors=_text_tuple(
                    _get(opinion, "opportunity_factors", ())
                ),
            )
            for opinion in (_get(item, "opinions", ()) or ())
        )
        result.append(
            RuntimeSnapshotRound(
                round_num=int(_get(item, "round_num", 0) or 0),
                summary=str(_get(item, "summary", "") or "").strip(),
                opinions=opinions,
            )
        )
    return tuple(result)


def _snapshot_active_roles(item: Any) -> tuple[str, ...]:
    roles: list[str] = []
    for value in _get(item, "active_roles", ()) or ():
        role = _role_text(value)
        if role and role not in roles:
            roles.append(role)
    for view in _get(item, "agent_views", ()) or ():
        role = _role_text(_get(view, "role_id", _get(view, "role", "")))
        if role and role not in roles:
            roles.append(role)
    for round_data in _get(item, "rounds", ()) or ():
        for opinion in _get(round_data, "opinions", ()) or ():
            role = _role_text(_get(opinion, "role", ""))
            if role and role not in roles:
                roles.append(role)
    return tuple(roles)


def _snapshot_quality_failure(issues: tuple[str, ...]) -> str:
    if "no_substantive_evidence" in issues:
        return "结论已阻断：缺少可核验证据"
    if "non_interactive_round" in issues:
        return "结论已阻断：多轮讨论未形成有效交锋"
    if "advisory_boundary_violation" in issues:
        return "结论已阻断：越过 advisory-only 边界"
    return "结论已阻断：讨论链路不完整"


def _role_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _snapshot_guardrails(runtime: Any) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            "研究辅助，不自动下单；委员会结论不改写确定性评分。",
            (
                f"数据源说明: {_get(runtime, 'source_reason', '')}"
                if str(_get(runtime, "source_reason", "") or "").strip()
                else ""
            ),
            (
                f"风险卡点: {_get(runtime, 'risk_reason', '')}"
                if str(_get(runtime, "risk_reason", "") or "").strip()
                else ""
            ),
        )
        if line
    )


def _candidate_symbol(candidate: Any) -> str:
    return str(_get(candidate, "symbol", "") or "").strip()


def _candidate_fingerprint(candidate: Any) -> str:
    metrics = _get(candidate, "metrics", {})
    metrics = metrics if isinstance(metrics, Mapping) else {}
    return str(
        _get(candidate, "candidate_fingerprint", "")
        or _get(candidate, "debate_candidate_fingerprint", "")
        or metrics.get("candidate_fingerprint", "")
        or metrics.get("debate_candidate_fingerprint", "")
        or ""
    ).strip()


def _debate_for_candidate(
    candidate: Any,
    *,
    debate_by_key: dict[tuple[str, str], RuntimeSnapshotDebate],
    debates_by_symbol: dict[str, list[RuntimeSnapshotDebate]],
) -> RuntimeSnapshotDebate | None:
    symbol = _candidate_symbol(candidate)
    fingerprint = _candidate_fingerprint(candidate)
    if fingerprint:
        return debate_by_key.get((symbol, fingerprint))
    matches = debates_by_symbol.get(symbol, [])
    return matches[0] if len(matches) == 1 else None


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _first_nonempty(*values: object) -> str:
    return next(
        (str(value).strip() for value in values if str(value or "").strip()), ""
    )


def _text_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values.strip(),) if values.strip() else ()
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value).strip() for value in values if str(value or "").strip())
