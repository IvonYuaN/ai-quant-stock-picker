"""Deterministic expansion of news events into full-market watch candidates.

The module is deliberately independent of the technical screener.  It accepts
the current universe metadata and expands an event through direct company,
industry, supply-chain, price/supply, product, geopolitical, and cross-market
links.  It produces observation records only; it never changes a score or
creates an order instruction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from aqsp.news.catalysts import CatalystEvent
from aqsp.news.entity_graph import DEFAULT_ENTITY_GRAPH, EntityGraph

WatchRelation = Literal[
    "company",
    "industry",
    "supply_chain",
    "price_supply",
    "product",
    "geopolitical",
    "cross_market",
]


@dataclass(frozen=True)
class NewsUniverseInstrument:
    """Minimal point-in-time universe metadata needed for news expansion."""

    symbol: str
    name: str = ""
    sectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsWatchCandidate:
    """A source-backed observation candidate derived from one catalyst event."""

    symbol: str
    name: str
    relation: WatchRelation
    event_title: str
    summary: str
    source: str
    source_url: str
    source_quality_label: str
    source_quality_score: int
    verification: str
    published_at: str
    impact: str
    priority_score: int
    confidence: float
    affected_sectors: tuple[str, ...]
    transmission_path: tuple[str, ...]
    transmission_hypothesis: str
    supporting_evidence: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    status: str = "消息产业链观察"
    supporting_event_count: int = 0
    contradicting_event_count: int = 0
    evidence_stack_summary: str = ""
    impact_direction: Literal["positive", "negative", "mixed", "neutral"] = "neutral"


def build_current_news_universe(
    current_symbols: Iterable[object],
    metadata: Iterable[NewsUniverseInstrument | Mapping[str, object]] = (),
    *,
    graph: EntityGraph = DEFAULT_ENTITY_GRAPH,
) -> tuple[NewsUniverseInstrument, ...]:
    """Combine the current live symbol pool with same-run metadata.

    Runtime quote/daily batches are intentionally smaller than the current
    universe. This helper keeps every symbol resolved by the current run while
    overlaying only metadata supplied by that run. A symbol without metadata
    remains eligible for direct symbol links, but is not assigned an industry
    by inference or historical lookup.
    """
    normalized_metadata = _normalize_universe(metadata, graph=graph)
    metadata_by_symbol = {item.symbol: item for item in normalized_metadata}
    result: list[NewsUniverseInstrument] = []
    seen: set[str] = set()
    for raw_symbol in current_symbols:
        symbol = _normalize_symbol(raw_symbol)
        if not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
            continue
        result.append(
            metadata_by_symbol.get(symbol, NewsUniverseInstrument(symbol=symbol))
        )
        seen.add(symbol)
    return tuple(result)


def discover_watch_candidates(
    events: Iterable[CatalystEvent],
    universe: Iterable[NewsUniverseInstrument | Mapping[str, object]],
    *,
    graph: EntityGraph = DEFAULT_ENTITY_GRAPH,
    max_candidates: int = 0,
    min_confidence: float = 0.0,
) -> tuple[NewsWatchCandidate, ...]:
    """Expand events against the whole supplied universe in stable order.

    ``max_candidates=0`` means no cap.  The input universe must be the current
    point-in-time universe; this function does not fetch data or use history.
    A candidate is emitted once per symbol, keeping the highest-priority event
    while merging all relevant event evidence deterministically.
    """
    instruments = _normalize_universe(universe, graph=graph)
    ranked_events = sorted(
        (event for event in events if float(event.confidence) >= float(min_confidence)),
        key=_event_key,
        reverse=True,
    )
    by_symbol: dict[str, NewsWatchCandidate] = {}
    for event in ranked_events:
        event_sectors = graph.canonicalize_sector_labels(event.affected_sectors)
        event_symbols = _event_symbols(event)
        for instrument in instruments:
            relation = _relation_for_event(
                event, instrument, event_sectors, event_symbols
            )
            if relation is None:
                continue
            candidate = _candidate_from_event(
                event, instrument, relation, event_sectors
            )
            existing = by_symbol.get(instrument.symbol)
            by_symbol[instrument.symbol] = (
                _merge_candidates(existing, candidate)
                if existing is not None
                else candidate
            )
    candidates = sorted(by_symbol.values(), key=_candidate_key, reverse=True)
    if max_candidates > 0:
        candidates = candidates[: int(max_candidates)]
    return tuple(candidates)


def _normalize_universe(
    universe: Iterable[NewsUniverseInstrument | Mapping[str, object]],
    *,
    graph: EntityGraph,
) -> tuple[NewsUniverseInstrument, ...]:
    result: list[NewsUniverseInstrument] = []
    seen: set[str] = set()
    for item in universe:
        if isinstance(item, NewsUniverseInstrument):
            instrument = item
        elif isinstance(item, Mapping):
            symbol = _first_value(item, "symbol", "代码", "证券代码", "ts_code")
            name = _first_value(item, "name", "名称", "证券名称")
            labels = _first_values(
                item,
                "sectors",
                "sector",
                "industry",
                "行业",
                "板块",
                "概念",
                "themes",
            )
            instrument = NewsUniverseInstrument(
                symbol=symbol,
                name=name,
                sectors=graph.canonicalize_sector_labels(labels),
            )
        else:
            continue
        symbol = _normalize_symbol(instrument.symbol)
        if not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
            continue
        sectors = graph.canonicalize_sector_labels(instrument.sectors)
        result.append(
            NewsUniverseInstrument(symbol, str(instrument.name or "").strip(), sectors)
        )
        seen.add(symbol)
    return tuple(result)


def _first_value(item: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_values(item: Mapping[str, object], *keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        value = item.get(key)
        if isinstance(value, (list, tuple, set, frozenset)):
            values.extend(str(part) for part in value)
        elif value is not None:
            values.append(str(value))
    return tuple(values)


def _event_symbols(event: CatalystEvent) -> frozenset[str]:
    values = {
        symbol
        for item in event.affected_symbols
        for symbol in _symbols_from_text(str(item))
    }
    if event.symbol:
        values.update(_symbols_from_text(str(event.symbol)))
    return frozenset(values)


def _symbols_from_text(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(?<!\d)\d{6}(?!\d)", value or "")))


def _normalize_symbol(value: object) -> str:
    symbols = _symbols_from_text(str(value or ""))
    return symbols[0] if symbols else ""


def _name_matches(event: CatalystEvent, instrument: NewsUniverseInstrument) -> bool:
    name = str(instrument.name or "").strip()
    if len(name) < 2:
        return False
    text = f"{event.title} {event.summary} {' '.join(event.supporting_evidence)}"
    normalized = re.sub(r"\s+", "", text).casefold()
    alias = re.sub(r"\s+", "", name).casefold()
    if re.fullmatch(r"[a-z0-9]+", alias):
        return (
            re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized)
            is not None
        )
    return alias in normalized


def _relation_for_event(
    event: CatalystEvent,
    instrument: NewsUniverseInstrument,
    event_sectors: tuple[str, ...],
    event_symbols: frozenset[str],
) -> WatchRelation | None:
    if instrument.symbol in event_symbols or _name_matches(event, instrument):
        return "company"
    if not set(event_sectors).intersection(instrument.sectors):
        return None
    category = str(event.category or "").casefold()
    title = f"{event.title} {event.summary}".casefold()
    if any(
        token in category or token in title
        for token in ("涨价", "缺货", "供需", "供给", "价格")
    ):
        return "price_supply"
    if any(
        token in category or token in title
        for token in ("新品", "发布", "产品", "量产", "商业化")
    ):
        return "product"
    if any(
        token in title for token in ("战争", "冲突", "制裁", "关税", "地缘", "红海")
    ):
        return "geopolitical"
    if any(
        token in title
        for token in ("美元", "美股", "纳斯达克", "标普", "美债", "原油", "黄金")
    ):
        return "cross_market"
    if event.transmission_path:
        return "supply_chain"
    return "industry"


def _candidate_from_event(
    event: CatalystEvent,
    instrument: NewsUniverseInstrument,
    relation: WatchRelation,
    event_sectors: tuple[str, ...],
) -> NewsWatchCandidate:
    summary = str(event.summary or event.inference or event.title).strip()
    score = int(event.weight) + min(4, int(event.source_quality_score))
    if relation == "company":
        score += 2
    return NewsWatchCandidate(
        symbol=instrument.symbol,
        name=instrument.name,
        relation=relation,
        event_title=str(event.title).strip(),
        summary=summary,
        source=str(event.source).strip(),
        source_url=str(event.url).strip(),
        source_quality_label=str(event.source_quality_label).strip(),
        source_quality_score=int(event.source_quality_score),
        verification=str(event.verification).strip(),
        published_at=str(event.published_at).strip(),
        impact=event.impact,
        priority_score=score,
        confidence=float(event.confidence),
        affected_sectors=event_sectors or instrument.sectors,
        transmission_path=tuple(event.transmission_path),
        transmission_hypothesis=str(event.transmission_hypothesis).strip(),
        supporting_evidence=tuple(event.supporting_evidence),
        validation_signals=tuple(event.validation_signals),
        invalidation_signals=tuple(event.invalidation_signals),
        supporting_event_count=int(event.impact == "positive"),
        contradicting_event_count=int(event.impact == "negative"),
        evidence_stack_summary=_evidence_stack_summary(
            int(event.impact == "positive"), int(event.impact == "negative")
        ),
        impact_direction=_impact_direction(
            int(event.impact == "positive"), int(event.impact == "negative")
        ),
    )


def _merge_candidates(
    existing: NewsWatchCandidate, incoming: NewsWatchCandidate
) -> NewsWatchCandidate:
    if incoming.priority_score > existing.priority_score:
        primary, secondary = incoming, existing
    else:
        primary, secondary = existing, incoming
    return NewsWatchCandidate(
        symbol=primary.symbol,
        name=primary.name or secondary.name,
        relation=primary.relation,
        event_title=primary.event_title,
        summary=primary.summary,
        source="、".join(_unique((primary.source, secondary.source))),
        source_url=primary.source_url or secondary.source_url,
        source_quality_label=primary.source_quality_label,
        source_quality_score=max(
            primary.source_quality_score, secondary.source_quality_score
        ),
        verification=primary.verification or secondary.verification,
        published_at=primary.published_at or secondary.published_at,
        impact=primary.impact,
        priority_score=max(primary.priority_score, secondary.priority_score),
        confidence=max(primary.confidence, secondary.confidence),
        affected_sectors=_unique(
            (*primary.affected_sectors, *secondary.affected_sectors)
        ),
        transmission_path=primary.transmission_path or secondary.transmission_path,
        transmission_hypothesis=primary.transmission_hypothesis
        or secondary.transmission_hypothesis,
        supporting_evidence=_unique(
            (*primary.supporting_evidence, *secondary.supporting_evidence)
        ),
        validation_signals=_unique(
            (*primary.validation_signals, *secondary.validation_signals)
        ),
        invalidation_signals=_unique(
            (*primary.invalidation_signals, *secondary.invalidation_signals)
        ),
        supporting_event_count=(
            existing.supporting_event_count + incoming.supporting_event_count
        ),
        contradicting_event_count=(
            existing.contradicting_event_count + incoming.contradicting_event_count
        ),
        evidence_stack_summary=_evidence_stack_summary(
            existing.supporting_event_count + incoming.supporting_event_count,
            existing.contradicting_event_count + incoming.contradicting_event_count,
        ),
        impact_direction=_impact_direction(
            existing.supporting_event_count + incoming.supporting_event_count,
            existing.contradicting_event_count + incoming.contradicting_event_count,
        ),
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return tuple(result)


def _event_key(event: CatalystEvent) -> tuple[int, int, float, str]:
    return (
        int(event.weight),
        int(event.source_quality_score),
        float(event.confidence),
        event.title,
    )


def _candidate_key(candidate: NewsWatchCandidate) -> tuple[int, float, str]:
    return (candidate.priority_score, candidate.confidence, candidate.symbol)


def _impact_direction(supporting: int, contradicting: int) -> str:
    if supporting and contradicting:
        return "mixed"
    if supporting:
        return "positive"
    if contradicting:
        return "negative"
    return "neutral"


def _evidence_stack_summary(supporting: int, contradicting: int) -> str:
    parts: list[str] = []
    if supporting:
        parts.append(f"支持 {supporting} 条")
    if contradicting:
        parts.append(f"反对 {contradicting} 条")
    return "｜".join(parts)


__all__ = [
    "build_current_news_universe",
    "NewsUniverseInstrument",
    "NewsWatchCandidate",
    "WatchRelation",
    "discover_watch_candidates",
]
