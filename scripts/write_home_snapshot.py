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
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aqsp.core.time import (
    get_previous_trading_day,
    now_shanghai,
    today_shanghai,
    to_shanghai,
)
from aqsp.market_context import MarketContextArtifact, build_market_context_artifact
from aqsp.data.source_factory import load_sqlite_symbol_name_map
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
    MAX_HOME_SNAPSHOT_VARIANTS,
    MAX_HOME_SNAPSHOT_CANDIDATES,
    MAX_HOME_SNAPSHOT_DEBATES,
    MAX_HOME_SNAPSHOT_INDEX_DAYS,
    HomeDashboardSnapshot,
    HomeSnapshotDay,
    HomeSnapshotCandidate,
    HomeSnapshotColdstart,
    HomeSnapshotRecommendationGate,
    HomeSnapshotCrossMarket,
    HomeSnapshotDebate,
    HomeSnapshotIndex,
    HomeSnapshotMarketContext,
    HomeSnapshotMessage,
    HomeSnapshotPhase,
    HomeSnapshotSource,
    HomeSnapshotTechnicalMetric,
    HomeSnapshotUniverse,
    HomeSnapshotHolding,
    HomeSnapshotVariant,
    HomeSnapshotVariantUniverse,
    MAX_HOME_SNAPSHOT_TECHNICAL_METRICS,
    HOME_RECOMMENDATION_LABELS,
    is_home_recommendation,
    load_home_snapshot_index,
    stale_after_for_task,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)


