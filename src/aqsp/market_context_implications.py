"""Cross-market implication computation engine.

Extracted from ``market_context.py`` to isolate the deterministic logic
that translates catalyst events + realtime observations into
``CrossMarketImplication`` objects with evidence stacks, strength ratings,
and action labels.

Dependency chain (no cycles):
    market_context_implications
        → market_context_cross_market  (rules, dataclasses)
        → market_context_realtime      (observation types)
        → news.catalysts               (CatalystEvent, Impact)
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from aqsp.core.time import to_shanghai
from aqsp.market_context_cross_market import (
    CrossMarketImplication,
    CrossMarketImplicationRule,
    _CROSS_MARKET_RULES,
)
from aqsp.market_context_realtime import (
    RealtimeCrossMarketContext,
    RealtimeCrossMarketObservation,
)
from aqsp.news.catalysts import CatalystEvent, Impact


_AUTHORITATIVE_SOURCE_TOKENS = (
    "公告",
    "交易所",
    "巨潮",
    "公司",
    "证监会",
    "SEC",
    "FederalReserve",
    "Federal Reserve",
    "ECB",
    "NASA",
)
_PRIORITY_MEDIA_SOURCE_TOKENS = ("新华社", "央视", "国常会", "发改委", "工信部")
_MAINSTREAM_MEDIA_SOURCE_TOKENS = (
    "中国新闻网",
    "中新网",
    "财联社",
    "证券报",
    "东财",
    "同花顺",
    "新浪",
    "路透",
    "彭博",
    "Reuters",
    "Bloomberg",
    "NVIDIA",
    "MarketWatch",
)

_CROSS_MARKET_STACK_SUPPORT_BONUS = 2
_CROSS_MARKET_STACK_CONFLICT_PENALTY = 2
_CROSS_MARKET_STRONG_SCORE = 3
_CROSS_MARKET_MEDIUM_SCORE = 1

_REALTIME_IMPLICATION_THRESHOLDS: dict[str, float] = {
    "SPX": 0.75,
    "NASDAQ100": 0.75,
    "HSI": 0.75,
    "DXY": 0.35,
    "US10Y": 0.10,
    "WTI": 1.00,
    "GOLD": 0.75,
}


def _cross_market_implications(
    events: list[CatalystEvent],
    *,
    generated_at: str,
    realtime_context: RealtimeCrossMarketContext | None = None,
) -> tuple[CrossMarketImplication, ...]:
    matched_events: dict[str, list[CatalystEvent]] = {}
    generated_dt = _parse_iso_datetime(generated_at)
    for event in events:
        text = _event_rule_text(event)
        if not text:
            continue
        for rule in _CROSS_MARKET_RULES:
            if _rule_matches_event(rule, text):
                matched_events.setdefault(rule.rule_id, []).append(event)
    for rule_id, realtime_events in _realtime_cross_market_events(
        realtime_context
    ).items():
        matched_events.setdefault(rule_id, []).extend(realtime_events)
    matched: list[CrossMarketImplication] = []
    for rule in _CROSS_MARKET_RULES:
        events_for_rule = matched_events.get(rule.rule_id, [])
        if not events_for_rule:
            continue
        if rule.required_keyword_groups:
            # A precise trigger starts the theme; broader keyword matches then
            # contribute corroborating or conflicting evidence only.
            events_for_rule = _expand_rule_evidence_events(
                rule,
                seed_events=events_for_rule,
                events=events,
            )
        matched.append(
            _implication_for_events(
                rule,
                tuple(events_for_rule),
                generated_dt=generated_dt,
            )
        )
    ordered = sorted(
        matched,
        key=lambda item: (
            _implication_priority_score(item),
            item.support_event_count,
            -item.conflict_event_count,
            item.theme,
        ),
        reverse=True,
    )
    return tuple(ordered[:5])


def _realtime_cross_market_events(
    context: RealtimeCrossMarketContext | None,
) -> dict[str, tuple[CatalystEvent, ...]]:
    """Translate fresh, significant market moves into rule evidence.

    This is intentionally a small deterministic bridge rather than a second
    scoring system.  It only emits evidence for relationships represented by
    existing rules: broad risk-on requires an equity-index move, liquidity
    easing requires both a weaker dollar and lower 10Y yield, and oil shock
    requires a meaningful WTI move.  Values that are stale, unavailable, or
    below the explicit thresholds remain display-only.
    """

    if context is None:
        return {}
    observations = {
        item.instrument: item
        for item in context.observations
        if item.status == "fresh"
        and item.change_pct is not None
        and math.isfinite(item.change_pct)
    }

    result: dict[str, tuple[CatalystEvent, ...]] = {}

    equity_triggered = any(
        (item := observations.get(instrument)) is not None
        and item.change_pct is not None
        and item.change_pct >= _realtime_threshold(instrument)
        for instrument in ("SPX", "NASDAQ100")
    )
    equity_events = tuple(
        _realtime_implication_event(item, rule_id="us_risk_on", impact="positive")
        for instrument in ("SPX", "NASDAQ100", "HSI")
        if (item := observations.get(instrument)) is not None
        and item.change_pct is not None
        and item.change_pct >= _realtime_threshold(instrument)
    )
    if equity_triggered:
        result["us_risk_on"] = equity_events

    dollar = observations.get("DXY")
    treasury = observations.get("US10Y")
    if (
        dollar is not None
        and treasury is not None
        and dollar.change_pct is not None
        and treasury.change_pct is not None
        and dollar.change_pct <= -_realtime_threshold("DXY")
        and treasury.change_pct <= -_realtime_threshold("US10Y")
    ):
        liquidity_events = [
            _realtime_implication_event(
                dollar,
                rule_id="global_liquidity_easing",
                impact="positive",
            ),
            _realtime_implication_event(
                treasury,
                rule_id="global_liquidity_easing",
                impact="positive",
            ),
        ]
        gold = observations.get("GOLD")
        if (
            gold is not None
            and gold.change_pct is not None
            and gold.change_pct >= _realtime_threshold("GOLD")
        ):
            liquidity_events.append(
                _realtime_implication_event(
                    gold,
                    rule_id="global_liquidity_easing",
                    impact="positive",
                )
            )
        result["global_liquidity_easing"] = tuple(liquidity_events)

    oil = observations.get("WTI")
    if (
        oil is not None
        and oil.change_pct is not None
        and abs(oil.change_pct) >= _realtime_threshold("WTI")
    ):
        result["oil_price_shock"] = (
            _realtime_implication_event(
                oil,
                rule_id="oil_price_shock",
                impact="positive" if oil.change_pct > 0 else "negative",
            ),
        )
    return result


def _realtime_threshold(instrument: str) -> float:
    return _REALTIME_IMPLICATION_THRESHOLDS[instrument]


def _realtime_implication_event(
    observation: RealtimeCrossMarketObservation,
    *,
    rule_id: str,
    impact: Impact,
) -> CatalystEvent:
    change_pct = float(observation.change_pct or 0.0)
    direction = "上涨" if change_pct > 0 else "下跌"
    source = observation.provenance.source or "实时跨市场行情"
    title = f"{observation.instrument} 实时{direction} {change_pct:+.2f}%"
    evidence = (
        f"{observation.instrument} 变动 {change_pct:+.2f}%；"
        f"观测时间 {observation.observed_at}；来源 {source}",
    )
    return CatalystEvent(
        title=title,
        source=source,
        published_at=observation.observed_at,
        source_fetched_at=observation.fetched_at,
        impact=impact,
        category="实时跨市场行情",
        confidence=0.68,
        source_count=1,
        verification="实时观测",
        source_quality_label="实时行情源",
        source_quality_score=3,
        inference=f"{observation.instrument} 的实时变动达到 {rule_id} 规则阈值。",
        url=observation.provenance.source_url,
        source_region="international",
        supporting_evidence=evidence,
    )


def _expand_rule_evidence_events(
    rule: CrossMarketImplicationRule,
    *,
    seed_events: list[CatalystEvent],
    events: list[CatalystEvent],
) -> list[CatalystEvent]:
    selected = list(seed_events)
    selected_ids = {id(event) for event in selected}
    for event in events:
        if id(event) in selected_ids:
            continue
        text = _event_rule_text(event)
        if any(keyword in text for keyword in rule.keywords):
            selected.append(event)
            selected_ids.add(id(event))
    return selected


def _implication_for_events(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
    *,
    generated_dt: datetime | None,
) -> CrossMarketImplication:
    ranked_events = sorted(
        events,
        key=lambda item: _rule_event_rank_key(rule, item, generated_dt=generated_dt),
        reverse=True,
    )
    primary_event = ranked_events[0]
    support_event_count, conflict_event_count = _implication_event_bias_counts(
        rule,
        events,
    )
    evidence_stack_summary = _implication_evidence_stack_summary(
        support_event_count=support_event_count,
        conflict_event_count=conflict_event_count,
    )
    strength = _implication_strength(
        rule,
        events,
        generated_dt=generated_dt,
    )
    action = _implication_action(strength)
    targets = "、".join(rule.a_share_targets[:5])
    evidence_points = _implication_evidence_points(
        primary_event,
        generated_dt=generated_dt,
    )
    evidence_suffix = _format_implication_evidence_suffix(evidence_points)
    stack_suffix = f"；{evidence_stack_summary}" if evidence_stack_summary else ""
    supporting_evidence, contradicting_evidence = _implication_evidence_lists(
        rule,
        events,
    )
    affected_symbols = _implication_affected_symbols(events)
    source_regions = _text_values(
        event.source_region for event in events if event.source_region
    )
    impact_direction = _implication_impact_direction(events)
    confidence = _implication_confidence(
        strength=strength,
        support_event_count=support_event_count,
        conflict_event_count=conflict_event_count,
    )
    summary_line = (
        f"传导推演[{strength}]: {rule.theme} -> A股{targets}；"
        f"动作 {action}；观察窗 {rule.observation_window}{stack_suffix}；"
        f"{rule.confirmation_hint}{evidence_suffix}"
    )
    return CrossMarketImplication(
        rule_id=rule.rule_id,
        theme=rule.theme,
        linkage_basis=rule.linkage_basis,
        a_share_targets=rule.a_share_targets,
        first_order_targets=rule.first_order_targets,
        second_order_targets=rule.second_order_targets,
        pressure_targets=rule.pressure_targets,
        execution_watchpoints=rule.execution_watchpoints,
        relevance_keywords=rule.relevance_keywords,
        lead_window=rule.lead_window,
        observation_window=rule.observation_window,
        transmission_path=rule.transmission_path,
        validation_signals=rule.validation_signals,
        invalidation_signals=rule.invalidation_signals,
        confirmation_hint=rule.confirmation_hint,
        strength=strength,
        action=action,
        source_title=str(primary_event.title or "").strip(),
        source_category=str(primary_event.category or "").strip(),
        source_quality_label=_event_source_quality_label(primary_event),
        source_quality_score=_event_source_quality_score(primary_event),
        source_published_at=str(primary_event.published_at or "").strip(),
        support_event_count=support_event_count,
        conflict_event_count=conflict_event_count,
        evidence_stack_summary=evidence_stack_summary,
        evidence_points=evidence_points,
        summary_line=summary_line,
        affected_sectors=rule.a_share_targets,
        affected_symbols=affected_symbols,
        transmission_hypothesis=" -> ".join(rule.transmission_path),
        confidence=confidence,
        time_horizon=(f"领先 {rule.lead_window}；观察 {rule.observation_window}"),
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        source_regions=source_regions,
        impact_direction=impact_direction,
        source_url=str(primary_event.url or "").strip(),
        source_fetched_at=str(primary_event.source_fetched_at or "").strip(),
    )


def _implication_priority_score(implication: CrossMarketImplication) -> int:
    return {"强": 3, "中": 2, "弱": 1}.get(implication.strength, 0)


def _text_values(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        text = values.strip()
        return (text,) if text else ()
    try:
        iterator = iter(values)  # type: ignore[arg-type]
    except TypeError:
        return ()
    result: list[str] = []
    for value in iterator:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _implication_affected_symbols(
    events: tuple[CatalystEvent, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for event in events:
        candidates = tuple(event.affected_symbols) or (
            (event.symbol,) if event.symbol else ()
        )
        for symbol in candidates:
            clean = str(symbol or "").strip()
            if clean and clean not in values:
                values.append(clean)
    return tuple(values)


def _evidence_label(event: CatalystEvent) -> str:
    source = str(event.source or "未标注来源").strip()
    title = str(event.title or "").strip()
    return f"{source}: {title}" if title else source


def _implication_evidence_lists(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    supporting: list[str] = []
    contradicting: list[str] = []
    for event in events:
        if _event_supports_rule(rule, event):
            target = supporting
            evidence = (event.title, *event.supporting_evidence)
        elif str(event.impact or "").strip() == "neutral":
            target = supporting
            evidence = event.supporting_evidence
        else:
            target = contradicting
            evidence = (event.title, *event.contradicting_evidence)
        for item in evidence:
            text = str(item or "").strip()
            if text and text not in target:
                target.append(text)
        if not event.supporting_evidence and event.title:
            label = _evidence_label(event)
            if label not in target:
                target.append(label)
    return tuple(supporting[:6]), tuple(contradicting[:6])


def _implication_confidence(
    *,
    strength: str,
    support_event_count: int,
    conflict_event_count: int,
) -> float:
    base = {"强": 0.82, "中": 0.64, "弱": 0.42}.get(strength, 0.0)
    if support_event_count >= 2:
        base += 0.06
    if conflict_event_count > 0:
        base -= 0.12
    return max(0.0, min(1.0, round(base, 2)))


def _implication_impact_direction(
    events: tuple[CatalystEvent, ...],
) -> Literal["positive", "negative", "mixed", "neutral"]:
    directions = {
        str(event.impact or "").strip()
        for event in events
        if str(event.impact or "").strip() in {"positive", "negative"}
    }
    if len(directions) > 1:
        return "mixed"
    if directions:
        return next(iter(directions))  # type: ignore[return-value]
    return "neutral"


def _cross_market_overview_from_implications(
    implications: tuple[CrossMarketImplication, ...],
) -> str:
    if not implications:
        return ""
    primary = sorted(
        implications,
        key=lambda item: (
            _implication_priority_score(item),
            item.support_event_count,
            -item.conflict_event_count,
            item.theme,
        ),
        reverse=True,
    )[0]
    targets = "、".join(primary.a_share_targets[:3])
    action = _cross_market_overview_action(primary.action)
    if targets:
        return f"{primary.theme}，{action} A股{targets}"
    return f"{primary.theme}，{action}"


def _cross_market_overview_action(action: str) -> str:
    if action == "优先复核":
        return "优先看"
    if action == "重点跟踪":
        return "重点看"
    if action == "观察为主":
        return "先观察"
    return "先看"


def _pick_implication_detail_lines(
    implication: CrossMarketImplication,
) -> tuple[str, ...]:
    lines: list[str] = []
    lines.append(
        "传导链: "
        f"{implication.linkage_basis}｜领先窗 {implication.lead_window}｜"
        + " -> ".join(implication.transmission_path[:2])
    )
    if implication.first_order_targets:
        lines.append(f"先看链条: {'、'.join(implication.first_order_targets[:3])}")
    if implication.second_order_targets:
        lines.append(f"扩散链条: {'、'.join(implication.second_order_targets[:3])}")
    if implication.pressure_targets:
        lines.append(f"承压方向: {'、'.join(implication.pressure_targets[:2])}")
    if implication.execution_watchpoints:
        lines.append(f"盘中锚点: {implication.execution_watchpoints[0]}")
    if implication.validation_signals:
        lines.append(f"确认信号: {implication.validation_signals[0]}")
    if implication.invalidation_signals:
        lines.append(f"失效条件: {implication.invalidation_signals[0]}")
    if implication.evidence_stack_summary:
        lines.append(f"证据堆栈: {implication.evidence_stack_summary}")
    return tuple(lines)


def _pick_chain_summary(implication: CrossMarketImplication) -> str:
    parts = [implication.linkage_basis]
    if implication.lead_window:
        parts.append(f"领先窗 {implication.lead_window}")
    if implication.first_order_targets:
        parts.append(f"先看 {implication.first_order_targets[0]}")
    if implication.execution_watchpoints:
        parts.append(f"锚点 {implication.execution_watchpoints[0]}")
    if implication.validation_signals:
        parts.append(f"确认 {implication.validation_signals[0]}")
    if implication.invalidation_signals:
        parts.append(f"失效 {implication.invalidation_signals[0]}")
    if implication.pressure_targets:
        parts.append(f"承压 {implication.pressure_targets[0]}")
    if implication.evidence_stack_summary:
        parts.append(implication.evidence_stack_summary)
    return "｜".join(parts)


def _implication_strength(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
    *,
    generated_dt: datetime | None,
) -> str:
    primary_event = max(
        events,
        key=lambda item: _rule_event_rank_key(rule, item, generated_dt=generated_dt),
    )
    score = 1  # 已命中明确跨市场规则，先给基础证据分
    if float(primary_event.confidence) >= 0.75:
        score += 1
    if _event_source_quality_score(primary_event) >= 3:
        score += 1
    if int(primary_event.source_count) >= 2:
        score += 1
    age_minutes = _event_age_minutes(
        primary_event.published_at,
        generated_dt=generated_dt,
    )
    if age_minutes is not None:
        if age_minutes <= 180:
            score += 1
        elif age_minutes > 720:
            score -= 1
    support_event_count, conflict_event_count = _implication_event_bias_counts(
        rule,
        events,
    )
    if support_event_count >= 2:
        score += _CROSS_MARKET_STACK_SUPPORT_BONUS
    if conflict_event_count > 0:
        score -= _CROSS_MARKET_STACK_CONFLICT_PENALTY
    if score >= _CROSS_MARKET_STRONG_SCORE:
        return "强"
    if score >= _CROSS_MARKET_MEDIUM_SCORE:
        return "中"
    return "弱"


def _rule_event_rank_key(
    rule: CrossMarketImplicationRule,
    event: CatalystEvent,
    *,
    generated_dt: datetime | None,
) -> tuple[int, int, float, int, int]:
    support_score = 1 if _event_supports_rule(rule, event) else 0
    age_minutes = _event_age_minutes(event.published_at, generated_dt=generated_dt)
    freshness_score = -age_minutes if age_minutes is not None else -(10**9)
    return (
        support_score,
        _event_source_quality_score(event),
        float(event.confidence),
        int(event.source_count),
        freshness_score,
    )


def _rule_matches_event(
    rule: CrossMarketImplicationRule,
    text: str,
) -> bool:
    if rule.keywords and not any(keyword in text for keyword in rule.keywords):
        return False
    for group in rule.required_keyword_groups:
        if group and not any(keyword in text for keyword in group):
            return False
    return True


def _event_rule_text(event: CatalystEvent) -> str:
    """Build rule input from both raw headlines and normalized chain evidence."""

    values = (
        event.title,
        event.inference,
        event.category,
        *event.affected_sectors,
        *event.transmission_path,
        *event.validation_signals,
        *event.supporting_evidence,
    )
    return " ".join(
        str(part).strip().casefold() for part in values if str(part).strip()
    )


def _event_supports_rule(
    rule: CrossMarketImplicationRule,
    event: CatalystEvent,
) -> bool:
    return str(event.impact or "").strip() in rule.supportive_impacts


def _implication_event_bias_counts(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
) -> tuple[int, int]:
    support_event_count = 0
    conflict_event_count = 0
    for event in events:
        impact = str(event.impact or "").strip()
        if not impact or impact == "neutral":
            continue
        if impact in rule.supportive_impacts:
            support_event_count += 1
        else:
            conflict_event_count += 1
    return support_event_count, conflict_event_count


def _implication_evidence_stack_summary(
    *,
    support_event_count: int,
    conflict_event_count: int,
) -> str:
    if support_event_count <= 1 and conflict_event_count <= 0:
        return ""
    return f"同向 {support_event_count} 条｜反向 {conflict_event_count} 条"


def _implication_action(strength: str) -> str:
    if strength == "强":
        return "优先复核"
    if strength == "中":
        return "重点跟踪"
    return "观察为主"


def _implication_evidence_points(
    event: CatalystEvent,
    *,
    generated_dt: datetime | None,
) -> tuple[str, ...]:
    parts: list[str] = []
    if float(event.confidence) > 0:
        parts.append(f"置信 {event.confidence:.2f}")
    if int(event.source_count) > 1:
        parts.append(f"{event.source_count} 源共振")
    if _event_source_quality_score(event) >= 3:
        parts.append(_event_source_quality_label(event))
    age_minutes = _event_age_minutes(event.published_at, generated_dt=generated_dt)
    if age_minutes is not None:
        parts.append(f"最新 {age_minutes} 分钟前")
    fetched_age_minutes = _event_age_minutes(
        event.source_fetched_at,
        generated_dt=generated_dt,
    )
    if fetched_age_minutes is not None:
        parts.append(f"抓取 {fetched_age_minutes} 分钟前")
    return tuple(parts)


def _format_implication_evidence_suffix(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    return "｜证据: " + " / ".join(parts) + "。"


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _event_age_minutes(
    published_at: str,
    *,
    generated_dt: datetime | None,
) -> int | None:
    if generated_dt is None:
        return None
    published_dt = _parse_iso_datetime(published_at)
    if published_dt is None:
        return None
    delta_seconds = (generated_dt - published_dt).total_seconds()
    if delta_seconds < 0:
        return None
    return int(delta_seconds // 60)


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return to_shanghai(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _event_source_quality_score(event: CatalystEvent) -> int:
    score = int(getattr(event, "source_quality_score", 0) or 0)
    if score > 1:
        return score
    source = str(getattr(event, "source", "") or "").strip()
    source_count = int(getattr(event, "source_count", 1) or 1)
    if any(token in source for token in _AUTHORITATIVE_SOURCE_TOKENS):
        return 4
    if (
        any(token in source for token in _PRIORITY_MEDIA_SOURCE_TOKENS)
        or source_count >= 2
    ):
        return 3
    if any(token in source for token in _MAINSTREAM_MEDIA_SOURCE_TOKENS):
        return 2
    return max(1, score)


def _event_source_quality_label(event: CatalystEvent) -> str:
    label = str(getattr(event, "source_quality_label", "") or "").strip()
    if label and label != "普通来源":
        return label
    return {
        4: "高价值来源",
        3: "多源/权威媒体",
        2: "主流媒体",
    }.get(_event_source_quality_score(event), "普通来源")
