"""Read-only bridge from AQSP runtime snapshots to the AQSP research API."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date as CalendarDate
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aqsp.core.time import now_shanghai


DEFAULT_SNAPSHOT_PATH = "data/runtime/home_dashboard_snapshot.json"
DEFAULT_DEBATE_RESULTS_PATH = "data/debate_results.jsonl"
SNAPSHOT_SCHEMA_VERSION = "v1"
INDEX_SCHEMA_VERSION = "v1-index"
MAX_SNAPSHOT_BYTES = 64 * 1024
MAX_DATES = 4
MAX_CANDIDATES = 5
MAX_DEBATES = 3
MAX_SUMMARIES = 3
MAX_MESSAGES = 5
MAX_DEBATE_RESULTS_BYTES = 8 * 1024 * 1024
MAX_CROSS_MARKET = 3
LEGACY_MESSAGE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AQSPBridgeError(Exception):
    """Base error for a request that cannot be served from the snapshot."""


class AQSPSnapshotUnavailable(AQSPBridgeError):
    """The configured snapshot is absent, malformed, or unsafe to use."""


class AQSPInvalidRequest(AQSPBridgeError):
    """A bridge query parameter is invalid."""


class AQSPDateNotFound(AQSPBridgeError):
    """The requested date is not present in the snapshot surface."""


class AQSPCandidateNotFound(AQSPBridgeError):
    """The requested candidate is not present for the exact date."""


class AQSPSnapshotStale(AQSPBridgeError):
    """The current snapshot passed its explicit freshness deadline."""


@dataclass(frozen=True)
class AQSPTechnicalMetric:
    key: str
    label: str
    value: str


@dataclass(frozen=True)
class AQSPCandidate:
    symbol: str
    display_name: str
    score: float
    research_status: str
    next_step: str
    context: str
    deterministic_reasons: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()
    evidence_status: str = "证据不足"
    score_breakdown: tuple[str, ...] = ()
    technical_metrics: tuple[AQSPTechnicalMetric, ...] = ()
    data_source: str = ""
    data_fetched_at: str = ""
    data_timestamp_source: str = ""
    freshness: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AQSPDebateEvidence:
    """One sourced evidence item attached to an advisory debate."""

    kind: str
    text: str


@dataclass(frozen=True)
class AQSPDebate:
    symbol: str
    display_name: str
    conclusion: str
    primary_risk_gate: str
    next_trigger: str
    active_roles: tuple[str, ...]
    round_count: int = 0
    bull_count: int = 0
    bear_count: int = 0
    neutral_count: int = 0
    process_summary: str = ""
    round_summaries: tuple[str, ...] = ()
    support_points: tuple[str, ...] = ()
    opposition_points: tuple[str, ...] = ()
    risk_warnings: tuple[str, ...] = ()
    watch_items: tuple[str, ...] = ()
    real_message_evidence: tuple[str, ...] = ()
    cross_market_evidence: tuple[str, ...] = ()
    rule_transmission_evidence: tuple[str, ...] = ()
    pending_confirmations: tuple[str, ...] = ()
    advisory_only: bool = True
    deterministic_score: float | None = None
    deterministic_score_unchanged: bool = True
    advisory_boundary_ok: bool = True
    process_recorded: bool = False
    conclusion_recorded: bool = False
    quality_issues: tuple[str, ...] = ()

    @property
    def evidence(self) -> tuple[AQSPDebateEvidence, ...]:
        """Return only evidence actually present in the source snapshot."""
        buckets = (
            ("message", self.real_message_evidence),
            ("cross_market", self.cross_market_evidence),
            ("transmission", self.rule_transmission_evidence),
        )
        return tuple(
            AQSPDebateEvidence(kind=kind, text=text)
            for kind, values in buckets
            for text in values
            if text.strip()
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [asdict(item) for item in self.evidence]
        return payload


@dataclass(frozen=True)
class AQSPMessage:
    title: str
    summary: str
    impact: str
    category: str
    source: str
    published_at: str
    url: str = ""
    source_region: str = "mixed"
    source_quality: str = ""
    event_type: str = ""
    affected_sectors: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    transmission_hypothesis: str = ""
    supporting_evidence: tuple[str, ...] = ()
    source_url: str = ""
    verification: str = ""
    transmission_path: tuple[str, ...] = ()
    validation_signals: tuple[str, ...] = ()
    invalidation_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class AQSPSource:
    effective: str
    latest_trade_date: str
    lag_days: int
    status: str


@dataclass(frozen=True)
class AQSPColdstart:
    status: str
    detail: str


@dataclass(frozen=True)
class AQSPRecommendationGate:
    recommendation_allowed: bool
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AQSPCrossMarket:
    rule_id: str
    theme: str
    strength: str
    action: str
    source_title: str
    source_region: str
    source_published_at: str
    affected_sectors: tuple[str, ...]
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AQSPMarketContext:
    status: str
    overview: str
    summary_lines: tuple[str, ...]
    cross_market: tuple[AQSPCrossMarket, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AQSPSnapshot:
    schema_version: str
    generated_at: str
    selected_date: str
    available_dates: tuple[str, ...]
    candidates: tuple[AQSPCandidate, ...]
    debates: tuple[AQSPDebate, ...]
    summaries: tuple[str, ...]
    source: AQSPSource
    coldstart: AQSPColdstart
    stale_after: str = ""
    message_status: str = "未产出"
    messages: tuple[AQSPMessage, ...] = ()
    market_context: AQSPMarketContext | None = None
    recommendation_gate: AQSPRecommendationGate | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_stale(self) -> bool:
        if not self.stale_after:
            return True
        deadline = _timestamp(self.stale_after, "stale_after")
        current_time = now_shanghai()
        return current_time >= deadline.astimezone(current_time.tzinfo)


@dataclass(frozen=True)
class AQSPResearchSurface:
    """Validated date-addressable snapshots loaded from one file surface."""

    source_path: Path
    current: AQSPSnapshot
    dated_snapshots: tuple[AQSPSnapshot, ...]

    @property
    def available_dates(self) -> tuple[str, ...]:
        if self.dated_snapshots:
            return tuple(item.selected_date for item in self.dated_snapshots)
        return (self.current.selected_date,)

    def snapshot_for_date(self, selected_date: str | None) -> AQSPSnapshot:
        requested = (
            self.current.selected_date if selected_date is None else selected_date
        )
        if not requested:
            raise AQSPInvalidRequest("date 不能为空")
        _validate_date(requested, "date", request=True)
        if self.dated_snapshots:
            for snapshot in self.dated_snapshots:
                if snapshot.selected_date == requested:
                    return snapshot
        elif self.current.selected_date == requested:
            return self.current
        raise AQSPDateNotFound(f"历史日期 {requested} 不存在，未替换为最新日期")


def snapshot_path() -> Path:
    """Resolve only the configured snapshot path or the repository default."""
    raw_path = os.environ.get("AQSP_RESEARCH_SURFACE_SNAPSHOT", "").strip()
    raw_path = raw_path or DEFAULT_SNAPSHOT_PATH
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def load_surface() -> AQSPResearchSurface:
    """Load and validate the read-only snapshot surface without network or state."""
    path = snapshot_path()
    payload = _read_json(path)
    schema_version = (
        payload.get("schema_version") if isinstance(payload, dict) else None
    )
    if schema_version == INDEX_SCHEMA_VERSION:
        snapshots, selected_date = _parse_index(payload)
        snapshots = _attach_runtime_debates(snapshots)
        current = _select_current(snapshots, selected_date)
        return AQSPResearchSurface(path, current, snapshots)
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise AQSPSnapshotUnavailable("schema_version 不支持或缺失")

    current = _attach_runtime_debates((_parse_snapshot(payload),))[0]
    index_path = path.with_name("home_dashboard_snapshot_index.json")
    if index_path != path and index_path.is_file():
        index_payload = _read_json(index_path)
        if index_payload.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise AQSPSnapshotUnavailable("日期索引 schema_version 不支持或缺失")
        snapshots, selected_date = _parse_index(index_payload)
        snapshots = _attach_runtime_debates(snapshots)
        indexed_current = _select_current(snapshots, selected_date)
        if indexed_current.selected_date != current.selected_date:
            raise AQSPSnapshotUnavailable("当前快照与日期索引不一致，拒绝回退旧日期")
        return AQSPResearchSurface(
            path,
            indexed_current,
            snapshots,
        )
    return AQSPResearchSurface(path, current, ())


def _debate_results_path() -> Path:
    raw_path = os.environ.get("AQSP_DEBATE_RESULTS", "").strip()
    raw_path = raw_path or DEFAULT_DEBATE_RESULTS_PATH
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _attach_runtime_debates(
    snapshots: tuple[AQSPSnapshot, ...],
) -> tuple[AQSPSnapshot, ...]:
    """Attach date-matched advisory debates without changing candidate scores."""
    records = _read_runtime_debate_records()
    if not records:
        return snapshots
    return tuple(
        _attach_debates(snapshot, records.get(snapshot.selected_date, ()))
        for snapshot in snapshots
    )


def _read_runtime_debate_records() -> dict[str, tuple[dict[str, Any], ...]]:
    """Read optional local debate output; malformed lines are ignored."""
    path = _debate_results_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if len(raw) > MAX_DEBATE_RESULTS_BYTES:
        return {}
    by_date: dict[str, list[dict[str, Any]]] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        match_date = _debate_record_date(item)
        if match_date:
            by_date.setdefault(match_date, []).append(item)
    return {key: tuple(value) for key, value in by_date.items()}


def _debate_record_date(item: Mapping[str, Any]) -> str:
    """Use the candidate's signal date; debate date is legacy fallback only."""
    for key in ("candidate_signal_date", "related_signal_date", "debate_date"):
        value = str(item.get(key, "") or "").strip()
        if value:
            try:
                return _validate_date(value, f"debate.{key}")
            except AQSPBridgeError:
                return ""
    return ""


