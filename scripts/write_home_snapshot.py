#!/usr/bin/env python3
"""Build the bounded dashboard-home snapshot from one local runtime digest."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aqsp.core.time import now_shanghai, today_shanghai, to_shanghai
from aqsp.market_context import MarketContextArtifact, build_market_context_artifact
from aqsp.news.catalysts import (
    CatalystEvent,
    CatalystReport,
    load_catalyst_report_artifact,
)
from aqsp.web.data_provider import DashboardDataProvider
from aqsp.web.home_snapshot import (
    HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
    HOME_SNAPSHOT_SCHEMA_VERSION,
    MAX_HOME_SNAPSHOT_CANDIDATES,
    MAX_HOME_SNAPSHOT_DEBATES,
    MAX_HOME_SNAPSHOT_INDEX_DAYS,
    HomeDashboardSnapshot,
    HomeSnapshotDay,
    HomeSnapshotCandidate,
    HomeSnapshotColdstart,
    HomeSnapshotCrossMarket,
    HomeSnapshotDebate,
    HomeSnapshotIndex,
    HomeSnapshotMarketContext,
    HomeSnapshotMessage,
    HomeSnapshotSource,
    HomeSnapshotTechnicalMetric,
    MAX_HOME_SNAPSHOT_TECHNICAL_METRICS,
    is_home_recommendation,
    stale_after_for_task,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)


DEFAULT_OUTPUT_PATH = "data/runtime/home_dashboard_snapshot.json"
DEFAULT_INDEX_OUTPUT_PATH = "data/runtime/home_dashboard_snapshot_index.json"
MAX_HOME_DATES = 4
MAX_HOME_CANDIDATES = MAX_HOME_SNAPSHOT_CANDIDATES
MAX_HOME_SUMMARIES = 3
MAX_HOME_MESSAGES = 5
NEWS_REPORT_MAX_AGE_SECONDS = 6 * 60 * 60
_SOURCE_STATUS_LABELS = {
    "ok": "可用",
    "partial": "部分可用",
    "empty": "无数据",
    "timeout": "超时",
    "failed": "失败",
}
_EVENT_STATUS_LABELS = {
    "high_impact": "可用",
    "no_high_impact": "无高影响消息",
    "stale_only": "旧消息已排除",
    "no_valid_news": "无可用消息",
    "source_failed": "来源失败",
    "stale_cache": "旧缓存已排除",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalize_timestamp(value: object) -> str:
    """Normalize a legacy timestamp at the snapshot production boundary."""
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return to_shanghai(parsed).isoformat(timespec="seconds")


def _normalize_catalyst_report_for_snapshot(
    report: CatalystReport,
    signal_date: str,
) -> tuple[CatalystReport, int, int]:
    """Keep only dated current-day events without inventing missing timestamps."""
    current_events: list[CatalystEvent] = []
    historical_count = 0
    invalid_count = 0
    for event in report.events:
        published_at = _normalize_timestamp(event.published_at)
        if not published_at:
            invalid_count += 1
            continue
        if published_at[:10] != signal_date:
            historical_count += 1
            continue
        current_events.append(
            replace(
                event,
                published_at=published_at,
                source_fetched_at=_normalize_timestamp(event.source_fetched_at),
            )
        )
    return (
        replace(
            report,
            generated_at=_normalize_timestamp(report.generated_at),
            events=tuple(current_events),
        ),
        historical_count,
        invalid_count,
    )


def _first_text(*values: object) -> str:
    return next((text for value in values if (text := _text(value))), "")


def _bounded_unique_text(values: Iterable[object], limit: int) -> tuple[str, ...]:
    """Keep first-seen non-empty text, preserving the task's deterministic order."""
    selected: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in selected:
            selected.append(text)
        if len(selected) == limit:
            break
    return tuple(selected)


