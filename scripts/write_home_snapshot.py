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
from datetime import date, datetime, timedelta, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aqsp.core.time import (
    get_previous_trading_day,
    latest_completed_trading_day,
    now_shanghai,
    today_shanghai,
    to_shanghai,
)
from aqsp.core.errors import DataError
from aqsp.market_context import MarketContextArtifact, build_market_context_artifact
from aqsp.news.catalysts import (
    CatalystEvent,
    CatalystReport,
    load_catalyst_report_artifact,
)
from aqsp.web.data_provider import DashboardDataProvider
from aqsp.ledger.runtime import count_paper_tracking_days
from aqsp.runtime.recommendation_gate import (
    DEFAULT_WALKFORWARD_MAX_AGE_DAYS,
    FreshnessEvidence,
    RecommendationGateInputs,
    evaluate as evaluate_recommendation_gate,
)
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
    HomeSnapshotRecommendationGate,
    HomeSnapshotResearchChain,
    HomeSnapshotCarriedReview,
    HomeSnapshotCrossMarket,
    HomeSnapshotDebate,
    HomeSnapshotAgentView,
    HomeSnapshotIndex,
    HomeSnapshotMarketContext,
    HomeSnapshotMessage,
    HomeSnapshotPhase,
    HomeSnapshotSource,
    HomeSnapshotTechnicalMetric,
    HomeSnapshotUniverse,
    HomeSnapshotHolding,
    HomeSnapshotVariant,
    HomeSnapshotVariantSuite,
    MAX_HOME_SNAPSHOT_TECHNICAL_METRICS,
    is_home_recommendation,
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
    stale_after_for_task,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)

try:
    from check_variant_results import validate_variant_payload
except ModuleNotFoundError:  # pragma: no cover - package import used by tests.
    from scripts.check_variant_results import validate_variant_payload


DEFAULT_OUTPUT_PATH = "data/runtime/home_dashboard_snapshot.json"
DEFAULT_INDEX_OUTPUT_PATH = "data/runtime/home_dashboard_snapshot_index.json"
MAX_HOME_DATES = 7
MAX_HOME_CANDIDATES = MAX_HOME_SNAPSHOT_CANDIDATES
MAX_HOME_SUMMARIES = 3
MAX_HOME_MESSAGES = 5
MAX_HOME_MESSAGES_PER_SOURCE = 2
MAX_HOME_VARIANTS = 160
MIN_HOME_VARIANT_COUNT = 24
MIN_HOME_VARIANT_SYMBOLS = 600
DEFAULT_RAW_PARTIAL_COVERAGE_FLOOR = 0.98
NEWS_REPORT_MAX_AGE_SECONDS = 6 * 60 * 60
CURRENT_MESSAGE_WINDOW = timedelta(hours=24)
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
_EVENT_TYPE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("新品", "发布", "launch", "platform", "physical ai", "具身", "机器人"),
        "新品发布",
    ),
    (
        ("政策", "国常会", "发改委", "工信部", "补贴", "行动方案", "指导意见"),
        "产业政策",
    ),
    (
        ("spacex", "nvidia", "英伟达", "tesla", "海外", "ipo", "starlink"),
        "海外公司事件",
    ),
    (
        ("涨价", "提价", "报价", "缺货", "供给", "库存", "油价", "原油", "opec"),
        "供应链/价格变化",
    ),
    (("战争", "地缘", "冲突", "袭击", "导弹", "war", "geopolitical"), "地缘事件"),
)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


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


def _current_message_window_start(signal_date: str, current_time: datetime) -> datetime:
    """Include overnight/weekend news for the current trading session only."""
    if signal_date != today_shanghai().isoformat():
        return current_time - CURRENT_MESSAGE_WINDOW
    try:
        previous_trade_day = get_previous_trading_day(current_time.date())
    except (OSError, ValueError):
        return current_time - CURRENT_MESSAGE_WINDOW
    return datetime.combine(previous_trade_day, time.min, tzinfo=SHANGHAI_TZ)