def _attach_debates(
    snapshot: AQSPSnapshot,
    records: tuple[dict[str, Any], ...],
) -> AQSPSnapshot:
    if snapshot.debates or not snapshot.candidates:
        return snapshot
    candidates_by_symbol = {item.symbol: item for item in snapshot.candidates}
    debates: list[AQSPDebate] = []
    seen: set[str] = set()
    for record in reversed(records):
        symbol = str(record.get("symbol", "") or "").strip()
        if symbol in seen or symbol not in candidates_by_symbol:
            continue
        if not _runtime_debate_is_complete(record):
            continue
        payload = _runtime_debate_payload(record, candidates_by_symbol[symbol])
        try:
            debate = _parse_debate(payload)
            _validate_advisory_boundary([payload], snapshot.candidates)
        except (AQSPBridgeError, TypeError, ValueError):
            continue
        debates.append(debate)
        seen.add(symbol)
        if len(debates) >= MAX_DEBATES:
            break
    return replace(snapshot, debates=tuple(reversed(debates))) if debates else snapshot


def _runtime_debate_is_complete(record: Mapping[str, Any]) -> bool:
    """Keep incomplete committee attempts out of the normal discussion lane."""
    if record.get("process_recorded") is False:
        return False
    if record.get("conclusion_recorded") is False:
        return False
    if record.get("evidence_sufficient") is False:
        return False
    quality_issues = record.get("debate_quality_issues", [])
    return not (isinstance(quality_issues, list) and quality_issues)


def _runtime_debate_payload(
    record: Mapping[str, Any], candidate: AQSPCandidate
) -> dict[str, Any]:
    rounds = record.get("rounds")
    round_summaries = [
        str(item.get("summary", "") or "").strip()
        for item in rounds
        if isinstance(item, dict) and str(item.get("summary", "") or "").strip()
    ] if isinstance(rounds, list) else []
    return {
        "symbol": candidate.symbol,
        "display_name": candidate.display_name,
        "conclusion": str(
            record.get("research_verdict") or record.get("final_consensus") or ""
        ).strip(),
        "primary_risk_gate": str(record.get("primary_risk_gate", "") or "").strip(),
        "next_trigger": str(record.get("next_trigger", "") or "").strip(),
        "active_roles": record.get("active_roles", []),
        "round_count": record.get("debate_rounds_completed", len(round_summaries)) or 0,
        "process_summary": str(record.get("role_selection_summary", "") or "").strip(),
        "round_summaries": round_summaries,
        "support_points": record.get("support_points", []),
        "opposition_points": record.get("opposition_points", []),
        "risk_warnings": record.get("risk_warnings", []),
        "watch_items": record.get("watch_items", []),
        "real_message_evidence": record.get("real_message_evidence", []),
        "cross_market_evidence": record.get("cross_market_evidence", []),
        "rule_transmission_evidence": record.get("rule_transmission_evidence", []),
        "pending_confirmations": record.get("pending_confirmations", []),
        "advisory_only": record.get("advisory_only", True),
        "deterministic_score": record.get("deterministic_score"),
        "deterministic_score_unchanged": record.get(
            "deterministic_score_unchanged", True
        ),
        "advisory_boundary_ok": record.get("advisory_boundary_ok", True),
        "process_recorded": record.get("process_recorded", False),
        "conclusion_recorded": record.get("conclusion_recorded", False),
        "debate_quality_issues": record.get("debate_quality_issues", []),
    }


def snapshot_payload(selected_date: str | None = None) -> dict[str, Any]:
    """Return one exact-date snapshot as a JSON-safe typed payload."""
    surface = load_surface()
    snapshot = surface.snapshot_for_date(selected_date)
    _require_current_snapshot_fresh(surface, selected_date)
    return _snapshot_payload(
        snapshot,
        historical=_is_historical(surface, selected_date),
    )


def snapshot_response(selected_date: str | None = None) -> dict[str, Any]:
    """Return snapshot data plus freshness metadata for the HTTP boundary."""
    surface = load_surface()
    snapshot = surface.snapshot_for_date(selected_date)
    historical = (
        selected_date is not None and selected_date != surface.current.selected_date
    )
    stale = snapshot.is_stale()
    _require_current_snapshot_fresh(surface, selected_date, stale=stale)
    return {
        "data": _snapshot_payload(snapshot, historical=historical),
        "meta": {
            "historical": historical,
            "stale": stale,
            "freshness": _snapshot_component_freshness(snapshot, historical=historical),
        },
    }


def _is_historical(surface: AQSPResearchSurface, selected_date: str | None) -> bool:
    return selected_date is not None and selected_date != surface.current.selected_date


def _snapshot_payload(snapshot: AQSPSnapshot, *, historical: bool) -> dict[str, Any]:
    """Prevent archive data from being presented as current realtime data."""
    payload = snapshot.to_dict()
    for raw_debate, debate in zip(payload.get("debates", ()), snapshot.debates):
        if isinstance(raw_debate, dict):
            raw_debate["evidence"] = [asdict(item) for item in debate.evidence]
    if not historical:
        return payload

    source = payload.get("source")
    if isinstance(source, dict) and source.get("status"):
        source["status"] = "historical"
    for candidate in payload.get("candidates", ()):
        if isinstance(candidate, dict) and candidate.get("freshness"):
            candidate["freshness"] = "historical"
    if payload.get("messages") and payload.get("message_status") in {
        "ok",
        "部分可用",
    }:
        payload["message_status"] = "历史记录"
    market_context = payload.get("market_context")
    if isinstance(market_context, dict):
        market_context["overview"] = str(
            market_context.get("overview", "") or ""
        ).replace("实时跨市", "历史跨市")
        market_context["summary_lines"] = [
            str(line or "").replace("实时跨市", "历史跨市")
            for line in market_context.get("summary_lines", ())
        ]
    return payload