DEFAULT_OUTPUT_PATH = "data/runtime/home_dashboard_snapshot.json"
DEFAULT_INDEX_OUTPUT_PATH = "data/runtime/home_dashboard_snapshot_index.json"
MAX_HOME_DATES = 4
MAX_HOME_CANDIDATES = MAX_HOME_SNAPSHOT_CANDIDATES
MAX_HOME_SUMMARIES = 3
MAX_HOME_MESSAGES = 8
MAX_HOME_MESSAGES_PER_SOURCE = 3
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
    current_events: list[CatalystEvent] = []
    historical_count = 0
    invalid_count = 0
    current_day = today_shanghai().isoformat()
    current_time = now_shanghai()
    live_window_start = _current_message_window_start(signal_date, current_time)
    for event in report.events:
        published_at = _normalize_timestamp(event.published_at)
        if not published_at:
            invalid_count += 1
            continue
        published_dt = datetime.fromisoformat(published_at)
        if published_dt > current_time:
            invalid_count += 1
            continue
        is_recent_live_event = (
            signal_date == current_day
            and live_window_start <= published_dt <= current_time
        )
        if published_at[:10] != signal_date and not is_recent_live_event:
            historical_count += 1
            continue
        current_events.append(
            replace(
                event,
                published_at=published_at,
                source_fetched_at=_normalize_timestamp(event.source_fetched_at),
            )
        )
    normalized_report = replace(
        report,
        generated_at=_normalize_timestamp(report.generated_at),
        events=tuple(current_events),
        event_status=(
            "stale_only"
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


def _candidate_score_breakdown(candidate: Any) -> tuple[str, ...]:
    raw = getattr(candidate, "score_breakdown", ()) or ()
    if isinstance(raw, str):
        raw = (raw,)
    return _bounded_unique_text(raw, 4)


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
    if not context:
        # Technical candidates often have no news context. Reuse their
        # deterministic reason so the homepage explains the evidence instead
        # of writing an invalid empty context.
        context = _first_text(*reasons, *score_breakdown, *strategies)
    if not context and reasons:
        context = "独立规则证据已记录"
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


def _snapshot_recommendation_count(payload: Any) -> int:
    """Count all distinct deterministic recommendation cards before the home cap."""
    ordered = (
        *(getattr(payload.task_view, "detail_cards", ()) or ()),
        *(getattr(payload, "spotlights", ()) or ()),
    )
    seen: set[str] = set()
    count = 0
    for item in ordered:
        raw_status = _first_text(
            getattr(item, "action_label", ""),
            getattr(item, "status_label", ""),
            getattr(item, "rank_label", ""),
        )
        if not any(label in raw_status for label in HOME_RECOMMENDATION_LABELS):
            continue
        if not _has_candidate_deterministic_evidence(item):
            continue
        symbol = _text(getattr(item, "symbol", ""))
        if symbol and symbol not in seen:
            seen.add(symbol)
            count += 1
    return count


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


def _align_count_summary(text: str, *, total: int, shown: int) -> str:
    """Make a legacy count headline explicit when the home card cap hides rows."""
    if not text:
        return text
    match = re.search(r"(?:纸面复核|待复核)\s*(\d+)\s*只", text)
    reported = int(match.group(1)) if match else 0
    total = max(total, reported)
    if total <= shown:
        return text
    return re.sub(
        r"(纸面复核|待复核)\s*\d+\s*只",
        rf"\1 {total} 只，首页展示 {shown} 只",
        text,
        count=1,
    )


def _snapshot_debates(
    payload: Any,
    candidates: tuple[HomeSnapshotCandidate, ...],
    *,
    runtime_debates: tuple[Any, ...] = (),
) -> tuple[HomeSnapshotDebate, ...]:
    candidate_symbols = {candidate.symbol for candidate in candidates}
    debates = tuple(getattr(payload, "debates", ()) or ()) + runtime_debates
    selected: list[HomeSnapshotDebate] = []
    selected_symbols: set[str] = set()
    for debate in debates:
        if not _debate_is_complete(debate):
            continue
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
                round_summaries=tuple(
                    _first_text(getattr(round_data, "summary", ""))
                    for round_data in (getattr(debate, "rounds", ()) or ())
                    if _first_text(getattr(round_data, "summary", ""))
                )[:5]
                or tuple(getattr(debate, "round_summaries", ()) or ())[:5],
                viewpoint_buckets={
                    str(bucket): tuple(points)[:4]
                    for bucket, points in (
                        getattr(debate, "viewpoint_buckets", {}) or {}
                    ).items()
                },
                disagreement_points=tuple(
                    getattr(debate, "disagreement_points", ()) or ()
                )[:4],
                uncertainty_points=tuple(
                    getattr(debate, "uncertainty_points", ()) or ()
                )[:4],
            )
        )
        selected_symbols.add(symbol)
        if len(selected) == MAX_HOME_SNAPSHOT_DEBATES:
            break
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
        if len(roles) < 2:
            continue
        agent_views = tuple(
            SimpleNamespace(role_id=role, role_label=role) for role in roles
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
    if len(roles) < 2:
        return False
    try:
        vote_counts = tuple(
            int(getattr(debate, field, 0) or 0)
            for field in ("bull_count", "bear_count", "neutral_count")
        )
    except (TypeError, ValueError):
        return False
    return all(count >= 0 for count in vote_counts) and sum(vote_counts) == len(roles)


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
    for event in report.events:
        published_at = _normalize_timestamp(event.published_at)
        source = _text(event.source)
        if not published_at or not source:
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
) -> HomeSnapshotRecommendationGate:
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
    *,
    premarket_messages: tuple[HomeSnapshotMessage, ...] = (),
) -> tuple[HomeSnapshotPhase, ...]:
    """Read phase artifacts and keep message-only premarket work visible."""
    phase_specs = (
        ("main_chain", "盘前", "盘前主链"),
        ("intraday", "盘中", "盘中观察"),
        ("closing_review", "盘后", "收盘复盘"),
    )
    phases: list[HomeSnapshotPhase] = []
    seen_symbols: set[str] = set()
    for task_id, label, _task_label in phase_specs:
        try:
            if task_id == "closing_review":
                rows = provider._same_day_unique_rows(signal_date)
                review = provider._lightweight_closing_review_summary(
                    signal_date,
                    include_report_insights=False,
                )
                status = {
                    "已复盘": "已产出",
                    "已验证未归档": "已产出",
                    "待复盘": "待复盘",
                }.get(str(review.status_label), "未产出")
                review_updated_at = str(review.created_at or "")
            else:
                rows = provider._signal_task_rows_for_date(task_id, signal_date)
                status = "已产出" if rows else "未产出"
                review_updated_at = ""
        except Exception:
            rows = []
            status = "未产出"
            review_updated_at = ""
        symbols = {
            str(row.get("symbol", "") or "").strip()
            for row in rows
            if str(row.get("symbol", "") or "").strip()
        }
        overlap = len(symbols & seen_symbols)
        seen_symbols.update(symbols)
        updated_at = str(
            review_updated_at
            or max((row.get("created_at", "") for row in rows), default="")
            or ""
        )
        if task_id == "main_chain" and not rows and premarket_messages:
            # News is a real premarket artifact, but it must not be counted as
            # a main-chain candidate signal.
            status = "消息已更新"
            updated_at = str(
                max((message.published_at for message in premarket_messages), default="")
                or ""
            )
        phases.append(
            HomeSnapshotPhase(
                task_id=task_id,
                label=label,
                status=status,
                candidate_count=len(rows),
                unique_symbols=len(symbols),
                overlap_symbols=overlap,
                updated_at=updated_at,
            )
        )
    return tuple(phases)


