"""Deterministic bounded view for the latest intraday candidate artifact."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence


LIVE_CANDIDATE_LIMIT = 3


@dataclass(frozen=True)
class LiveCandidateViewConfig:
    """Limits for the display-only live candidate view."""

    max_candidates: int = LIVE_CANDIDATE_LIMIT
    max_age: timedelta = timedelta(minutes=30)


@dataclass(frozen=True)
class LiveArtifactMetadata:
    """Metadata that determines whether an artifact may represent today."""

    artifact_date: str
    updated_at: str
    source: str = "intraday_csv"
    freshness_status: str = ""
    source_status: str = ""
    source_reason: str = ""


@dataclass(frozen=True)
class LiveCandidate:
    """One candidate retained by the bounded view."""

    symbol: str
    name: str
    score: float
    rating: str
    status: str
    blocker: str
    next_step: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_quality: str
    evidence_quality_label: str
    freshness_label: str
    source: str
    artifact_date: str
    updated_at: str
    row: Mapping[str, Any]


@dataclass(frozen=True)
class LiveCandidateView:
    """Bounded, deterministic result for the homepage live candidate lane."""

    status: str
    stale_reason: str
    artifact_date: str
    updated_at: str
    source: str
    candidates: tuple[LiveCandidate, ...]
    actionable_count: int
    watch_count: int
    blocked_count: int
    recommendation_blocked: bool = True


def build_live_candidate_view(
    rows: Sequence[Mapping[str, Any]],
    *,
    metadata: LiveArtifactMetadata,
    now: datetime,
    requested_date: str = "",
    config: LiveCandidateViewConfig | None = None,
) -> LiveCandidateView:
    """Build a bounded view without changing deterministic scores or ratings."""
    resolved_config = config or LiveCandidateViewConfig()
    max_candidates = max(1, int(resolved_config.max_candidates))
    normalized_rows = _dedupe_rows(rows)
    freshness_status, stale_reason = _freshness_status(
        metadata=metadata,
        now=now,
        requested_date=requested_date,
        max_age=resolved_config.max_age,
        rows=rows,
    )
    candidates = tuple(
        _candidate_from_row(
            row,
            metadata=metadata,
            freshness_status=freshness_status,
            freshness_reason=stale_reason,
        )
        for row in normalized_rows
    )
    ordered = tuple(sorted(candidates, key=_candidate_sort_key)[:max_candidates])
    return LiveCandidateView(
        status=freshness_status,
        stale_reason=stale_reason,
        artifact_date=metadata.artifact_date,
        updated_at=metadata.updated_at,
        source=metadata.source,
        candidates=ordered,
        actionable_count=sum(item.status == "actionable" for item in candidates),
        watch_count=sum(item.status == "watch" for item in candidates),
        blocked_count=sum(item.status == "blocked" for item in candidates),
        recommendation_blocked=freshness_status != "fresh",
    )


def _dedupe_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    selected: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = _canonical_symbol(row.get("symbol"))
        if not symbol or symbol == "__RUN__":
            continue
        normalized = dict(row)
        normalized["symbol"] = symbol
        existing = selected.get(symbol)
        if existing is None or _row_preference_key(normalized) > _row_preference_key(
            existing
        ):
            selected[symbol] = normalized
    return tuple(selected.values())


def _row_preference_key(row: Mapping[str, Any]) -> tuple[str, float, int]:
    return (
        str(row.get("created_at") or row.get("updated_at") or "").strip(),
        _number(row.get("score")),
        _rating_rank(row.get("rating")),
    )


def _candidate_from_row(
    row: Mapping[str, Any],
    *,
    metadata: LiveArtifactMetadata,
    freshness_status: str,
    freshness_reason: str,
) -> LiveCandidate:
    status, blocker = _candidate_status(row)
    if freshness_status == "watch":
        if status == "actionable":
            status = "watch"
        blocker = _join_blockers(blocker, freshness_reason or "实时数据延迟，降级为观察")
    elif freshness_status != "fresh":
        status = "blocked"
        blocker = _join_blockers(blocker, freshness_reason or "实时数据不可验证")
    reasons = _text_tuple(row.get("reasons"))
    risks = _text_tuple(row.get("risks"))
    evidence_quality, evidence_quality_label = _evidence_quality(row, reasons, risks)
    freshness_label = (
        "新鲜"
        if freshness_status == "fresh"
        else "数据延迟，降级观察"
        if freshness_status == "watch"
        else "数据已过期"
        if freshness_status == "stale"
        else "数据源失败"
        if freshness_status == "failed"
        else "新鲜度未知"
    )
    return LiveCandidate(
        symbol=str(row.get("symbol") or "").strip(),
        name=str(row.get("name") or "").strip(),
        score=_number(row.get("score")),
        rating=str(row.get("rating") or "").strip(),
        status=status,
        blocker=blocker,
        next_step=str(
            row.get("candidate_next_step") or row.get("debate_next_trigger") or ""
        ).strip(),
        reasons=reasons,
        risks=risks,
        evidence_quality=evidence_quality,
        evidence_quality_label=evidence_quality_label,
        freshness_label=freshness_label,
        source=metadata.source,
        artifact_date=metadata.artifact_date,
        updated_at=metadata.updated_at,
        row=row,
    )


def _candidate_status(row: Mapping[str, Any]) -> tuple[str, str]:
    blocker = str(row.get("candidate_blocker") or "").strip()
    candidate_status = str(row.get("candidate_status") or "").strip()
    action = str(row.get("portfolio_action") or "").strip()
    quality_gate_action = str(row.get("quality_gate_action") or "").strip()
    rating = str(row.get("rating") or "").strip()
    if blocker or "阻塞" in candidate_status or (
        action == "downgrade" and quality_gate_action != "observe"
    ):
        return "blocked", blocker or candidate_status or "候选存在阻塞条件"
    quality_status = _normalized_quality_status(row.get("data_quality_status"))
    execution_blocker = _execution_blocker_reason(row)
    if quality_status in {"blocked", "error", "failed"} or execution_blocker:
        return "blocked", execution_blocker or f"数据质量状态: {quality_status}"
    if (
        quality_gate_action == "observe"
        or quality_status == "watch"
        or _text_tuple(row.get("data_quality_alerts"))
    ):
        return "watch", _quality_watch_reason(row)
    if action == "promote" or rating in {"strong_buy_candidate", "buy_candidate"}:
        return "actionable", ""
    return "watch", ""


def _normalized_quality_status(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _execution_blocker_reason(row: Mapping[str, Any]) -> str:
    boolean_fields = (
        "not_executable",
        "executable",
        "suspended",
        "is_suspended",
        "limit_up",
        "limit_down",
        "limit_up_at_open",
        "limit_down_at_open",
        "is_limit_up",
        "is_limit_down",
        "at_limit_up",
        "at_limit_down",
    )
    for field in boolean_fields:
        value = row.get(field)
        if field == "executable" and (
            value is False
            or str(value or "").strip().lower() in {"0", "false", "no", "n"}
        ):
            return "数据标记不可成交"
        if field != "executable" and _is_true(value):
            return f"数据标记 {field}"
    text_fields = (
        "not_executable_reason",
        "candidate_blocker",
        "candidate_status",
        "execution_status",
        "status",
        "limit_status",
        "tradeability",
        "price_status",
        "risks",
        "risk_flags",
        "data_quality_alerts",
    )
    execution_markers = (
        "涨停",
        "跌停",
        "停牌",
        "不可成交",
        "无法成交",
        "limit_up",
        "limit_down",
        "suspended",
        "not_executable",
    )
    for field in text_fields:
        text = " ".join(_text_tuple(row.get(field))).lower()
        if any(marker.lower() in text for marker in execution_markers):
            return text or f"数据字段 {field} 存在不可成交风险"
    return ""


def _quality_watch_reason(row: Mapping[str, Any]) -> str:
    alerts = _text_tuple(row.get("data_quality_alerts"))
    if alerts:
        return f"数据质量告警: {'；'.join(alerts)}"
    quality_status = _normalized_quality_status(row.get("data_quality_status"))
    return f"数据质量状态: {quality_status}"


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _candidate_sort_key(candidate: LiveCandidate) -> tuple[int, float, int, str, str]:
    status_rank = {"actionable": 0, "watch": 1, "blocked": 2}
    return (
        status_rank.get(candidate.status, 3),
        -candidate.score,
        -_rating_rank(candidate.rating),
        str(candidate.updated_at or ""),
        candidate.symbol,
    )


def _freshness_status(
    *,
    metadata: LiveArtifactMetadata,
    now: datetime,
    requested_date: str,
    max_age: timedelta,
    rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    source_status, source_reason = _source_status(metadata, rows)
    if source_status == "failed":
        return "failed", source_reason or "数据源失败，禁止实时推荐"
    if source_status == "unknown":
        return "unknown", source_reason or "数据源状态未知，禁止实时推荐"

    hinted_status, hinted_reason = _freshness_hint(metadata, rows)
    if hinted_status == "failed":
        return "failed", hinted_reason or "数据源失败，禁止实时推荐"
    if hinted_status == "unknown":
        return "unknown", hinted_reason or "实时数据新鲜度未知，禁止推荐"

    artifact_date = metadata.artifact_date.strip()
    expected_date = requested_date.strip()
    today = now.date().isoformat()
    if not artifact_date:
        return "unknown", "盘中产物缺少日期，无法确认是否为今日实时数据"
    if expected_date and artifact_date != expected_date:
        return "stale", f"盘中产物日期为 {artifact_date}，请求日期为 {expected_date}"
    if artifact_date != today:
        return "stale", f"盘中产物日期为 {artifact_date}，今日为 {today}"
    updated_at = _parse_datetime(metadata.updated_at)
    if updated_at is None:
        return "unknown", "盘中产物缺少可识别更新时间，无法确认实时性"
    if now.tzinfo is None or now.utcoffset() is None:
        return "unknown", "当前时间缺少时区，无法确认实时性"
    age = now - updated_at
    if age < timedelta(0):
        return "unknown", "盘中产物更新时间晚于当前时间，无法确认实时性"
    if age > max_age:
        return "stale", f"盘中产物更新时间为 {metadata.updated_at}，已超过实时窗口"
    if hinted_status == "watch":
        return "watch", hinted_reason or "实时数据存在延迟，降级为观察"
    if hinted_status == "stale":
        return "stale", hinted_reason or "实时数据已过期"
    return "fresh", ""


_FAILED_STATUS_VALUES = frozenset(
    {"failed", "failure", "error", "timeout", "unavailable", "source_failed"}
)
_UNKNOWN_STATUS_VALUES = frozenset({"unknown", "not_loaded", "missing"})
_FRESH_STATUS_VALUES = frozenset({"fresh", "ok", "passed", "ready"})
_WATCH_STATUS_VALUES = frozenset({"watch", "delayed", "degraded", "partial"})
_STALE_STATUS_VALUES = frozenset({"stale", "expired", "historical", "end_of_day"})
_SOURCE_FAILURE_MARKERS = (
    "source_failed",
    "data_source_failure",
    "数据源失败",
    "源失败",
    "抓取失败",
    "请求失败",
    "timeout",
    "超时",
    "unavailable",
    "不可用",
)


def _freshness_hint(
    metadata: LiveArtifactMetadata,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    explicit = _normalized_status(metadata.freshness_status)
    reason = metadata.source_reason.strip()
    if explicit:
        return _classify_freshness(explicit, reason)

    run_rows = _run_rows(rows)
    if not run_rows:
        return "", ""
    run_row = run_rows[0]
    status = _normalized_status(
        run_row.get("freshness_status") or run_row.get("live_freshness_status")
    )
    if status:
        return _classify_freshness(
            status,
            _first_text(
                run_row.get("freshness_reason"),
                run_row.get("run_source_health_message"),
            ),
        )
    tier = _normalized_status(run_row.get("run_source_freshness_tier"))
    if tier in {"realtime", "terminal_realtime"}:
        return "fresh", ""
    if tier in {"delayed_realtime", "delayed"}:
        return "watch", "实时源存在延迟，降级为观察"
    if tier:
        return "stale", f"来源新鲜度层级为 {tier}，不能作为实时推荐"
    return "unknown", "盘中产物未记录新鲜度状态"


def _source_status(
    metadata: LiveArtifactMetadata,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    explicit = _normalized_status(metadata.source_status)
    if explicit in _FAILED_STATUS_VALUES:
        return "failed", metadata.source_reason.strip()
    if explicit in _UNKNOWN_STATUS_VALUES:
        return "unknown", metadata.source_reason.strip()

    for row in _run_rows(rows):
        status = _normalized_status(
            row.get("source_status")
            or row.get("run_source_status")
            or row.get("source_health_status")
        )
        if status in _FAILED_STATUS_VALUES:
            return "failed", _source_reason(row) or "数据源失败，禁止实时推荐"
        if status in _UNKNOWN_STATUS_VALUES:
            return "unknown", _source_reason(row) or "数据源状态未知，禁止实时推荐"
        text = " ".join(
            str(row.get(field) or "").strip().lower()
            for field in (
                "run_source_health_label",
                "run_source_health_message",
                "source_health_label",
                "source_health_message",
            )
        )
        if any(marker.lower() in text for marker in _SOURCE_FAILURE_MARKERS):
            return "failed", _source_reason(row) or "数据源失败，禁止实时推荐"
    return "", ""


def _classify_freshness(status: str, reason: str) -> tuple[str, str]:
    if status in _FAILED_STATUS_VALUES:
        return "failed", reason or "数据源失败，禁止实时推荐"
    if status in _UNKNOWN_STATUS_VALUES:
        return "unknown", reason or "实时数据新鲜度未知，禁止推荐"
    if status in _STALE_STATUS_VALUES:
        return "stale", reason or "实时数据已过期"
    if status in _WATCH_STATUS_VALUES:
        return "watch", reason or "实时数据存在延迟，降级为观察"
    if status in _FRESH_STATUS_VALUES:
        return "fresh", ""
    return "unknown", reason or f"无法识别的新鲜度状态: {status}"


def _run_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if _canonical_symbol(row.get("symbol")) == "__RUN__"
    )


def _source_reason(row: Mapping[str, Any]) -> str:
    return _first_text(
        row.get("run_source_health_message"),
        row.get("source_health_message"),
        row.get("source_reason"),
    )


def _first_text(*values: Any) -> str:
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _join_blockers(*values: str) -> str:
    selected: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in selected:
            selected.append(text)
    return "；".join(selected)


def _evidence_quality(
    row: Mapping[str, Any],
    reasons: tuple[str, ...],
    risks: tuple[str, ...],
) -> tuple[str, str]:
    signals = sum(
        bool(value)
        for value in (
            row.get("score") not in (None, ""),
            row.get("rating"),
            reasons,
            risks,
            row.get("candidate_next_step") or row.get("debate_next_trigger"),
            row.get("run_actual_source") or row.get("data_source"),
        )
    )
    if signals >= 5:
        return "strong", "证据较充分"
    if signals >= 3:
        return "medium", "证据可复核"
    return "weak", "证据偏薄"


def _canonical_symbol(value: Any) -> str:
    symbol = str(value or "").strip()
    return symbol.zfill(6) if symbol.isdigit() and len(symbol) < 6 else symbol


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rating_rank(value: Any) -> int:
    return {
        "avoid": 0,
        "watch": 1,
        "buy_candidate": 2,
        "strong_buy_candidate": 3,
    }.get(str(value or "").strip(), -1)


def _text_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(
            item.strip() for item in value.replace("；", ",").split(",") if item.strip()
        )
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed
