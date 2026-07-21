"""Bounded, file-only snapshot contract for the dashboard home page."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date as CalendarDate
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aqsp.core.time import now_shanghai, to_shanghai
from aqsp.utils.jsonl_io import atomic_write_text


HOME_SNAPSHOT_SCHEMA_VERSION = "v1"
MAX_HOME_SNAPSHOT_BYTES = 512 * 1024
MAX_HOME_SNAPSHOT_DATES = 4
MAX_HOME_SNAPSHOT_CANDIDATES = 5
MAX_HOME_SNAPSHOT_TECHNICAL_METRICS = 8
MAX_HOME_SNAPSHOT_DEBATES = 3
MAX_HOME_SNAPSHOT_SUMMARIES = 3
MAX_HOME_SNAPSHOT_MESSAGES = 5
MAX_HOME_SNAPSHOT_MARKET_LINES = 5
MAX_HOME_SNAPSHOT_CROSS_MARKET = 3
MAX_HOME_SNAPSHOT_VARIANTS = 192
HOME_SNAPSHOT_INDEX_SCHEMA_VERSION = "v1-index"
MAX_HOME_SNAPSHOT_INDEX_DAYS = 4
HOME_SNAPSHOT_DEFAULT_TTL = timedelta(hours=24)
HOME_SNAPSHOT_INTRADAY_TTL = timedelta(minutes=30)
HOME_SNAPSHOT_CLOSE_TTL = timedelta(hours=18)
_INTRADAY_TASK_IDS = frozenset({"intraday", "midday"})


@dataclass(frozen=True)
class HomeSnapshotTechnicalMetric:
    """One bounded, deterministic short-term technical metric."""

    key: str
    label: str
    value: str


@dataclass(frozen=True)
class HomeSnapshotCandidate:
    """One bounded deterministic candidate card for the home page."""

    symbol: str
    display_name: str
    score: float
    research_status: str
    next_step: str
    context: str
    deterministic_reasons: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()
    score_breakdown: tuple[str, ...] = ()
    evidence_status: str = "证据不足"
    technical_metrics: tuple[HomeSnapshotTechnicalMetric, ...] = ()
    data_source: str = ""
    data_fetched_at: str = ""
    data_timestamp_source: str = ""
    freshness: str = ""

    @property
    def has_deterministic_evidence(self) -> bool:
        """Whether this card carries independent rule-derived evidence."""
        return bool(self.deterministic_reasons) and bool(math.isfinite(self.score))


HOME_RECOMMENDATION_LABELS = (
    "纸面复核",
    "优先复核",
    "上调优先级",
    "第一顺位",
    "第二顺位",
    "后续顺位",
)
HOME_NON_RECOMMENDATION_MARKERS = (
    "阻塞",
    "不可用",
    "过期",
    "待核对",
)


def is_home_recommendation(candidate: HomeSnapshotCandidate) -> bool:
    """Return whether a candidate may appear in the homepage recommendation cards."""
    status = candidate.research_status.strip()
    if not status or any(
        marker in status for marker in HOME_NON_RECOMMENDATION_MARKERS
    ):
        return False
    return candidate.has_deterministic_evidence and any(
        label in status for label in HOME_RECOMMENDATION_LABELS
    )


@dataclass(frozen=True)
class HomeSnapshotMessage:
    """One current-day news item; historical notifications never enter here."""

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
class HomeSnapshotCrossMarket:
    """Bounded, advisory-only cross-market transmission summary."""

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


@dataclass(frozen=True)
class HomeSnapshotMarketContext:
    """Structured current-day message and transmission context for the home page."""

    status: str
    overview: str
    summary_lines: tuple[str, ...]
    cross_market: tuple[HomeSnapshotCrossMarket, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class HomeSnapshotDebate:
    """One advisory-only committee result shown beside the candidates."""

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
    viewpoint_buckets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    disagreement_points: tuple[str, ...] = ()
    uncertainty_points: tuple[str, ...] = ()


@dataclass(frozen=True)
class HomeSnapshotHolding:
    """One bounded end-of-run holding in an isolated experiment account."""

    symbol: str
    quantity: int
    average_price: float
    last_price: float
    market_value: float
    unrealized_pnl: float
    name: str = ""


@dataclass(frozen=True)
class HomeSnapshotVariant:
    """Bounded result for one isolated 100,000 yuan experiment account."""

    variant_id: str
    label: str
    initial_cash: float
    cash: float
    final_equity: float
    total_pnl: float
    return_pct: float
    filled_orders: int
    rejected_orders: int
    start_date: str
    end_date: str
    data_mode: str
    rank: int = 0
    strategy: str = ""
    holdings: tuple[HomeSnapshotHolding, ...] = ()
    previous_holdings: tuple[HomeSnapshotHolding, ...] | None = None
    recent_actions: tuple[str, ...] = ()
    hard_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class HomeSnapshotVariantUniverse:
    """Exact universe and freshness scope used by the isolated variants."""

    symbol_count: int = 0
    board_scope: str = ""
    excluded: tuple[str, ...] = ()
    latest_trade_date: str = ""
    coverage_pct: float = 0.0
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class HomeSnapshotSource:
    """Freshness and effective source status for the current run."""

    effective: str
    latest_trade_date: str
    lag_days: int
    status: str


@dataclass(frozen=True)
class HomeSnapshotColdstart:
    """Cold-start progress, kept separate from live recommendation evidence."""

    status: str
    detail: str


@dataclass(frozen=True)
class HomeSnapshotRecommendationGate:
    """Global research-only recommendation eligibility evidence."""

    recommendation_allowed: bool
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class HomeSnapshotPhase:
    """One research phase with explicit overlap accounting."""

    task_id: str
    label: str
    status: str
    candidate_count: int
    unique_symbols: int
    overlap_symbols: int
    updated_at: str = ""


@dataclass(frozen=True)
class HomeSnapshotUniverse:
    """Observable universe coverage; zero means the producer did not report it."""

    total: int = 0
    resolved: int = 0
    screened: int = 0
    final: int = 0
    max_universe: int = 0
    source: str = ""
    batch_active: bool = False
    batch_id: str = ""
    batch_size: int = 0
    cycle_id: int = 0
    coverage_pct: float = 0.0
    last_error: str = ""


@dataclass(frozen=True, init=False)
class HomeDashboardSnapshot:
    """Small home-page payload that is safe to load without historical fan-out."""

    schema_version: str
    generated_at: str
    selected_date: str
    available_dates: tuple[str, ...]
    candidates: tuple[HomeSnapshotCandidate, ...]
    debates: tuple[HomeSnapshotDebate, ...]
    summaries: tuple[str, ...]
    source: HomeSnapshotSource
    coldstart: HomeSnapshotColdstart
    # Empty means a legacy v1 payload that predates freshness metadata.
    stale_after: str = ""
    message_status: str = "未产出"
    messages: tuple[HomeSnapshotMessage, ...] = ()
    market_context: HomeSnapshotMarketContext | None = None
    recommendation_gate: HomeSnapshotRecommendationGate | None = None
    phases: tuple[HomeSnapshotPhase, ...] = ()
    universe: HomeSnapshotUniverse = HomeSnapshotUniverse()
    variant_universe: HomeSnapshotVariantUniverse = HomeSnapshotVariantUniverse()
    variants: tuple[HomeSnapshotVariant, ...] = ()

    def __init__(
        self,
        *,
        schema_version: str,
        generated_at: str,
        selected_date: str,
        available_dates: tuple[str, ...],
        candidates: tuple[HomeSnapshotCandidate, ...],
        summaries: tuple[str, ...],
        source: HomeSnapshotSource,
        coldstart: HomeSnapshotColdstart,
        debates: tuple[HomeSnapshotDebate, ...] = (),
        debate: HomeSnapshotDebate | None = None,
        stale_after: str = "",
        message_status: str = "未产出",
        messages: tuple[HomeSnapshotMessage, ...] = (),
        market_context: HomeSnapshotMarketContext | None = None,
        recommendation_gate: HomeSnapshotRecommendationGate | None = None,
        phases: tuple[HomeSnapshotPhase, ...] = (),
        universe: HomeSnapshotUniverse | None = None,
        variant_universe: HomeSnapshotVariantUniverse | None = None,
        variants: tuple[HomeSnapshotVariant, ...] = (),
    ) -> None:
        normalized_debates = tuple(debates or ())
        if debate is not None:
            if normalized_debates and normalized_debates[0] != debate:
                raise ValueError("debate must match the first debates item")
            if not normalized_debates:
                normalized_debates = (debate,)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "selected_date", selected_date)
        object.__setattr__(self, "available_dates", available_dates)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "debates", normalized_debates)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "coldstart", coldstart)
        object.__setattr__(self, "stale_after", stale_after)
        object.__setattr__(self, "message_status", message_status)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "market_context", market_context)
        object.__setattr__(
            self,
            "recommendation_gate",
            recommendation_gate
            or HomeSnapshotRecommendationGate(
                recommendation_allowed=False,
                status="blocked",
                reasons=("recommendation_gate_missing",),
            ),
        )
        object.__setattr__(self, "phases", tuple(phases or ()))
        object.__setattr__(self, "universe", universe or HomeSnapshotUniverse())
        object.__setattr__(
            self,
            "variant_universe",
            variant_universe or HomeSnapshotVariantUniverse(),
        )
        object.__setattr__(self, "variants", tuple(variants or ()))
        self.__post_init__()

    @property
    def debate(self) -> HomeSnapshotDebate | None:
        """Compatibility accessor for callers that still use the old field."""
        return self.debates[0] if self.debates else None

    def __post_init__(self) -> None:
        _validate_snapshot(self)

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON object written to the runtime snapshot file."""
        return asdict(self)

    def to_json(self) -> str:
        """Return compact UTF-8-safe JSON for atomic persistence."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def is_stale(self, as_of: datetime | None = None) -> bool:
        """Return True when freshness is missing, invalid, or past its deadline."""
        if not self.stale_after:
            return True
        deadline = _parse_timestamp(self.stale_after, "stale_after")
        current = _coerce_timestamp(as_of or now_shanghai(), "as_of")
        return current >= deadline


@dataclass(frozen=True)
class HomeSnapshotDay:
    """One date-addressable home snapshot in an optional index."""

    date: str
    snapshot: HomeDashboardSnapshot

    def __post_init__(self) -> None:
        _validate_date(self.date, "day.date")
        if self.snapshot.selected_date != self.date:
            raise ValueError("day.date must match snapshot.selected_date")

    @property
    def signal_date(self) -> str:
        """Compatibility name for providers that call the date a signal date."""
        return self.date

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "snapshot": self.snapshot.to_dict()}


@dataclass(frozen=True)
class HomeSnapshotIndex:
    """Optional bounded index for reading home snapshots by date."""

    schema_version: str
    generated_at: str
    stale_after: str
    days: tuple[HomeSnapshotDay, ...]
    selected_date: str = ""

    def __post_init__(self) -> None:
        _validate_index(self)

    @property
    def available_dates(self) -> tuple[str, ...]:
        return tuple(day.date for day in self.days)

    def snapshot_for_date(self, selected_date: str) -> HomeDashboardSnapshot | None:
        """Return the exact dated snapshot; never substitute another date."""
        return next(
            (day.snapshot for day in self.days if day.date == selected_date.strip()),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "stale_after": self.stale_after,
            "selected_date": self.selected_date,
            "days": [day.to_dict() for day in self.days],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# Descriptive aliases keep the contract discoverable to future dashboard code.
HomeDashboardDaySnapshot = HomeSnapshotDay
HomeDashboardSnapshotIndex = HomeSnapshotIndex


def write_home_dashboard_snapshot(
    path: str | Path,
    snapshot: HomeDashboardSnapshot,
) -> None:
    """Atomically persist one validated home snapshot.

    The byte budget is checked before replacing the previous file, so an oversized
    payload never turns the dashboard home route into an unbounded data reader.
    """
    snapshot = _normalize_snapshot_for_write(snapshot)
    existing = load_home_dashboard_snapshot(path)
    if existing is not None and existing.selected_date > snapshot.selected_date:
        raise ValueError("refusing to replace a newer home snapshot with an older date")
    payload = f"{snapshot.to_json()}\n"
    if len(payload.encode("utf-8")) > MAX_HOME_SNAPSHOT_BYTES:
        raise ValueError("home snapshot exceeds the 256 KiB byte budget")
    atomic_write_text(path, payload)
    _set_runtime_snapshot_mode(path)


def write_home_snapshot_index(path: str | Path, index: HomeSnapshotIndex) -> None:
    """Atomically persist a validated, bounded date index."""
    index = _normalize_index_for_write(index)
    existing = load_home_snapshot_index(path)
    if (
        existing is not None
        and existing.selected_date
        and index.selected_date
        and existing.selected_date > index.selected_date
    ):
        raise ValueError("refusing to replace a newer home snapshot index")
    payload = f"{index.to_json()}\n"
    if len(payload.encode("utf-8")) > MAX_HOME_SNAPSHOT_BYTES:
        raise ValueError("home snapshot index exceeds the 256 KiB byte budget")
    atomic_write_text(path, payload)
    _set_runtime_snapshot_mode(path)


def _set_runtime_snapshot_mode(path: str | Path) -> None:
    """Keep atomically replaced dashboard files readable by the service group.

    Production writes are performed by a different account than the read-only
    AQSP research API. The runtime directory supplies the group ownership; the
    file mode keeps the payload private from all other users.
    """
    Path(path).chmod(0o640)


def load_home_dashboard_snapshot(path: str | Path) -> HomeDashboardSnapshot | None:
    """Load a valid bounded snapshot, returning ``None`` for every unsafe input.

    This function only reads its one JSON file. It deliberately does not fall back
    to ledger, report, network, or other historical runtime inputs.
    """
    payload = _read_json_payload(path)
    if payload is None:
        return None
    try:
        return _snapshot_from_dict(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def load_home_snapshot_index(path: str | Path) -> HomeSnapshotIndex | None:
    """Load only an index payload; return None for legacy single snapshots."""
    payload = _read_json_payload(path)
    if payload is None:
        return None
    try:
        return _index_from_dict(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def load_home_snapshot_for_date(
    path: str | Path, selected_date: str
) -> HomeDashboardSnapshot | None:
    """Read an exact date from an index or exact-match a single v1 snapshot."""
    payload = _read_json_payload(path)
    if payload is None:
        return None
    try:
        mapping = _mapping(payload, "home snapshot")
        if mapping.get("schema_version") == HOME_SNAPSHOT_INDEX_SCHEMA_VERSION:
            return _index_from_dict(mapping).snapshot_for_date(selected_date)
        snapshot = _snapshot_from_dict(mapping)
        return snapshot if snapshot.selected_date == selected_date.strip() else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def stale_after_for(
    generated_at: str,
    validity: timedelta = HOME_SNAPSHOT_DEFAULT_TTL,
) -> str:
    """Compute a timezone-preserving freshness deadline for a new snapshot."""
    generated = _parse_timestamp(generated_at, "generated_at")
    if validity <= timedelta(0):
        raise ValueError("snapshot validity must be positive")
    return (generated + validity).isoformat(timespec="seconds")


def stale_after_for_task(generated_at: str, task_id: str) -> str:
    """Compute freshness for a live task while keeping history independently readable."""
    validity = (
        HOME_SNAPSHOT_INTRADAY_TTL
        if str(task_id or "").strip().lower() in _INTRADAY_TASK_IDS
        else HOME_SNAPSHOT_CLOSE_TTL
    )
    return stale_after_for(generated_at, validity)


def _normalize_timestamp_for_write(
    value: str,
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError(f"{name} must be a timezone-aware ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a timezone-aware ISO 8601 timestamp") from exc
    return to_shanghai(parsed).isoformat(timespec="seconds")


def _normalize_snapshot_for_write(
    snapshot: HomeDashboardSnapshot,
) -> HomeDashboardSnapshot:
    """Normalize legacy timestamp offsets only when producing runtime JSON."""
    messages = tuple(
        replace(
            message,
            published_at=_normalize_timestamp_for_write(
                message.published_at,
                "message.published_at",
            ),
        )
        for message in snapshot.messages
        if str(message.published_at or "").strip()
    )
    market_context = snapshot.market_context
    if market_context is not None:
        cross_market = tuple(
            replace(
                item,
                source_published_at=_normalize_timestamp_for_write(
                    item.source_published_at,
                    "cross_market.source_published_at",
                ),
            )
            for item in market_context.cross_market
            if str(item.source_published_at or "").strip()
        )
        market_context = replace(market_context, cross_market=cross_market)
    return replace(
        snapshot,
        generated_at=_normalize_timestamp_for_write(
            snapshot.generated_at,
            "generated_at",
        ),
        stale_after=_normalize_timestamp_for_write(
            snapshot.stale_after,
            "stale_after",
            allow_empty=True,
        ),
        messages=messages,
        market_context=market_context,
    )


def _normalize_index_for_write(index: HomeSnapshotIndex) -> HomeSnapshotIndex:
    return replace(
        index,
        generated_at=_normalize_timestamp_for_write(
            index.generated_at,
            "index.generated_at",
        ),
        stale_after=_normalize_timestamp_for_write(
            index.stale_after,
            "index.stale_after",
        ),
        days=tuple(
            HomeSnapshotDay(
                date=day.date,
                snapshot=_normalize_snapshot_for_write(day.snapshot),
            )
            for day in index.days
        ),
    )


def _snapshot_from_dict(payload: object) -> HomeDashboardSnapshot:
    mapping = _mapping(payload, "snapshot")
    _require_keys(
        mapping,
        {
            "schema_version",
            "generated_at",
            "selected_date",
            "available_dates",
            "candidates",
            "summaries",
            "source",
            "coldstart",
        },
        "snapshot",
        optional={
            "debate",
            "debates",
            "stale_after",
            "message_status",
            "messages",
            "market_context",
            "recommendation_gate",
            "phases",
            "universe",
            "variant_universe",
            "variants",
        },
    )
    if "debates" in mapping:
        raw_debates = mapping["debates"]
        debates = (
            ()
            if raw_debates is None
            else tuple(
                _debate_from_dict(item) for item in _list(raw_debates, "debates")
            )
        )
    elif "debate" in mapping and mapping["debate"] is not None:
        debates = (_debate_from_dict(mapping["debate"]),)
    else:
        debates = ()
    return HomeDashboardSnapshot(
        schema_version=_text(mapping["schema_version"], "schema_version"),
        generated_at=_text(mapping["generated_at"], "generated_at"),
        selected_date=_text(mapping["selected_date"], "selected_date"),
        available_dates=_text_tuple(mapping["available_dates"], "available_dates"),
        candidates=tuple(
            _candidate_from_dict(item)
            for item in _list(mapping["candidates"], "candidates")
        ),
        debates=debates,
        summaries=_text_tuple(mapping["summaries"], "summaries"),
        source=_source_from_dict(mapping["source"]),
        coldstart=_coldstart_from_dict(mapping["coldstart"]),
        stale_after=_optional_text(mapping.get("stale_after"), "stale_after"),
        message_status=_optional_text(mapping.get("message_status"), "message_status")
        or "未产出",
        messages=tuple(
            _message_from_dict(item)
            for item in _list(mapping.get("messages", ()), "messages")
        ),
        market_context=(
            None
            if mapping.get("market_context") is None
            else _market_context_from_dict(mapping["market_context"])
        ),
        recommendation_gate=(
            None
            if mapping.get("recommendation_gate") is None
            else _recommendation_gate_from_dict(mapping["recommendation_gate"])
        ),
        phases=tuple(
            _phase_from_dict(item)
            for item in _list(mapping.get("phases", ()), "phases")
        ),
        universe=_universe_from_dict(mapping.get("universe", {})),
        variant_universe=_variant_universe_from_dict(
            mapping.get("variant_universe", {})
        ),
        variants=tuple(
            _variant_from_dict(item)
            for item in _list(mapping.get("variants", ()), "variants")
        ),
    )


def _index_from_dict(payload: object) -> HomeSnapshotIndex:
    mapping = _mapping(payload, "snapshot index")
    _require_keys(
        mapping,
        {"schema_version", "generated_at", "stale_after", "selected_date", "days"},
        "snapshot index",
    )
    return HomeSnapshotIndex(
        schema_version=_text(mapping["schema_version"], "schema_version"),
        generated_at=_text(mapping["generated_at"], "generated_at"),
        stale_after=_text(mapping["stale_after"], "stale_after"),
        selected_date=_text(mapping["selected_date"], "selected_date"),
        days=tuple(_day_from_dict(item) for item in _list(mapping["days"], "days")),
    )


def _day_from_dict(payload: object) -> HomeSnapshotDay:
    mapping = _mapping(payload, "day")
    _require_keys(mapping, {"date", "snapshot"}, "day")
    return HomeSnapshotDay(
        date=_text(mapping["date"], "day.date"),
        snapshot=_snapshot_from_dict(mapping["snapshot"]),
    )


def _variant_from_dict(payload: object) -> HomeSnapshotVariant:
    mapping = _mapping(payload, "variant")
    return HomeSnapshotVariant(
        variant_id=_text(mapping.get("variant_id", ""), "variant.variant_id"),
        label=_text(mapping.get("label", ""), "variant.label"),
        initial_cash=float(mapping.get("initial_cash", 0.0) or 0.0),
        cash=float(mapping.get("cash", 0.0) or 0.0),
        final_equity=float(mapping.get("final_equity", 0.0) or 0.0),
        total_pnl=float(mapping.get("total_pnl", 0.0) or 0.0),
        return_pct=float(mapping.get("return_pct", 0.0) or 0.0),
        filled_orders=int(mapping.get("filled_orders", 0) or 0),
        rejected_orders=int(mapping.get("rejected_orders", 0) or 0),
        start_date=_text(mapping.get("start_date", ""), "variant.start_date"),
        end_date=_text(mapping.get("end_date", ""), "variant.end_date"),
        data_mode=_text(mapping.get("data_mode", ""), "variant.data_mode"),
        rank=int(mapping.get("rank", 0) or 0),
        strategy=_text(mapping.get("strategy", ""), "variant.strategy"),
        holdings=tuple(
            HomeSnapshotHolding(
                symbol=_text(item.get("symbol", ""), "holding.symbol"),
                quantity=int(item.get("quantity", 0) or 0),
                average_price=float(item.get("average_price", 0.0) or 0.0),
                last_price=float(item.get("last_price", 0.0) or 0.0),
                market_value=float(item.get("market_value", 0.0) or 0.0),
                unrealized_pnl=float(item.get("unrealized_pnl", 0.0) or 0.0),
                name=_optional_text(item.get("name"), "holding.name"),
            )
            for item in mapping.get("holdings", ())
            if isinstance(item, dict)
        ),
        previous_holdings=(
            None
            if "previous_holdings" not in mapping
            or mapping.get("previous_holdings") is None
            else tuple(
                HomeSnapshotHolding(
                    symbol=_text(item.get("symbol", ""), "previous_holding.symbol"),
                    quantity=int(item.get("quantity", 0) or 0),
                    average_price=float(item.get("average_price", 0.0) or 0.0),
                    last_price=float(item.get("last_price", 0.0) or 0.0),
                    market_value=float(item.get("market_value", 0.0) or 0.0),
                    unrealized_pnl=float(item.get("unrealized_pnl", 0.0) or 0.0),
                    name=_optional_text(item.get("name"), "previous_holding.name"),
                )
                for item in _list(mapping["previous_holdings"], "previous_holdings")
                if isinstance(item, dict)
            )
        ),
        recent_actions=_text_tuple(
            mapping.get("recent_actions", ()), "variant.recent_actions"
        ),
        hard_rules=_text_tuple(mapping.get("hard_rules", ()), "variant.hard_rules"),
    )


def _candidate_from_dict(payload: object) -> HomeSnapshotCandidate:
    mapping = _mapping(payload, "candidate")
    _require_keys(
        mapping,
        {"symbol", "display_name", "score", "research_status", "next_step", "context"},
        "candidate",
        optional={
            "deterministic_reasons",
            "strategies",
            "score_breakdown",
            "evidence_status",
            "technical_metrics",
            "data_source",
            "data_fetched_at",
            "data_timestamp_source",
            "freshness",
        },
    )
    return HomeSnapshotCandidate(
        symbol=_text(mapping["symbol"], "candidate.symbol"),
        display_name=_text(mapping["display_name"], "candidate.display_name"),
        score=_number(mapping["score"], "candidate.score"),
        research_status=_text(mapping["research_status"], "candidate.research_status"),
        next_step=_text(mapping["next_step"], "candidate.next_step"),
        context=_text(mapping["context"], "candidate.context"),
        deterministic_reasons=_text_tuple(
            mapping.get("deterministic_reasons", ()),
            "candidate.deterministic_reasons",
        ),
        strategies=_text_tuple(
            mapping.get("strategies", ()),
            "candidate.strategies",
        ),
        score_breakdown=_text_tuple(
            mapping.get("score_breakdown", ()),
            "candidate.score_breakdown",
        ),
        evidence_status=_optional_text(
            mapping.get("evidence_status"), "candidate.evidence_status"
        )
        or "证据不足",
        technical_metrics=tuple(
            _technical_metric_from_dict(item)
            for item in _list(
                mapping.get("technical_metrics", ()), "candidate.technical_metrics"
            )
        ),
        data_source=_optional_text(mapping.get("data_source"), "candidate.data_source"),
        data_fetched_at=_optional_text(
            mapping.get("data_fetched_at"), "candidate.data_fetched_at"
        ),
        data_timestamp_source=_optional_text(
            mapping.get("data_timestamp_source"), "candidate.data_timestamp_source"
        ),
        freshness=_optional_text(mapping.get("freshness"), "candidate.freshness"),
    )


def _technical_metric_from_dict(payload: object) -> HomeSnapshotTechnicalMetric:
    mapping = _mapping(payload, "technical metric")
    _require_keys(mapping, {"key", "label", "value"}, "technical metric")
    return HomeSnapshotTechnicalMetric(
        key=_text(mapping["key"], "technical metric.key"),
        label=_text(mapping["label"], "technical metric.label"),
        value=_text(mapping["value"], "technical metric.value"),
    )


def _message_from_dict(payload: object) -> HomeSnapshotMessage:
    mapping = _mapping(payload, "message")
    _require_keys(
        mapping,
        {"title", "summary", "impact", "category", "source", "published_at"},
        "message",
        optional={
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
    return HomeSnapshotMessage(
        title=_text(mapping["title"], "message.title"),
        summary=_text(mapping["summary"], "message.summary"),
        impact=_text(mapping["impact"], "message.impact"),
        category=_text(mapping["category"], "message.category"),
        source=_text(mapping["source"], "message.source"),
        published_at=_text(mapping["published_at"], "message.published_at"),
        url=_optional_text(mapping.get("url"), "message.url"),
        source_region=_optional_text(
            mapping.get("source_region"), "message.source_region"
        )
        or "mixed",
        source_quality=_optional_text(
            mapping.get("source_quality"), "message.source_quality"
        ),
        event_type=_optional_text(mapping.get("event_type"), "message.event_type"),
        affected_sectors=_text_tuple(
            mapping.get("affected_sectors", []), "message.affected_sectors"
        ),
        affected_symbols=_text_tuple(
            mapping.get("affected_symbols", []), "message.affected_symbols"
        ),
        transmission_hypothesis=_optional_text(
            mapping.get("transmission_hypothesis"), "message.transmission_hypothesis"
        ),
        supporting_evidence=_text_tuple(
            mapping.get("supporting_evidence", []), "message.supporting_evidence"
        ),
        source_url=_optional_text(mapping.get("source_url"), "message.source_url"),
        verification=_optional_text(
            mapping.get("verification"), "message.verification"
        ),
        transmission_path=_text_tuple(
            mapping.get("transmission_path", []), "message.transmission_path"
        ),
        validation_signals=_text_tuple(
            mapping.get("validation_signals", []), "message.validation_signals"
        ),
        invalidation_signals=_text_tuple(
            mapping.get("invalidation_signals", []), "message.invalidation_signals"
        ),
    )


def _market_context_from_dict(payload: object) -> HomeSnapshotMarketContext:
    mapping = _mapping(payload, "market_context")
    _require_keys(
        mapping,
        {"status", "overview", "summary_lines", "cross_market", "warnings"},
        "market_context",
    )
    cross_market = tuple(
        _cross_market_from_dict(item)
        for item in _list(mapping["cross_market"], "market_context.cross_market")
    )
    return HomeSnapshotMarketContext(
        status=_text(mapping["status"], "market_context.status"),
        overview=_text(mapping["overview"], "market_context.overview"),
        summary_lines=_text_tuple(
            mapping["summary_lines"], "market_context.summary_lines"
        ),
        cross_market=cross_market,
        warnings=_text_tuple(mapping["warnings"], "market_context.warnings"),
    )


def _cross_market_from_dict(payload: object) -> HomeSnapshotCrossMarket:
    mapping = _mapping(payload, "cross_market")
    _require_keys(
        mapping,
        {
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
        },
        "cross_market",
    )
    return HomeSnapshotCrossMarket(
        rule_id=_text(mapping["rule_id"], "cross_market.rule_id"),
        theme=_text(mapping["theme"], "cross_market.theme"),
        strength=_text(mapping["strength"], "cross_market.strength"),
        action=_text(mapping["action"], "cross_market.action"),
        source_title=_text(mapping["source_title"], "cross_market.source_title"),
        source_region=_text(mapping["source_region"], "cross_market.source_region"),
        source_published_at=_text(
            mapping["source_published_at"], "cross_market.source_published_at"
        ),
        affected_sectors=_text_tuple(
            mapping["affected_sectors"], "cross_market.affected_sectors"
        ),
        transmission_path=_text_tuple(
            mapping["transmission_path"], "cross_market.transmission_path"
        ),
        validation_signals=_text_tuple(
            mapping["validation_signals"], "cross_market.validation_signals"
        ),
        invalidation_signals=_text_tuple(
            mapping["invalidation_signals"], "cross_market.invalidation_signals"
        ),
        summary=_text(mapping["summary"], "cross_market.summary"),
    )


def _debate_from_dict(payload: object) -> HomeSnapshotDebate:
    mapping = _mapping(payload, "debate")
    _require_keys(
        mapping,
        {
            "symbol",
            "display_name",
            "conclusion",
            "primary_risk_gate",
            "next_trigger",
            "active_roles",
        },
        "debate",
        optional={
            "round_count",
            "bull_count",
            "bear_count",
            "neutral_count",
            "process_summary",
            "round_summaries",
            "viewpoint_buckets",
            "disagreement_points",
            "uncertainty_points",
        },
    )
    return HomeSnapshotDebate(
        symbol=_text(mapping["symbol"], "debate.symbol"),
        display_name=_text(mapping["display_name"], "debate.display_name"),
        conclusion=_text(mapping["conclusion"], "debate.conclusion"),
        primary_risk_gate=_text(
            mapping["primary_risk_gate"], "debate.primary_risk_gate"
        ),
        next_trigger=_text(mapping["next_trigger"], "debate.next_trigger"),
        active_roles=_text_tuple(mapping["active_roles"], "debate.active_roles"),
        round_count=_integer(mapping.get("round_count", 0), "debate.round_count"),
        bull_count=_integer(mapping.get("bull_count", 0), "debate.bull_count"),
        bear_count=_integer(mapping.get("bear_count", 0), "debate.bear_count"),
        neutral_count=_integer(mapping.get("neutral_count", 0), "debate.neutral_count"),
        process_summary=_optional_text(
            mapping.get("process_summary"), "debate.process_summary"
        ),
        round_summaries=_text_tuple(
            mapping.get("round_summaries", []), "debate.round_summaries"
        ),
        viewpoint_buckets={
            str(bucket): _text_tuple(points, f"debate.viewpoint_buckets.{bucket}")
            for bucket, points in _mapping(
                mapping.get("viewpoint_buckets", {}), "debate.viewpoint_buckets"
            ).items()
        },
        disagreement_points=_text_tuple(
            mapping.get("disagreement_points", []), "debate.disagreement_points"
        ),
        uncertainty_points=_text_tuple(
            mapping.get("uncertainty_points", []), "debate.uncertainty_points"
        ),
    )


def _source_from_dict(payload: object) -> HomeSnapshotSource:
    mapping = _mapping(payload, "source")
    _require_keys(
        mapping,
        {"effective", "latest_trade_date", "lag_days", "status"},
        "source",
    )
    return HomeSnapshotSource(
        effective=_text(mapping["effective"], "source.effective"),
        latest_trade_date=_text(
            mapping["latest_trade_date"], "source.latest_trade_date"
        ),
        lag_days=_integer(mapping["lag_days"], "source.lag_days"),
        status=_text(mapping["status"], "source.status"),
    )


def _coldstart_from_dict(payload: object) -> HomeSnapshotColdstart:
    mapping = _mapping(payload, "coldstart")
    _require_keys(mapping, {"status", "detail"}, "coldstart")
    return HomeSnapshotColdstart(
        status=_text(mapping["status"], "coldstart.status"),
        detail=_text(mapping["detail"], "coldstart.detail"),
    )


def _recommendation_gate_from_dict(
    payload: object,
) -> HomeSnapshotRecommendationGate:
    mapping = _mapping(payload, "recommendation_gate")
    _require_keys(
        mapping,
        {"recommendation_allowed", "status", "reasons"},
        "recommendation_gate",
    )
    return HomeSnapshotRecommendationGate(
        recommendation_allowed=bool(mapping["recommendation_allowed"]),
        status=_text(mapping["status"], "recommendation_gate.status"),
        reasons=_text_tuple(mapping["reasons"], "recommendation_gate.reasons"),
    )


def _phase_from_dict(payload: object) -> HomeSnapshotPhase:
    mapping = _mapping(payload, "phase")
    _require_keys(
        mapping,
        {
            "task_id",
            "label",
            "status",
            "candidate_count",
            "unique_symbols",
            "overlap_symbols",
        },
        "phase",
        optional={"updated_at"},
    )
    return HomeSnapshotPhase(
        task_id=_text(mapping["task_id"], "phase.task_id"),
        label=_text(mapping["label"], "phase.label"),
        status=_text(mapping["status"], "phase.status"),
        candidate_count=_integer(mapping["candidate_count"], "phase.candidate_count"),
        unique_symbols=_integer(mapping["unique_symbols"], "phase.unique_symbols"),
        overlap_symbols=_integer(mapping["overlap_symbols"], "phase.overlap_symbols"),
        updated_at=_optional_text(mapping.get("updated_at"), "phase.updated_at"),
    )


def _universe_from_dict(payload: object) -> HomeSnapshotUniverse:
    if payload in (None, {}):
        return HomeSnapshotUniverse()
    mapping = _mapping(payload, "universe")
    return HomeSnapshotUniverse(
        total=_integer(mapping.get("total", 0), "universe.total"),
        resolved=_integer(mapping.get("resolved", 0), "universe.resolved"),
        screened=_integer(mapping.get("screened", 0), "universe.screened"),
        final=_integer(mapping.get("final", 0), "universe.final"),
        max_universe=_integer(mapping.get("max_universe", 0), "universe.max_universe"),
        source=_optional_text(mapping.get("source"), "universe.source"),
        batch_active=bool(mapping.get("batch_active", False)),
        batch_id=_optional_text(mapping.get("batch_id"), "universe.batch_id"),
        batch_size=_integer(mapping.get("batch_size", 0), "universe.batch_size"),
        cycle_id=_integer(mapping.get("cycle_id", 0), "universe.cycle_id"),
        coverage_pct=float(mapping.get("coverage_pct", 0.0) or 0.0),
        last_error=_optional_text(mapping.get("last_error"), "universe.last_error"),
    )


def _variant_universe_from_dict(payload: object) -> HomeSnapshotVariantUniverse:
    if payload in (None, {}):
        return HomeSnapshotVariantUniverse()
    mapping = _mapping(payload, "variant_universe")
    symbol_count = _integer(
        mapping.get("symbol_count", 0), "variant_universe.symbol_count"
    )
    if symbol_count < 0:
        raise ValueError("variant_universe.symbol_count must not be negative")
    coverage_pct = _number(
        mapping.get("coverage_pct", 0.0), "variant_universe.coverage_pct"
    )
    if not 0.0 <= coverage_pct <= 100.0:
        raise ValueError("variant_universe.coverage_pct must be between 0 and 100")
    return HomeSnapshotVariantUniverse(
        symbol_count=symbol_count,
        board_scope=_optional_text(mapping.get("board_scope"), "variant_universe.board_scope"),
        excluded=_text_tuple(mapping.get("excluded", ()), "variant_universe.excluded"),
        latest_trade_date=_optional_text(
            mapping.get("latest_trade_date"), "variant_universe.latest_trade_date"
        ),
        coverage_pct=coverage_pct,
        sources=_text_tuple(mapping.get("sources", ()), "variant_universe.sources"),
    )


def _validate_snapshot(snapshot: HomeDashboardSnapshot) -> None:
    if snapshot.schema_version != HOME_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported home snapshot schema version")
    if not snapshot.generated_at.strip() or not snapshot.selected_date.strip():
        raise ValueError("home snapshot requires generated_at and selected_date")
    generated_at = _parse_timestamp(snapshot.generated_at, "generated_at")
    if snapshot.stale_after:
        stale_after = _parse_timestamp(snapshot.stale_after, "stale_after")
        if stale_after < generated_at:
            raise ValueError("stale_after must not precede generated_at")
    _validate_limit(
        snapshot.available_dates, MAX_HOME_SNAPSHOT_DATES, "available_dates"
    )
    _validate_limit(snapshot.candidates, MAX_HOME_SNAPSHOT_CANDIDATES, "candidates")
    _validate_limit(snapshot.debates, MAX_HOME_SNAPSHOT_DEBATES, "debates")
    _validate_limit(snapshot.summaries, MAX_HOME_SNAPSHOT_SUMMARIES, "summaries")
    _validate_limit(snapshot.messages, MAX_HOME_SNAPSHOT_MESSAGES, "messages")
    _validate_limit(snapshot.phases, 3, "phases")
    _validate_limit(snapshot.variants, MAX_HOME_SNAPSHOT_VARIANTS, "variants")
    if any(not isinstance(value, HomeSnapshotPhase) for value in snapshot.phases):
        raise ValueError("phases must contain HomeSnapshotPhase values")
    if not isinstance(snapshot.universe, HomeSnapshotUniverse):
        raise ValueError("universe must be a HomeSnapshotUniverse value")
    if not isinstance(snapshot.variant_universe, HomeSnapshotVariantUniverse):
        raise ValueError("variant_universe must be a HomeSnapshotVariantUniverse value")
    if snapshot.variant_universe.symbol_count < 0:
        raise ValueError("variant_universe.symbol_count must not be negative")
    if not 0.0 <= snapshot.variant_universe.coverage_pct <= 100.0:
        raise ValueError("variant_universe.coverage_pct must be between 0 and 100")
    if snapshot.variant_universe.latest_trade_date:
        _validate_date(
            snapshot.variant_universe.latest_trade_date,
            "variant_universe.latest_trade_date",
        )
    if not all(isinstance(value, str) for value in snapshot.variant_universe.excluded):
        raise ValueError("variant_universe.excluded must contain text")
    if not all(isinstance(value, str) for value in snapshot.variant_universe.sources):
        raise ValueError("variant_universe.sources must contain text")
    if not all(isinstance(value, HomeSnapshotVariant) for value in snapshot.variants):
        raise ValueError("variants must contain HomeSnapshotVariant values")
    if any(value.initial_cash != 100_000.0 for value in snapshot.variants):
        raise ValueError("variant accounts must start with 100000 yuan")
    _validate_date(snapshot.selected_date, "selected_date")
    if snapshot.selected_date not in snapshot.available_dates:
        raise ValueError("selected_date must exist in available_dates")
    if not all(isinstance(value, str) for value in snapshot.available_dates):
        raise ValueError("available_dates must contain text")
    if not all(_is_valid_date(value) for value in snapshot.available_dates):
        raise ValueError("available_dates must contain YYYY-MM-DD dates")
    if not all(
        isinstance(value, HomeSnapshotCandidate) for value in snapshot.candidates
    ):
        raise ValueError("candidates must contain HomeSnapshotCandidate values")
    candidate_symbols = tuple(value.symbol for value in snapshot.candidates)
    if len(set(candidate_symbols)) != len(candidate_symbols):
        raise ValueError("candidates must not contain duplicate symbols")
    if not all(isinstance(value, HomeSnapshotDebate) for value in snapshot.debates):
        raise ValueError("debates must contain HomeSnapshotDebate values")
    for debate in snapshot.debates:
        vote_counts = (
            debate.bull_count,
            debate.bear_count,
            debate.neutral_count,
        )
        if debate.round_count not in (2, 3):
            raise ValueError("published debates must contain two or three rounds")
        if any(count < 0 for count in vote_counts):
            raise ValueError("debate vote counts must not be negative")
        if sum(vote_counts) != len(debate.active_roles) or len(debate.active_roles) < 2:
            raise ValueError("debate vote counts must match active roles")
    debate_symbols = tuple(value.symbol for value in snapshot.debates)
    if len(set(debate_symbols)) != len(debate_symbols):
        raise ValueError("debates must not contain duplicate symbols")
    candidate_symbol_set = set(candidate_symbols)
    missing_debate_symbols = set(debate_symbols) - candidate_symbol_set
    if missing_debate_symbols:
        raise ValueError("debates symbols must belong to candidates")
    if not all(isinstance(value, str) for value in snapshot.summaries):
        raise ValueError("summaries must contain text")
    if not isinstance(snapshot.message_status, str):
        raise ValueError("message_status must contain text")
    if not all(isinstance(value, HomeSnapshotMessage) for value in snapshot.messages):
        raise ValueError("messages must contain HomeSnapshotMessage values")
    if snapshot.market_context is not None:
        if not isinstance(snapshot.market_context, HomeSnapshotMarketContext):
            raise ValueError("market_context must be a HomeSnapshotMarketContext value")
        _validate_limit(
            snapshot.market_context.summary_lines,
            MAX_HOME_SNAPSHOT_MARKET_LINES,
            "market_context.summary_lines",
        )
        _validate_limit(
            snapshot.market_context.cross_market,
            MAX_HOME_SNAPSHOT_CROSS_MARKET,
            "market_context.cross_market",
        )
        _validate_limit(
            snapshot.market_context.warnings,
            3,
            "market_context.warnings",
        )
        if not all(
            isinstance(value, HomeSnapshotCrossMarket)
            for value in snapshot.market_context.cross_market
        ):
            raise ValueError(
                "market_context.cross_market must contain "
                "HomeSnapshotCrossMarket values"
            )
    if not isinstance(snapshot.source, HomeSnapshotSource):
        raise ValueError("source must be a HomeSnapshotSource value")
    if not isinstance(snapshot.coldstart, HomeSnapshotColdstart):
        raise ValueError("coldstart must be a HomeSnapshotColdstart value")
    if not isinstance(snapshot.recommendation_gate, HomeSnapshotRecommendationGate):
        raise ValueError(
            "recommendation_gate must be a HomeSnapshotRecommendationGate value"
        )
    if len(set(snapshot.available_dates)) != len(snapshot.available_dates):
        raise ValueError("available_dates must not contain duplicates")


def _validate_index(index: HomeSnapshotIndex) -> None:
    if index.schema_version != HOME_SNAPSHOT_INDEX_SCHEMA_VERSION:
        raise ValueError("unsupported home snapshot index schema version")
    generated_at = _parse_timestamp(index.generated_at, "generated_at")
    stale_after = _parse_timestamp(index.stale_after, "stale_after")
    if stale_after < generated_at:
        raise ValueError("stale_after must not precede generated_at")
    _validate_limit(index.days, MAX_HOME_SNAPSHOT_INDEX_DAYS, "days")
    if not all(isinstance(day, HomeSnapshotDay) for day in index.days):
        raise ValueError("days must contain HomeSnapshotDay values")
    if len({day.date for day in index.days}) != len(index.days):
        raise ValueError("days must not contain duplicate dates")
    if index.selected_date:
        _validate_date(index.selected_date, "selected_date")
        if index.selected_date not in {day.date for day in index.days}:
            raise ValueError("selected_date must exist in days")


def _validate_date(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a date")
    try:
        parsed = CalendarDate.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")


def _is_valid_date(value: object) -> bool:
    try:
        _validate_date(value, "date")
    except (TypeError, ValueError):
        return False
    return True


def _validate_limit(values: tuple[object, ...], limit: int, field: str) -> None:
    if len(values) > limit:
        raise ValueError(f"{field} must contain at most {limit} items")


def _mapping(payload: object, name: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be an object")
    return payload


def _require_keys(
    mapping: dict[str, object],
    expected: set[str],
    name: str,
    *,
    optional: set[str] | None = None,
) -> None:
    allowed = expected | (optional or set())
    if not expected.issubset(mapping) or not set(mapping).issubset(allowed):
        raise ValueError(f"{name} schema does not match")


def _list(payload: object, name: str) -> list[object]:
    if not isinstance(payload, list):
        raise ValueError(f"{name} must be a list")
    return payload


def _text_tuple(payload: object, name: str) -> tuple[str, ...]:
    return tuple(_text(item, name) for item in _list(payload, name))


def _text(payload: object, name: str) -> str:
    if not isinstance(payload, str):
        raise ValueError(f"{name} must be text")
    return payload


def _optional_text(payload: object, name: str) -> str:
    if payload is None:
        return ""
    return _text(payload, name)


def _read_json_payload(path: str | Path) -> object | None:
    try:
        raw_bytes = Path(path).read_bytes()
    except OSError:
        return None
    if len(raw_bytes) > MAX_HOME_SNAPSHOT_BYTES:
        return None
    try:
        return json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _parse_timestamp(value: str, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a timezone-aware ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a timezone-aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return parsed


def _coerce_timestamp(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return value


def _number(payload: object, name: str) -> float:
    if isinstance(payload, bool) or not isinstance(payload, (int, float)):
        raise ValueError(f"{name} must be a number")
    value = float(payload)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _integer(payload: object, name: str) -> int:
    if isinstance(payload, bool) or not isinstance(payload, int):
        raise ValueError(f"{name} must be an integer")
    return payload