def _resolve_selected_date(payload: Any, requested_date: str) -> str:
    task_view = payload.task_view
    requested = _text(requested_date)
    payload_date = _first_text(getattr(task_view, "selected_date", ""))
    if requested:
        if payload_date and payload_date != requested:
            raise ValueError(
                "provider returned a historical date for the requested snapshot date"
            )
        return requested
    return _first_text(
        payload_date,
        getattr(task_view, "latest_date", ""),
        today_shanghai().isoformat(),
    )


def _snapshot_dates(task_view: Any, selected_date: str) -> tuple[str, ...]:
    return _bounded_unique_text(
        (selected_date, *(getattr(task_view, "available_dates", ()) or ())),
        MAX_HOME_DATES,
    )


def _snapshot_task_id(task_id: str) -> str:
    """Use the live intraday artifact for the midday display refresh."""
    return "intraday" if task_id.strip() == "midday" else task_id.strip()


def _snapshot_realtime_cross_market(task_id: str) -> dict | None:
    """Read the bounded sidecar; snapshot generation never performs network I/O."""
    if task_id.strip().lower() not in {"intraday", "live_short"}:
        return None
    configured = str(os.getenv("AQSP_REALTIME_CROSS_MARKET_PATH", "")).strip()
    path = (
        Path(configured).expanduser()
        if configured
        else PROJECT_ROOT / "data/runtime/realtime_cross_market_context.json"
    )
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"实时跨市场 sidecar 不可读，保留不可用状态: {exc}", file=sys.stderr)
        return None
    payload = artifact.get("payload") if isinstance(artifact, dict) else None
    return payload if isinstance(payload, dict) else None


def _candidate_context(candidate: Any) -> str:
    return _first_text(
        getattr(candidate, "news_catalyst_summary", ""),
        getattr(candidate, "cross_market_summary", ""),
        getattr(candidate, "decision_note", ""),
        getattr(candidate, "review_meta", ""),
    )


def _candidate_reasons(candidate: Any) -> tuple[str, ...]:
    raw = getattr(candidate, "reasons", ()) or ()
    if isinstance(raw, str):
        raw = (raw,)
    return _bounded_unique_text(raw, 8)


def _candidate_strategies(candidate: Any) -> tuple[str, ...]:
    raw = getattr(candidate, "strategies", ()) or ()
    if isinstance(raw, str):
        raw = tuple(item.strip() for item in raw.split(","))
    return _bounded_unique_text(raw, 6)


def _candidate_technical_metrics(
    candidate: Any,
) -> tuple[HomeSnapshotTechnicalMetric, ...]:
    """Expose only deterministic short-term fields already present in the card."""
    specifications = (
        ("close", "现价", "{:.2f}"),
        ("ret5_pct", "5日动能", "{:+.2f}%"),
        ("ret20_pct", "20日动能", "{:+.2f}%"),
        ("volume_ratio", "量比", "{:.2f}x"),
        ("rsi12", "RSI12", "{:.1f}"),
        ("bias20_pct", "MA20偏离", "{:+.2f}%"),
        ("stop_loss", "纸面止损", "{:.2f}"),
        ("take_profit", "纸面止盈", "{:.2f}"),
    )
    metrics: list[HomeSnapshotTechnicalMetric] = []
    for key, label, template in specifications:
        raw = getattr(candidate, key, None)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        metrics.append(
            HomeSnapshotTechnicalMetric(
                key=key, label=label, value=template.format(value)
            )
        )
        if len(metrics) == MAX_HOME_SNAPSHOT_TECHNICAL_METRICS:
            break
    return tuple(metrics)


