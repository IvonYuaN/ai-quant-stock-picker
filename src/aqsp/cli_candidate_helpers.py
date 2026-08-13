"""Candidate annotation helpers extracted from ``cli.py``.

Contains candidate status enrichment, data-quality annotation, cross-market
context annotation, news-watch candidate appending, and the observation-card
cap reader. All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import replace
from typing import Any

import pandas as pd

from aqsp.models import PickResult, ScreeningConfig
from aqsp.strategy import score_symbol

LOGGER = logging.getLogger(__name__)


def _candidate_blocker_map(portfolio_summary: Any | None) -> dict[str, str]:
    blockers: dict[str, str] = {}
    if portfolio_summary is None:
        return blockers
    for item in tuple(getattr(portfolio_summary, "execution_blockers", ()) or ()):
        raw = str(item).strip()
        if not raw or ":" not in raw:
            continue
        display, reason = raw.split(":", 1)
        symbol = display.split(" ", 1)[0].strip()
        clean_reason = reason.strip()
        if symbol and clean_reason:
            blockers[symbol] = clean_reason
    return blockers


def _candidate_review_map(portfolio_summary: Any | None) -> dict[str, dict[str, str]]:
    reviews: dict[str, dict[str, str]] = {}
    if portfolio_summary is None:
        return reviews
    for item in tuple(getattr(portfolio_summary, "watch_reviews", ()) or ()):
        symbol = str(getattr(item, "symbol", "") or "").strip()
        if not symbol:
            continue
        reviews[symbol] = {
            "blocker": str(getattr(item, "blocker", "") or ""),
            "next_step": str(getattr(item, "next_step", "") or ""),
            "review_window": str(getattr(item, "review_window", "") or ""),
            "priority": str(getattr(item, "priority", "") or ""),
        }
    return reviews


def _default_candidate_review(status: str) -> dict[str, str]:
    if status == "新晋":
        return {
            "next_step": "等待量价继续走强后，再评估是否转入纸面复核名单",
            "review_window": "盘中走强后",
            "priority": "high",
        }
    if status == "延续上升":
        return {
            "next_step": "优先复核趋势延续与承接强度，再决定是否提升纸面复核优先级",
            "review_window": "午前确认后",
            "priority": "medium",
        }
    if status == "延续下降":
        return {
            "next_step": "若弱势延续则继续观察，等待重新企稳后再恢复关注",
            "review_window": "尾盘前",
            "priority": "low",
        }
    return {}


def _annotate_candidate_status(
    picks: list[PickResult],
    *,
    diff: Any | None,
    portfolio_summary: Any | None,
) -> list[PickResult]:
    if not picks:
        return picks

    from aqsp.portfolio.snapshot import build_candidate_status_map

    status_map = build_candidate_status_map(diff)
    blocker_map = _candidate_blocker_map(portfolio_summary)
    review_map = _candidate_review_map(portfolio_summary)

    enriched: list[PickResult] = []
    for pick in picks:
        status = status_map.get(pick.symbol, "")
        review = review_map.get(pick.symbol, {})
        blocker_reason = str(
            review.get("blocker", "") or blocker_map.get(pick.symbol, "")
        )
        if not status and blocker_reason:
            status = "观察阻塞"
        if not review and status:
            review = _default_candidate_review(status)
        if not status and not blocker_reason:
            enriched.append(pick)
            continue
        metrics = dict(pick.metrics)
        if status:
            metrics["candidate_status"] = status
        if blocker_reason:
            metrics["candidate_blocker"] = blocker_reason
        if review:
            metrics["candidate_next_step"] = str(review.get("next_step", "") or "")
            metrics["candidate_review_window"] = str(
                review.get("review_window", "") or ""
            )
            # 消息后置复核优先级是证据派生字段，不能被快照状态的通用优先级覆盖。
            context_priority = str(
                metrics.get("candidate_review_priority", "") or ""
            ).strip()
            metrics["candidate_review_priority"] = (
                context_priority
                if context_priority in {"优先复核", "风险复核", "常规"}
                else str(review.get("priority", "") or "")
            )
        enriched.append(replace(pick, metrics=metrics))
    return enriched


def _market_context_review_priority(metrics: dict[str, Any]) -> tuple[str, str]:
    """Map post-screening evidence to a display-only review priority."""
    news_judgement = str(metrics.get("news_catalyst_judgement", "") or "").strip()
    news_priority = int(metrics.get("news_catalyst_priority_score", 0) or 0)
    cross_action = str(metrics.get("cross_market_action", "") or "").strip()
    cross_priority = float(metrics.get("cross_market_priority_score", 0) or 0)
    support_count = int(metrics.get("news_catalyst_support_count", 0) or 0)
    oppose_count = int(metrics.get("news_catalyst_oppose_count", 0) or 0)
    conflict_count = int(metrics.get("cross_market_conflict_event_count", 0) or 0)
    has_evidence = bool(
        news_judgement
        or news_priority > 0
        or cross_action
        or cross_priority > 0
        or metrics.get("cross_market_rule_ids")
        or metrics.get("cross_market_summaries")
    )
    if not has_evidence:
        return "", ""
    if (
        news_judgement == "opposes"
        or cross_action == "风险复核"
        or oppose_count > 0
        or conflict_count > support_count
    ):
        return "风险复核", "存在负向或冲突证据，先做风险复核"
    if (
        cross_action == "优先复核"
        or news_priority >= 3
        or cross_priority >= 3
        or news_judgement == "supports"
        and support_count > 0
    ):
        return "优先复核", "存在明确正向消息或跨市场传导证据"
    return "常规", "存在消息或跨市场线索，但尚未达到强复核条件"


def _merge_candidate_note(existing: str, note: str) -> str:
    existing = str(existing or "").strip()
    note = str(note or "").strip()
    if not existing:
        return note
    if not note or note in existing:
        return existing
    return f"{existing}；{note}"


def _annotate_data_quality_context(
    picks: list[PickResult],
    *,
    anomaly_alerts: list[Any],
    freshness_reports: list[Any],
    current_dates: dict[str, str] | None = None,
) -> list[PickResult]:
    if not picks:
        return picks

    alerts_by_symbol: dict[str, list[Any]] = {}
    for alert in anomaly_alerts:
        symbol = str(getattr(alert, "symbol", "") or "").strip()
        if symbol:
            observed_date = str(getattr(alert, "observed_date", "") or "").strip()
            current_date = str((current_dates or {}).get(symbol, "") or "").strip()
            if current_date and observed_date and observed_date != current_date:
                continue
            alerts_by_symbol.setdefault(symbol, []).append(alert)

    freshness_by_symbol = {
        str(getattr(report, "symbol", "") or "").strip(): report
        for report in freshness_reports
        if str(getattr(report, "symbol", "") or "").strip()
    }

    enriched: list[PickResult] = []
    for pick in picks:
        alerts = alerts_by_symbol.get(pick.symbol, [])
        freshness = freshness_by_symbol.get(pick.symbol)
        quality_notes: list[str] = []
        severe_notes: list[str] = []

        if freshness is not None and getattr(freshness, "status", "fresh") != "fresh":
            note = (
                f"数据新鲜度{getattr(freshness, 'status', '')}: "
                f"{getattr(freshness, 'last_date', '') or 'N/A'}"
                f"/延迟{getattr(freshness, 'delay_days', 'N/A')}天"
            )
            quality_notes.append(note)
            if getattr(freshness, "status", "") == "critical":
                severe_notes.append(note)

        for alert in alerts:
            note = str(getattr(alert, "detail", "") or "").strip()
            if not note:
                continue
            quality_notes.append(note)
            if getattr(alert, "severity", "") == "critical":
                severe_notes.append(note)

        if not quality_notes:
            enriched.append(pick)
            continue

        metrics = dict(pick.metrics)
        metrics["data_quality_status"] = "critical" if severe_notes else "watch"
        metrics["data_quality_alerts"] = tuple(quality_notes[:5])
        if severe_notes:
            metrics["candidate_next_step"] = _merge_candidate_note(
                str(metrics.get("candidate_next_step", "") or ""),
                "先复核数据质量: " + "；".join(severe_notes[:2]),
            )

        risks = tuple(
            dict.fromkeys(
                (*pick.risks, *(f"数据质量: {note}" for note in quality_notes[:3]))
            )
        )
        enriched.append(replace(pick, risks=risks, metrics=metrics))
    return enriched


def _annotate_cross_market_context(
    picks: list[PickResult],
    *,
    market_context: Any | None,
) -> list[PickResult]:
    if not picks or market_context is None:
        return picks

    from aqsp.market_context import market_context_metrics_for_pick

    context_by_symbol: dict[str, dict[str, object]] = {}
    symbols_by_rule: dict[str, list[str]] = {}
    for pick in picks:
        context_metrics = market_context_metrics_for_pick(pick, market_context)
        if not context_metrics:
            continue
        context_by_symbol[pick.symbol] = context_metrics
        for rule_id in tuple(context_metrics.get("cross_market_rule_ids", ()) or ()):
            clean_rule_id = str(rule_id).strip()
            if clean_rule_id and pick.symbol not in symbols_by_rule.setdefault(
                clean_rule_id, []
            ):
                symbols_by_rule[clean_rule_id].append(pick.symbol)

    enriched: list[PickResult] = []
    for pick in picks:
        context_metrics = context_by_symbol.get(pick.symbol)
        if not context_metrics:
            enriched.append(pick)
            continue
        metrics = dict(pick.metrics)
        metrics.update(context_metrics)
        rule_ids = tuple(context_metrics.get("cross_market_rule_ids", ()) or ())
        mapped_symbols = tuple(
            symbol
            for rule_id in rule_ids
            for symbol in symbols_by_rule.get(str(rule_id).strip(), ())
            if symbol != pick.symbol
        )
        metrics["cross_market_candidate_symbols"] = tuple(dict.fromkeys(mapped_symbols))
        metrics["cross_market_candidate_count"] = len(
            metrics["cross_market_candidate_symbols"]
        )
        metrics["cross_market_candidate_mapping_status"] = (
            "matched_current_candidates"
            if mapped_symbols
            else "single_current_candidate"
        )
        evidence_ids = {
            str(item).strip()
            for item in tuple(metrics.get("artifact_ids", ()) or ())
            if str(item).strip()
        }
        news_payload = "|".join(
            str(metrics.get(key, "") or "").strip()
            for key in (
                "news_catalyst_source",
                "news_catalyst_url",
                "news_catalyst_title",
                "news_catalyst_published_at",
            )
        )
        if news_payload.strip("|"):
            evidence_ids.add(
                "news:" + hashlib.sha256(news_payload.encode("utf-8")).hexdigest()[:16]
            )
        rule_payload = "|".join(
            str(metrics.get(key, "") or "")
            for key in ("cross_market_rule_ids", "cross_market_chain_summary")
        )
        if rule_payload.strip("|"):
            evidence_ids.add(
                "market-context:"
                + hashlib.sha256(rule_payload.encode("utf-8")).hexdigest()[:16]
            )
        if evidence_ids:
            metrics["artifact_ids"] = tuple(sorted(evidence_ids))
        review_priority, review_reason = _market_context_review_priority(metrics)
        if review_priority:
            metrics["candidate_review_priority"] = review_priority
            metrics["candidate_review_priority_reason"] = review_reason
        enriched.append(replace(pick, metrics=metrics))
    return enriched


def _append_cross_market_watch_candidates(
    picks: list[PickResult],
    screened_picks: list[PickResult],
    *,
    market_context: Any | None,
    screen_frames: dict[str, pd.DataFrame] | None = None,
    screening_config: ScreeningConfig | None = None,
    thresholds: Any | None = None,
    max_candidates: int = 0,
) -> list[PickResult]:
    """Expose message-linked candidates after an independent technical check.

    The regular ranking can omit a symbol before the news layer sees it. For
    affected symbols with a fresh frame, score it with the same deterministic
    technical function, then keep it observation-only instead of promoting it
    into the ranked picks.
    """
    if market_context is None or max_candidates < 0:
        return picks

    from aqsp.market_context import market_context_metrics_for_pick

    candidate_pool = list(screened_picks)
    news_watch_candidates = tuple(
        getattr(market_context, "news_watch_candidates", ()) or ()
    )
    if screen_frames and screening_config is not None and thresholds is not None:
        ranked_symbols = {candidate.symbol for candidate in candidate_pool}
        affected_symbols = {
            str(symbol).strip()
            for implication in getattr(market_context, "cross_market_implications", ())
            for symbol in tuple(getattr(implication, "affected_symbols", ()) or ())
            if str(symbol).strip()
        }
        affected_symbols.update(
            str(getattr(candidate, "symbol", "") or "").strip()
            for candidate in news_watch_candidates
            if str(getattr(candidate, "symbol", "") or "").strip()
        )
        for symbol in sorted(affected_symbols - ranked_symbols):
            frame = screen_frames.get(symbol)
            if frame is None or frame.empty:
                continue
            try:
                technical_candidate = score_symbol(
                    symbol,
                    frame,
                    screening_config,
                    thresholds.scoring,
                    thresholds,
                )
            except (ValueError, IndexError, KeyError, TypeError) as exc:
                LOGGER.debug("消息关联标的技术确认失败 %s: %s", symbol, exc)
                continue
            if technical_candidate is not None:
                candidate_pool.append(technical_candidate)

    selected_symbols = {pick.symbol for pick in picks}
    watch_candidates: list[PickResult] = []
    for candidate in candidate_pool:
        if candidate.symbol in selected_symbols:
            continue
        metrics = market_context_metrics_for_pick(candidate, market_context)
        if not metrics:
            watch = next(
                (
                    item
                    for item in news_watch_candidates
                    if str(getattr(item, "symbol", "") or "").strip()
                    == candidate.symbol
                ),
                None,
            )
            if watch is not None:
                metrics = {
                    "cross_market_primary_theme": str(
                        getattr(watch, "relation", "") or ""
                    ),
                    "cross_market_rule_ids": (
                        "news_watch:"
                        + str(getattr(watch, "relation", "") or "industry"),
                    ),
                    "cross_market_action": "观察为主",
                    "cross_market_priority_score": int(
                        getattr(watch, "priority_score", 0) or 0
                    ),
                    "cross_market_affected_sectors": tuple(
                        getattr(watch, "affected_sectors", ()) or ()
                    ),
                    "cross_market_transmission_path": tuple(
                        getattr(watch, "transmission_path", ()) or ()
                    ),
                    "cross_market_validation_signals": tuple(
                        getattr(watch, "validation_signals", ()) or ()
                    ),
                    "cross_market_invalidation_signals": tuple(
                        getattr(watch, "invalidation_signals", ()) or ()
                    ),
                    "news_catalyst_title": str(getattr(watch, "event_title", "") or ""),
                    "news_catalyst_summary": str(getattr(watch, "summary", "") or ""),
                    "news_catalyst_source": str(getattr(watch, "source", "") or ""),
                    "news_catalyst_url": str(getattr(watch, "source_url", "") or ""),
                    "news_catalyst_published_at": str(
                        getattr(watch, "published_at", "") or ""
                    ),
                }
        rule_ids = tuple(metrics.get("cross_market_rule_ids", ()) or ())
        priority = int(metrics.get("cross_market_priority_score", 0) or 0)
        if not rule_ids or priority < 2:
            continue
        validation = tuple(metrics.get("cross_market_validation_signals", ()) or ())
        next_step = (
            str(validation[0]).strip()
            if validation and str(validation[0]).strip()
            else "等待盘中量价与板块扩散确认"
        )
        metrics.update(
            {
                "candidate_status": "消息产业链观察",
                "observation_only": True,
                "portfolio_action": "observation_only",
                "candidate_review_priority": "优先复核",
                "candidate_review_priority_reason": "实时消息规则与当前技术扫描结果匹配，尚未进入正式排序",
                "candidate_next_step": next_step,
                "cross_market_candidate_origin": "news_to_current_universe",
                "news_watch_relation": str(
                    getattr(
                        next(
                            (
                                item
                                for item in news_watch_candidates
                                if str(getattr(item, "symbol", "") or "").strip()
                                == candidate.symbol
                            ),
                            None,
                        ),
                        "relation",
                        "",
                    )
                    or "",
                ),
            }
        )
        watch_candidates.append(replace(candidate, metrics=metrics))
        selected_symbols.add(candidate.symbol)
        if max_candidates > 0 and len(watch_candidates) >= max_candidates:
            break
    return [*picks, *watch_candidates]


def _news_watch_candidate_limit() -> int:
    """Read the optional observation-card cap; zero means keep all matches."""
    raw = os.getenv("AQSP_NEWS_WATCH_MAX_CANDIDATES", "0").strip()
    try:
        value = int(raw)
    except ValueError:
        return 0
    return max(value, 0)