def _normalize_catalyst_report_for_snapshot(
    report: CatalystReport,
    signal_date: str,
) -> tuple[CatalystReport, int, int]:
    """Keep only dated current-day events without inventing missing timestamps."""
    historical_count = 0
    invalid_count = 0
    current_day = today_shanghai().isoformat()
    current_time = now_shanghai()
    live_window_start = _current_message_window_start(signal_date, current_time)

    def normalize_events(
        events: tuple[CatalystEvent, ...], *, count_exclusions: bool
    ) -> tuple[CatalystEvent, ...]:
        nonlocal historical_count, invalid_count
        normalized: list[CatalystEvent] = []
        for event in events:
            published_at = _normalize_timestamp(event.published_at)
            if not published_at:
                if count_exclusions:
                    invalid_count += 1
                continue
            published_dt = datetime.fromisoformat(published_at)
            if published_dt > current_time:
                if count_exclusions:
                    invalid_count += 1
                continue
            is_recent_live_event = (
                signal_date == current_day
                and live_window_start <= published_dt <= current_time
            )
            if published_at[:10] != signal_date and not is_recent_live_event:
                if count_exclusions:
                    historical_count += 1
                continue
            normalized.append(
                replace(
                    event,
                    published_at=published_at,
                    source_fetched_at=_normalize_timestamp(event.source_fetched_at),
                )
            )
        return tuple(normalized)

    current_events = normalize_events(report.events, count_exclusions=True)
    current_clues = normalize_events(report.market_clues, count_exclusions=False)
    normalized_report = replace(
        report,
        generated_at=_normalize_timestamp(report.generated_at),
        events=current_events,
        market_clues=current_clues,
        event_status=(
            "no_high_impact"
            if current_clues and not current_events
            else "stale_only"
            if historical_count and not current_events
            else report.event_status
        ),
    )
    return (
        normalized_report,
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
    completed_date = latest_completed_trading_day().isoformat()
    dates = _bounded_unique_text(
        (selected_date, *(getattr(task_view, "available_dates", ()) or ())),
        MAX_HOME_DATES,
    )
    anchor = date.fromisoformat(max(dates, default=selected_date))
    recent_dates = {anchor.isoformat()}
    for _ in range(MAX_HOME_DATES - 1):
        anchor = get_previous_trading_day(anchor)
        recent_dates.add(anchor.isoformat())
    return tuple(
        value
        for value in dates
        if value in recent_dates
        and (value == selected_date or value <= completed_date)
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


def _candidate_score_breakdown(candidate: Any) -> tuple[str, ...]:
    raw = getattr(candidate, "score_breakdown", ()) or ()
    if isinstance(raw, str):
        raw = (raw,)
    return _bounded_unique_text(raw, 4)


_REQUIRED_TECHNICAL_METRICS = frozenset({"volume_ratio", "macd_hist", "kdj_j"})


def _candidate_metric_value(candidate: Any, key: str) -> object:
    """Read a metric from the card or its preserved runtime metric mapping."""
    value = getattr(candidate, key, None)
    if value not in (None, ""):
        return value
    for field in ("metrics", "technical_metrics"):
        raw_metrics = getattr(candidate, field, None)
        if isinstance(raw_metrics, dict) and raw_metrics.get(key) not in (None, ""):
            return raw_metrics[key]
    return None


def _candidate_technical_metrics(
    candidate: Any,
) -> tuple[HomeSnapshotTechnicalMetric, ...]:
    """Expose deterministic technical fields and make missing required inputs explicit."""
    specifications = (
        ("close", "现价", "{:.2f}"),
        ("ret5_pct", "5日动能", "{:+.2f}%"),
        ("ret20_pct", "20日动能", "{:+.2f}%"),
        ("volume_ratio", "量比", "{:.2f}x"),
        ("rsi12", "RSI12", "{:.1f}"),
        ("macd_hist", "MACD柱", "{:+.3f}"),
        ("kdj_j", "KDJ-J", "{:.1f}"),
        ("bias20_pct", "MA20偏离", "{:+.2f}%"),
        ("stop_loss", "纸面止损", "{:.2f}"),
        ("take_profit", "纸面止盈", "{:.2f}"),
    )
    metrics: list[HomeSnapshotTechnicalMetric] = []
    for key, label, template in specifications:
        raw = _candidate_metric_value(candidate, key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = math.nan
        if math.isfinite(value):
            metrics.append(
                HomeSnapshotTechnicalMetric(
                    key=key, label=label, value=template.format(value)
                )
            )
        elif key in _REQUIRED_TECHNICAL_METRICS:
            # Do not invent an indicator when its source row omitted it.  The
            # dashboard still exposes the broken contract instead of silently
            # presenting an incomplete technical case as complete.
            metrics.append(
                HomeSnapshotTechnicalMetric(key=key, label=label, value="未提供")
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


def _candidate_freshness(candidate: Any) -> str:
    """Preserve a machine-readable freshness status at the snapshot boundary."""
    explicit = _text(getattr(candidate, "freshness", ""))
    if explicit:
        return explicit
    label = _text(getattr(candidate, "freshness_label", ""))
    if "新鲜" in label:
        return "fresh"
    if "观察" in label or "偏旧" in label:
        return "watch"
    if "过期" in label:
        return "stale"
    if "失败" in label or "不可用" in label:
        return "failed"
    if "未知" in label:
        return "unknown"
    return ""


def _snapshot_candidate(candidate: Any) -> HomeSnapshotCandidate | None:
    symbol = _text(getattr(candidate, "symbol", ""))
    if not symbol:
        return None
    reasons = _candidate_reasons(candidate)
    strategies = _candidate_strategies(candidate)
    score_breakdown = _candidate_score_breakdown(candidate)
    context = _candidate_context(candidate)
    data_source = _text(getattr(candidate, "data_source", ""))
    if data_source:
        source_context = f"数据源: {data_source}"
        context = " / ".join(part for part in (context, source_context) if part)
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
        context=context,
        deterministic_reasons=reasons,
        strategies=strategies,
        score_breakdown=score_breakdown,
        evidence_status=("有独立规则证据" if reasons else "证据不足"),
        technical_metrics=_candidate_technical_metrics(candidate),
        data_source=data_source,
        data_fetched_at=_text(getattr(candidate, "data_fetched_at", "")),
        data_timestamp_source=_text(getattr(candidate, "data_timestamp_source", "")),
        freshness=_candidate_freshness(candidate),
        news_catalyst_summary=_text(
            getattr(candidate, "news_catalyst_summary", "")
        ),
        news_catalyst_source=_text(getattr(candidate, "news_catalyst_source", "")),
        news_catalyst_url=_text(
            getattr(candidate, "news_catalyst_url", "")
        ),
        news_catalyst_published_at=_text(
            getattr(candidate, "news_catalyst_published_at", "")
        ),
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
        "实时推荐",
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


def _apply_recommendation_gate(
    candidates: tuple[HomeSnapshotCandidate, ...],
    gate: HomeSnapshotRecommendationGate,
) -> tuple[HomeSnapshotCandidate, ...]:
    """Downgrade formal cards to observation when the global gate is closed."""
    if gate.recommendation_allowed:
        return candidates
    return tuple(
        replace(
            candidate,
            research_status=(
                "仅观察（推荐 gate 阻塞）"
                if is_home_recommendation(candidate)
                else candidate.research_status
            ),
        )
        for candidate in candidates
    )


def _snapshot_debates(
    payload: Any,
    candidates: tuple[HomeSnapshotCandidate, ...],
    *,
    runtime_debates: tuple[Any, ...] = (),
) -> tuple[HomeSnapshotDebate, ...]:
    candidate_symbols = {candidate.symbol for candidate in candidates}
    candidates_by_symbol = {candidate.symbol: candidate for candidate in candidates}
    debates = tuple(getattr(payload, "debates", ()) or ()) + runtime_debates
    selected: list[HomeSnapshotDebate] = []
    selected_symbols: set[str] = set()
    selected_content_keys: set[str] = set()
    for debate in debates:
        if not _debate_is_complete(debate):
            continue
        symbol = _text(getattr(debate, "symbol", ""))
        if symbol not in candidate_symbols or symbol in selected_symbols:
            continue
        candidate = candidates_by_symbol[symbol]
        raw_viewpoints = getattr(debate, "viewpoint_buckets", {}) or {}
        viewpoint_buckets = _presentation_viewpoint_buckets(
            raw_viewpoints,
            candidate,
            _text(getattr(debate, "primary_risk_gate", "")),
        )
        agent_views = _snapshot_agent_views(
            getattr(debate, "agent_views", ()) or ()
        )
        published_roles = tuple(view.role for view in agent_views)
        if len(published_roles) < 2:
            published_roles = tuple(
                dict.fromkeys(
                    _first_text(
                        getattr(view, "role_label", ""),
                        getattr(view, "role_id", ""),
                    )
                    for view in (getattr(debate, "agent_views", ()) or ())
                    if _first_text(
                        getattr(view, "role_label", ""),
                        getattr(view, "role_id", ""),
                    )
                )
            )
        if len(agent_views) >= 2:
            published_votes = {
                "bull_count": sum(
                    view.stance.strip().lower() in {"bull", "bullish"}
                    for view in agent_views
                ),
                "bear_count": sum(
                    view.stance.strip().lower() in {"bear", "bearish"}
                    for view in agent_views
                ),
                "neutral_count": sum(
                    view.stance.strip().lower() not in {"bull", "bullish", "bear", "bearish"}
                    for view in agent_views
                ),
            }
        else:
            published_votes = {
                "bull_count": int(getattr(debate, "bull_count", 0) or 0),
                "bear_count": int(getattr(debate, "bear_count", 0) or 0),
                "neutral_count": int(getattr(debate, "neutral_count", 0) or 0),
            }
        disagreement_points = tuple(
            _clean_legacy_debate_text(point)[:240]
            for point in (getattr(debate, "disagreement_points", ()) or ())
            if not _is_raw_debate_template(point)
            and not _is_shared_debate_context(point)
        )[:4]
        # Shared market context is not an instrument-level rebuttal. Do not
        # publish a committee result until a real candidate-specific conflict exists.
        if not disagreement_points:
            continue
        structured_rounds = tuple(
            f"{bucket}：{_clean_legacy_debate_text(points[0])[:240]}"
            for bucket, points in viewpoint_buckets.items()
            if points
        )[:2]
        if disagreement_points:
            structured_rounds += (
                f"分歧：{_clean_legacy_debate_text(disagreement_points[0])[:240]}",
            )
        fallback_rounds = _distinct_research_lines(
            tuple(getattr(debate, "round_summaries", ()) or ()), limit=3
        )
        snapshot_debate = HomeSnapshotDebate(
            symbol=symbol,
            display_name=_text(getattr(debate, "display_name", "")),
            conclusion=_first_text(
                getattr(debate, "research_verdict", ""),
                getattr(debate, "consensus", ""),
            ),
            primary_risk_gate=_text(getattr(debate, "primary_risk_gate", "")),
            next_trigger=_text(getattr(debate, "next_trigger", "")),
            active_roles=published_roles,
            round_count=int(getattr(debate, "round_count", 0) or 0),
            bull_count=published_votes["bull_count"],
            bear_count=published_votes["bear_count"],
            neutral_count=published_votes["neutral_count"],
            process_summary=(
                f"{getattr(debate, 'round_count', 0)} 轮讨论 · "
                f"参与角色 {len(published_roles)}"
                if getattr(debate, "round_count", 0)
                else ""
            ),
            round_summaries=structured_rounds or fallback_rounds,
            agent_views=agent_views,
            viewpoint_buckets=viewpoint_buckets,
            disagreement_points=disagreement_points,
            uncertainty_points=tuple(getattr(debate, "uncertainty_points", ()) or ())[
                :4
            ],
            review_kind="multi_agent",
        )
        content_key = _debate_content_key(snapshot_debate)
        if content_key and content_key in selected_content_keys:
            continue
        selected.append(snapshot_debate)
        if content_key:
            selected_content_keys.add(content_key)
        selected_symbols.add(symbol)
        if len(selected) == MAX_HOME_SNAPSHOT_DEBATES:
            break
    return tuple(selected)


def _research_text_key(value: object) -> str:
    return re.sub(r"\s+", "", _text(value)).lower()


def _debate_content_key(debate: HomeSnapshotDebate) -> str:
    """Identify copied cross-symbol conclusions while ignoring workflow counters."""
    values = (
        debate.conclusion,
        debate.primary_risk_gate,
        debate.next_trigger,
        *debate.disagreement_points,
        *debate.uncertainty_points,
        *(point for points in debate.viewpoint_buckets.values() for point in points),
        *(
            point
            for view in debate.agent_views
            for point in (*view.arguments, *view.opportunities, *view.risks)
        ),
    )
    text = "|".join(_text(value) for value in values if _text(value))
    for marker in (debate.symbol, debate.display_name):
        if marker:
            text = text.replace(marker, "")
    return _research_text_key(text)


def _clean_legacy_debate_text(value: object) -> str:
    """Remove the retired vote template before old debate artifacts reach UI."""
    text = _text(value)
    text = re.sub(
        r"看多\s*\d+\s*[/／]\s*看空\s*\d+\s*[/／]\s*中性\s*\d+\s*[；;]?",
        "",
        text,
    )
    return re.sub(r"\s*[；;]\s*[；;]\s*", "；", text).strip(" ；;")


def _is_raw_debate_template(value: object) -> bool:
    text = _text(value)
    return "候选专属证据" in text or ("ret5=" in text and "技术=" in text)


def _is_shared_debate_context(value: object) -> bool:
    text = _text(value)
    return any(
        marker in text
        for marker in (
            "跨市传导质询",
            "组合保护",
            "全局雷达",
            "来源质量",
            "当前bullish立场与该主张方向相反",
            "当前bearish立场与该主张方向相反",
            "若该主张成立，当前方向假设将失效",
            "未形成方向性判断",
            "当前维持中性",
            "输入未提供",
            "未提供可核验",
            "未提供板块",
            "未提供融资",
            "未提供北向",
            "未提供散户",
        )
    )


def _presentation_viewpoint_buckets(
    raw_viewpoints: object,
    candidate: HomeSnapshotCandidate,
    primary_risk_gate: str,
) -> dict[str, tuple[str, ...]]:
    """Keep candidate-specific agent evidence, with deterministic fallback only when absent."""
    result: dict[str, tuple[str, ...]] = {}
    allowed_buckets = {
        "bullish",
        "bearish",
        "technical",
        "strategy",
        "uncertainty",
        "risk_counterevidence",
    }
    if isinstance(raw_viewpoints, dict):
        for raw_bucket, raw_points in raw_viewpoints.items():
            bucket = _text(raw_bucket)
            if (
                not bucket
                or bucket not in allowed_buckets
                or not isinstance(raw_points, (list, tuple))
            ):
                continue
            points: list[str] = []
            for raw_point in raw_points:
                point = _clean_legacy_debate_text(raw_point)[:240]
                if (
                    not point
                    or _is_raw_debate_template(point)
                    or _is_shared_debate_context(point)
                    or point == _text(primary_risk_gate)
                ):
                    continue
                if _research_text_key(point) not in {
                    _research_text_key(item) for item in points
                }:
                    points.append(point)
                if len(points) == 3:
                    break
            if points:
                result[bucket] = tuple(points)
    if result:
        return result

    metric_evidence = tuple(
        f"{metric.label}：{metric.value}"
        for metric in candidate.technical_metrics
        if metric.value and metric.value != "未提供"
    )[:3]
    if metric_evidence:
        result["technical"] = metric_evidence
    if candidate.strategies:
        result["strategy"] = (f"命中策略：{'、'.join(candidate.strategies)}",)
    elif candidate.deterministic_reasons:
        result["technical"] = tuple(candidate.deterministic_reasons[:2])
    return result


def _distinct_research_lines(
    values: Iterable[object], *, limit: int
) -> tuple[str, ...]:
    """Keep only semantically non-identical discussion lines in the UI payload."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_legacy_debate_text(value)
        key = _research_text_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) == limit:
            break
    return tuple(result)


def _snapshot_agent_views(views: Iterable[object]) -> tuple[HomeSnapshotAgentView, ...]:
    """Serialize the final role views instead of flattening them into one summary."""
    selected: list[HomeSnapshotAgentView] = []
    seen: set[str] = set()
    substantive_roles = {"bull", "bear", "risk_control"}
    for view in views:
        role = _first_text(getattr(view, "role_id", ""), getattr(view, "role", ""))
        if not role or role in seen:
            continue
        if role not in substantive_roles:
            continue
        seen.add(role)
        argument = _clean_legacy_debate_text(getattr(view, "key_argument", ""))
        opportunity = _clean_legacy_debate_text(getattr(view, "key_opportunity", ""))
        risk = _clean_legacy_debate_text(getattr(view, "key_risk", ""))
        if _is_raw_debate_template(argument):
            argument = ""
        if _is_raw_debate_template(opportunity):
            opportunity = ""
        if _is_raw_debate_template(risk):
            risk = ""
        if _is_shared_debate_context(argument):
            argument = ""
        if _is_shared_debate_context(opportunity):
            opportunity = ""
        if _is_shared_debate_context(risk):
            risk = ""
        if not any((argument, opportunity, risk)):
            continue
        selected.append(
            HomeSnapshotAgentView(
                role=role,
                stance=_text(getattr(view, "stance", "")) or "neutral",
                confidence=float(getattr(view, "confidence", 0.0) or 0.0),
                arguments=(argument,) if argument else (),
                opportunities=(opportunity,) if opportunity else (),
                risks=(risk,) if risk else (),
                counterarguments=(),
            )
        )
    return tuple(selected)


def _runtime_debate_path() -> Path:
    """Resolve the private debate sidecar from the release or runtime root."""
    raw_path = os.getenv("AQSP_DEBATE_RESULTS", "").strip()
    path = Path(raw_path or "data/debate_results.jsonl").expanduser()
    if path.is_absolute():
        return path
    runtime_root = os.getenv("AQSP_RUNTIME_ROOT", "").strip()
    if runtime_root:
        return Path(runtime_root).expanduser() / path
    return PROJECT_ROOT / path


def _runtime_debate_date(record: dict[str, Any]) -> str:
    for key in ("candidate_signal_date", "related_signal_date", "debate_date"):
        value = _text(record.get(key))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
    return ""


def _runtime_debates_for_snapshot(
    signal_date: str,
    candidate_symbols: set[str],
) -> tuple[Any, ...]:
    """Adapt completed JSONL debates when the provider task view omitted them."""
    path = _runtime_debate_path()
    try:
        raw_lines = path.read_bytes().splitlines()
    except OSError:
        return ()
    if sum(len(line) for line in raw_lines) > 8 * 1024 * 1024:
        return ()

    selected: list[Any] = []
    seen: set[str] = set()
    for line in reversed(raw_lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or _runtime_debate_date(record) != signal_date:
            continue
        symbol = _text(record.get("symbol"))
        if not symbol or symbol not in candidate_symbols or symbol in seen:
            continue
        rounds = record.get("rounds")
        rounds = (
            [item for item in rounds if isinstance(item, dict)]
            if isinstance(rounds, list)
            else []
        )
        if not rounds:
            continue
        final_round = max(
            rounds,
            key=lambda item: int(item.get("round_num") or item.get("round") or 0),
        )
        opinions = [
            item for item in final_round.get("opinions", ()) if isinstance(item, dict)
        ]
        vote_map = record.get("final_vote")
        if not isinstance(vote_map, dict):
            vote_map = {
                _text(item.get("role")): _text(
                    item.get("final_position") or item.get("stance")
                )
                for item in opinions
                if _text(item.get("role"))
            }
        roles = tuple(dict.fromkeys(_text(role) for role in vote_map if _text(role)))
        if len(roles) < 3:
            continue
        opinions_by_role = {
            _text(item.get("role")): item
            for item in opinions
            if _text(item.get("role"))
        }
        agent_views = tuple(
            SimpleNamespace(
                role_id=role,
                role_label=role,
                stance=_first_text(
                    vote_map.get(role),
                    opinions_by_role.get(role, {}).get("final_position"),
                    opinions_by_role.get(role, {}).get("stance"),
                    "neutral",
                ),
                confidence=float(
                    opinions_by_role.get(role, {}).get("confidence") or 0.0
                ),
                key_argument=_first_text(
                    *(opinions_by_role.get(role, {}).get("arguments") or ())[:1]
                ),
                key_opportunity=_first_text(
                    *(opinions_by_role.get(role, {}).get("opportunity_factors") or ())[
                        :1
                    ]
                ),
                key_risk=_first_text(
                    *(opinions_by_role.get(role, {}).get("risk_factors") or ())[:1]
                ),
            )
            for role in roles
        )
        counts = {
            "bull_count": sum(
                str(v).strip().lower() in {"bull", "bullish"} for v in vote_map.values()
            ),
            "bear_count": sum(
                str(v).strip().lower() in {"bear", "bearish"} for v in vote_map.values()
            ),
            "neutral_count": sum(
                str(v).strip().lower() in {"neutral", "watch"}
                for v in vote_map.values()
            ),
        }
        selected.append(
            SimpleNamespace(
                symbol=symbol,
                display_name=_first_text(record.get("name"), symbol),
                research_verdict=_first_text(
                    record.get("research_verdict"), record.get("final_consensus")
                ),
                consensus=_text(record.get("final_consensus")),
                primary_risk_gate=_text(record.get("primary_risk_gate")),
                next_trigger=_text(record.get("next_trigger")),
                agent_views=agent_views,
                round_count=len(rounds),
                round_summaries=tuple(
                    _text(item.get("summary"))
                    for item in rounds
                    if _text(item.get("summary"))
                ),
                process_recorded=record.get("process_recorded"),
                conclusion_recorded=record.get("conclusion_recorded"),
                evidence_sufficient=record.get("evidence_sufficient"),
                debate_quality_issues=record.get("debate_quality_issues", ()),
                viewpoint_buckets=record.get("viewpoint_buckets", {}),
                disagreement_points=record.get("disagreement_points", ()),
                uncertainty_points=record.get("uncertainty_points", ()),
                review_kind="multi_agent",
                **counts,
            )
        )
        seen.add(symbol)
        if len(selected) == MAX_HOME_SNAPSHOT_DEBATES:
            break
    return tuple(selected)


def _debate_is_complete(debate: Any) -> bool:
    """Keep incomplete committee attempts out of the formal debate lane."""
    for field in ("process_recorded", "conclusion_recorded", "evidence_sufficient"):
        if getattr(debate, field, None) is False:
            return False
    quality_issues = getattr(
        debate,
        "quality_issues",
        getattr(debate, "debate_quality_issues", ()),
    )
    if tuple(quality_issues or ()):
        return False

    try:
        round_count = int(getattr(debate, "round_count", 0) or 0)
    except (TypeError, ValueError):
        return False
    if round_count not in (2, 3):
        return False

    roles = tuple(
        dict.fromkeys(
            _first_text(getattr(view, "role_label", ""), getattr(view, "role_id", ""))
            for view in (getattr(debate, "agent_views", ()) or ())
            if _first_text(
                getattr(view, "role_label", ""), getattr(view, "role_id", "")
            )
        )
    )
    if len(roles) < 3:
        return False
    try:
        vote_counts = tuple(
            int(getattr(debate, field, 0) or 0)
            for field in ("bull_count", "bear_count", "neutral_count")
        )
    except (TypeError, ValueError):
        return False
    if not all(count >= 0 for count in vote_counts) or sum(vote_counts) != len(roles):
        return False
    viewpoints = getattr(debate, "viewpoint_buckets", {}) or {}
    if (
        not isinstance(viewpoints, dict)
        or len([points for points in viewpoints.values() if tuple(points or ())]) < 2
    ):
        return False
    return bool(tuple(getattr(debate, "disagreement_points", ()) or ()))


def _news_report_path() -> Path:
    raw_path = os.getenv("AQSP_NEWS_OUTPUT", "reports/news_catalysts.md").strip()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    runtime_root = os.getenv("AQSP_RUNTIME_ROOT", "").strip()
    return (
        Path(runtime_root).expanduser() / path if runtime_root else PROJECT_ROOT / path
    )


def _news_json_report_path() -> Path:
    raw_path = os.getenv(
        "AQSP_NEWS_JSON_OUTPUT", "data/runtime/news_catalysts_latest.json"
    ).strip()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    runtime_root = os.getenv("AQSP_RUNTIME_ROOT", "").strip()
    return (
        Path(runtime_root).expanduser() / path if runtime_root else PROJECT_ROOT / path
    )


def _news_json_archive_path(signal_date: str) -> Path:
    raw_path = os.getenv("AQSP_NEWS_ARCHIVE_DIR", "data/runtime/news_archive").strip()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        runtime_root = os.getenv("AQSP_RUNTIME_ROOT", "").strip()
        path = (
            Path(runtime_root).expanduser() / path
            if runtime_root
            else PROJECT_ROOT / path
        )
    return path / f"news-{signal_date}.json"


def _news_archive_dates() -> tuple[str, ...]:
    raw_path = os.getenv("AQSP_NEWS_ARCHIVE_DIR", "data/runtime/news_archive").strip()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        runtime_root = os.getenv("AQSP_RUNTIME_ROOT", "").strip()
        path = (
            Path(runtime_root).expanduser() / path
            if runtime_root
            else PROJECT_ROOT / path
        )
    if not path.is_dir():
        return ()
    dates: list[str] = []
    for item in path.glob("news-??????????.json"):
        value = item.stem.removeprefix("news-")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            continue
        dates.append(value)
    return tuple(sorted(set(dates), reverse=True))


def _messages_from_catalyst_report(
    report: CatalystReport,
) -> tuple[HomeSnapshotMessage, ...]:
    impact_labels = {
        "positive": "利好",
        "negative": "利空",
        "neutral": "中性",
    }
    messages: list[HomeSnapshotMessage] = []
    display_events = report.events
    using_market_clues = not display_events and report.news_status in {
        "no_high_impact",
        "no_valid_news",
    }
    if using_market_clues:
        display_events = report.market_clues
    for event in display_events:
        published_at = _normalize_timestamp(event.published_at)
        source = _text(event.source)
        parsed_url = urlsplit(_text(event.url))
        if (
            not published_at
            or not source
            or (
                using_market_clues
                and (
                    parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc
                )
            )
        ):
            continue
        messages.append(
            HomeSnapshotMessage(
                title=event.title,
                summary=event.inference or event.title,
                impact=impact_labels.get(event.impact, event.impact),
                category=event.category,
                source=source,
                published_at=published_at,
                url=event.url,
                source_region=event.source_region,
                source_quality=event.source_quality_label,
                event_type=_event_type_for_snapshot(event),
                affected_sectors=event.affected_sectors[:5],
                affected_symbols=event.affected_symbols[:5],
                transmission_hypothesis=event.transmission_hypothesis,
                supporting_evidence=event.supporting_evidence[:5],
                source_url=event.url,
                verification=event.verification,
                transmission_path=event.transmission_path[:5],
                validation_signals=event.validation_signals[:3],
                invalidation_signals=event.invalidation_signals[:3],
            )
        )
    # Do not let one source or a burst of near-identical headlines hide other
    # catalysts. A single available source may still fill the whole digest.
    selected: list[HomeSnapshotMessage] = []
    covered: set[tuple[str, str]] = set()
    source_counts: dict[str, int] = {}
    source_count = len({_message_source_family(message.source) for message in messages})
    source_limit = (
        MAX_HOME_MESSAGES if source_count == 1 else MAX_HOME_MESSAGES_PER_SOURCE
    )
    for message in messages:
        topic = (message.event_type or message.category or "消息").strip()
        region = (message.source_region or "mixed").strip().lower()
        key = (topic, region)
        source_key = _message_source_family(message.source)
        family_limit = (
            1 if source_key in {"nvidia", "英伟达", "openai"} else source_limit
        )
        if key in covered or source_counts.get(source_key, 0) >= family_limit:
            continue
        selected.append(message)
        covered.add(key)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        if len(selected) == MAX_HOME_MESSAGES:
            return tuple(selected)
    for message in messages:
        if message in selected:
            continue
        source_key = _message_source_family(message.source)
        family_limit = (
            1 if source_key in {"nvidia", "英伟达", "openai"} else source_limit
        )
        if source_counts.get(source_key, 0) >= family_limit:
            continue
        selected.append(message)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        if len(selected) == MAX_HOME_MESSAGES:
            break
    return tuple(selected)


def _message_source_family(source: str) -> str:
    """Group branded feeds so one publisher cannot fill the daily digest."""
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", str(source or "").casefold()).strip()
    for token in (
        "nvidia",
        "英伟达",
        "openai",
        "美联储",
        "federal reserve",
        "证券日报",
    ):
        if token in text:
            return token
    return text or "unknown"


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
    if structured_report is None:
        archive_path = _news_json_archive_path(signal_date)
        structured_report = load_catalyst_report_artifact(
            archive_path,
            expected_date=signal_date,
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
        if not source or not published_at or published_at[:10] != signal_date:
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
                url=fields.get("原文", "").strip(),
                source_region=source_region,
                source_quality=quality_label,
                event_type=_event_type_from_text(title, fields["category"]),
                supporting_evidence=(f"{source}: {title}" if source else title,),
                source_url=fields.get("原文", "").strip(),
                affected_sectors=(),
                transmission_hypothesis=fields.get("结论", "").strip(),
                verification="已记录来源",
                transmission_path=(),
                validation_signals=(),
                invalidation_signals=(),
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


def _with_news_source_coverage(
    context: HomeSnapshotMarketContext,
    report: CatalystReport,
) -> HomeSnapshotMarketContext:
    """Expose source coverage when strict same-day filtering yields no event."""
    coverage = tuple(
        f"来源覆盖: {'国内' if item.region == 'domestic' else '国际'} "
        f"{item.successful_sources}/{item.source_count} 路 · {item.row_count} 条原始消息 · {item.freshness}"
        for item in report.region_statuses
        if item.source_count
    )
    filtering = (
        f"时效筛选: 原始 {report.raw_news_count} 条"
        f" · 已排除过期 {report.stale_news_count} 条"
        if report.raw_news_count or report.stale_news_count
        else ""
    )
    return replace(
        context,
        summary_lines=tuple(
            dict.fromkeys((*coverage, filtering, *context.summary_lines))
        )[:5],
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


def _event_type_for_snapshot(event: CatalystEvent) -> str:
    return _event_type_from_text(
        " ".join(
            (
                event.title,
                event.category,
                event.source,
                " ".join(event.affected_sectors),
            )
        ),
        event.category,
    )


def _event_type_from_text(title: str, category: str = "") -> str:
    text = f"{title} {category}".casefold()
    for keywords, label in _EVENT_TYPE_RULES:
        if any(keyword.casefold() in text for keyword in keywords):
            return label
    if "政策" in category:
        return "产业政策"
    if "地缘" in category:
        return "地缘事件"
    if "供需" in category or "涨价" in category or "油价" in category:
        return "供应链/价格变化"
    return "其他事件"


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
            url=item.source_url,
            source_region="、".join(item.source_regions) or "mixed",
            source_quality=item.source_quality_label,
            event_type=_event_type_from_text(
                f"{item.source_title} {item.theme}", item.source_category
            ),
            affected_sectors=item.affected_sectors[:5],
            affected_symbols=item.affected_symbols[:5],
            transmission_hypothesis=item.transmission_hypothesis,
            supporting_evidence=item.supporting_evidence[:5],
            source_url=item.source_url,
            verification="多源/规则映射",
            transmission_path=item.transmission_path[:5],
            validation_signals=item.validation_signals[:3],
            invalidation_signals=item.invalidation_signals[:3],
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


def _intraday_source_provenance() -> dict[str, object]:
    """Read the latest bounded intraday provenance without fetching data."""
    state = _read_json_object(
        _runtime_json_path("AQSP_INTRADAY_STATUS", "data/intraday_refresh_status.json")
    )
    for field in ("provenance", "source_provenance"):
        value = state.get(field)
        if isinstance(value, dict):
            return value
    return {}


def _intraday_failure_summary() -> str:
    state = _read_json_object(
        _runtime_json_path("AQSP_INTRADAY_STATUS", "data/intraday_refresh_status.json")
    )
    if str(state.get("status") or "").strip() not in {"failed", "error"}:
        return ""
    reason = _text(state.get("reason") or state.get("detail"))
    return f"盘中任务失败：{reason}" if reason else "盘中任务失败：未记录原因"


def _intraday_running_summary() -> str:
    state = _read_json_object(
        _runtime_json_path("AQSP_INTRADAY_STATUS", "data/intraday_refresh_status.json")
    )
    if _text(state.get("status")) != "running":
        return ""
    reason = _text(state.get("reason") or state.get("detail"))
    return f"盘中刷新中：{reason}" if reason else "盘中刷新中"


def _snapshot_source(
    runtime: Any, task_view: Any, *, selected_date: str
) -> HomeSnapshotSource:
    source_status = getattr(task_view, "source_status", {}) or {}
    if not isinstance(source_status, dict):
        source_status = {}
    provenance = _intraday_source_provenance()
    intraday_state = _read_json_object(
        _runtime_json_path("AQSP_INTRADAY_STATUS", "data/intraday_refresh_status.json")
    )
    intraday_status = _text(intraday_state.get("status"))
    latest_trade_date = _first_text(
        getattr(runtime, "data_latest_trade_date", ""),
        source_status.get("data_latest_trade_date"),
        provenance.get("latest_trade_date"),
        "未记录",
    )
    completed_date = latest_completed_trading_day().isoformat()
    if selected_date == completed_date and latest_trade_date > completed_date:
        latest_trade_date = completed_date
    return HomeSnapshotSource(
        effective=_first_text(
            getattr(runtime, "effective_source", ""),
            getattr(runtime, "requested_source", ""),
            source_status.get("effective_source"),
            source_status.get("actual_source"),
            provenance.get("actual_source"),
            provenance.get("requested_source"),
            "未记录",
        ),
        latest_trade_date=latest_trade_date,
        lag_days=_lag_days(
            _first_text(
                getattr(runtime, "lag_days", ""),
                source_status.get("lag_days"),
                provenance.get("lag_days"),
            )
        ),
        status=_first_text(
            (
                _intraday_failure_summary()
                if intraday_status in {"failed", "error", "partial_failed"}
                else ""
            ),
            _intraday_running_summary() if intraday_status == "running" else "",
            getattr(runtime, "run_status", ""),
            source_status.get("status"),
            provenance.get("status"),
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


def _progress_days(value: str) -> int:
    match = re.match(r"\s*(\d+)\s*/", str(value or ""))
    return int(match.group(1)) if match else 0


def _runtime_json_path(env_name: str, default: str) -> Path:
    raw_path = os.getenv(env_name, default).strip()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    runtime_root = os.getenv("AQSP_RUNTIME_ROOT", "").strip()
    return (
        Path(runtime_root).expanduser() / path if runtime_root else PROJECT_ROOT / path
    )


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _raw_partial_coverage_floor() -> float:
    """Return the minimum completed raw-data coverage allowed into research."""
    raw = os.getenv("AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO", "").strip()
    if not raw:
        return DEFAULT_RAW_PARTIAL_COVERAGE_FLOOR
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RAW_PARTIAL_COVERAGE_FLOOR
    return value if 0 < value <= 1 else DEFAULT_RAW_PARTIAL_COVERAGE_FLOOR


def _raw_rebuild_universe_snapshot() -> HomeSnapshotUniverse | None:
    """Expose the resumable clean-database rebuild without treating it as live data."""
    state_path = _runtime_json_path(
        "AQSP_RAW_REBUILD_STATE_PATH",
        "data/.state/raw-rebuild-cursor.json",
    )
    payload = _read_json_object(state_path)
    if (
        not payload
        or _text(payload.get("target_day"))
        != latest_completed_trading_day().isoformat()
    ):
        return None
    total = int(payload.get("universe_size") or 0)
    raw_covered = payload.get("covered_ts_codes")
    covered_symbols = (
        tuple(dict.fromkeys(str(symbol) for symbol in raw_covered if symbol))
        if isinstance(raw_covered, list)
        else ()
    )
    coverage_pct = (len(covered_symbols) / total) if total else 0.0
    floor = _raw_partial_coverage_floor()
    complete = bool(payload.get("complete"))
    publish_ready = bool(payload.get("publish_ready"))
    missing = max(0, total - len(covered_symbols))
    if publish_ready:
        detail = (
            f"原始日线重建当日可用 {len(covered_symbols)}/{total}；"
            f"{missing} 只已排除；完成轮次覆盖达到 {floor:.0%} 下限，"
            "成功股票进入研究池"
        )
    elif complete:
        detail = (
            f"原始日线重建完成但仅覆盖 {len(covered_symbols)}/{total}；"
            f"未达到 {floor:.0%} 下限，候选继续阻塞"
        )
    else:
        detail = (
            f"原始日线重建仅覆盖 {len(covered_symbols)}/{total}；全市场重建尚未完成"
        )
    batch = payload.get("update")
    update = batch if isinstance(batch, dict) else {}
    batch_size = int(update.get("processed_symbols") or 0)
    next_offset = int(payload.get("next_offset") or 0)
    return HomeSnapshotUniverse(
        total=total,
        resolved=len(covered_symbols),
        screened=len(covered_symbols),
        max_universe=0,
        source="sqlite_raw_rebuild",
        batch_active=not publish_ready,
        batch_id=_text(payload.get("target_day")),
        batch_size=batch_size,
        cycle_id=(next_offset // batch_size + 1) if batch_size else 0,
        coverage_pct=coverage_pct,
        last_error=detail,
    )


def _universe_coverage_ratio(universe: HomeSnapshotUniverse) -> float:
    """Calculate the gate input from counts, not the optional display percentage."""
    return universe.resolved / universe.total if universe.total else 0.0


def _walkforward_evidence(*, evaluated_at: datetime) -> tuple[bool, datetime | None]:
    """Load production status and gate sidecar as one fail-closed evidence set."""
    status = _read_json_object(
        _runtime_json_path(
            "AQSP_WALKFORWARD_PRODUCTION_STATUS",
            "data/walkforward_production_status.json",
        )
    )
    if status.get("status") != "completed":
        return False, None

    sidecar = _read_json_object(
        _runtime_json_path(
            "AQSP_WALKFORWARD_GATE_PATH",
            "data/walkforward_gate.json",
        )
    )
    raw_run_date = sidecar.get("run_date")
    if not isinstance(raw_run_date, str):
        return False, None
    try:
        run_date = date.fromisoformat(raw_run_date)
    except ValueError:
        return False, None
    if sidecar.get("both_pass") is not True:
        return False, None

    evaluated_shanghai = to_shanghai(evaluated_at)
    age_days = (evaluated_shanghai.date() - run_date).days
    if age_days < 0 or age_days > DEFAULT_WALKFORWARD_MAX_AGE_DAYS:
        return False, datetime.combine(run_date, time.min, tzinfo=SHANGHAI_TZ)
    return True, datetime.combine(run_date, time.min, tzinfo=SHANGHAI_TZ)


def _recommendation_gate(
    provider: DashboardDataProvider,
    runtime: Any,
    source: Any,
    message_status: str,
    *,
    evaluated_at: datetime,
    universe: HomeSnapshotUniverse | None = None,
    candidates: tuple[HomeSnapshotCandidate, ...] = (),
    messages: tuple[HomeSnapshotMessage, ...] = (),
    research_chain: HomeSnapshotResearchChain | None = None,
) -> HomeSnapshotRecommendationGate:
    if (
        universe is not None
        and universe.source in {"sqlite_raw_refresh", "sqlite_raw_rebuild"}
        and universe.total > 0
        and _universe_coverage_ratio(universe) < _raw_partial_coverage_floor()
    ):
        return HomeSnapshotRecommendationGate(
            recommendation_allowed=False,
            status="blocked_incomplete_raw_data",
            reasons=(
                universe.last_error
                or f"原始日线仅覆盖 {universe.resolved}/{universe.total}；全市场刷新尚未完成",
            ),
        )
    cooldown_until = str(getattr(runtime, "cooldown_until", "") or "").strip()
    cooldown_date = None
    if cooldown_until:
        try:
            cooldown_date = date.fromisoformat(cooldown_until[:10])
        except ValueError:
            cooldown_date = None
    walkforward_ok, walkforward_updated_at = _walkforward_evidence(
        evaluated_at=evaluated_at
    )
    source_status = str(getattr(source, "status", "") or "").strip()
    # Risk cooldown limits paper-portfolio actions, not quote freshness.
    # News is a separate evidence stream: a failed news refresh must remain
    # visible in the message section without hiding a valid quote-based pick.
    raw_lag_days = getattr(source, "lag_days", None)
    lag_days = 999 if raw_lag_days in (None, "") else int(raw_lag_days)
    freshness_ok = source_status not in {"", "failed", "stale"} and lag_days <= 0
    paper_ledger_path = getattr(provider, "paper_ledger_path", None)
    paper_tracking_days = (
        count_paper_tracking_days(str(paper_ledger_path)) if paper_ledger_path else 0
    )
    result = evaluate_recommendation_gate(
        RecommendationGateInputs(
            coldstart_days=_progress_days(getattr(runtime, "coldstart_progress", "")),
            paper_tracking_days=paper_tracking_days,
            walkforward_ok=walkforward_ok,
            walkforward_updated_at=walkforward_updated_at,
            freshness=FreshnessEvidence(
                ok=freshness_ok,
                status=message_status,
                reason=("实时行情或消息源未达到最低新鲜度" if not freshness_ok else ""),
            ),
            circuit_breaker_until=cooldown_date,
            evaluated_at=evaluated_at,
        )
    )
    gate = HomeSnapshotRecommendationGate(
        recommendation_allowed=result.recommendation_allowed,
        status=result.status,
        reasons=result.reasons,
    )
    stale_symbols = tuple(
        candidate.symbol
        for candidate in candidates
        if candidate.freshness.strip().lower() not in {"", "fresh"}
    )
    if stale_symbols:
        return HomeSnapshotRecommendationGate(
            recommendation_allowed=False,
            status="freshness_not_ready",
            reasons=(f"候选行情已过期：{'、'.join(stale_symbols)}",),
        )
    if candidates:
        message_dependent_symbols = tuple(
            candidate.symbol
            for candidate in candidates
            if any(
                (
                    candidate.news_catalyst_summary,
                    candidate.news_catalyst_source,
                    candidate.news_catalyst_url,
                    candidate.news_catalyst_published_at,
                )
            )
        )
        linked_message_symbols = {
            symbol
            for message in messages
            if (message.source_url.strip() or message.url.strip())
            for symbol in message.affected_symbols
        }
        missing_message_symbols = tuple(
            candidate.symbol
            for candidate in candidates
            if candidate.symbol in message_dependent_symbols
            and candidate.symbol not in linked_message_symbols
        )
        if missing_message_symbols:
            return HomeSnapshotRecommendationGate(
                recommendation_allowed=False,
                status="research_evidence_not_ready",
                reasons=(
                    f"候选缺少可引用消息证据：{'、'.join(missing_message_symbols)}",
                ),
            )
        if research_chain is None or research_chain.status != "linked":
            return HomeSnapshotRecommendationGate(
                recommendation_allowed=False,
                status="research_validation_not_ready",
                reasons=(
                    (research_chain.blocker if research_chain else "研究验证链未生成")
                    or "讨论与变体验证尚未完整联动",
                ),
            )
    override = os.getenv("AQSP_RESEARCH_DISPLAY_OVERRIDE", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return HomeSnapshotRecommendationGate(
            recommendation_allowed=True,
            status="research_display",
            reasons=("research_display_override",),
        )
    return gate


def _phase_snapshot(
    provider: DashboardDataProvider,
    signal_date: str,
    current_candidates: tuple[HomeSnapshotCandidate, ...] = (),
) -> tuple[HomeSnapshotPhase, ...]:
    """Read phase artifacts without network calls and calculate symbol overlap."""
    phase_specs = (
        ("main_chain", "盘前", "盘前主链"),
        ("intraday", "盘中", "盘中观察"),
        ("closing_review", "盘后", "收盘复盘"),
    )
    phases: list[HomeSnapshotPhase] = []
    seen_symbols: set[str] = set()
    for task_id, label, _task_label in phase_specs:
        try:
            rows = provider._signal_task_rows_for_date(task_id, signal_date)
        except Exception:
            rows = []
        if task_id == "intraday" and not rows and current_candidates:
            rows = [
                {"symbol": candidate.symbol, "created_at": candidate.data_fetched_at}
                for candidate in current_candidates
            ]
        symbols = {
            str(row.get("symbol", "") or "").strip()
            for row in rows
            if str(row.get("symbol", "") or "").strip()
        }
        prior_symbols = set(seen_symbols)
        overlap = len(symbols & prior_symbols)
        seen_symbols.update(symbols)
        phases.append(
            HomeSnapshotPhase(
                task_id=task_id,
                label=label,
                status=(
                    "未产出"
                    if not rows
                    else "复用盘中结果"
                    if task_id == "closing_review"
                    and symbols
                    and symbols <= prior_symbols
                    else "已产出"
                ),
                candidate_count=len(rows),
                unique_symbols=len(symbols),
                overlap_symbols=overlap,
                updated_at=str(
                    max((row.get("created_at", "") for row in rows), default="") or ""
                ),
            )
        )
    return tuple(phases)


def _universe_snapshot() -> HomeSnapshotUniverse:
    daily_cursor = _runtime_json_path(
        "AQSP_DAILY_RESEARCH_CURSOR_PATH",
        "data/.state/daily-research-cursor.json",
    )
    daily_payload = _read_json_object(daily_cursor)
    if (
        daily_payload
        and str(daily_payload.get("trade_date") or "") == today_shanghai().isoformat()
    ):
        universe_count = int(daily_payload.get("universe_count") or 0)
        scanned_count = int(daily_payload.get("scanned_count") or 0)
        return HomeSnapshotUniverse(
            total=universe_count,
            resolved=scanned_count,
            screened=scanned_count,
            max_universe=int(daily_payload.get("batch_size") or 0),
            source="sqlite_db",
            batch_active=str(daily_payload.get("active_state") or "") == "selected",
            batch_id=_text(
                daily_payload.get("active_batch_id")
                or daily_payload.get("last_batch_id")
            ),
            batch_size=int(daily_payload.get("batch_size") or 0),
            cycle_id=int(daily_payload.get("cycle_id") or 0),
            coverage_pct=float(daily_payload.get("coverage_pct") or 0.0),
            last_error=_text(daily_payload.get("last_error")),
        )
    rebuild_universe = _raw_rebuild_universe_snapshot()
    if rebuild_universe is not None:
        return rebuild_universe
    raw_cursor = _runtime_json_path(
        "AQSP_SQLITE_REFRESH_CURSOR_PATH",
        "data/.state/sqlite-refresh-cursor.json",
    )
    raw_payload = _read_json_object(raw_cursor)
    if (
        raw_payload
        and _text(raw_payload.get("target_day"))
        == latest_completed_trading_day().isoformat()
    ):
        symbols = raw_payload.get("target_day_symbols")
        covered_symbols = (
            tuple(dict.fromkeys(str(symbol) for symbol in symbols if symbol))
            if isinstance(symbols, list)
            else ()
        )
        total = int(raw_payload.get("universe_size") or 0)
        last_batch = raw_payload.get("last_batch")
        batch = last_batch if isinstance(last_batch, dict) else {}
        batch_size = int(batch.get("processed_symbols") or 0)
        coverage_pct = (len(covered_symbols) / total) if total else 0.0
        coverage_error = _text(batch.get("coverage_error"))
        target_day = _text(raw_payload.get("target_day"))
        partial_coverage_floor = _raw_partial_coverage_floor()
        verified_exclusions = (
            total > 0
            and coverage_pct >= partial_coverage_floor
            and _text(batch.get("raw_max_trade_date")) == target_day
        )
        # A cursor only describes the next bounded refresh chunk. It cannot
        # keep an otherwise verified raw universe blocked after unavailable
        # symbols have been explicitly excluded for the target trading day.
        cycle_complete = bool(covered_symbols) and (
            int(raw_payload.get("offset") or 0) == 0 or verified_exclusions
        )
        if total and len(covered_symbols) < total and not coverage_error:
            missing = total - len(covered_symbols)
            coverage_error = (
                f"原始日线当日可用 {len(covered_symbols)}/{total}；"
                f"{missing} 只未返回当日日线，已排除；"
                f"完成轮次覆盖达到 {partial_coverage_floor:.0%} 下限，成功股票进入研究池"
                if cycle_complete
                else f"原始日线仅覆盖 {len(covered_symbols)}/{total}；全市场刷新尚未完成"
            )
        return HomeSnapshotUniverse(
            total=total,
            resolved=len(covered_symbols),
            screened=len(covered_symbols),
            max_universe=0,
            source="sqlite_raw_refresh",
            batch_active=bool(
                total and len(covered_symbols) < total and not cycle_complete
            ),
            batch_id=_text(raw_payload.get("target_day")),
            batch_size=batch_size,
            cycle_id=(int(raw_payload.get("offset") or 0) // batch_size + 1)
            if batch_size
            else 0,
            coverage_pct=coverage_pct,
            last_error=coverage_error,
        )
    raw = _runtime_json_path(
        "AQSP_INTRADAY_REFRESH_STATUS_PATH",
        "data/runtime/intraday_refresh_status.json",
    )
    payload = _read_json_object(raw)
    if not payload:
        legacy = PROJECT_ROOT / "data" / "intraday_refresh_status.json"
        if legacy != raw:
            payload = _read_json_object(legacy)
    if not payload:
        return HomeSnapshotUniverse()
    if not isinstance(payload, dict):
        return HomeSnapshotUniverse()
    batch = payload.get("universe")
    batch_payload = batch if isinstance(batch, dict) else {}
    return HomeSnapshotUniverse(
        total=int(
            batch_payload.get("universe_count")
            or payload.get("universe_total")
            or payload.get("total")
            or 0
        ),
        resolved=int(
            batch_payload.get("resolved_count")
            or payload.get("resolved_symbol_count")
            or 0
        ),
        screened=int(
            batch_payload.get("screened_count") or payload.get("screened_count") or 0
        ),
        final=int(
            batch_payload.get("final_count")
            or payload.get("final_count")
            or payload.get("candidate_count")
            or 0
        ),
        max_universe=int(payload.get("max_universe") or 0),
        source=_text(payload.get("actual_source") or payload.get("source")),
        batch_active=bool(batch_payload.get("batch_active", False)),
        batch_id=_text(batch_payload.get("batch_id")),
        batch_size=int(batch_payload.get("batch_size") or 0),
        cycle_id=int(batch_payload.get("cycle_id") or 0),
        coverage_pct=float(batch_payload.get("coverage_pct") or 0.0),
        last_error=_text(batch_payload.get("last_error")),
    )


def _variant_results_payload() -> tuple[dict[str, Any] | None, str]:
    path = _runtime_json_path(
        "AQSP_VARIANT_RESULTS",
        "data/runtime/variant_results.json",
    )
    payload = _read_json_object(path)
    if not payload:
        return None, "变体产物不存在。"
    if payload.get("initial_cash") != 100_000.0:
        return None, "变体初始资金不符合 100000 元纸面账户契约。"
    universe = payload.get("universe")
    variants = payload.get("variants")
    if (
        payload.get("schema_version") != "variant-suite-v2"
        or not isinstance(universe, dict)
        or not isinstance(variants, list)
        or len(variants) < MIN_HOME_VARIANT_COUNT
        or int(universe.get("selected_symbols") or 0) < MIN_HOME_VARIANT_SYMBOLS
    ):
        return (
            None,
            "变体产物未达到 schema、24 个有效多元变体或 600 只合格股票的最低契约。",
        )
    try:
        validate_variant_payload(
            payload,
            path=str(path),
            min_variants=MIN_HOME_VARIANT_COUNT,
            min_symbols=MIN_HOME_VARIANT_SYMBOLS,
        )
    except (TypeError, ValueError) as exc:
        return None, f"变体数据契约校验失败：{exc}"
    return payload, ""


def _variant_refresh_status_error() -> str:
    path = _runtime_json_path(
        "AQSP_VARIANT_REFRESH_STATUS",
        "data/runtime/variant_refresh_status.json",
    )
    payload = _read_json_object(path)
    if not payload:
        return ""
    status = _text(payload.get("status"))
    message = _text(payload.get("message"))
    reason = _text(payload.get("reason"))
    generated_at = _text(payload.get("generated_at"))
    if status == "waiting" and generated_at[:10] != now_shanghai().date().isoformat():
        return "变体调度状态已过期，等待下一次正式刷新。"
    staged = int(payload.get("profiles_staged") or 0)
    total = int(payload.get("profiles_total") or 0)
    if status == "staged":
        progress = f"已完成 {staged}/{total} 个变体" if total else "已写入分段 staging"
        return f"变体分段构建中：{progress}；{message or '等待下一错峰窗口继续'}"
    if status == "waiting":
        return f"变体等待：{message or '等待下一个错峰运行窗口'}"
    if status == "completed":
        return ""
    if status in {"timed_out", "skipped_lock", "rejected", "failed"}:
        detail = reason or message
        return f"变体未发布：{detail or status}"
    return ""


def _variant_suite_snapshot() -> HomeSnapshotVariantSuite:
    """Read bounded metadata from the isolated experiment artifact."""
    payload, error = _variant_results_payload()
    if not payload:
        universe = _universe_snapshot()
        if universe.last_error:
            return HomeSnapshotVariantSuite(
                last_error=f"变体等待：{universe.last_error}"
            )
        return HomeSnapshotVariantSuite(
            last_error=_variant_refresh_status_error() or error
        )
    universe = payload.get("universe")
    if not isinstance(universe, dict):
        universe = {}
    return HomeSnapshotVariantSuite(
        schema_version=_text(payload.get("schema_version")),
        generated_at=_text(payload.get("generated_at")),
        data_mode=_text(payload.get("data_mode")),
        end_date=_text(payload.get("end_date")),
        variant_count=len(payload.get("variants", ()))
        if isinstance(payload.get("variants"), list)
        else 0,
        selected_symbols=int(universe.get("selected_symbols") or 0),
        supported_symbols=int(universe.get("supported_symbols") or 0),
        batch_active=bool(universe.get("batch_active", False)),
        batch_id=_text(universe.get("batch_id")),
        batch_size=int(universe.get("batch_size") or 0),
        cycle_id=int(universe.get("cycle_id") or 0),
        coverage_pct=float(universe.get("coverage_pct") or 0.0),
        filters=_text(universe.get("filters")),
    )


def _phase_conclusion_summaries(
    provider: DashboardDataProvider,
    signal_date: str,
    debates: tuple[HomeSnapshotDebate, ...],
    current_candidates: tuple[HomeSnapshotCandidate, ...] = (),
) -> tuple[str, ...]:
    """Produce one decision statement per phase from that phase's own artifact."""
    phase_specs = (
        ("盘前", "main_chain"),
        ("盘中", "intraday"),
        ("盘后", "closing_review"),
    )
    debate_by_symbol = {debate.symbol: debate for debate in debates}
    lines: list[str] = []
    for label, task_id in phase_specs:
        try:
            rows = provider._signal_task_rows_for_date(task_id, signal_date)
        except Exception:
            rows = []
        if task_id == "intraday" and current_candidates:
            rows_by_symbol = {
                _text(row.get("symbol")): row
                for row in rows
                if _text(row.get("symbol"))
            }
            rows = [
                rows_by_symbol.get(candidate.symbol)
                or {
                    "symbol": candidate.symbol,
                    "name": candidate.display_name,
                    "score": candidate.score,
                    "reasons": "；".join(
                        getattr(candidate, "deterministic_reasons", ())
                        or getattr(candidate, "reasons", ())
                    ),
                }
                for candidate in current_candidates
            ]
        if not rows:
            lines.append(f"{label}：未产出，等待{label}任务完成。")
            continue
        lead = max(rows, key=lambda row: float(row.get("score") or 0.0))
        symbol = _text(lead.get("symbol"))
        name = _first_text(_text(lead.get("name")), symbol, "主线对象")
        reasons = _text(lead.get("reasons"))
        rule = reasons.split("；", 1)[0] if reasons else "规则条件"
        debate = debate_by_symbol.get(symbol)

        if task_id == "main_chain":
            detail = (
                f"计划：{len(rows)} 个对象进入开盘观察；优先核对 {name} 的{rule}，"
                "开盘后只确认量价承接与数据新鲜度。"
            )
        elif task_id == "intraday":
            review = (
                _first_text(debate.primary_risk_gate, debate.next_trigger)
                if debate is not None
                else "未形成独立复核结论"
            )
            detail = (
                f"判断：{len(rows)} 个对象通过盘中筛选；{name} 的{rule}仍有效。"
                f"约束：{review}。"
            )
        else:
            closing_symbols = {
                str(row.get("symbol", "") or "").strip()
                for row in rows
                if str(row.get("symbol", "") or "").strip()
            }
            intraday_rows = provider._signal_task_rows_for_date("intraday", signal_date)
            intraday_symbols = {
                str(row.get("symbol", "") or "").strip()
                for row in intraday_rows
                if str(row.get("symbol", "") or "").strip()
            }
            if closing_symbols and closing_symbols == intraday_symbols:
                lines.append(
                    "盘后：未形成独立收盘复盘；本轮仅复用盘中结果，不重复计入当天结论。"
                )
                continue
            detail = (
                f"复盘：{len(rows)} 个对象写入收盘记录；{name} 的{rule}"
                "仅保留为次日观察依据，不把盘中信号外推为结论。"
            )
        lines.append(f"{label}：{detail}")
    return tuple(lines)


def _variant_snapshot() -> tuple[HomeSnapshotVariant, ...]:
    """Read only bounded summaries from the isolated experiment artifact."""
    payload, _ = _variant_results_payload()
    if not payload:
        return ()
    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, list):
        return ()
    rules = payload.get("execution_rules")
    rule_labels = (
        "T+1：买入当日不可卖",
        "100 股整手",
        "停牌/涨停买入/跌停卖出拒绝",
        "含佣金、印花税、滑点",
    )
    variants: list[HomeSnapshotVariant] = []
    for item in raw_variants[:MAX_HOME_VARIANTS]:
        if not isinstance(item, dict) or item.get("initial_cash") != 100_000.0:
            continue
        holdings = _variant_holdings(
            item.get("holdings", ()), "holding", _text(payload.get("end_date"))
        )
        previous_holdings = _variant_holdings(
            item.get("previous_holdings", ()),
            "previous_holding",
            _text(payload.get("end_date")),
        )
        recent_actions = _variant_recent_actions(item)
        variants.append(
            HomeSnapshotVariant(
                variant_id=_text(item.get("variant_id")),
                label=_text(item.get("label")) or _text(item.get("variant_id")),
                initial_cash=100_000.0,
                cash=float(item.get("cash") or 0.0),
                final_equity=float(item.get("final_equity") or 0.0),
                total_pnl=float(item.get("total_pnl") or 0.0),
                rank=int(item.get("rank") or 0),
                return_pct=float(item.get("return_pct") or 0.0),
                filled_orders=int(item.get("filled_orders") or 0),
                rejected_orders=int(item.get("rejected_orders") or 0),
                start_date=_text(payload.get("start_date")),
                end_date=_text(payload.get("end_date")),
                data_mode=_text(payload.get("data_mode")),
                strategy=_variant_strategy_text(item),
                holdings=holdings,
                holdings_date=_text(item.get("holdings_date")),
                previous_holdings=previous_holdings,
                previous_holdings_date=_text(item.get("previous_holdings_date")),
                recent_actions=recent_actions,
                adjustments=_variant_adjustment_lines(
                    item, holdings, previous_holdings, recent_actions
                ),
                technical_evidence=_variant_technical_evidence(item, recent_actions),
                hard_rules=rule_labels if isinstance(rules, dict) else (),
                generation=int(item.get("generation") or 1),
                parent_variant_id=_text(item.get("parent_variant_id")),
                independent_signal_days=int(
                    item.get("independent_signal_days") or 0
                ),
                lifecycle_status=_text(item.get("lifecycle_status"))
                or "样本积累",
                lifecycle_reason=_text(item.get("lifecycle_reason")),
                discussion_links=tuple(
                    link
                    for link in item.get("discussion_links", ())
                    if isinstance(link, dict)
                ),
            )
        )
    return tuple(variants)


def _variant_experiment_symbols() -> tuple[str, ...]:
    """Return the validated raw experiment pool, separate from current holdings."""
    payload, _ = _variant_results_payload()
    if not payload:
        return ()
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list):
        return ()
    return tuple(dict.fromkeys(_text(value) for value in raw_symbols if _text(value)))


def _research_chain_snapshot(
    candidates: tuple[HomeSnapshotCandidate, ...],
    debates: tuple[HomeSnapshotDebate, ...],
    variant_suite: HomeSnapshotVariantSuite,
    variants: tuple[HomeSnapshotVariant, ...],
    experiment_symbols: tuple[str, ...] = (),
    selected_date: str = "",
    carried_reviews: tuple[HomeSnapshotCarriedReview, ...] = (),
) -> HomeSnapshotResearchChain:
    """Join current-day evidence without allowing variants to affect scoring."""
    candidate_symbols = tuple(candidate.symbol for candidate in candidates)
    debated_symbols = tuple(debate.symbol for debate in debates)
    debated_set = set(debated_symbols)
    holding_symbols = tuple(
        dict.fromkeys(
            holding.symbol
            for variant in variants
            for holding in variant.holdings
            if holding.symbol
        )
    )
    experiment_set = set(experiment_symbols)
    holding_set = set(holding_symbols)
    variant_current_for_selected_date = not selected_date or (
        variant_suite.end_date == selected_date
    )
    variant_candidate_symbols = tuple(
        symbol for symbol in candidate_symbols if symbol in experiment_set
    )
    variant_review_symbols = tuple(
        symbol for symbol in debated_symbols if symbol in experiment_set
    )
    variant_holding_candidate_symbols = tuple(
        symbol for symbol in candidate_symbols if symbol in holding_set
    )
    variant_holding_review_symbols = tuple(
        symbol for symbol in debated_symbols if symbol in holding_set
    )
    pending_review_symbols = tuple(
        symbol for symbol in candidate_symbols if symbol not in debated_set
    )
    if not variants and not experiment_symbols:
        return HomeSnapshotResearchChain(
            status="blocked",
            candidate_symbols=candidate_symbols,
            debated_symbols=debated_symbols,
            pending_review_symbols=pending_review_symbols,
            carried_reviews=carried_reviews,
            blocker=variant_suite.last_error or "变体产物不存在。",
        )
    if not variant_current_for_selected_date:
        return HomeSnapshotResearchChain(
            status="waiting_validation",
            candidate_symbols=candidate_symbols,
            debated_symbols=debated_symbols,
            pending_review_symbols=pending_review_symbols,
            carried_reviews=carried_reviews,
            blocker=(
                f"变体结果截至 {variant_suite.end_date or '未知日期'}，"
                "不作为当天结论的验证证据。"
            ),
        )
    return HomeSnapshotResearchChain(
        status=(
            "linked"
            if not pending_review_symbols
            and set(candidate_symbols).issubset(experiment_set)
            else "waiting_validation"
        ),
        candidate_symbols=candidate_symbols,
        debated_symbols=debated_symbols,
        pending_review_symbols=pending_review_symbols,
        variant_candidate_symbols=variant_candidate_symbols,
        variant_review_symbols=variant_review_symbols,
        variant_holding_candidate_symbols=variant_holding_candidate_symbols,
        variant_holding_review_symbols=variant_holding_review_symbols,
        carried_reviews=carried_reviews,
        blocker=(
            "当天候选尚未全部进入本轮 raw 变体实验池，等待下轮覆盖。"
            if not set(candidate_symbols).issubset(experiment_set)
            else "当天候选讨论尚未全部完成。"
            if pending_review_symbols
            else ""
        ),
    )


def _carried_reviews_snapshot(
    provider: DashboardDataProvider,
    selected_date: str,
    current_symbols: set[str],
) -> tuple[HomeSnapshotCarriedReview, ...]:
    """Retain prior review conclusions without promoting them to today's list."""
    anchor = date.fromisoformat(selected_date)
    carried: list[HomeSnapshotCarriedReview] = []
    seen = set(current_symbols)
    if not hasattr(provider, "debate_summaries"):
        return ()
    for _ in range(MAX_HOME_DATES - 1):
        anchor = get_previous_trading_day(anchor)
        try:
            summaries = provider.debate_summaries(anchor.isoformat(), limit=8)
        except (OSError, ValueError):
            continue
        for summary in summaries:
            symbol = _text(getattr(summary, "symbol", ""))
            if not symbol or symbol in seen:
                continue
            conclusion = _first_text(
                getattr(summary, "research_verdict", ""),
                getattr(summary, "consensus", ""),
                getattr(summary, "adjustment_reason", ""),
            )
            next_trigger = _text(getattr(summary, "next_trigger", ""))
            carried.append(
                HomeSnapshotCarriedReview(
                    signal_date=anchor.isoformat(),
                    symbol=symbol,
                    display_name=_text(getattr(summary, "display_name", "")),
                    conclusion=conclusion or "原复核未形成最终结论。",
                    primary_risk_gate=_text(
                        getattr(summary, "primary_risk_gate", "")
                    ),
                    next_trigger=next_trigger,
                    status="等待复现条件" if next_trigger else "待补最终结论",
                )
            )
            seen.add(symbol)
            if len(carried) >= 12:
                return tuple(carried)
    return tuple(carried)


def _variant_strategy_text(item: dict) -> str:
    raw = item.get("strategy")
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False, sort_keys=True)
    return _text(raw) or _text(item.get("strategy_label")) or _text(item.get("label"))


def _variant_holdings(
    payload: object, field_name: str, end_date: str = ""
) -> tuple[HomeSnapshotHolding, ...]:
    def entry_date_for(holding: dict[str, object]) -> str:
        evidence = holding.get("entry_evidence")
        if isinstance(evidence, dict):
            return (
                _text(evidence.get("execution_date"))
                or _text(evidence.get("signal_date"))
                or _text(evidence.get("date"))
            )
        return _text(holding.get("entry_date"))

    def holding_days_for(entry_date: str) -> int:
        if not entry_date or not end_date:
            return 0
        try:
            return max(
                0,
                (
                    date.fromisoformat(end_date[:10])
                    - date.fromisoformat(entry_date[:10])
                ).days,
            )
        except ValueError:
            return 0

    return tuple(
        HomeSnapshotHolding(
            symbol=_text(holding.get("symbol")),
            quantity=int(holding.get("quantity") or 0),
            average_price=float(holding.get("average_price") or 0.0),
            last_price=float(holding.get("last_price") or 0.0),
            market_value=float(holding.get("market_value") or 0.0),
            unrealized_pnl=float(holding.get("unrealized_pnl") or 0.0),
            name=_text(holding.get("name")),
            entry_date=entry_date_for(holding),
            holding_days=holding_days_for(entry_date_for(holding)),
        )
        for holding in payload
        if isinstance(holding, dict) and _text(holding.get("symbol"))
    )


def _variant_recent_actions(item: dict) -> tuple[dict[str, object], ...]:
    raw_actions = tuple(
        action for action in item.get("recent_actions", ()) if isinstance(action, dict)
    )
    if raw_actions:
        return raw_actions
    fills = [
        fill
        for fill in item.get("fills", ())
        if isinstance(fill, dict) and _text(fill.get("status")) == "filled"
    ][-8:]
    return tuple(
        {
            "date": _text(fill.get("date")),
            "symbol": _text(fill.get("symbol")),
            "side": _text(fill.get("side")),
            "quantity": int(fill.get("quantity") or 0),
            "price": float(fill.get("price") or 0.0),
            "reason": _text(fill.get("reason"))
            or "旧版变体产物只保留成交记录；v2 重算后补齐 MACD/KDJ/量比触发原因。",
            "evidence": fill.get("evidence")
            if isinstance(fill.get("evidence"), dict)
            else {},
        }
        for fill in fills
    )


def _variant_technical_evidence(
    item: dict, recent_actions: tuple[dict[str, object], ...]
) -> tuple[dict[str, object], ...]:
    raw = tuple(
        value for value in item.get("technical_evidence", ()) if isinstance(value, dict)
    )
    if raw:
        return raw[:8]
    derived = tuple(
        {
            **dict(action.get("evidence")),
            "date": action.get("date", ""),
            "symbol": action.get("symbol", ""),
            "name": action.get("name") or action.get("display_name") or "",
            "side": action.get("side") or action.get("action") or "",
            "reason": action.get("reason", ""),
        }
        for action in recent_actions
        if isinstance(action.get("evidence"), dict) and action.get("evidence")
    )
    return derived[:8]


def _variant_adjustment_lines(
    item: dict,
    holdings: tuple[HomeSnapshotHolding, ...],
    previous_holdings: tuple[HomeSnapshotHolding, ...],
    recent_actions: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    raw = tuple(_text(action) for action in item.get("adjustments", ()) if action)
    if raw:
        return raw
    current = {holding.symbol: holding.quantity for holding in holdings}
    previous = {holding.symbol: holding.quantity for holding in previous_holdings}
    lines: list[str] = []
    for symbol in sorted(set(current) | set(previous)):
        before = previous.get(symbol, 0)
        after = current.get(symbol, 0)
        if before == after and after:
            lines.append(
                f"保留 {symbol}：昨日 {before} 股，今日 {after} 股；仓位未变化。"
            )
        elif before == 0 and after:
            lines.append(
                f"持有 {symbol}：今日 {after} 股；旧版产物无昨日基线，v2 产物会补齐换票原因。"
            )
        elif before and after == 0:
            lines.append(f"移出 {symbol}：昨日 {before} 股，今日无。")
        elif after > before:
            lines.append(f"加仓 {symbol}：昨日 {before} 股，今日 {after} 股。")
        elif after < before:
            lines.append(f"减仓 {symbol}：昨日 {before} 股，今日 {after} 股。")
    for action in recent_actions:
        if len(lines) >= 8:
            break
        side = _text(action.get("side"))
        symbol = _text(action.get("symbol"))
        quantity = int(action.get("quantity") or 0)
        reason = _text(action.get("reason"))
        if side and symbol:
            lines.append(f"{side} {symbol} {quantity} 股；{reason}")
    return tuple(lines[:8]) or ("今日/昨日持仓无变化，未发生换票。",)


def build_home_snapshot(
    provider: DashboardDataProvider,
    *,
    signal_date: str = "",
    task_id: str = "",
) -> HomeDashboardSnapshot:
    """Build a bounded, file-ready home snapshot from local runtime artifacts only."""
    selected_task_id = _snapshot_task_id(task_id) or provider.default_task_id()
    # Intraday is a realtime surface. Historical daily views remain pinned to
    # the latest completed close, but the live homepage must use today's
    # intraday artifact while the market is open.
    requested_date = _text(signal_date) or (
        today_shanghai().isoformat()
        if selected_task_id == "intraday"
        else latest_completed_trading_day().isoformat()
    )
    payload = provider.home_digest_payload(
        selected_task_id,
        signal_date=requested_date,
    )
    task_view = payload.task_view
    selected_date = _resolve_selected_date(payload, requested_date)
    runtime = provider.runtime_overview(selected_date)
    universe = _universe_snapshot()
    generated_at = to_shanghai(now_shanghai()).isoformat(timespec="seconds")
    source = _snapshot_source(runtime, task_view, selected_date=selected_date)
    candidates = _snapshot_candidates(payload)
    runtime_debates = _runtime_debates_for_snapshot(
        selected_date,
        {candidate.symbol for candidate in candidates},
    )
    debates = _snapshot_debates(
        payload,
        candidates,
        runtime_debates=runtime_debates,
    )
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
    market_context = _with_news_source_coverage(market_context, catalyst_report)
    messages = _append_cross_market_messages(messages, artifact)
    variant_suite = _variant_suite_snapshot()
    variants = _variant_snapshot()
    carried_reviews = _carried_reviews_snapshot(
        provider, selected_date, {candidate.symbol for candidate in candidates}
    )
    research_chain = _research_chain_snapshot(
        candidates,
        debates,
        variant_suite,
        variants,
        _variant_experiment_symbols(),
        selected_date,
        carried_reviews,
    )
    recommendation_gate = _recommendation_gate(
        provider,
        runtime,
        source,
        message_status,
        evaluated_at=now_shanghai(),
        universe=universe,
        candidates=candidates,
        messages=messages,
        research_chain=research_chain,
    )
    candidates = _apply_recommendation_gate(candidates, recommendation_gate)
    phases = _phase_snapshot(provider, selected_date, candidates)
    # Home conclusions are a fixed three-part timeline. Empty-data and
    # quality blockers have dedicated status surfaces and must not displace a
    # market phase from this bounded timeline.
    summaries = _phase_conclusion_summaries(
        provider, selected_date, debates, candidates
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
        source=source,
        coldstart=_snapshot_coldstart(runtime),
        stale_after=stale_after_for_task(generated_at, selected_task_id),
        message_status=message_status,
        messages=messages,
        market_context=market_context,
        recommendation_gate=recommendation_gate,
        phases=phases,
        universe=universe,
        variant_suite=variant_suite,
        variants=variants,
        research_chain=research_chain,
    )


def _historical_gap_snapshot(
    template: HomeDashboardSnapshot, selected_date: str
) -> HomeDashboardSnapshot:
    """Represent a missing archive day without fabricating candidates or evidence."""
    generated = datetime.fromisoformat(f"{selected_date}T23:59:59+08:00")
    return replace(
        template,
        generated_at=generated.isoformat(timespec="seconds"),
        selected_date=selected_date,
        available_dates=(selected_date,),
        candidates=(),
        debates=(),
        summaries=("该交易日没有可恢复的独立候选、讨论或复核产物。",),
        message_status="历史归档缺失",
        messages=(),
        market_context=None,
        recommendation_gate=HomeSnapshotRecommendationGate(
            recommendation_allowed=False,
            status="blocked",
            reasons=("historical_artifact_missing",),
        ),
        phases=(),
        universe=HomeSnapshotUniverse(source="historical_archive_missing"),
        variant_suite=HomeSnapshotVariantSuite(
            data_mode="historical_archive_missing",
            end_date=selected_date,
            last_error="该日没有独立变体产物",
        ),
        variants=(),
        research_chain=HomeSnapshotResearchChain(
            status="blocked",
            blocker="该日没有独立研究产物；此页面不使用其他日期数据代填。",
        ),
        stale_after=(generated + timedelta(days=1)).isoformat(timespec="seconds"),
    )


def build_home_snapshot_index(
    provider: DashboardDataProvider,
    *,
    signal_date: str = "",
    task_id: str = "",
    initial_snapshot: HomeDashboardSnapshot | None = None,
    existing_index: HomeSnapshotIndex | None = None,
) -> HomeSnapshotIndex:
    """Build seven exact-date snapshots without making history block today."""
    first = initial_snapshot or build_home_snapshot(
        provider,
        signal_date=signal_date,
        task_id=task_id,
    )
    selected_task_id = _snapshot_task_id(task_id) or provider.default_task_id()
    day_snapshots = [HomeSnapshotDay(date=first.selected_date, snapshot=first)]
    existing_by_date = {
        day.date: day
        for day in (existing_index.days if existing_index is not None else ())
    }
    if existing_index is not None:
        # Refreshes must not rebuild archived days from mutable runtime inputs.
        requested_dates = [first.selected_date, *first.available_dates]
    else:
        anchor = date.fromisoformat(first.selected_date)
        requested_dates = [first.selected_date]
        for _ in range(MAX_HOME_SNAPSHOT_INDEX_DAYS - 1):
            anchor = get_previous_trading_day(anchor)
            requested_dates.append(anchor.isoformat())
        requested_dates.extend(
            value for value in first.available_dates if value not in requested_dates
        )
    for available_date in requested_dates:
        if available_date == first.selected_date:
            continue
        if len(day_snapshots) >= MAX_HOME_SNAPSHOT_INDEX_DAYS:
            break
        existing = existing_by_date.get(available_date)
        if existing is not None:
            day_snapshots.append(existing)
            continue
        try:
            snapshot = build_home_snapshot(
                provider,
                signal_date=available_date,
                task_id=selected_task_id,
            )
        except (DataError, OSError, ValueError):
            # Keep the date selectable, but never reuse today's candidate data.
            snapshot = _historical_gap_snapshot(first, available_date)
        if snapshot.selected_date != available_date:
            continue
        day_snapshots.append(HomeSnapshotDay(date=available_date, snapshot=snapshot))

    generated_at = to_shanghai(now_shanghai()).isoformat(timespec="seconds")
    return HomeSnapshotIndex(
        schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
        generated_at=generated_at,
        stale_after=stale_after_for_task(generated_at, selected_task_id),
        selected_date=first.selected_date,
        days=tuple(day_snapshots),
    )


def merge_home_snapshot_index(
    existing: HomeSnapshotIndex | None,
    refreshed: HomeSnapshotIndex,
) -> HomeSnapshotIndex:
    """Refresh one requested date without erasing older indexed evidence.

    Intraday artifacts are intentionally short-lived. A later refresh may not
    be able to reproduce an older day's candidate file, so existing historical
    snapshots remain authoritative unless that date was explicitly requested.
    """
    if existing is None:
        return refreshed

    completed_date = latest_completed_trading_day().isoformat()
    selected_date = refreshed.selected_date
    newest_date = max(
        (day.date for day in (*existing.days, *refreshed.days)),
        default=selected_date,
    )
    recent_dates = {newest_date}
    anchor = date.fromisoformat(newest_date)
    for _ in range(MAX_HOME_SNAPSHOT_INDEX_DAYS - 1):
        anchor = get_previous_trading_day(anchor)
        recent_dates.add(anchor.isoformat())
    existing_by_date = {
        day.date: day
        for day in existing.days
        if day.date in recent_dates
        and (day.date == selected_date or day.date <= completed_date)
    }
    refreshed_by_date = {
        day.date: day
        for day in refreshed.days
        if day.date in recent_dates
        and (day.date == selected_date or day.date <= completed_date)
    }
    dates = set(existing_by_date) | set(refreshed_by_date)
    ordered_dates = [refreshed.selected_date]
    ordered_dates.extend(
        sorted(
            (value for value in dates if value != refreshed.selected_date),
            reverse=True,
        )
    )
    selected_days: list[HomeSnapshotDay] = []
    for value in ordered_dates[:MAX_HOME_SNAPSHOT_INDEX_DAYS]:
        if value == refreshed.selected_date:
            selected_days.append(refreshed_by_date[value])
            continue
        previous = existing_by_date.get(value)
        if previous is not None:
            selected_days.append(previous)
            continue
        current = refreshed_by_date.get(value)
        if current is not None:
            selected_days.append(current)

    merged_dates = tuple(day.date for day in selected_days)
    normalized_days = tuple(
        HomeSnapshotDay(
            date=day.date,
            snapshot=replace(day.snapshot, available_dates=merged_dates),
        )
        for day in selected_days
    )
    return HomeSnapshotIndex(
        schema_version=refreshed.schema_version,
        generated_at=refreshed.generated_at,
        stale_after=refreshed.stale_after,
        selected_date=refreshed.selected_date,
        days=normalized_days,
    )


def _resolve_output_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _guard_empty_same_day_refresh(
    existing: HomeDashboardSnapshot | None,
    refreshed: HomeDashboardSnapshot,
) -> None:
    """Never replace a valid same-day result with a transient empty refresh."""
    if (
        existing is not None
        and existing.selected_date == refreshed.selected_date
        and existing.candidates
        and not refreshed.candidates
    ):
        raise DataError(
            "refusing to replace a non-empty same-day home snapshot with empty data"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=os.environ.get("AQSP_HOME_SNAPSHOT_PATH", DEFAULT_OUTPUT_PATH),
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
    existing_index = load_home_snapshot_index(index_path)
    index = build_home_snapshot_index(
        provider,
        signal_date=args.date.strip(),
        task_id=args.task_id.strip(),
        initial_snapshot=snapshot,
        existing_index=existing_index,
    )
    index = merge_home_snapshot_index(existing_index, index)
    current_snapshot = next(
        day.snapshot for day in index.days if day.date == index.selected_date
    )
    _guard_empty_same_day_refresh(
        load_home_dashboard_snapshot(output_path), current_snapshot
    )
    write_home_dashboard_snapshot(output_path, current_snapshot)
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