def _universe_snapshot() -> HomeSnapshotUniverse:
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


def _variant_snapshot(
    candidate_names: dict[str, str] | None = None,
) -> tuple[HomeSnapshotVariant, ...]:
    """Read only bounded summaries from the isolated experiment artifact."""
    path = _runtime_json_path(
        "AQSP_VARIANT_RESULTS",
        "data/runtime/variant_results.json",
    )
    payload = _read_json_object(path)
    if not payload or payload.get("initial_cash") != 100_000.0:
        return ()
    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, list):
        return ()
    rules = payload.get("execution_rules")
    variant_symbols = {
        _text(holding.get("symbol"))
        for item in raw_variants[:MAX_HOME_SNAPSHOT_VARIANTS]
        if isinstance(item, dict)
        for holding in item.get("holdings", ())
        if isinstance(holding, dict) and _text(holding.get("symbol"))
    }
    variant_symbols.update(
        _text(fill.get("symbol"))
        for item in raw_variants[:MAX_HOME_SNAPSHOT_VARIANTS]
        if isinstance(item, dict)
        for fill in item.get("fills", ())
        if isinstance(fill, dict) and _text(fill.get("symbol"))
    )
    variant_symbols.update(
        _text(holding.get("symbol"))
        for item in raw_variants[:MAX_HOME_SNAPSHOT_VARIANTS]
        if isinstance(item, dict)
        for holding in item.get("previous_holdings", ())
        if isinstance(holding, dict) and _text(holding.get("symbol"))
    )
    variant_names = {
        **(candidate_names or {}),
        **load_sqlite_symbol_name_map(sorted(variant_symbols)),
    }
    rule_labels = (
        "T+1：买入当日不可卖",
        "100 股整手",
        "停牌/涨停买入/跌停卖出拒绝",
        "含佣金、印花税、滑点",
    )
    variants: list[HomeSnapshotVariant] = []
    for item in raw_variants[:MAX_HOME_SNAPSHOT_VARIANTS]:
        if not isinstance(item, dict) or item.get("initial_cash") != 100_000.0:
            continue
        holdings = tuple(
            parsed
            for holding in item.get("holdings", ())
            if (parsed := _variant_holding_from_payload(holding, variant_names))
            is not None
        )
        raw_previous_holdings = item.get("previous_holdings")
        previous_holdings = (
            tuple(
                parsed
                for holding in raw_previous_holdings
                if (parsed := _variant_holding_from_payload(holding, variant_names))
                is not None
            )
            if isinstance(raw_previous_holdings, list)
            else _previous_variant_holdings(item.get("fills"), holdings, variant_names)
        )
        raw_strategy = item.get("strategy")
        strategy = (
            json.dumps(raw_strategy, ensure_ascii=False, separators=(",", ":"))
            if isinstance(raw_strategy, dict)
            else _text(item.get("strategy_label")) or _text(item.get("label"))
        )
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
                strategy=strategy,
                holdings=holdings,
                previous_holdings=previous_holdings,
                recent_actions=_variant_recent_actions(
                    item.get("fills"), variant_names
                ),
                hard_rules=rule_labels if isinstance(rules, dict) else (),
            )
        )
    return tuple(variants)


