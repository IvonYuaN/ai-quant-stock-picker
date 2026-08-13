from __future__ import annotations

import math  # noqa: F401
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.core.types import PickResult
from aqsp.goal_switches import goal_switch_enabled
from aqsp.news.catalysts import CatalystEvent, CatalystReport
from aqsp.news.watch_candidates import (
    NewsUniverseInstrument,
    NewsWatchCandidate,
    discover_watch_candidates,
)
from aqsp.market_context_realtime import (  # noqa: F401
    REALTIME_CROSS_MARKET_INSTRUMENTS,
    RealtimeCrossMarketContext,
    RealtimeCrossMarketObservation,
    RealtimeCrossMarketPolicy,
    RealtimeCrossMarketProvenance,
    _DEFAULT_REALTIME_CROSS_MARKET_POLICY,
    build_realtime_cross_market_context,
)
from aqsp.market_context_cross_market import (  # noqa: F401
    CrossMarketImplication,
    CrossMarketImplicationRule,
    CrossMarketRuleRuntimeSummary,
    _CROSS_MARKET_RULES,
    cross_market_rule_runtime_lines,
    cross_market_rule_runtime_summary,
)
from aqsp.market_context_implications import (
    _as_text_tuple,
    _cross_market_implications,
    _cross_market_overview_from_implications,
    _event_age_minutes,
    _event_source_quality_label,
    _event_source_quality_score,
    _implication_priority_score,
    _parse_iso_datetime,
    _pick_chain_summary,
    _pick_implication_detail_lines,
)

_NORTHBOUND_STRONG_Z = 1.0
_MARGIN_STRONG_CHANGE = 0.03
_SENTIMENT_STRONG_Z = 1.0
_MACRO_STRONG_SCORE = 0.5
_NEWS_DIRECT_STRONG_SCORE = 3
_NEWS_DIRECT_MEDIUM_SCORE = 2
_NEWS_DIRECT_WEAK_SCORE = 1
# Realtime observations only become implication evidence after a meaningful
# move.  The thresholds are deliberately explicit so ordinary quote noise
# remains display-only and cannot create a theme by itself.
_DEFAULT_ACTIONABLE_NEWS_AGE_MINUTES = 12 * 60
_ACTIONABLE_NEWS_MIN_SOURCE_QUALITY = 2

# Deterministic issuer tags fill the sector gap of realtime quote sources.
# They only enable event-to-industry relevance; score changes remain governed
# by the existing evidence-quality and threshold gates below.
_SYMBOL_THEME_TAGS: dict[str, tuple[str, ...]] = {
    "603019": ("算力", "ai", "边缘计算"),
    "600879": ("商业航天", "卫星", "军工电子"),
    "603893": ("ai芯片", "边缘计算", "芯片"),
    "600276": ("创新药",),
    "600150": ("军工", "船舶"),
    "000034": ("算力", "ai", "云计算"),
    "000066": ("信创", "国产算力", "军工电子"),
    "000977": ("算力", "ai", "服务器"),
    "000938": ("算力", "ai", "云计算"),
    "688981": ("半导体", "芯片", "先进制程"),
    "000063": ("通信设备", "算力", "服务器"),
    "300604": ("半导体设备", "芯片", "设备"),
}


@dataclass(frozen=True)
class MarketContextArtifact:
    date: str
    generated_at: str
    source_status: str
    summary_lines: tuple[str, ...]
    cross_market_implications: tuple[CrossMarketImplication, ...] = ()
    cross_market_overview: str = ""
    warnings: tuple[str, ...] = ()
    catalyst_events: tuple[CatalystEvent, ...] = ()
    news_status: str = ""
    realtime_cross_market: RealtimeCrossMarketContext | None = None
    news_watch_candidates: tuple[NewsWatchCandidate, ...] = ()


@dataclass(frozen=True)
class PickMarketContext:
    symbol: str
    primary_theme: str
    linkage_basis: str
    primary_action: str
    primary_strength: str
    primary_source_quality_label: str
    primary_source_quality_score: int
    lead_window: str
    observation_window: str
    priority_score: int
    themes: tuple[str, ...]
    rule_ids: tuple[str, ...]
    first_order_targets: tuple[str, ...]
    second_order_targets: tuple[str, ...]
    pressure_targets: tuple[str, ...]
    execution_watchpoints: tuple[str, ...]
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    chain_summary: str
    support_event_count: int
    conflict_event_count: int
    evidence_stack_summary: str
    summary_lines: tuple[str, ...]
    affected_sectors: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    transmission_hypothesis: str = ""
    confidence: float = 0.0
    time_horizon: str = ""
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    source_regions: tuple[str, ...] = ()
    impact_direction: Literal["positive", "negative", "mixed", "neutral"] = "neutral"
    source_url: str = ""
    source_fetched_at: str = ""