def _has_candidate_deterministic_evidence(candidate: Any) -> bool:
    try:
        score = float(getattr(candidate, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return math.isfinite(score) and bool(_candidate_reasons(candidate))


def _snapshot_candidate(candidate: Any) -> HomeSnapshotCandidate | None:
    symbol = _text(getattr(candidate, "symbol", ""))
    if not symbol:
        return None
    reasons = _candidate_reasons(candidate)
    strategies = _candidate_strategies(candidate)
    return HomeSnapshotCandidate(
        symbol=symbol,
        display_name=_first_text(
            getattr(candidate, "display_name", ""), getattr(candidate, "name", "")
        ),
        # This value comes only from the deterministic task candidate card.
        score=float(getattr(candidate, "score", 0.0) or 0.0),
        research_status=_first_text(
            getattr(candidate, "action_label", ""),
            getattr(candidate, "status_label", ""),
            "待复核",
        ),
        next_step=_text(getattr(candidate, "next_step", "")),
        context=_candidate_context(candidate),
        deterministic_reasons=reasons,
        strategies=strategies,
        evidence_status=("有独立规则证据" if reasons else "证据不足"),
        technical_metrics=_candidate_technical_metrics(candidate),
    )


def _snapshot_candidates(payload: Any) -> tuple[HomeSnapshotCandidate, ...]:
    """Return bounded recommendation, observation, and blocked cards.

    Observation-only data must remain visible on the home page without becoming a
    recommendation. The typed candidate status is what keeps that boundary clear.
    """
    candidates: list[HomeSnapshotCandidate] = []
    symbols: set[str] = set()
    ordered = (
        *(getattr(payload.task_view, "detail_cards", ()) or ()),
        *(getattr(payload, "spotlights", ()) or ()),
    )
    recommendation_labels = (
        "纸面复核",
        "优先复核",
        "上调优先级",
        "第一顺位",
        "第二顺位",
        "后续顺位",
    )
    observation_markers = (
        "观察",
        "阻塞",
        "质量",
        "不可用",
        "过期",
        "待核对",
        "仅观察",
    )

    def card_kind(item: Any) -> str:
        raw_status = _first_text(
            getattr(item, "action_label", ""),
            getattr(item, "status_label", ""),
            getattr(item, "rank_label", ""),
        )
        if any(label in raw_status for label in recommendation_labels) and (
            _has_candidate_deterministic_evidence(item)
        ):
            return "recommendation"
        if getattr(item, "blocker", "") or any(
            marker in raw_status for marker in observation_markers
        ):
            return "observation"
        return ""

    # Keep recommendations first while retaining current observation evidence.
    for wanted_kind in ("recommendation", "observation"):
        for item in ordered:
            if card_kind(item) != wanted_kind:
                continue
            candidate = _snapshot_candidate(item)
            if candidate is None or candidate.symbol in symbols:
                continue
            if wanted_kind == "recommendation" and not is_home_recommendation(
                candidate
            ):
                continue
            candidates.append(candidate)
            symbols.add(candidate.symbol)
            if len(candidates) == MAX_HOME_CANDIDATES:
                return tuple(candidates)
    return tuple(candidates)


def _snapshot_debates(
    payload: Any,
    candidates: tuple[HomeSnapshotCandidate, ...],
) -> tuple[HomeSnapshotDebate, ...]:
    candidate_symbols = {candidate.symbol for candidate in candidates}
    debates = tuple(getattr(payload, "debates", ()) or ())
    selected: list[HomeSnapshotDebate] = []
    selected_symbols: set[str] = set()
    for debate in debates:
        symbol = _text(getattr(debate, "symbol", ""))
        if symbol not in candidate_symbols or symbol in selected_symbols:
            continue
        selected.append(
            HomeSnapshotDebate(
                symbol=symbol,
                display_name=_text(getattr(debate, "display_name", "")),
                conclusion=_first_text(
                    getattr(debate, "research_verdict", ""),
                    getattr(debate, "consensus", ""),
                ),
                primary_risk_gate=_text(getattr(debate, "primary_risk_gate", "")),
                next_trigger=_text(getattr(debate, "next_trigger", "")),
                active_roles=tuple(
                    _first_text(
                        getattr(view, "role_label", ""),
                        getattr(view, "role_id", ""),
                    )
                    for view in (getattr(debate, "agent_views", ()) or ())
                    if _first_text(
                        getattr(view, "role_label", ""),
                        getattr(view, "role_id", ""),
                    )
                ),
                round_count=int(getattr(debate, "round_count", 0) or 0),
                bull_count=int(getattr(debate, "bull_count", 0) or 0),
                bear_count=int(getattr(debate, "bear_count", 0) or 0),
                neutral_count=int(getattr(debate, "neutral_count", 0) or 0),
                process_summary=_first_text(
                    (
                        f"{getattr(debate, 'round_count', 0)} 轮；"
                        f"看多 {getattr(debate, 'bull_count', 0)} / "
                        f"看空 {getattr(debate, 'bear_count', 0)} / "
                        f"中性 {getattr(debate, 'neutral_count', 0)}"
                    )
                    if getattr(debate, "round_count", 0)
                    else "",
                    *(getattr(debate, "round_summaries", ()) or ())[:1],
                ),
            )
        )
        selected_symbols.add(symbol)
        if len(selected) == MAX_HOME_SNAPSHOT_DEBATES:
            break
    return tuple(selected)


def _news_report_path() -> Path:
    raw_path = os.getenv("AQSP_NEWS_OUTPUT", "reports/news_catalysts.md").strip()
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _news_json_report_path() -> Path:
    raw_path = os.getenv(
        "AQSP_NEWS_JSON_OUTPUT", "data/runtime/news_catalysts_latest.json"
    ).strip()
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _messages_from_catalyst_report(
    report: CatalystReport,
) -> tuple[HomeSnapshotMessage, ...]:
    impact_labels = {
        "positive": "利好",
        "negative": "利空",
        "neutral": "中性",
    }
    messages: list[HomeSnapshotMessage] = []
    for event in report.events:
        published_at = _normalize_timestamp(event.published_at)
        if not published_at:
            continue
        messages.append(
            HomeSnapshotMessage(
                title=event.title,
                summary=event.inference or event.title,
                impact=impact_labels.get(event.impact, event.impact),
                category=event.category,
                source=event.source,
                published_at=published_at,
            )
        )
        if len(messages) == MAX_HOME_MESSAGES:
            break
    return tuple(messages)


def _news_report_source_status(data_status: str, report_status: str) -> str:
    normalized = report_status.strip().lower()
    if normalized in {"ok", "partial", "empty", "timeout", "failed"}:
        return normalized
    return {
        "可用": "ok",
        "部分可用": "partial",
        "无数据": "empty",
        "超时": "timeout",
        "失败": "failed",
    }.get(data_status.strip(), "unknown")


def _report_event_status(text: str, source_status: str) -> str:
    match = re.search(r"^- 事件状态:\s*(.+)$", text, re.MULTILINE)
    status = match.group(1).strip() if match else ""
    normalized = {
        "已筛出高影响事件": "high_impact",
        "抓取成功但未筛出高影响事件": "no_high_impact",
        "仅发现旧新闻，已排除": "stale_only",
        "无可用新闻记录": "no_valid_news",
        "来源失败，无有效事件": "source_failed",
        "来源失败，使用受限旧缓存": "stale_cache",
    }.get(status, status)
    if normalized:
        return normalized
    if re.search(r"未筛出高影响消息|无强事件|未发现高影响事件", text):
        return "no_high_impact"
    if source_status in {"failed", "timeout"}:
        return "source_failed"
    return "high_impact"


def _parse_event_source(raw_source: str) -> tuple[str, str, int, str]:
    parts = tuple(part.strip() for part in raw_source.split("|"))
    source = parts[0] if parts else ""
    quality_label = "普通来源"
    quality_score = 1
    source_region = "mixed"
    for part in parts[1:]:
        if part.startswith("质量 "):
            quality = part.removeprefix("质量 ").strip()
            match = re.match(r"(.+?)（(\d+)/4）$", quality)
            if match:
                quality_label = match.group(1).strip() or quality_label
                quality_score = int(match.group(2))
            elif quality:
                quality_label = quality
        elif part.startswith("区域 "):
            source_region = part.removeprefix("区域 ").strip() or source_region
    return source, quality_label, quality_score, source_region


def _parse_news_report_payload(
    signal_date: str,
) -> tuple[str, tuple[HomeSnapshotMessage, ...], CatalystReport | None]:
    structured_path = _news_json_report_path()
    structured_report = load_catalyst_report_artifact(
        structured_path,
        expected_date=signal_date,
        max_age_seconds=NEWS_REPORT_MAX_AGE_SECONDS,
    )
    if structured_report is None and structured_path.is_file():
        is_current_day = signal_date == now_shanghai().date().isoformat()
        if is_current_day:
            unbounded_report = load_catalyst_report_artifact(
                structured_path,
                expected_date=signal_date,
            )
            if unbounded_report is not None:
                warning = (
                    "当前日消息源产物超过 6 小时有效窗口，旧消息已排除；"
                    "请检查消息刷新调度。"
                )
                source_status = "timeout"
            else:
                warning = "当前日消息源产物不可用，旧消息已排除；请检查消息刷新调度。"
                source_status = "failed"
            report = CatalystReport(
                date=signal_date,
                generated_at=now_shanghai().isoformat(timespec="seconds"),
                events=(),
                source_status=source_status,
                warnings=(warning,),
                event_status="source_failed",
            )
            return _SOURCE_STATUS_LABELS[source_status], (), report
    if structured_report is not None:
        structured_report, historical_count, invalid_count = (
            _normalize_catalyst_report_for_snapshot(structured_report, signal_date)
        )
        source_failed = structured_report.source_status in {"failed", "timeout"}
        cache_restricted = structured_report.news_status in {
            "source_failed",
            "stale_cache",
        }
        if source_failed or cache_restricted:
            structured_report = replace(structured_report, events=())
            messages = ()
        else:
            messages = _messages_from_catalyst_report(structured_report)
        if messages:
            status = _SOURCE_STATUS_LABELS.get(
                structured_report.source_status,
                structured_report.source_status or "可用",
            )
        elif historical_count and not invalid_count:
            status = "历史消息已排除"
        else:
            status = _EVENT_STATUS_LABELS.get(
                structured_report.news_status,
                _SOURCE_STATUS_LABELS.get(
                    structured_report.source_status,
                    structured_report.source_status or "无可用消息",
                ),
            )
        return status, messages, structured_report

    try:
        text = _news_report_path().read_text(encoding="utf-8")
    except OSError:
        return "未产出", (), None
    heading_line = next(
        (line for line in text.splitlines() if line.startswith("# 消息面雷达-")),
        "",
    )
    heading = re.match(r"^# 消息面雷达-(\d{4}-\d{2}-\d{2})", heading_line)
    if heading is None:
        return "未产出", (), None
    if heading.group(1) != signal_date:
        return "历史消息已排除", (), None
    status_match = re.search(r"^- 数据状态:\s*(.+)$", text, re.MULTILINE)
    heading_status = (
        heading_line.split("|", 1)[1].strip() if "|" in heading_line else ""
    )
    data_status = status_match.group(1).strip() if status_match else heading_status
    report_status_match = re.search(r"^- 状态:\s*(.+)$", text, re.MULTILINE)
    source_status = _news_report_source_status(
        data_status,
        report_status_match.group(1) if report_status_match else "",
    )
    event_status = _report_event_status(text, source_status)
    event_section = text.split("## 事件", 1)[-1].split("## 状态", 1)[0]
    blocks = re.split(r"(?m)^- \d+\. ", event_section)
    messages: list[HomeSnapshotMessage] = []
    events: list[CatalystEvent] = []
    for block in blocks[1:]:
        first_line, _, remainder = block.partition("\n")
        parts = tuple(part.strip() for part in first_line.split("|"))
        fields: dict[str, str] = {
            "impact": parts[0] if parts else "消息",
            "category": parts[-1] if len(parts) >= 3 else "消息",
        }
        for line in remainder.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key.strip().lstrip("- ")] = value.strip()
        title = fields.get("结果", "").strip()
        if not title:
            continue
        impact = {
            "利好": "positive",
            "利空": "negative",
            "中性": "neutral",
        }.get(fields["impact"], "neutral")
        source, quality_label, quality_score, source_region = _parse_event_source(
            fields.get("来源", "")
        )
        published_at = _normalize_timestamp(fields.get("时间", ""))
        if not published_at or published_at[:10] != signal_date:
            continue
        category = fields["category"]
        events.append(
            CatalystEvent(
                title=title,
                source=source,
                published_at=published_at,
                impact=impact,
                category=category,
                confidence=1.0,
                source_quality_label=quality_label,
                source_quality_score=quality_score,
                inference=fields.get("结论", "").strip(),
                url=fields.get("原文", "").strip(),
                source_region=source_region,
            )
        )
        messages.append(
            HomeSnapshotMessage(
                title=title,
                summary=fields.get("结论", "").strip() or title,
                impact=fields.get("影响", fields["impact"]).strip(),
                category=fields["category"],
                source=source,
                published_at=published_at,
            )
        )
    if source_status in {"failed", "timeout"} or event_status in {
        "source_failed",
        "stale_cache",
    }:
        messages = []
        events = []
    if not messages:
        status = _EVENT_STATUS_LABELS.get(
            event_status,
            _SOURCE_STATUS_LABELS.get(source_status, data_status or "无可用消息"),
        )
    else:
        status = _SOURCE_STATUS_LABELS.get(source_status, data_status or "可用")
    generated_at = next(
        (
            event.published_at
            for event in reversed(events)
            if event.published_at.strip()
        ),
        f"{signal_date}T23:59:59+08:00",
    )
    warnings: tuple[str, ...] = ()
    warning_match = re.search(r"^- (?:原因|告警):\s*(.+)$", text, re.MULTILINE)
    if warning_match:
        warnings = (warning_match.group(1).strip(),)
    elif source_status in {"failed", "timeout"}:
        warnings = ("当前日消息源失败，当前日无可用消息。",)
    report = CatalystReport(
        date=signal_date,
        generated_at=generated_at,
        events=tuple(events),
        source_status=source_status,
        warnings=warnings,
        event_status=event_status,
    )
    return status, tuple(messages[:MAX_HOME_MESSAGES]), report


def _parse_news_report(
    signal_date: str,
) -> tuple[str, tuple[HomeSnapshotMessage, ...]]:
    status, messages, _report = _parse_news_report_payload(signal_date)
    return status, messages


def _snapshot_market_context(
    artifact: MarketContextArtifact,
    *,
    status_override: str = "",
) -> HomeSnapshotMarketContext:
    has_international_event = any(
        str(event.source_region or "").strip().lower()
        in {"international", "global", "overseas"}
        for event in artifact.catalyst_events
    )
    summary_lines = tuple(
        line
        for line in artifact.summary_lines[:5]
        if not (
            line.startswith("海外风险:")
            and not has_international_event
            and not artifact.cross_market_implications
        )
    )
    cross_market_items: list[HomeSnapshotCrossMarket] = []
    for item in artifact.cross_market_implications[:3]:
        source_published_at = _normalize_timestamp(item.source_published_at)
        if not source_published_at:
            continue
        cross_market_items.append(
            HomeSnapshotCrossMarket(
                rule_id=item.rule_id,
                theme=item.theme,
                strength=item.strength,
                action=item.action,
                source_title=item.source_title,
                source_region="、".join(item.source_regions),
                source_published_at=source_published_at,
                affected_sectors=item.affected_sectors[:5],
                transmission_path=item.transmission_path[:3],
                validation_signals=item.validation_signals[:3],
                invalidation_signals=item.invalidation_signals[:3],
                summary=item.summary_line,
            )
        )
    cross_market = tuple(cross_market_items)
    status = status_override or _SOURCE_STATUS_LABELS.get(
        artifact.source_status, artifact.source_status
    )
    if (
        not status_override
        and not artifact.catalyst_events
        and artifact.source_status == "ok"
    ):
        status = "无高影响消息"
    return HomeSnapshotMarketContext(
        status=status or "未产出",
        overview=artifact.cross_market_overview,
        summary_lines=summary_lines,
        cross_market=cross_market,
        warnings=artifact.warnings[:3],
    )


def _empty_snapshot_market_context(status: str) -> HomeSnapshotMarketContext:
    clean_status = status.strip() or "未产出"
    return HomeSnapshotMarketContext(
        status=clean_status,
        overview="",
        summary_lines=(f"消息状态: {clean_status}",),
        cross_market=(),
        warnings=(),
    )


def _append_cross_market_messages(
    messages: tuple[HomeSnapshotMessage, ...],
    artifact: MarketContextArtifact,
) -> tuple[HomeSnapshotMessage, ...]:
    cross_market_messages = tuple(
        HomeSnapshotMessage(
            title=f"跨市传导｜{item.theme}",
            summary=item.summary_line,
            impact=item.action,
            category="跨市场传导",
            source=item.source_title,
            published_at=published_at,
        )
        for item in artifact.cross_market_implications[:3]
        if (published_at := _normalize_timestamp(item.source_published_at))
    )
    event_limit = max(0, MAX_HOME_MESSAGES - len(cross_market_messages))
    return tuple((*messages[:event_limit], *cross_market_messages))


def _lag_days(value: object) -> int:
    try:
        return int(float(_text(value)))
    except ValueError:
        return 0


def _snapshot_source(runtime: Any, task_view: Any) -> HomeSnapshotSource:
    source_status = getattr(task_view, "source_status", {}) or {}
    if not isinstance(source_status, dict):
        source_status = {}
    return HomeSnapshotSource(
        effective=_first_text(
            getattr(runtime, "effective_source", ""),
            getattr(runtime, "requested_source", ""),
            source_status.get("effective_source"),
            source_status.get("actual_source"),
            "未记录",
        ),
        latest_trade_date=_first_text(
            getattr(runtime, "data_latest_trade_date", ""),
            source_status.get("data_latest_trade_date"),
            "未记录",
        ),
        lag_days=_lag_days(
            _first_text(getattr(runtime, "lag_days", ""), source_status.get("lag_days"))
        ),
        status=_first_text(
            getattr(runtime, "run_status", ""),
            source_status.get("status"),
            getattr(runtime, "source_reason", ""),
            "未记录",
        ),
    )


def _snapshot_coldstart(runtime: Any) -> HomeSnapshotColdstart:
    return HomeSnapshotColdstart(
        status=_first_text(getattr(runtime, "coldstart_progress", ""), "未记录"),
        detail=_first_text(
            getattr(runtime, "coldstart_handoff_line", ""),
            getattr(runtime, "gate_blocker_line", ""),
            getattr(runtime, "conclusion", ""),
            "暂无冷启动状态",
        ),
    )


def build_home_snapshot(
    provider: DashboardDataProvider,
    *,
    signal_date: str = "",
    task_id: str = "",
) -> HomeDashboardSnapshot:
    """Build a bounded, file-ready home snapshot from local runtime artifacts only."""
    selected_task_id = _snapshot_task_id(task_id) or provider.default_task_id()
    requested_date = _text(signal_date) or today_shanghai().isoformat()
    payload = provider.home_digest_payload(
        selected_task_id,
        signal_date=requested_date,
    )
    task_view = payload.task_view
    selected_date = _resolve_selected_date(payload, requested_date)
    runtime = provider.runtime_overview(selected_date)
    overview = payload.overview
    generated_at = to_shanghai(now_shanghai()).isoformat(timespec="seconds")
    candidates = _snapshot_candidates(payload)
    debates = _snapshot_debates(payload, candidates)
    message_status, messages, catalyst_report = _parse_news_report_payload(
        selected_date
    )
    realtime_cross_market = _snapshot_realtime_cross_market(selected_task_id)
    if catalyst_report is None:
        # A failed or empty news feed must not erase independently fetched
        # realtime macro observations from the intraday research surface.
        catalyst_report = CatalystReport(
            date=selected_date,
            generated_at=generated_at,
            events=(),
            source_status="empty",
            event_status="no_valid_news",
        )
    artifact = build_market_context_artifact(
        catalyst_report=catalyst_report,
        realtime_cross_market=realtime_cross_market,
    )
    market_context = _snapshot_market_context(
        artifact,
        status_override=(message_status if not artifact.catalyst_events else ""),
    )
    messages = _append_cross_market_messages(messages, artifact)
    debate_missing = bool(getattr(payload, "debates", ()) or ()) and not debates
    summaries = _bounded_unique_text(
        (
            "委员会结论缺少当前候选映射，已隐藏" if debate_missing else "",
            getattr(runtime, "conclusion", ""),
            getattr(overview, "focus_headline", ""),
            getattr(overview, "blocker_headline", ""),
            getattr(overview, "top_headline", ""),
            getattr(task_view, "headline", ""),
        ),
        MAX_HOME_SUMMARIES,
    )

    return HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at=generated_at,
        selected_date=selected_date,
        available_dates=_snapshot_dates(task_view, selected_date),
        candidates=candidates,
        # Debate summaries are adjacent advisory cards and never ranking inputs.
        debates=debates,
        summaries=summaries,
        source=_snapshot_source(runtime, task_view),
        coldstart=_snapshot_coldstart(runtime),
        stale_after=stale_after_for_task(generated_at, selected_task_id),
        message_status=message_status,
        messages=messages,
        market_context=market_context,
    )