def _variant_holding_from_payload(
    raw_holding: object,
    symbol_names: dict[str, str],
) -> HomeSnapshotHolding | None:
    """Normalize a current or carried-forward holding without inventing symbols."""
    if not isinstance(raw_holding, dict):
        return None
    symbol = _text(raw_holding.get("symbol"))
    if not symbol:
        return None
    return HomeSnapshotHolding(
        symbol=symbol,
        quantity=int(raw_holding.get("quantity") or 0),
        average_price=float(raw_holding.get("average_price") or 0.0),
        last_price=float(raw_holding.get("last_price") or 0.0),
        market_value=float(raw_holding.get("market_value") or 0.0),
        unrealized_pnl=float(raw_holding.get("unrealized_pnl") or 0.0),
        name=_first_text(
            raw_holding.get("name"),
            raw_holding.get("display_name"),
            symbol_names.get(symbol),
        ),
    )


def _variant_provenance() -> dict[str, object]:
    """Read verified raw-data provenance for the isolated variant artifact."""
    payload = _read_json_object(
        _runtime_json_path("AQSP_VARIANT_RESULTS", "data/runtime/variant_results.json")
    )
    coverage = payload.get("data_coverage")
    raw_coverage = (
        coverage.get("end_date_coverage_pct") if isinstance(coverage, dict) else None
    )
    try:
        coverage_pct = float(raw_coverage) if raw_coverage is not None else None
    except (TypeError, ValueError):
        coverage_pct = None
    return {
        "symbol_count": int(
            (payload.get("universe_scope") or {}).get("symbol_count", 0)
            if isinstance(payload.get("universe_scope"), dict)
            else 0
        ),
        "board_scope": _text(
            (payload.get("universe_scope") or {}).get("board_scope", "")
            if isinstance(payload.get("universe_scope"), dict)
            else ""
        ),
        "excluded": tuple(
            _text(value)
            for value in (
                (payload.get("universe_scope") or {}).get("excluded", ())
                if isinstance(payload.get("universe_scope"), dict)
                else ()
            )
            if _text(value)
        ),
        "latest_trade_date": _text(payload.get("data_latest_trade_date")),
        "sources": tuple(
            _text(value) for value in payload.get("data_sources", ()) if _text(value)
        ),
        "coverage_pct": coverage_pct,
    }


def _variant_universe_snapshot() -> HomeSnapshotVariantUniverse:
    provenance = _variant_provenance()
    return HomeSnapshotVariantUniverse(
        symbol_count=int(provenance.get("symbol_count", 0) or 0),
        board_scope=_text(provenance.get("board_scope")),
        excluded=tuple(provenance.get("excluded", ())),
        latest_trade_date=_text(provenance.get("latest_trade_date")),
        coverage_pct=float(provenance.get("coverage_pct") or 0.0),
        sources=tuple(provenance.get("sources", ())),
    )