def _snapshot_component_freshness(
    snapshot: AQSPSnapshot, *, historical: bool = False
) -> dict[str, str]:
    """Expose component freshness without hiding usable realtime candidates."""
    candidate_states = {
        item.freshness.strip().lower()
        for item in snapshot.candidates
        if item.freshness.strip()
    }
    if historical and candidate_states:
        candidates = "historical"
    elif candidate_states == {"fresh"}:
        candidates = "fresh"
    elif candidate_states:
        candidates = "degraded"
    else:
        candidates = "unavailable"

    if (
        historical
        and snapshot.messages
        and snapshot.message_status
        in {
            "ok",
            "部分可用",
        }
    ):
        messages = "historical"
    elif snapshot.messages and snapshot.message_status in {"ok", "部分可用"}:
        messages = "partial" if snapshot.message_status == "部分可用" else "fresh"
    elif snapshot.message_status in {"来源失败", "timeout", "failed"}:
        messages = "unavailable"
    else:
        messages = "no_data"

    summary = (
        "；".join(snapshot.market_context.summary_lines)
        if snapshot.market_context
        else ""
    )
    if historical and (
        "实时跨市: stale" in summary.lower() or "实时跨市: fresh" in summary.lower()
    ):
        cross_market = "historical"
    elif "实时跨市: stale" in summary.lower():
        cross_market = "stale"
    elif "实时跨市: fresh" in summary.lower():
        cross_market = "fresh"
    else:
        cross_market = "unavailable"
    return {
        "candidates": candidates,
        "messages": messages,
        "cross_market": cross_market,
    }


def dates_payload() -> dict[str, Any]:
    """Return the dates actually exposed by the loaded snapshot surface."""
    surface = load_surface()
    _require_current_snapshot_fresh(surface, None)
    return {
        "selected_date": surface.current.selected_date,
        "available_dates": list(surface.available_dates),
    }


def candidate_payload(symbol: str, selected_date: str | None = None) -> dict[str, Any]:
    """Return one candidate and its advisory-only debate for an exact date."""
    normalized = _validate_symbol(symbol, request=True)
    surface = load_surface()
    snapshot = surface.snapshot_for_date(selected_date)
    _require_current_snapshot_fresh(surface, selected_date)
    candidate = next(
        (item for item in snapshot.candidates if item.symbol == normalized), None
    )
    if candidate is None:
        raise AQSPCandidateNotFound(
            f"候选 {normalized} 在 {snapshot.selected_date} 不存在"
        )
    debate = next(
        (item for item in snapshot.debates if item.symbol == normalized), None
    )
    payload = candidate.to_dict()
    if _is_historical(surface, selected_date) and payload.get("freshness"):
        payload["freshness"] = "historical"
    payload["date"] = snapshot.selected_date
    payload["debate"] = debate.to_dict() if debate is not None else None
    return payload