def build_market_context_artifact(
    *,
    catalyst_report: CatalystReport | None,
    northbound_flow_5d_z: float = 0.0,
    margin_balance_change_5d: float = 0.0,
    sentiment_z: float = 0.0,
    macro_climate: str = "",
    macro_detail: str = "",
    enable_domestic_intelligence: bool | None = None,
    enable_global_intelligence: bool | None = None,
    max_actionable_news_age_minutes: int = _DEFAULT_ACTIONABLE_NEWS_AGE_MINUTES,
    realtime_cross_market: Mapping[str, object] | None = None,
    realtime_now: datetime | None = None,
    realtime_policy: RealtimeCrossMarketPolicy = _DEFAULT_REALTIME_CROSS_MARKET_POLICY,
    news_universe: Iterable[NewsUniverseInstrument | Mapping[str, object]] = (),
) -> MarketContextArtifact:
    normalized_news_universe = tuple(news_universe)
    domestic_enabled = (
        enable_domestic_intelligence
        if enable_domestic_intelligence is not None
        else goal_switch_enabled("domestic_market_intelligence", default=True)
    )
    global_enabled = (
        enable_global_intelligence
        if enable_global_intelligence is not None
        else goal_switch_enabled("global_market_intelligence", default=True)
    )
    lines: list[str] = []
    warnings: tuple[str, ...] = ()
    source_status = "not_loaded"
    global_events: list[CatalystEvent] = []
    domestic_events: list[CatalystEvent] = []
    symbol_events: list[CatalystEvent] = []
    cross_market_events: list[CatalystEvent] = []
    cross_market_implications: tuple[CrossMarketImplication, ...] = ()
    news_watch_candidates: tuple[NewsWatchCandidate, ...] = ()
    realtime_context = (
        build_realtime_cross_market_context(
            realtime_cross_market,
            now=realtime_now,
            policy=realtime_policy,
        )
        if realtime_cross_market is not None
        else None
    )

    if catalyst_report is not None:
        source_status = catalyst_report.source_status
        warnings = tuple(
            str(item) for item in catalyst_report.warnings if str(item).strip()
        )
        stale_cache_only = catalyst_report.news_status == "stale_cache"
        if stale_cache_only:
            # A bounded cache fallback is display context only.  Keeping it out
            # of the artifact's evidence collections prevents debate and
            # industry expansion from treating a failed live fetch as fresh.
            display_events = tuple(catalyst_report.events[:2])
            if display_events:
                lines.append(
                    "消息缓存展示（不参与判断）: "
                    + "；".join(_event_brief(event) for event in display_events)
                )
            actionable_events = ()
            gate_warnings = ("情报门禁: stale_cache 仅展示，不进入判断或产业链候选",)
        else:
            actionable_events, gate_warnings = _actionable_catalyst_events(
                catalyst_report.events,
                generated_at=catalyst_report.generated_at,
                max_age_minutes=max_actionable_news_age_minutes,
            )
        warnings = tuple(_dedupe_texts((*warnings, *gate_warnings)))
        if normalized_news_universe:
            news_watch_candidates = discover_watch_candidates(
                actionable_events,
                normalized_news_universe,
            )
        symbol_events = [
            event for event in actionable_events if event.symbol and domestic_enabled
        ]
        domestic_events = [
            event
            for event in actionable_events
            if not event.symbol
            and str(getattr(event, "source_region", "mixed") or "mixed")
            .strip()
            .casefold()
            == "domestic"
            and domestic_enabled
        ]
        global_events = [
            event
            for event in actionable_events
            if not event.symbol
            and str(getattr(event, "source_region", "mixed") or "mixed")
            .strip()
            .casefold()
            != "domestic"
            and global_enabled
        ]
        if symbol_events:
            lines.append(
                "个股催化: "
                + "；".join(_event_brief(event) for event in symbol_events[:2])
            )
        if global_events:
            lines.append(
                "全局雷达: "
                + "；".join(_event_brief(event) for event in global_events[:2])
            )
            source_quality_line = _source_quality_summary_line(global_events)
            if source_quality_line:
                lines.append(source_quality_line)
            global_risk_line = _global_risk_line(global_events)
            if global_risk_line:
                lines.append(global_risk_line)
        if domestic_events:
            lines.append(
                "国内雷达: "
                + "；".join(_event_brief(event) for event in domestic_events[:2])
            )
        cross_market_events = [*domestic_events, *global_events]
        if cross_market_events:
            cross_market_implications = _cross_market_implications(
                cross_market_events,
                generated_at=catalyst_report.generated_at,
                realtime_context=realtime_context,
            )
            lines.extend(
                implication.summary_line
                for implication in cross_market_implications[:3]
            )
        if catalyst_report.source_status != "ok":
            lines.append(
                f"消息状态: {_source_status_text(catalyst_report.source_status)}"
            )
        if not actionable_events:
            lines.append(
                f"消息结果: {_catalyst_result_status_text(catalyst_report.news_status, warnings)}"
            )
        warning_line = _market_context_warning_line(warnings)
        if warning_line:
            lines.append(warning_line)
        freshness_line = _event_freshness_line(
            events=tuple((*symbol_events, *domestic_events, *global_events)),
            generated_at=catalyst_report.generated_at,
        )
        if freshness_line:
            lines.append(freshness_line)

    northbound_line = (
        _northbound_signal_line(northbound_flow_5d_z) if domestic_enabled else ""
    )
    if northbound_line:
        lines.append(northbound_line)

    margin_line = (
        _margin_signal_line(margin_balance_change_5d) if domestic_enabled else ""
    )
    if margin_line:
        lines.append(margin_line)

    sentiment_line = _sentiment_signal_line(sentiment_z) if domestic_enabled else ""
    if sentiment_line:
        lines.append(sentiment_line)

    macro_line = (
        _macro_climate_line(macro_climate, macro_detail) if domestic_enabled else ""
    )
    if macro_line:
        lines.append(macro_line)

    combined_line = _combined_context_line(
        symbol_events=symbol_events,
        domestic_events=domestic_events,
        global_events=global_events,
        northbound_flow_5d_z=northbound_flow_5d_z if domestic_enabled else 0.0,
        margin_balance_change_5d=margin_balance_change_5d if domestic_enabled else 0.0,
        sentiment_z=sentiment_z if domestic_enabled else 0.0,
        macro_climate=macro_climate if domestic_enabled else "",
    )
    if combined_line:
        lines.append(combined_line)

    coverage_line = _coverage_line(
        symbol_events=symbol_events,
        domestic_events=domestic_events,
        global_events=global_events,
        northbound_flow_5d_z=northbound_flow_5d_z if domestic_enabled else 0.0,
        margin_balance_change_5d=margin_balance_change_5d if domestic_enabled else 0.0,
        sentiment_z=sentiment_z if domestic_enabled else 0.0,
        macro_climate=macro_climate if domestic_enabled else "",
    )
    if coverage_line:
        lines.append(coverage_line)

    if realtime_context is not None:
        if not cross_market_events:
            cross_market_implications = _cross_market_implications(
                [],
                generated_at=realtime_context.generated_at,
                realtime_context=realtime_context,
            )
            lines.extend(
                implication.summary_line
                for implication in cross_market_implications[:3]
            )
        lines.append(_realtime_cross_market_summary_line(realtime_context))
        warnings = tuple(_dedupe_texts((*warnings, *realtime_context.warnings)))

    if not lines:
        if not domestic_enabled and not global_enabled:
            lines.append("市场上下文: 当前已关闭国内外信息融合，维持价格与成交主导。")
        elif catalyst_report is None:
            warnings = ("消息源未加载：不得将空结果视为无消息。",)
            lines.append("消息状态: 未加载，不能据此判断暂无消息；维持价格与成交主导。")
        else:
            lines.append("市场上下文: 暂无强外部信号，维持价格与成交主导。")

    return MarketContextArtifact(
        date=(
            catalyst_report.date
            if catalyst_report is not None
            else today_shanghai().isoformat()
        ),
        generated_at=(
            catalyst_report.generated_at
            if catalyst_report is not None
            else now_shanghai().isoformat(timespec="seconds")
        ),
        source_status=source_status,
        summary_lines=tuple(lines[:11]),
        cross_market_implications=cross_market_implications[:5],
        cross_market_overview=_cross_market_overview_from_implications(
            cross_market_implications[:5]
        ),
        warnings=warnings[:3],
        catalyst_events=tuple((*symbol_events, *domestic_events, *global_events)),
        news_status=(
            catalyst_report.news_status if catalyst_report is not None else ""
        ),
        realtime_cross_market=realtime_context,
        news_watch_candidates=news_watch_candidates,
    )