def build_home_snapshot_index(
    provider: DashboardDataProvider,
    *,
    signal_date: str = "",
    task_id: str = "",
    initial_snapshot: HomeDashboardSnapshot | None = None,
) -> HomeSnapshotIndex:
    """Build at most four exact-date snapshots without substituting history."""
    first = initial_snapshot or build_home_snapshot(
        provider,
        signal_date=signal_date,
        task_id=task_id,
    )
    selected_task_id = _snapshot_task_id(task_id) or provider.default_task_id()
    day_snapshots = [HomeSnapshotDay(date=first.selected_date, snapshot=first)]
    for available_date in first.available_dates:
        if available_date == first.selected_date:
            continue
        if len(day_snapshots) >= MAX_HOME_SNAPSHOT_INDEX_DAYS:
            break
        snapshot = build_home_snapshot(
            provider,
            signal_date=available_date,
            task_id=selected_task_id,
        )
        if snapshot.selected_date != available_date:
            raise ValueError(
                "provider returned a different date while building the snapshot index"
            )
        day_snapshots.append(HomeSnapshotDay(date=available_date, snapshot=snapshot))

    generated_at = to_shanghai(now_shanghai()).isoformat(timespec="seconds")
    return HomeSnapshotIndex(
        schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
        generated_at=generated_at,
        stale_after=stale_after_for_task(generated_at, selected_task_id),
        selected_date=first.selected_date,
        days=tuple(day_snapshots),
    )