def _previous_variant_holdings(
    raw_fills: object,
    current_holdings: tuple[HomeSnapshotHolding, ...],
    symbol_names: dict[str, str] | None = None,
) -> tuple[HomeSnapshotHolding, ...] | None:
    """Replay filled trades before the last filled date for a comparable view."""
    fills = [
        fill
        for fill in (raw_fills if isinstance(raw_fills, list) else [])
        if isinstance(fill, dict)
        and _text(fill.get("status")) == "filled"
        and _text(fill.get("date"))
        and _text(fill.get("symbol"))
    ]
    if not fills:
        return None
    last_date = max(_text(fill.get("date"))[:10] for fill in fills)
    if not any(_text(fill.get("date"))[:10] < last_date for fill in fills):
        return None
    positions: dict[str, tuple[int, float]] = {}
    for fill in fills:
        if _text(fill.get("date"))[:10] >= last_date:
            continue
        symbol = _text(fill.get("symbol"))
        quantity = max(0, int(fill.get("quantity") or 0))
        price = max(0.0, float(fill.get("price") or 0.0))
        if not symbol or quantity <= 0:
            continue
        current_quantity, average_price = positions.get(symbol, (0, 0.0))
        if _text(fill.get("side")) == "buy":
            total_quantity = current_quantity + quantity
            average_price = (
                (current_quantity * average_price + quantity * price) / total_quantity
                if total_quantity
                else 0.0
            )
            positions[symbol] = (total_quantity, average_price)
        elif _text(fill.get("side")) == "sell":
            remaining = max(0, current_quantity - quantity)
            if remaining:
                positions[symbol] = (remaining, average_price)
            else:
                positions.pop(symbol, None)

    current_by_symbol = {holding.symbol: holding for holding in current_holdings}
    previous: list[HomeSnapshotHolding] = []
    for symbol, (quantity, average_price) in positions.items():
        current = current_by_symbol.get(symbol)
        last_price = current.last_price if current else 0.0
        previous.append(
            HomeSnapshotHolding(
                symbol=symbol,
                quantity=quantity,
                average_price=average_price,
                last_price=last_price,
                market_value=quantity * last_price,
                unrealized_pnl=quantity * (last_price - average_price),
                name=(current.name if current else "")
                or (symbol_names or {}).get(symbol, ""),
            )
        )
    return tuple(previous)