def _realtime_cross_market_summary_line(
    context: RealtimeCrossMarketContext,
) -> str:
    status_text = "；".join(
        f"{item.instrument} {item.status}" for item in context.observations
    )
    return f"实时跨市: {context.status}｜{status_text}"


def _market_context_warning_line(warnings: tuple[str, ...]) -> str:
    for warning in warnings:
        text = str(warning or "").strip()
        if not text:
            continue
        if "情报门禁" in text or "消息缓存过期" in text:
            return text
        if "消息缓存回退" in text:
            return text
        if "超时" in text or "连接中断" in text:
            return "消息补位: 部分来源超时，已按可用摘要继续。"
    return ""


def _actionable_catalyst_events(
    events: tuple[CatalystEvent, ...],
    *,
    generated_at: str,
    max_age_minutes: int,
) -> tuple[tuple[CatalystEvent, ...], tuple[str, ...]]:
    if not events:
        return (), ()
    generated_dt = _parse_iso_datetime(generated_at)
    if generated_dt is None:
        return (), (f"情报门禁: 报告生成时间不可解析，已排除 {len(events)} 条消息",)

    actionable: list[CatalystEvent] = []
    stale_count = 0
    undated_count = 0
    future_count = 0
    source_missing_count = 0
    low_quality_count = 0
    max_age = max(0, int(max_age_minutes))
    for event in events:
        age_minutes = _event_age_minutes(
            event.published_at,
            generated_dt=generated_dt,
        )
        if age_minutes is None:
            published_dt = _parse_iso_datetime(event.published_at)
            if published_dt is not None and published_dt > generated_dt:
                future_count += 1
            else:
                undated_count += 1
            continue
        if max_age > 0 and age_minutes > max_age:
            stale_count += 1
            continue
        if not str(event.source or "").strip():
            source_missing_count += 1
            continue
        if _event_source_quality_score(event) < _ACTIONABLE_NEWS_MIN_SOURCE_QUALITY:
            low_quality_count += 1
            continue
        actionable.append(event)

    warnings: list[str] = []
    if stale_count > 0:
        warnings.append(f"情报门禁: 已排除 {stale_count} 条超出短线窗口的旧消息")
    if undated_count > 0:
        warnings.append(f"情报门禁: 已排除 {undated_count} 条无有效时间戳消息")
    if future_count > 0:
        warnings.append(f"情报门禁: 已排除 {future_count} 条未来时间戳消息")
    if source_missing_count > 0:
        warnings.append(f"情报门禁: 已排除 {source_missing_count} 条无可追踪来源消息")
    if low_quality_count > 0:
        warnings.append(f"情报门禁: 已排除 {low_quality_count} 条普通单源消息")
    return tuple(actionable), tuple(warnings)