def _require_current_snapshot_fresh(
    surface: AQSPResearchSurface,
    selected_date: str | None,
    *,
    stale: bool | None = None,
) -> None:
    """Block current-data reads while allowing explicitly requested archives."""
    is_current = selected_date is None or selected_date == surface.current.selected_date
    if not is_current:
        return
    if not surface.current.stale_after:
        raise AQSPSnapshotStale("当前 AQSP 研究快照缺少 stale_after")
    is_stale = surface.current.is_stale() if stale is None else stale
    if is_stale:
        raise AQSPSnapshotStale("当前 AQSP 研究快照已过期")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AQSPSnapshotUnavailable(f"无法读取快照文件：{path}") from exc
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise AQSPSnapshotUnavailable("快照超过 64 KiB 大小上限")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AQSPSnapshotUnavailable("快照不是合法 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise AQSPSnapshotUnavailable("快照顶层必须是 JSON object")
    return payload


def _parse_index(payload: Mapping[str, Any]) -> tuple[tuple[AQSPSnapshot, ...], str]:
    _check_keys(
        payload,
        {"schema_version", "generated_at", "stale_after", "selected_date", "days"},
        "日期索引",
    )
    _timestamp(
        _text(payload["generated_at"], "index.generated_at"), "index.generated_at"
    )
    _timestamp(_text(payload["stale_after"], "index.stale_after"), "index.stale_after")
    days = _list(payload["days"], "index.days")
    if not days:
        raise AQSPSnapshotUnavailable("日期索引 days 不能为空")
    _check_limit(days, MAX_DATES, "index.days")
    snapshots: list[AQSPSnapshot] = []
    seen: set[str] = set()
    for raw_day in days:
        day = _object(raw_day, "index.day")
        _check_keys(day, {"date", "snapshot"}, "日期索引 day")
        day_date = _validate_date(_text(day["date"], "day.date"), "day.date")
        snapshot = _parse_snapshot(_object(day["snapshot"], "day.snapshot"))
        if snapshot.selected_date != day_date or day_date in seen:
            raise AQSPSnapshotUnavailable("日期索引包含不一致或重复日期")
        seen.add(day_date)
        snapshots.append(snapshot)
    selected_date = _text(payload["selected_date"], "index.selected_date")
    if not selected_date or selected_date not in seen:
        raise AQSPSnapshotUnavailable("index.selected_date 不存在于 days")
    return tuple(snapshots), selected_date


def _parse_snapshot(payload: Mapping[str, Any]) -> AQSPSnapshot:
    required = {
        "schema_version",
        "generated_at",
        "selected_date",
        "available_dates",
        "candidates",
        "summaries",
        "source",
        "coldstart",
    }
    optional = {
        "debate",
        "debates",
        "stale_after",
        "message_status",
        "messages",
        "market_context",
        "recommendation_gate",
    }
    _check_keys(payload, required, "快照", optional)
    schema_version = _text(payload["schema_version"], "schema_version")
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise AQSPSnapshotUnavailable("快照 schema_version 不支持")
    generated_at = _text(payload["generated_at"], "generated_at")
    _timestamp(generated_at, "generated_at")
    selected_date = _validate_date(
        _text(payload["selected_date"], "selected_date"), "selected_date"
    )
    available_dates = tuple(
        _validate_date(value, "available_dates")
        for value in _text_list(payload["available_dates"], "available_dates")
    )
    _check_limit(available_dates, MAX_DATES, "available_dates")
    if selected_date not in available_dates or len(set(available_dates)) != len(
        available_dates
    ):
        raise AQSPSnapshotUnavailable(
            "selected_date 必须存在且 available_dates 不得重复"
        )
    raw_debates = payload.get("debates")
    if raw_debates is None and payload.get("debate") is not None:
        raw_debates = [payload["debate"]]
    debates = tuple(_parse_debate(item) for item in _list(raw_debates or [], "debates"))
    candidates = tuple(
        _parse_candidate(item) for item in _list(payload["candidates"], "candidates")
    )
    _check_limit(candidates, MAX_CANDIDATES, "candidates")
    _check_limit(debates, MAX_DEBATES, "debates")
    summaries = tuple(_text_list(payload["summaries"], "summaries"))
    _check_limit(summaries, MAX_SUMMARIES, "summaries")
    messages = tuple(
        _parse_message(item) for item in _list(payload.get("messages", []), "messages")
    )
    _check_limit(messages, MAX_MESSAGES, "messages")
    if len({item.symbol for item in candidates}) != len(candidates):
        raise AQSPSnapshotUnavailable("candidates 不得包含重复 symbol")
    if len({item.symbol for item in debates}) != len(debates):
        raise AQSPSnapshotUnavailable("debates 不得包含重复 symbol")
    _validate_advisory_boundary(raw_debates or [], candidates)
    stale_after = _optional_text(payload.get("stale_after"), "stale_after")
    if stale_after:
        _timestamp(stale_after, "stale_after")
    return AQSPSnapshot(
        schema_version=schema_version,
        generated_at=generated_at,
        selected_date=selected_date,
        available_dates=available_dates,
        candidates=candidates,
        debates=debates,
        summaries=summaries,
        source=_parse_source(payload["source"]),
        coldstart=_parse_coldstart(payload["coldstart"]),
        stale_after=stale_after,
        message_status=_optional_text(payload.get("message_status"), "message_status")
        or "未产出",
        messages=messages,
        market_context=_parse_market_context(payload.get("market_context")),
        recommendation_gate=_parse_recommendation_gate(
            payload.get("recommendation_gate")
        ),
    )


def _parse_recommendation_gate(payload: object) -> AQSPRecommendationGate:
    if payload is None:
        return AQSPRecommendationGate(
            recommendation_allowed=False,
            status="blocked",
            reasons=("recommendation_gate_missing",),
        )
    item = _object(payload, "recommendation_gate")
    _check_keys(
        item,
        {"recommendation_allowed", "status", "reasons"},
        "recommendation_gate",
    )
    allowed = item["recommendation_allowed"]
    if not isinstance(allowed, bool):
        raise AQSPSnapshotUnavailable(
            "recommendation_gate.recommendation_allowed 必须是布尔值"
        )
    return AQSPRecommendationGate(
        recommendation_allowed=allowed,
        status=_text(item["status"], "recommendation_gate.status"),
        reasons=tuple(
            _text_list(item["reasons"], "recommendation_gate.reasons")
        ),
    )


def _parse_candidate(payload: object) -> AQSPCandidate:
    item = _object(payload, "candidate")
    _check_keys(
        item,
        {"symbol", "display_name", "score", "research_status", "next_step", "context"},
        "candidate",
        {
            "deterministic_reasons",
            "strategies",
            "evidence_status",
            "score_breakdown",
            "technical_metrics",
            "data_source",
            "data_fetched_at",
            "data_timestamp_source",
            "freshness",
        },
    )
    return AQSPCandidate(
        symbol=_validate_symbol(_text(item["symbol"], "candidate.symbol")),
        display_name=_text(item["display_name"], "candidate.display_name"),
        score=_number(item["score"], "candidate.score"),
        research_status=_text(item["research_status"], "candidate.research_status"),
        next_step=_text(item["next_step"], "candidate.next_step"),
        context=_text(item["context"], "candidate.context"),
        deterministic_reasons=tuple(
            _text_list(
                item.get("deterministic_reasons", []), "candidate.deterministic_reasons"
            )
        ),
        strategies=tuple(
            _text_list(item.get("strategies", []), "candidate.strategies")
        ),
        evidence_status=_optional_text(
            item.get("evidence_status"), "candidate.evidence_status"
        )
        or "证据不足",
        score_breakdown=tuple(
            _text_list(item.get("score_breakdown", []), "candidate.score_breakdown")
        ),
        technical_metrics=tuple(
            _parse_technical_metric(value)
            for value in _list(
                item.get("technical_metrics", []), "candidate.technical_metrics"
            )
        ),
        data_source=_optional_text(item.get("data_source"), "candidate.data_source"),
        data_fetched_at=_optional_text(
            item.get("data_fetched_at"), "candidate.data_fetched_at"
        ),
        data_timestamp_source=_optional_text(
            item.get("data_timestamp_source"), "candidate.data_timestamp_source"
        ),
        freshness=_optional_text(item.get("freshness"), "candidate.freshness"),
    )


def _parse_technical_metric(payload: object) -> AQSPTechnicalMetric:
    item = _object(payload, "technical metric")
    _check_keys(item, {"key", "label", "value"}, "technical metric")
    return AQSPTechnicalMetric(
        key=_text(item["key"], "technical metric.key"),
        label=_text(item["label"], "technical metric.label"),
        value=_text(item["value"], "technical metric.value"),
    )


def _parse_debate(payload: object) -> AQSPDebate:
    item = _object(payload, "debate")
    _check_keys(
        item,
        {
            "symbol",
            "display_name",
            "conclusion",
            "primary_risk_gate",
            "next_trigger",
            "active_roles",
        },
        "debate",
        {
            "round_count",
            "bull_count",
            "bear_count",
            "neutral_count",
            "process_summary",
            "advisory_only",
            "deterministic_score",
            "deterministic_score_unchanged",
            "advisory_boundary_ok",
            "round_summaries",
            "support_points",
            "opposition_points",
            "risk_warnings",
            "watch_items",
            "real_message_evidence",
            "cross_market_evidence",
            "rule_transmission_evidence",
            "pending_confirmations",
            "process_recorded",
            "conclusion_recorded",
            "debate_quality_issues",
            "evidence",
        },
    )
    raw_evidence = tuple(
        _parse_debate_evidence(value)
        for value in _list(item.get("evidence", []), "debate.evidence")
    )
    evidence_by_kind = {
        "message": tuple(
            entry.text for entry in raw_evidence if entry.kind == "message"
        ),
        "cross_market": tuple(
            entry.text for entry in raw_evidence if entry.kind == "cross_market"
        ),
        "transmission": tuple(
            entry.text for entry in raw_evidence if entry.kind == "transmission"
        ),
    }
    message_evidence = (
        tuple(
            _text_list(
                item.get("real_message_evidence", []), "debate.real_message_evidence"
            )
        )
        or evidence_by_kind["message"]
    )
    cross_market_evidence = (
        tuple(
            _text_list(
                item.get("cross_market_evidence", []), "debate.cross_market_evidence"
            )
        )
        or evidence_by_kind["cross_market"]
    )
    transmission_evidence = (
        tuple(
            _text_list(
                item.get("rule_transmission_evidence", []),
                "debate.rule_transmission_evidence",
            )
        )
        or evidence_by_kind["transmission"]
    )
    return AQSPDebate(
        symbol=_validate_symbol(_text(item["symbol"], "debate.symbol")),
        display_name=_text(item["display_name"], "debate.display_name"),
        conclusion=_text(item["conclusion"], "debate.conclusion"),
        primary_risk_gate=_text(item["primary_risk_gate"], "debate.primary_risk_gate"),
        next_trigger=_text(item["next_trigger"], "debate.next_trigger"),
        active_roles=tuple(_text_list(item["active_roles"], "debate.active_roles")),
        round_count=_integer(item.get("round_count", 0), "debate.round_count"),
        bull_count=_integer(item.get("bull_count", 0), "debate.bull_count"),
        bear_count=_integer(item.get("bear_count", 0), "debate.bear_count"),
        neutral_count=_integer(item.get("neutral_count", 0), "debate.neutral_count"),
        process_summary=_optional_text(
            item.get("process_summary"), "debate.process_summary"
        ),
        round_summaries=tuple(
            _text_list(item.get("round_summaries", []), "debate.round_summaries")
        ),
        support_points=tuple(
            _text_list(item.get("support_points", []), "debate.support_points")
        ),
        opposition_points=tuple(
            _text_list(item.get("opposition_points", []), "debate.opposition_points")
        ),
        risk_warnings=tuple(
            _text_list(item.get("risk_warnings", []), "debate.risk_warnings")
        ),
        watch_items=tuple(
            _text_list(item.get("watch_items", []), "debate.watch_items")
        ),
        real_message_evidence=message_evidence,
        cross_market_evidence=cross_market_evidence,
        rule_transmission_evidence=transmission_evidence,
        pending_confirmations=tuple(
            _text_list(
                item.get("pending_confirmations", []),
                "debate.pending_confirmations",
            )
        ),
        advisory_only=_boolean(item.get("advisory_only", True), "debate.advisory_only"),
        deterministic_score=(
            _number(item["deterministic_score"], "debate.deterministic_score")
            if "deterministic_score" in item
            else None
        ),
        deterministic_score_unchanged=_boolean(
            item.get("deterministic_score_unchanged", True),
            "debate.deterministic_score_unchanged",
        ),
        advisory_boundary_ok=_boolean(
            item.get("advisory_boundary_ok", True),
            "debate.advisory_boundary_ok",
        ),
        process_recorded=_boolean(
            item.get("process_recorded", False), "debate.process_recorded"
        ),
        conclusion_recorded=_boolean(
            item.get("conclusion_recorded", False), "debate.conclusion_recorded"
        ),
        quality_issues=tuple(
            _text_list(
                item.get("debate_quality_issues", []),
                "debate.debate_quality_issues",
            )
        ),
    )


def _parse_debate_evidence(payload: object) -> AQSPDebateEvidence:
    item = _object(payload, "debate.evidence")
    _check_keys(item, {"kind", "text"}, "debate.evidence")
    kind = _text(item["kind"], "debate.evidence.kind")
    if kind not in {"message", "cross_market", "transmission"}:
        raise AQSPSnapshotUnavailable("debate.evidence.kind 不支持")
    return AQSPDebateEvidence(
        kind=kind,
        text=_text(item["text"], "debate.evidence.text"),
    )


def _validate_advisory_boundary(
    payloads: list[Any], candidates: tuple[AQSPCandidate, ...]
) -> None:
    """Reject agent metadata that can be mistaken for deterministic scoring."""
    candidates_by_symbol = {candidate.symbol: candidate for candidate in candidates}
    for payload in payloads:
        item = _object(payload, "debate")
        if "advisory_only" in item and item["advisory_only"] is not True:
            raise AQSPSnapshotUnavailable("debate 必须保持 advisory-only")
        if (
            "deterministic_score_unchanged" in item
            and item["deterministic_score_unchanged"] is not True
        ):
            raise AQSPSnapshotUnavailable("debate 不得改写确定性评分")
        if "advisory_boundary_ok" in item and item["advisory_boundary_ok"] is not True:
            raise AQSPSnapshotUnavailable("debate advisory 边界校验未通过")
        if "deterministic_score" not in item:
            continue
        symbol = _validate_symbol(_text(item["symbol"], "debate.symbol"))
        candidate = candidates_by_symbol.get(symbol)
        if candidate is None:
            raise AQSPSnapshotUnavailable("debate.deterministic_score 必须对应一个候选")
        deterministic_score = _number(
            item["deterministic_score"], "debate.deterministic_score"
        )
        if deterministic_score != candidate.score:
            raise AQSPSnapshotUnavailable("debate 不得覆盖 candidate.score")


def _parse_message(payload: object) -> AQSPMessage:
    item = _object(payload, "message")
    _check_keys(
        item,
        {"title", "summary", "impact", "category", "source", "published_at"},
        "message",
        {
            "url",
            "source_region",
            "source_quality",
            "event_type",
            "affected_sectors",
            "affected_symbols",
            "transmission_hypothesis",
            "supporting_evidence",
            "source_url",
            "verification",
            "transmission_path",
            "validation_signals",
            "invalidation_signals",
        },
    )
    published_at = _normalize_message_timestamp(
        _text(item["published_at"], "message.published_at")
    )
    return AQSPMessage(
        title=_text(item["title"], "message.title"),
        summary=_text(item["summary"], "message.summary"),
        impact=_text(item["impact"], "message.impact"),
        category=_text(item["category"], "message.category"),
        source=_text(item["source"], "message.source"),
        published_at=published_at,
        url=_optional_text(item.get("url"), "message.url"),
        source_region=_optional_text(item.get("source_region"), "message.source_region")
        or "mixed",
        source_quality=_optional_text(
            item.get("source_quality"), "message.source_quality"
        ),
        event_type=_optional_text(item.get("event_type"), "message.event_type"),
        affected_sectors=tuple(
            _text_list(item.get("affected_sectors", []), "message.affected_sectors")
        ),
        affected_symbols=tuple(
            _text_list(item.get("affected_symbols", []), "message.affected_symbols")
        ),
        transmission_hypothesis=_optional_text(
            item.get("transmission_hypothesis"), "message.transmission_hypothesis"
        ),
        supporting_evidence=tuple(
            _text_list(
                item.get("supporting_evidence", []), "message.supporting_evidence"
            )
        ),
        source_url=_optional_text(item.get("source_url"), "message.source_url"),
        verification=_optional_text(item.get("verification"), "message.verification"),
        transmission_path=tuple(
            _text_list(item.get("transmission_path", []), "message.transmission_path")
        ),
        validation_signals=tuple(
            _text_list(item.get("validation_signals", []), "message.validation_signals")
        ),
        invalidation_signals=tuple(
            _text_list(
                item.get("invalidation_signals", []), "message.invalidation_signals"
            )
        ),
    )


def _parse_source(payload: object) -> AQSPSource:
    item = _object(payload, "source")
    _check_keys(
        item, {"effective", "latest_trade_date", "lag_days", "status"}, "source"
    )
    return AQSPSource(
        effective=_text(item["effective"], "source.effective"),
        latest_trade_date=_text(item["latest_trade_date"], "source.latest_trade_date"),
        lag_days=_integer(item["lag_days"], "source.lag_days"),
        status=_text(item["status"], "source.status"),
    )


def _parse_coldstart(payload: object) -> AQSPColdstart:
    item = _object(payload, "coldstart")
    _check_keys(item, {"status", "detail"}, "coldstart")
    return AQSPColdstart(
        status=_text(item["status"], "coldstart.status"),
        detail=_text(item["detail"], "coldstart.detail"),
    )


def _parse_market_context(payload: object) -> AQSPMarketContext | None:
    if payload is None:
        return None
    item = _object(payload, "market_context")
    _check_keys(
        item,
        {"status", "overview", "summary_lines", "cross_market", "warnings"},
        "market_context",
    )
    cross_market = tuple(
        _parse_cross_market(value)
        for value in _list(item["cross_market"], "market_context.cross_market")
    )
    _check_limit(cross_market, MAX_CROSS_MARKET, "market_context.cross_market")
    return AQSPMarketContext(
        status=_text(item["status"], "market_context.status"),
        overview=_text(item["overview"], "market_context.overview"),
        summary_lines=tuple(
            _text_list(item["summary_lines"], "market_context.summary_lines")
        ),
        cross_market=cross_market,
        warnings=tuple(_text_list(item["warnings"], "market_context.warnings")),
    )


def _parse_cross_market(payload: object) -> AQSPCrossMarket:
    item = _object(payload, "cross_market")
    fields = {
        "rule_id",
        "theme",
        "strength",
        "action",
        "source_title",
        "source_region",
        "source_published_at",
        "affected_sectors",
        "transmission_path",
        "validation_signals",
        "invalidation_signals",
        "summary",
    }
    _check_keys(item, fields, "cross_market")
    return AQSPCrossMarket(
        rule_id=_text(item["rule_id"], "cross_market.rule_id"),
        theme=_text(item["theme"], "cross_market.theme"),
        strength=_text(item["strength"], "cross_market.strength"),
        action=_text(item["action"], "cross_market.action"),
        source_title=_text(item["source_title"], "cross_market.source_title"),
        source_region=_text(item["source_region"], "cross_market.source_region"),
        source_published_at=_text(
            item["source_published_at"], "cross_market.source_published_at"
        ),
        affected_sectors=tuple(
            _text_list(item["affected_sectors"], "cross_market.affected_sectors")
        ),
        transmission_path=tuple(
            _text_list(item["transmission_path"], "cross_market.transmission_path")
        ),
        validation_signals=tuple(
            _text_list(item["validation_signals"], "cross_market.validation_signals")
        ),
        invalidation_signals=tuple(
            _text_list(
                item["invalidation_signals"], "cross_market.invalidation_signals"
            )
        ),
        summary=_text(item["summary"], "cross_market.summary"),
    )


def _select_current(
    snapshots: tuple[AQSPSnapshot, ...], selected_date: str
) -> AQSPSnapshot:
    if not snapshots:
        raise AQSPSnapshotUnavailable("日期索引没有可用快照")
    for snapshot in snapshots:
        if snapshot.selected_date == selected_date:
            return snapshot
    raise AQSPSnapshotUnavailable("index.selected_date 对应的快照不存在")


def _check_keys(
    payload: Mapping[str, Any],
    required: set[str],
    name: str,
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        raise AQSPSnapshotUnavailable(f"{name} schema 不匹配")


def _check_limit(values: tuple[Any, ...] | list[Any], limit: int, name: str) -> None:
    if len(values) > limit:
        raise AQSPSnapshotUnavailable(f"{name} 超过 {limit} 项上限")


def _object(payload: object, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AQSPSnapshotUnavailable(f"{name} 必须是 JSON object")
    return payload


def _list(payload: object, name: str) -> list[Any]:
    if not isinstance(payload, list):
        raise AQSPSnapshotUnavailable(f"{name} 必须是 JSON array")
    return payload


def _text(payload: object, name: str) -> str:
    if not isinstance(payload, str):
        raise AQSPSnapshotUnavailable(f"{name} 必须是字符串")
    return payload


def _text_list(payload: object, name: str) -> list[str]:
    return [_text(item, name) for item in _list(payload, name)]


def _optional_text(payload: object, name: str) -> str:
    return "" if payload is None else _text(payload, name)


def _number(payload: object, name: str) -> float:
    if isinstance(payload, bool) or not isinstance(payload, (int, float)):
        raise AQSPSnapshotUnavailable(f"{name} 必须是有限数字")
    value = float(payload)
    if not math.isfinite(value):
        raise AQSPSnapshotUnavailable(f"{name} 必须是有限数字")
    return value


def _boolean(payload: object, name: str) -> bool:
    if not isinstance(payload, bool):
        raise AQSPSnapshotUnavailable(f"{name} 必须是布尔值")
    return payload


def _integer(payload: object, name: str) -> int:
    if isinstance(payload, bool) or not isinstance(payload, int):
        raise AQSPSnapshotUnavailable(f"{name} 必须是整数")
    return payload


def _timestamp(value: str, name: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AQSPSnapshotUnavailable(f"{name} 必须是带时区的 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AQSPSnapshotUnavailable(f"{name} 必须包含时区偏移")
    return parsed


def _normalize_message_timestamp(value: str) -> str:
    """Normalize legacy local news timestamps without weakening API output."""
    try:
        legacy = datetime.strptime(value, LEGACY_MESSAGE_TIMESTAMP_FORMAT)
    except ValueError:
        _timestamp(value, "message.published_at")
        return value
    return legacy.replace(tzinfo=SHANGHAI_TZ).isoformat()


def _validate_date(value: str, name: str, *, request: bool = False) -> str:
    error_type = AQSPInvalidRequest if request else AQSPSnapshotUnavailable
    try:
        parsed = CalendarDate.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise error_type(f"{name} 必须使用 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise error_type(f"{name} 必须使用 YYYY-MM-DD")
    return value


def _validate_symbol(value: str, *, request: bool = False) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 32
        or any(char.isspace() for char in normalized)
    ):
        error_type = AQSPInvalidRequest if request else AQSPSnapshotUnavailable
        raise error_type("candidate symbol 非法")
    return normalized