def _resolve_output_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help="runtime snapshot path, relative to the project root",
    )
    parser.add_argument(
        "--index-output",
        default=os.environ.get(
            "AQSP_HOME_SNAPSHOT_INDEX_PATH", DEFAULT_INDEX_OUTPUT_PATH
        ),
        help="date-index path; writes up to four exact day snapshots",
    )
    parser.add_argument("--date", default="", help="signal date in YYYY-MM-DD")
    parser.add_argument("--task-id", default="", help="dashboard task identifier")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provider = DashboardDataProvider()
    snapshot = build_home_snapshot(
        provider,
        signal_date=args.date.strip(),
        task_id=args.task_id.strip(),
    )
    output_path = _resolve_output_path(args.output)
    index_path = _resolve_output_path(args.index_output)
    if index_path.resolve() == output_path.resolve():
        raise ValueError("home snapshot and snapshot index must use different paths")
    write_home_dashboard_snapshot(output_path, snapshot)
    index = build_home_snapshot_index(
        provider,
        signal_date=args.date.strip(),
        task_id=args.task_id.strip(),
        initial_snapshot=snapshot,
    )
    write_home_snapshot_index(index_path, index)
    print(
        "home snapshot written "
        f"date={snapshot.selected_date} task={args.task_id.strip() or 'main_chain'} "
        f"candidates={len(snapshot.candidates)} debates={len(snapshot.debates)} "
        f"output={output_path}"
        f" index={index_path} days={len(index.days)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