def _dedupe_texts(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return tuple(deduped)


def market_context_lines_for_pick(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> tuple[str, ...]:
    relevant = relevant_cross_market_implications_for_pick(
        pick,
        artifact.cross_market_implications,
    )
    relevant_lines = {item.summary_line for item in relevant}
    lines: list[str] = []
    for line in artifact.summary_lines:
        if line.startswith("传导推演["):
            if line in relevant_lines:
                lines.append(line)
                implication = next(
                    (item for item in relevant if item.summary_line == line),
                    None,
                )
                if implication is not None:
                    lines.extend(_pick_implication_detail_lines(implication))
            continue
        lines.append(line)
    direct_line = _pick_news_judgement_line(pick, artifact)
    if direct_line:
        lines.append(direct_line)
    return tuple(lines[:11])


def relevant_cross_market_implications_for_pick(
    pick: PickResult,
    implications: tuple[CrossMarketImplication, ...],
) -> tuple[CrossMarketImplication, ...]:
    haystack = _pick_relevance_text(pick)
    matched: list[CrossMarketImplication] = []
    for implication in implications:
        if any(
            str(keyword or "").casefold() in haystack
            for keyword in implication.relevance_keywords
        ):
            matched.append(implication)
    return tuple(matched[:2])


def build_pick_market_context(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> PickMarketContext:
    implications = relevant_cross_market_implications_for_pick(
        pick,
        artifact.cross_market_implications,
    )
    if not implications:
        return PickMarketContext(
            symbol=pick.symbol,
            primary_theme="",
            linkage_basis="",
            primary_action="",
            primary_strength="",
            primary_source_quality_label="",
            primary_source_quality_score=0,
            lead_window="",
            observation_window="",
            priority_score=0,
            themes=(),
            rule_ids=(),
            first_order_targets=(),
            second_order_targets=(),
            pressure_targets=(),
            execution_watchpoints=(),
            transmission_path=(),
            validation_signals=(),
            invalidation_signals=(),
            chain_summary="",
            support_event_count=0,
            conflict_event_count=0,
            evidence_stack_summary="",
            summary_lines=(),
        )
    ordered = sorted(
        implications,
        key=lambda item: (
            _implication_priority_score(item),
            item.theme,
        ),
        reverse=True,
    )
    primary = ordered[0]
    return PickMarketContext(
        symbol=pick.symbol,
        primary_theme=primary.theme,
        linkage_basis=primary.linkage_basis,
        primary_action=primary.action,
        primary_strength=primary.strength,
        primary_source_quality_label=primary.source_quality_label,
        primary_source_quality_score=primary.source_quality_score,
        lead_window=primary.lead_window,
        observation_window=primary.observation_window,
        priority_score=_implication_priority_score(primary),
        themes=tuple(item.theme for item in ordered),
        rule_ids=tuple(item.rule_id for item in ordered),
        first_order_targets=primary.first_order_targets,
        second_order_targets=primary.second_order_targets,
        pressure_targets=primary.pressure_targets,
        execution_watchpoints=primary.execution_watchpoints,
        transmission_path=primary.transmission_path,
        validation_signals=primary.validation_signals,
        invalidation_signals=primary.invalidation_signals,
        chain_summary=_pick_chain_summary(primary),
        support_event_count=primary.support_event_count,
        conflict_event_count=primary.conflict_event_count,
        evidence_stack_summary=primary.evidence_stack_summary,
        summary_lines=tuple(item.summary_line for item in ordered),
        affected_sectors=primary.affected_sectors,
        affected_symbols=primary.affected_symbols,
        transmission_hypothesis=primary.transmission_hypothesis,
        confidence=primary.confidence,
        time_horizon=primary.time_horizon,
        supporting_evidence=primary.supporting_evidence,
        contradicting_evidence=primary.contradicting_evidence,
        source_regions=primary.source_regions,
        impact_direction=primary.impact_direction,
        source_url=primary.source_url,
        source_fetched_at=primary.source_fetched_at,
    )


def market_context_metrics_for_pick(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> dict[str, object]:
    context = build_pick_market_context(pick, artifact)
    news_metrics = _pick_news_judgement_metrics(pick, artifact)
    if not context.summary_lines and not news_metrics:
        return {}
    structured_rule_match = bool(context.rule_ids)
    metrics: dict[str, object] = {
        "cross_market_primary_theme": context.primary_theme,
        "cross_market_linkage_basis": context.linkage_basis,
        "cross_market_action": context.primary_action,
        "cross_market_strength": context.primary_strength,
        "cross_market_source_quality_label": context.primary_source_quality_label,
        "cross_market_source_quality_score": context.primary_source_quality_score,
        "cross_market_lead_window": context.lead_window,
        "cross_market_observation_window": context.observation_window,
        "cross_market_priority_score": context.priority_score,
        "cross_market_themes": context.themes,
        "cross_market_rule_ids": context.rule_ids,
        "cross_market_first_order_targets": context.first_order_targets,
        "cross_market_second_order_targets": context.second_order_targets,
        "cross_market_pressure_targets": context.pressure_targets,
        "cross_market_execution_watchpoints": context.execution_watchpoints,
        "cross_market_transmission_path": context.transmission_path,
        "cross_market_validation_signals": context.validation_signals,
        "cross_market_invalidation_signals": context.invalidation_signals,
        "cross_market_chain_summary": context.chain_summary,
        "cross_market_support_event_count": context.support_event_count,
        "cross_market_conflict_event_count": context.conflict_event_count,
        "cross_market_evidence_stack_summary": context.evidence_stack_summary,
        "cross_market_summaries": context.summary_lines,
        "cross_market_affected_sectors": context.affected_sectors,
        "cross_market_affected_symbols": context.affected_symbols,
        "cross_market_transmission_hypothesis": context.transmission_hypothesis,
        "cross_market_confidence": context.confidence,
        "cross_market_time_horizon": context.time_horizon,
        "cross_market_supporting_evidence": (context.supporting_evidence),
        "cross_market_contradicting_evidence": (context.contradicting_evidence),
        "cross_market_source_regions": context.source_regions,
        "cross_market_impact_direction": context.impact_direction,
        "cross_market_source_url": context.source_url,
        "cross_market_source_fetched_at": context.source_fetched_at,
        "cross_market_score_adjustment_allowed": structured_rule_match,
        "cross_market_priority_boost": structured_rule_match,
        "cross_market_context_only": not structured_rule_match,
    }
    if news_metrics:
        metrics.update(news_metrics)
        if not context.summary_lines:
            metrics.update(_cross_market_fallback_from_news(news_metrics))
    return metrics


def format_pick_market_context_summary(
    pick: PickResult,
    *,
    compact: bool = False,
) -> str:
    metrics = pick.metrics or {}
    theme = str(metrics.get("cross_market_primary_theme", "") or "").strip()
    action = str(metrics.get("cross_market_action", "") or "").strip()
    window = str(metrics.get("cross_market_observation_window", "") or "").strip()
    if not theme:
        return ""
    if compact:
        if action:
            return f"{theme}({action})"
        return theme
    parts = [part for part in (action, theme) if part]
    if window:
        parts.append(f"观察窗 {window}")
    return "｜".join(parts)


def format_pick_market_context_chain_summary(pick: PickResult) -> str:
    metrics = pick.metrics or {}
    basis = str(metrics.get("cross_market_linkage_basis", "") or "").strip()
    lead_window = str(metrics.get("cross_market_lead_window", "") or "").strip()
    validation = _as_text_tuple(metrics.get("cross_market_validation_signals"))
    invalidation = _as_text_tuple(metrics.get("cross_market_invalidation_signals"))
    first_order_targets = _as_text_tuple(
        metrics.get("cross_market_first_order_targets")
    )
    pressure_targets = _as_text_tuple(metrics.get("cross_market_pressure_targets"))
    execution_watchpoints = _as_text_tuple(
        metrics.get("cross_market_execution_watchpoints")
    )
    evidence_stack_summary = str(
        metrics.get("cross_market_evidence_stack_summary", "") or ""
    ).strip()
    parts: list[str] = []
    if basis:
        parts.append(basis)
    if lead_window:
        parts.append(f"领先窗 {lead_window}")
    if first_order_targets:
        parts.append(f"先看 {first_order_targets[0]}")
    if execution_watchpoints:
        parts.append(f"锚点 {execution_watchpoints[0]}")
    if validation:
        parts.append(f"确认 {validation[0]}")
    if invalidation:
        parts.append(f"失效 {invalidation[0]}")
    if pressure_targets:
        parts.append(f"承压 {pressure_targets[0]}")
    if evidence_stack_summary:
        parts.append(evidence_stack_summary)
    return "｜".join(parts)


def combine_cross_market_overview(
    candidate_overview: str,
    artifact: MarketContextArtifact,
) -> str:
    candidate_text = str(candidate_overview or "").strip()
    market_text = str(artifact.cross_market_overview or "").strip()
    if not candidate_text:
        return market_text
    if not market_text:
        return candidate_text
    candidate_theme = candidate_text.split("，", 1)[0].strip()
    matched = next(
        (
            implication
            for implication in artifact.cross_market_implications
            if implication.theme == candidate_theme
        ),
        None,
    )
    if matched is not None:
        targets = "、".join(matched.a_share_targets[:3])
        if targets:
            return f"{candidate_text}；方向 {targets}"
    return f"{candidate_text}；全局 {market_text}"


def _event_brief(event: CatalystEvent) -> str:
    target = f"{event.symbol} {event.name}".strip() if event.symbol else "全市场"
    title = str(event.inference or event.title or "").strip()
    title = " ".join(title.split())
    if len(title) > 26:
        title = title[:25].rstrip() + "…"
    impact = {"positive": "偏多", "negative": "偏空", "neutral": "中性"}.get(
        event.impact,
        "中性",
    )
    return f"{target} {impact}｜{event.category}｜{title}"


def _source_status_text(status: str) -> str:
    return {
        "ok": "可用",
        "partial": "部分可用",
        "empty": "无强事件",
        "failed": "抓取失败",
        "not_loaded": "未加载",
    }.get(status, status or "未知")


def _catalyst_result_status_text(status: str, warnings: tuple[str, ...]) -> str:
    if any("超出短线窗口" in str(item) for item in warnings):
        return "旧新闻已排除"
    return {
        "high_impact": "已筛出高影响事件",
        "no_high_impact": "抓取成功但未筛出高影响事件",
        "stale_only": "仅发现旧新闻，已排除",
        "no_valid_news": "无可用新闻记录",
        "source_failed": "来源失败，无有效事件",
        "stale_cache": "来源失败，使用受限旧缓存",
    }.get(status, status or "未知")


def _northbound_signal_line(value: float) -> str:
    if value >= _NORTHBOUND_STRONG_Z:
        return f"北向资金: 偏强（5日 z={value:.2f}），外资风险偏好改善。"
    if value <= -_NORTHBOUND_STRONG_Z:
        return f"北向资金: 偏弱（5日 z={value:.2f}），需防范系统性回撤。"
    return ""


def _margin_signal_line(value: float) -> str:
    if value >= _MARGIN_STRONG_CHANGE:
        return f"融资情绪: 升温（5日变化 {value:.1%}），短线拥挤度上升。"
    if value <= -_MARGIN_STRONG_CHANGE:
        return f"融资情绪: 降温（5日变化 {value:.1%}），杠杆风险偏好回落。"
    return ""


def _sentiment_signal_line(value: float) -> str:
    if value >= _SENTIMENT_STRONG_Z:
        return f"市场情绪: 偏热（涨停 z={value:.2f}），注意追高风险。"
    if value <= -_SENTIMENT_STRONG_Z:
        return f"市场情绪: 偏冷（涨停 z={value:.2f}），关注超跌反弹机会。"
    return ""


def _macro_climate_line(climate: str, detail: str) -> str:
    if climate == "expansion":
        return f"宏观气候: 扩张（{detail}），风险偏好支撑。"
    if climate == "contraction":
        return f"宏观气候: 收缩（{detail}），注意系统性风险。"
    return ""


def _global_risk_line(events: list[CatalystEvent]) -> str:
    if not events:
        return ""
    positive = sum(1 for event in events if event.impact == "positive")
    negative = sum(1 for event in events if event.impact == "negative")
    if positive == 0 and negative == 0:
        return ""
    categories = _top_categories(events)
    category_text = f"｜{categories}" if categories else ""
    if negative > positive:
        return (
            f"海外风险: 偏空（正面 {positive} / 负面 {negative}）"
            f"{category_text}｜海外风险偏好回落。"
        )
    if positive > negative:
        return (
            f"海外风险: 偏多（正面 {positive} / 负面 {negative}）"
            f"{category_text}｜海外风险偏好回暖。"
        )
    return (
        f"海外风险: 分化（正面 {positive} / 负面 {negative}）"
        f"{category_text}｜外部线索未形成单边共识。"
    )


def _source_quality_summary_line(events: list[CatalystEvent]) -> str:
    if not events:
        return ""
    high_value = sum(1 for event in events if _event_source_quality_score(event) >= 4)
    authoritative = sum(
        1 for event in events if _event_source_quality_score(event) == 3
    )
    mainstream = sum(1 for event in events if _event_source_quality_score(event) == 2)
    if high_value <= 0 and authoritative <= 0 and mainstream <= 0:
        return ""
    parts: list[str] = []
    if high_value > 0:
        parts.append(f"高价值 {high_value} 条")
    if authoritative > 0:
        parts.append(f"多源/权威 {authoritative} 条")
    if mainstream > 0:
        parts.append(f"主流媒体 {mainstream} 条")
    return "来源质量: " + "｜".join(parts)


def _combined_context_line(
    *,
    symbol_events: list[CatalystEvent],
    domestic_events: list[CatalystEvent],
    global_events: list[CatalystEvent],
    northbound_flow_5d_z: float,
    margin_balance_change_5d: float,
    sentiment_z: float = 0.0,
    macro_climate: str = "",
) -> str:
    reasons: list[str] = []
    score = 0

    symbol_score = _impact_balance(symbol_events)
    if symbol_score > 0:
        score += 1
        reasons.append("个股催化偏多")
    elif symbol_score < 0:
        score -= 1
        reasons.append("个股催化偏空")

    domestic_score = _impact_balance(domestic_events)
    if domestic_score > 0:
        score += 1
        reasons.append("国内催化偏多")
    elif domestic_score < 0:
        score -= 1
        reasons.append("国内催化偏空")

    global_score = _impact_balance(global_events)
    if global_score > 0:
        score += 1
        reasons.append("海外线索偏多")
    elif global_score < 0:
        score -= 1
        reasons.append("海外线索偏空")

    if northbound_flow_5d_z >= _NORTHBOUND_STRONG_Z:
        score += 1
        reasons.append("北向改善")
    elif northbound_flow_5d_z <= -_NORTHBOUND_STRONG_Z:
        score -= 1
        reasons.append("北向走弱")

    if margin_balance_change_5d >= _MARGIN_STRONG_CHANGE:
        reasons.append("融资升温")
    elif margin_balance_change_5d <= -_MARGIN_STRONG_CHANGE:
        score -= 1
        reasons.append("融资降温")

    if sentiment_z >= _SENTIMENT_STRONG_Z:
        reasons.append("情绪偏热")
    elif sentiment_z <= -_SENTIMENT_STRONG_Z:
        score -= 1
        reasons.append("情绪偏冷")

    if macro_climate == "expansion":
        score += 1
        reasons.append("宏观扩张")
    elif macro_climate == "contraction":
        score -= 1
        reasons.append("宏观收缩")

    if not reasons:
        return ""
    if score >= 2:
        bias = "偏多"
    elif score <= -2:
        bias = "偏空"
    else:
        bias = "分化"
    return f"综合风向: {bias}｜{'；'.join(reasons[:3])}。"


def _coverage_line(
    *,
    symbol_events: list[CatalystEvent],
    domestic_events: list[CatalystEvent],
    global_events: list[CatalystEvent],
    northbound_flow_5d_z: float,
    margin_balance_change_5d: float,
    sentiment_z: float = 0.0,
    macro_climate: str = "",
) -> str:
    coverage: list[str] = []
    if symbol_events:
        coverage.append("个股催化")
    if domestic_events:
        coverage.append("国内政策/行业")
    if global_events:
        coverage.append("海外线索")
    if abs(northbound_flow_5d_z) >= _NORTHBOUND_STRONG_Z:
        coverage.append("北向资金")
    if abs(margin_balance_change_5d) >= _MARGIN_STRONG_CHANGE:
        coverage.append("融资情绪")
    if abs(sentiment_z) >= _SENTIMENT_STRONG_Z:
        coverage.append("市场情绪")
    if macro_climate in ("expansion", "contraction"):
        coverage.append("宏观气候")
    if not coverage:
        return ""
    return f"情报覆盖: {' + '.join(coverage[:4])}。"


def _event_freshness_line(
    *,
    events: tuple[CatalystEvent, ...],
    generated_at: str,
) -> str:
    if not events:
        return ""
    generated_dt = _parse_iso_datetime(generated_at)
    if generated_dt is None:
        return ""
    ages: list[int] = []
    undated_count = 0
    for event in events:
        published_dt = _parse_iso_datetime(event.published_at)
        if published_dt is None:
            undated_count += 1
            continue
        delta_seconds = (generated_dt - published_dt).total_seconds()
        if delta_seconds < 0:
            continue
        ages.append(int(delta_seconds // 60))
    if not ages and undated_count <= 0:
        return ""
    if not ages:
        return (
            f"情报时效: 未能确认具体时间（无时间戳 {undated_count} 条），仅作辅助参考。"
        )
    freshest = min(ages)
    if freshest <= 120:
        freshness = "偏新"
        hint = "可优先进入短线复核。"
    elif freshest <= 720:
        freshness = "可用"
        hint = "适合作为次日预案参考。"
    else:
        freshness = "偏旧"
        hint = "更适合解释背景，不宜单独驱动短线判断。"
    suffix = f"；无时间戳 {undated_count} 条" if undated_count > 0 else ""
    return f"情报时效: {freshness}（最新 {freshest} 分钟前）{suffix}｜{hint}"


def _impact_balance(events: list[CatalystEvent]) -> int:
    positive = sum(1 for event in events if event.impact == "positive")
    negative = sum(1 for event in events if event.impact == "negative")
    if positive > negative:
        return 1
    if negative > positive:
        return -1
    return 0


def _top_categories(events: list[CatalystEvent]) -> str:
    counts: dict[str, int] = {}
    for event in events:
        category = str(event.category or "").strip()
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "、".join(category for category, _count in ordered[:2])


def _pick_news_judgement_metrics(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> dict[str, object]:
    matched = _pick_relevant_catalyst_events(pick, artifact.catalyst_events)
    if not matched:
        # Industry/supply-chain expansion is intentionally separate from the
        # raw event list.  A generic headline can map to a symbol through the
        # point-in-time universe, even when the headline never names that
        # company or the PickResult has no sector metadata.
        watch_candidate = next(
            (
                candidate
                for candidate in artifact.news_watch_candidates
                if candidate.symbol == pick.symbol
            ),
            None,
        )
        if watch_candidate is None:
            return {}
        return _news_watch_candidate_metrics(watch_candidate)
    supports = tuple(event for event in matched if event.impact == "positive")
    opposes = tuple(event for event in matched if event.impact == "negative")
    needs_review = tuple(
        event
        for event in matched
        if event.impact == "neutral" or event.confidence < 0.55
    )
    judgement = _news_judgement_label(
        support_count=len(supports),
        oppose_count=len(opposes),
        review_count=len(needs_review),
    )
    priority_score = _news_priority_score(supports=supports, opposes=opposes)
    lead = _lead_news_event(supports=supports, opposes=opposes, matched=matched)
    return {
        "news_catalyst_judgement": judgement,
        "news_catalyst_priority_score": priority_score,
        "news_catalyst_support_count": len(supports),
        "news_catalyst_oppose_count": len(opposes),
        "news_catalyst_review_count": len(needs_review),
        "news_catalyst_supports": tuple(_event_brief(event) for event in supports[:3]),
        "news_catalyst_opposes": tuple(_event_brief(event) for event in opposes[:3]),
        "news_catalyst_needs_review": tuple(
            _event_brief(event) for event in needs_review[:3]
        ),
        "news_catalyst_lead": _event_brief(lead) if lead is not None else "",
        "news_catalyst_source": str(lead.source if lead is not None else ""),
        "news_catalyst_url": str(lead.url if lead is not None else ""),
        "news_catalyst_title": str(lead.title if lead is not None else ""),
        "news_catalyst_published_at": str(
            lead.published_at if lead is not None else ""
        ),
        "news_catalyst_source_quality_label": str(
            _event_source_quality_label(lead) if lead is not None else ""
        ),
        "news_catalyst_source_quality_score": int(
            _event_source_quality_score(lead) if lead is not None else 0
        ),
        "news_catalyst_confidence": float(lead.confidence if lead is not None else 0.0),
        "news_catalyst_deterministic_score": int(
            lead.deterministic_score if lead is not None else 0
        ),
        "news_catalyst_symbol": str(lead.symbol if lead is not None else ""),
        "news_catalyst_name": str(lead.name if lead is not None else ""),
        "news_catalyst_category": str(lead.category if lead is not None else ""),
        "news_catalyst_affected_sectors": (
            tuple(lead.affected_sectors) if lead is not None else ()
        ),
        "news_catalyst_affected_symbols": (
            tuple(lead.affected_symbols) if lead is not None else ()
        ),
        "news_catalyst_transmission_path": (
            tuple(lead.transmission_path) if lead is not None else ()
        ),
        "news_catalyst_validation_signals": (
            tuple(lead.validation_signals) if lead is not None else ()
        ),
        "news_catalyst_invalidation_signals": (
            tuple(lead.invalidation_signals) if lead is not None else ()
        ),
        "news_catalyst_transmission_hypothesis": str(
            lead.transmission_hypothesis if lead is not None else ""
        ),
        "news_catalyst_time_horizon": str(
            lead.time_horizon if lead is not None else ""
        ),
        "news_catalyst_supporting_evidence": (
            tuple(lead.supporting_evidence) if lead is not None else ()
        ),
        "news_catalyst_contradicting_evidence": (
            tuple(lead.contradicting_evidence) if lead is not None else ()
        ),
        "news_catalyst_sector": str(
            (pick.metrics or {}).get("sector", "") if pick.metrics else ""
        ),
        "news_catalyst_industry": str(
            (pick.metrics or {}).get("industry", "") if pick.metrics else ""
        ),
    }


def _news_watch_candidate_metrics(
    candidate: NewsWatchCandidate,
) -> dict[str, object]:
    """Project a source-backed expanded news candidate into pick evidence."""
    impact = str(candidate.impact or "neutral").strip().lower()
    support_count = max(0, int(candidate.supporting_event_count))
    oppose_count = max(0, int(candidate.contradicting_event_count))
    impact_direction = str(candidate.impact_direction or "").strip().lower()
    if support_count > 0 and oppose_count > 0:
        judgement = "mixed"
        display_impact = "分化"
    elif support_count > 0 or impact_direction == "positive" or impact == "positive":
        judgement = "supports"
        display_impact = "偏多"
    elif oppose_count > 0 or impact_direction == "negative" or impact == "negative":
        judgement = "opposes"
        display_impact = "偏空"
    else:
        judgement = "needs_review"
        display_impact = "中性"
    lead = (
        f"{candidate.symbol} {candidate.name} "
        f"{display_impact}｜"
        f"{candidate.relation}｜{candidate.summary or candidate.event_title}"
    ).strip()
    review_count = int(not support_count and not oppose_count)
    return {
        "news_catalyst_judgement": judgement,
        "news_catalyst_priority_score": int(candidate.priority_score),
        "news_catalyst_support_count": support_count,
        "news_catalyst_oppose_count": oppose_count,
        "news_catalyst_review_count": review_count,
        "news_catalyst_supports": (lead,) if support_count else (),
        "news_catalyst_opposes": (lead,) if oppose_count else (),
        "news_catalyst_needs_review": (lead,) if judgement == "needs_review" else (),
        "news_catalyst_lead": lead,
        "news_catalyst_source": candidate.source,
        "news_catalyst_url": candidate.source_url,
        "news_catalyst_title": candidate.event_title,
        "news_catalyst_published_at": candidate.published_at,
        "news_catalyst_source_quality_label": candidate.source_quality_label,
        "news_catalyst_source_quality_score": int(candidate.source_quality_score),
        "news_catalyst_confidence": float(candidate.confidence),
        "news_catalyst_symbol": candidate.symbol,
        "news_catalyst_name": candidate.name,
        "news_catalyst_category": candidate.relation,
        "news_catalyst_affected_sectors": candidate.affected_sectors,
        "news_catalyst_affected_symbols": (),
        "news_catalyst_transmission_path": candidate.transmission_path,
        "news_catalyst_validation_signals": candidate.validation_signals,
        "news_catalyst_invalidation_signals": candidate.invalidation_signals,
        "news_catalyst_transmission_hypothesis": candidate.transmission_hypothesis,
        "news_catalyst_supporting_evidence": candidate.supporting_evidence,
        "news_catalyst_contradicting_evidence": (),
        "news_catalyst_sector": (
            candidate.affected_sectors[0] if candidate.affected_sectors else ""
        ),
        "news_catalyst_industry": "",
    }


def _cross_market_fallback_from_news(
    metrics: dict[str, object],
) -> dict[str, object]:
    judgement = str(metrics.get("news_catalyst_judgement", "") or "")
    priority_score = int(metrics.get("news_catalyst_priority_score", 0) or 0)
    if priority_score <= 0:
        return {}
    action = "观察为主"
    if judgement == "supports":
        action = (
            "优先复核" if priority_score >= _NEWS_DIRECT_STRONG_SCORE else "重点跟踪"
        )
    elif judgement == "opposes":
        action = "风险复核"
    target = (
        " ".join(
            value
            for value in (
                str(metrics.get("news_catalyst_symbol", "") or "").strip(),
                str(metrics.get("news_catalyst_name", "") or "").strip(),
            )
            if value
        )
        or "消息直接对象"
    )
    sector = str(metrics.get("news_catalyst_sector", "") or "").strip()
    industry = str(metrics.get("news_catalyst_industry", "") or "").strip()
    affected_sectors = _as_text_tuple(metrics.get("news_catalyst_affected_sectors"))
    second_order = _dedupe_texts(
        tuple(
            value
            for value in (*affected_sectors, industry, sector, "同主题竞品/上下游")
            if value and value != target
        )
    )
    event_path = _as_text_tuple(metrics.get("news_catalyst_transmission_path"))
    event_validation = _as_text_tuple(metrics.get("news_catalyst_validation_signals"))
    event_invalidation = _as_text_tuple(
        metrics.get("news_catalyst_invalidation_signals")
    )
    first_order = (target,)
    source_to_target = (
        f"{metrics.get('news_catalyst_source', '') or '可追踪来源'}消息 -> {target}"
    )
    transmission_path = (
        (source_to_target, *event_path, "价格与成交确认后再判断催化是否延续")
        if event_path
        else (
            source_to_target,
            f"{target} -> {industry or sector or '所属行业/上下游'}",
            "价格与成交确认后再判断催化是否延续",
        )
    )
    validation_signals = event_validation or (
        "原文来源与发布时间可复核",
        "竞价及首小时价格、成交同步确认",
        f"{industry or sector or '所属板块'}出现至少两只跟随标的",
    )
    invalidation_signals = event_invalidation or (
        "来源无法复核或后续公告澄清",
        "高开低走或放量不涨",
        f"{industry or sector or '所属板块'}没有扩散而仅单点脉冲",
    )
    evidence_points = tuple(
        value
        for value in (
            str(metrics.get("news_catalyst_source", "") or "").strip(),
            str(metrics.get("news_catalyst_published_at", "") or "").strip(),
            str(metrics.get("news_catalyst_url", "") or "").strip(),
        )
        if value
    )
    return {
        "cross_market_primary_theme": "消息面直接催化",
        "cross_market_linkage_basis": "新闻催化",
        "cross_market_action": action,
        "cross_market_strength": "强"
        if priority_score >= _NEWS_DIRECT_STRONG_SCORE
        else "中",
        "cross_market_priority_score": priority_score,
        "cross_market_lead_window": "消息发布-当日",
        "cross_market_observation_window": "当日-2日",
        "cross_market_source_quality_label": str(
            metrics.get("news_catalyst_source_quality_label", "") or ""
        ),
        "cross_market_source_quality_score": int(
            metrics.get("news_catalyst_source_quality_score", 0) or 0
        ),
        "cross_market_source_title": str(metrics.get("news_catalyst_title", "") or ""),
        "cross_market_source_published_at": str(
            metrics.get("news_catalyst_published_at", "") or ""
        ),
        "cross_market_affected_sectors": _as_text_tuple(
            metrics.get("news_catalyst_affected_sectors")
        ),
        "cross_market_affected_symbols": _as_text_tuple(
            metrics.get("news_catalyst_affected_symbols")
        ),
        "cross_market_transmission_hypothesis": str(
            metrics.get("news_catalyst_transmission_hypothesis", "") or ""
        ),
        "cross_market_confidence": float(
            metrics.get("news_catalyst_confidence", 0.0) or 0.0
        ),
        "cross_market_time_horizon": str(
            metrics.get("news_catalyst_time_horizon", "当日-2日") or "当日-2日"
        ),
        "cross_market_supporting_evidence": _as_text_tuple(
            metrics.get("news_catalyst_supporting_evidence")
        ),
        "cross_market_contradicting_evidence": _as_text_tuple(
            metrics.get("news_catalyst_contradicting_evidence")
        ),
        "cross_market_first_order_targets": first_order,
        "cross_market_second_order_targets": second_order,
        "cross_market_transmission_path": transmission_path,
        "cross_market_validation_signals": validation_signals,
        "cross_market_invalidation_signals": invalidation_signals,
        "cross_market_execution_watchpoints": (
            "竞价强度与首小时成交承接",
            "板块扩散与相对强度",
        ),
        "cross_market_chain_summary": (
            f"{target} -> {industry or sector or '所属行业/上下游'} -> 价格/成交确认"
        ),
        "cross_market_evidence_points": evidence_points,
        "cross_market_support_event_count": int(
            metrics.get("news_catalyst_support_count", 0) or 0
        ),
        "cross_market_conflict_event_count": int(
            metrics.get("news_catalyst_oppose_count", 0) or 0
        ),
        "cross_market_evidence_stack_summary": _news_evidence_stack_summary(metrics),
        "cross_market_summaries": (str(metrics.get("news_catalyst_lead", "") or ""),),
        "cross_market_score_adjustment_allowed": False,
        "cross_market_context_only": True,
    }


def _pick_news_judgement_line(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> str:
    metrics = _pick_news_judgement_metrics(pick, artifact)
    if not metrics:
        return ""
    judgement = str(metrics.get("news_catalyst_judgement", "") or "")
    label = {
        "supports": "消息支持",
        "opposes": "消息反对",
        "needs_review": "消息待复核",
        "mixed": "消息分歧",
    }.get(judgement, "消息观察")
    lead = str(metrics.get("news_catalyst_lead", "") or "")
    stack = _news_evidence_stack_summary(metrics)
    source = str(metrics.get("news_catalyst_source", "") or "").strip()
    path = _as_text_tuple(metrics.get("news_catalyst_transmission_path"))
    suffix_parts = [
        item for item in (stack, f"来源 {source}" if source else "") if item
    ]
    if path:
        suffix_parts.append(f"传导 {' -> '.join(path[:2])}")
    suffix = f"｜{'｜'.join(suffix_parts)}" if suffix_parts else ""
    return f"{label}: {lead}{suffix}"


def _pick_relevant_catalyst_events(
    pick: PickResult,
    events: tuple[CatalystEvent, ...],
) -> tuple[CatalystEvent, ...]:
    matched: list[CatalystEvent] = []
    pick_tokens = _pick_relevance_tokens(pick)
    for event in events:
        if event.symbol and event.symbol == pick.symbol:
            matched.append(event)
            continue
        text = " ".join(
            str(part or "").lower()
            for part in (event.title, event.inference, event.category, event.name)
        )
        if pick.symbol and pick.symbol in text:
            matched.append(event)
            continue
        if pick.name and pick.name.lower() in text:
            matched.append(event)
            continue
        if pick_tokens and any(token in text for token in pick_tokens):
            matched.append(event)
    return tuple(sorted(matched, key=_news_event_rank_key, reverse=True)[:5])


def _pick_relevance_tokens(pick: PickResult) -> tuple[str, ...]:
    metrics = pick.metrics or {}
    raw_tokens = (
        str(metrics.get("sector", "") or ""),
        str(metrics.get("industry", "") or ""),
        *tuple(str(strategy) for strategy in pick.strategies),
    )
    tokens: list[str] = []
    for token in raw_tokens:
        clean = token.strip().lower()
        if len(clean) >= 2 and clean not in tokens:
            tokens.append(clean)
    return tuple(tokens)


def _news_event_rank_key(event: CatalystEvent) -> tuple[int, int, float, int]:
    return (
        int(event.weight),
        int(event.source_quality_score),
        float(event.confidence),
        int(event.source_count),
    )


def _lead_news_event(
    *,
    supports: tuple[CatalystEvent, ...],
    opposes: tuple[CatalystEvent, ...],
    matched: tuple[CatalystEvent, ...],
) -> CatalystEvent | None:
    if opposes:
        return sorted(opposes, key=_news_event_rank_key, reverse=True)[0]
    if supports:
        return sorted(supports, key=_news_event_rank_key, reverse=True)[0]
    if matched:
        return sorted(matched, key=_news_event_rank_key, reverse=True)[0]
    return None


def _news_judgement_label(
    *,
    support_count: int,
    oppose_count: int,
    review_count: int,
) -> str:
    if support_count > 0 and oppose_count > 0:
        return "mixed"
    if oppose_count > 0:
        return "opposes"
    if support_count > 0:
        return "supports"
    if review_count > 0:
        return "needs_review"
    return ""


def _news_priority_score(
    *,
    supports: tuple[CatalystEvent, ...],
    opposes: tuple[CatalystEvent, ...],
) -> int:
    events = supports or opposes
    if not events:
        return _NEWS_DIRECT_WEAK_SCORE
    lead = sorted(events, key=_news_event_rank_key, reverse=True)[0]
    if lead.source_quality_score >= 3 or lead.source_count >= 2:
        return _NEWS_DIRECT_STRONG_SCORE
    if lead.confidence >= 0.45:
        return _NEWS_DIRECT_MEDIUM_SCORE
    return _NEWS_DIRECT_WEAK_SCORE


def _news_evidence_stack_summary(metrics: dict[str, object]) -> str:
    support_count = int(metrics.get("news_catalyst_support_count", 0) or 0)
    oppose_count = int(metrics.get("news_catalyst_oppose_count", 0) or 0)
    review_count = int(metrics.get("news_catalyst_review_count", 0) or 0)
    parts: list[str] = []
    if support_count:
        parts.append(f"支持 {support_count} 条")
    if oppose_count:
        parts.append(f"反对 {oppose_count} 条")
    if review_count:
        parts.append(f"待复核 {review_count} 条")
    return "｜".join(parts)


def _pick_relevance_text(pick: PickResult) -> str:
    values: list[str] = [pick.symbol, pick.name]
    values.extend(str(reason) for reason in pick.reasons)
    values.extend(str(strategy) for strategy in pick.strategies)
    metrics = pick.metrics or {}
    values.extend(
        str(metrics.get(key, "")) for key in ("sector", "industry", "candidate_status")
    )
    values.extend(_SYMBOL_THEME_TAGS.get(pick.symbol, ()))
    text = " ".join(value.strip().lower() for value in values if str(value).strip())
    return text