def _variant_recent_actions(
    raw_fills: object,
    symbol_names: dict[str, str],
) -> tuple[str, ...]:
    """Summarize the last filled trading date as actual adjustment evidence."""
    fills = [
        fill
        for fill in (raw_fills if isinstance(raw_fills, list) else [])
        if isinstance(fill, dict)
        and _text(fill.get("status")) == "filled"
        and _text(fill.get("date"))
        and _text(fill.get("symbol"))
        and _text(fill.get("side")) in {"buy", "sell"}
    ]
    if not fills:
        return ()
    last_date = max(_text(fill.get("date"))[:10] for fill in fills)
    totals: dict[tuple[str, str], int] = {}
    for fill in fills:
        if _text(fill.get("date"))[:10] != last_date:
            continue
        key = (_text(fill.get("side")), _text(fill.get("symbol")))
        totals[key] = totals.get(key, 0) + max(0, int(fill.get("quantity") or 0))
    actions: list[str] = []
    for (side, symbol), quantity in totals.items():
        if quantity <= 0:
            continue
        action = "买入" if side == "buy" else "卖出"
        name = symbol_names.get(symbol, "名称未记录")
        evidence_values: list[str] = []
        for fill in fills:
            if (
                _text(fill.get("date"))[:10] == last_date
                and _text(fill.get("side")) == side
                and _text(fill.get("symbol")) == symbol
            ):
                raw_evidence = fill.get("evidence")
                if isinstance(raw_evidence, list):
                    evidence_values.extend(_text(value) for value in raw_evidence)
        evidence = _bounded_unique_text(evidence_values, 5)
        action_text = f"{last_date} {action} {name} {symbol} {quantity} 股"
        if evidence:
            action_text += "；技术证据：" + "，".join(evidence)
        actions.append(action_text)
        if len(actions) == 4:
            break
    return tuple(actions)


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
    source = _snapshot_source(runtime, task_view)
    primary_source = source
    variant_provenance = _variant_provenance()
    variant_date = _text(variant_provenance.get("latest_trade_date"))
    if source.latest_trade_date == "未记录" and variant_date:
        try:
            variant_lag_days = max(
                0, (now_shanghai().date() - date.fromisoformat(variant_date)).days
            )
        except ValueError:
            variant_lag_days = 0
        coverage_pct = variant_provenance.get("coverage_pct")
        source = HomeSnapshotSource(
            effective="、".join(variant_provenance.get("sources", ()))
            or "原始行情缓存",
            latest_trade_date=variant_date,
            lag_days=variant_lag_days,
            status=(
                "纸面变体原始数据已核验"
                if coverage_pct == 100.0
                else "纸面变体原始数据覆盖不完整"
            ),
        )
    candidates = _snapshot_candidates(payload)
    recommendation_count = _snapshot_recommendation_count(payload)
    runtime_debates = _runtime_debates_for_snapshot(
        selected_date,
        {candidate.symbol for candidate in candidates},
    )
    debates = _snapshot_debates(
        payload,
        candidates,
        runtime_debates=runtime_debates,
    )
    shown_recommendation_count = sum(
        is_home_recommendation(candidate) for candidate in candidates
    )
    candidate_symbols = {candidate.symbol for candidate in candidates}
    debate_symbols = {debate.symbol for debate in debates}
    debate_gap_summary = ""
    if debates and candidate_symbols - debate_symbols:
        debate_gap_summary = (
            f"讨论复核 {len(debate_symbols)}/{len(candidate_symbols)} 只；"
            f"{len(candidate_symbols - debate_symbols)} 只未通过质量门，已隐藏"
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
    messages = _append_cross_market_messages(messages, artifact)
    recommendation_gate = _recommendation_gate(
        provider,
        runtime,
        primary_source,
        message_status,
        evaluated_at=now_shanghai(),
    )
    candidates = _apply_recommendation_gate(candidates, recommendation_gate)
    if not recommendation_gate.recommendation_allowed:
        recommendation_count = 0
        shown_recommendation_count = 0
    phases = _phase_snapshot(
        provider,
        selected_date,
        premarket_messages=tuple(messages),
    )
    live_phase_produced = any(phase.candidate_count > 0 for phase in phases)
    debate_missing = bool(getattr(payload, "debates", ()) or ()) and not debates
    raw_summaries = (
        "委员会结论缺少当前候选映射，已隐藏" if debate_missing else "",
        debate_gap_summary,
        getattr(runtime, "conclusion", ""),
        getattr(overview, "focus_headline", ""),
        getattr(overview, "blocker_headline", ""),
        getattr(overview, "top_headline", ""),
        getattr(task_view, "headline", ""),
    )
    if not candidates:
        if messages and not live_phase_produced:
            empty_day_summary = "今日消息已更新；实时行情任务尚未产出，未使用历史候选"
        elif messages:
            empty_day_summary = "今日消息已更新；实时行情筛选暂无候选，未使用历史结果"
        else:
            empty_day_summary = "当前日期没有候选，未使用其他日期或旧运行计数填充"
        raw_summaries = (
            empty_day_summary,
            *tuple(
                summary
                for summary in raw_summaries
                if not re.search(
                    r"(?:\d+\s*个?\s*候选|候选\s*\d+|纸面复核\s*\d+|待复核\s*\d+)",
                    str(summary),
                )
            ),
        )
    aligned_summaries = tuple(
        _align_count_summary(
            str(summary),
            total=recommendation_count,
            shown=shown_recommendation_count,
        )
        for summary in raw_summaries
    )
    count_summaries = tuple(
        summary
        for summary in aligned_summaries
        if re.search(r"(?:纸面复核|待复核)\s*\d+\s*只", summary)
    )
    summaries = _bounded_unique_text(
        (*count_summaries, debate_gap_summary, *aligned_summaries),
        MAX_HOME_SUMMARIES,
    )

    return HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at=generated_at,
        selected_date=selected_date,
        available_dates=_bounded_unique_text(
            (
                selected_date,
                *_snapshot_dates(task_view, selected_date),
            ),
            MAX_HOME_DATES,
        ),
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
        universe=_universe_snapshot(),
        variant_universe=_variant_universe_snapshot(),
        variants=_variant_snapshot(
            {candidate.symbol: candidate.display_name for candidate in candidates}
        ),
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

    existing_by_date = {day.date: day for day in existing.days}
    refreshed_by_date = {day.date: day for day in refreshed.days}
    dates = {day.date for day in existing.days} | set(refreshed_by_date)
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
    existing_index = load_home_snapshot_index(index_path)
    index = build_home_snapshot_index(
        provider,
        signal_date=args.date.strip(),
        task_id=args.task_id.strip(),
        initial_snapshot=snapshot,
    )
    index = merge_home_snapshot_index(existing_index, index)
    current_snapshot = next(
        day.snapshot for day in index.days if day.date == index.selected_date
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
