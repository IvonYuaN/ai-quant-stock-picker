from __future__ import annotations

import json
import multiprocessing
import os
import signal
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pandas as pd

from aqsp.data.news_source import (
    NewsSource,
    NewsSourceHealth,
    NewsSourceStatus,
    build_default_news_source,
)
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.notification_style import compact_notification_markdown
from aqsp.presentation import normalize_research_tone
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text
from aqsp.news.entity_graph import match_news_entities

Impact = Literal["positive", "negative", "neutral"]
CatalystResultStatus = Literal[
    "high_impact",
    "no_high_impact",
    "stale_only",
    "no_valid_news",
    "source_failed",
    "stale_cache",
]
_DEFAULT_MAX_STALE_CACHE_AGE_SECONDS = 30 * 60
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class NewsCatalystConfig:
    symbols: tuple[str, ...] = ()
    max_symbol_news: int = 5
    max_global_news: int = 20
    max_events: int = 8
    min_confidence: float = 0.45
    max_news_age_days: int = 30
    allow_undated_news: bool = False
    enable_llm_review: bool = False
    source_timeout_seconds: float = 8.0
    llm_timeout_seconds: float = 8.0
    max_llm_review_events: int = 3
    cache_path: str = ""
    cache_ttl_seconds: float = 0.0
    allow_stale_cache_on_failure: bool = False
    max_stale_cache_age_seconds: float = _DEFAULT_MAX_STALE_CACHE_AGE_SECONDS
    isolate_external_sources: bool = False


@dataclass(frozen=True)
class CatalystEvent:
    title: str
    source: str
    published_at: str
    source_fetched_at: str = ""
    symbol: str = ""
    name: str = ""
    impact: Impact = "neutral"
    category: str = "消息"
    weight: int = 1
    confidence: float = 0.0
    source_count: int = 1
    verification: str = "待证实"
    source_quality_label: str = "普通来源"
    source_quality_score: int = 1
    inference: str = ""
    llm_review: str = ""
    url: str = ""
    source_region: str = "mixed"
    affected_sectors: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    transmission_hypothesis: str = ""
    time_horizon: str = ""
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    transmission_path: tuple[str, ...] = ()
    validation_signals: tuple[str, ...] = ()
    invalidation_signals: tuple[str, ...] = ()
    summary: str = ""

    @property
    def deterministic_score(self) -> int:
        """The rule-derived score; LLM review is deliberately not part of it."""

        return int(self.weight)


NewsRegion = Literal["domestic", "international"]
NewsRegionStatusValue = Literal[
    "ok", "partial", "empty", "timeout", "failed", "unavailable"
]


@dataclass(frozen=True)
class NewsRegionStatus:
    """Aggregated source health for one domestic/international region."""

    region: NewsRegion
    status: NewsRegionStatusValue
    source_count: int = 0
    successful_sources: int = 0
    row_count: int = 0


@dataclass(frozen=True)
class CatalystReport:
    date: str
    generated_at: str
    events: tuple[CatalystEvent, ...]
    source_status: str
    warnings: tuple[str, ...] = ()
    source_statuses: tuple[NewsSourceHealth, ...] = ()
    event_status: str = ""
    raw_news_count: int = 0
    stale_news_count: int = 0
    undated_news_count: int = 0
    future_news_count: int = 0
    # Added as a derived field so callers constructing legacy reports remain valid.
    region_statuses: tuple[NewsRegionStatus, ...] = ()

    def __post_init__(self) -> None:
        if not self.region_statuses:
            object.__setattr__(
                self,
                "region_statuses",
                _region_statuses_from_sources(self.source_statuses),
            )

    @property
    def news_status(self) -> CatalystResultStatus:
        """Result status is separate from source availability status."""

        value = str(self.event_status or "").strip()
        if value:
            return value  # type: ignore[return-value]
        if self.events:
            return "high_impact"
        if self.source_status in {"failed", "timeout"}:
            return "source_failed"
        if self.stale_news_count > 0 or self.future_news_count > 0:
            return "stale_only"
        if self.source_status == "ok":
            return "no_high_impact"
        return "no_valid_news"

    @property
    def has_high_impact_events(self) -> bool:
        return bool(self.events) and self.news_status == "high_impact"


@dataclass
class _SourceStats:
    attempted: int = 0
    successful: int = 0
    failed: int = 0
    raw_rows: int = 0
    health: list[NewsSourceHealth] = field(default_factory=list)
    _health_keys: set[tuple[str, str, str, str, tuple[str, ...]]] = field(
        default_factory=set
    )

    def record_frame(
        self,
        df: pd.DataFrame,
        warnings: Sequence[str],
        *,
        name: str,
        region: str,
    ) -> None:
        self.attempted += 1
        row_count = len(_iter_news_rows(df))
        metadata = _source_health_from_frame(df)
        if metadata:
            for item in metadata:
                key = (
                    item.name,
                    item.region,
                    item.status,
                    item.fetched_at,
                    item.warnings,
                )
                if key not in self._health_keys:
                    self._health_keys.add(key)
                    self.health.append(item)
        else:
            item = NewsSourceHealth(
                name=name,
                region=_normalize_region(region),
                status=_status_from_frame(row_count, warnings),
                successful=1 if row_count > 0 else 0,
                row_count=row_count,
                fetched_at=now_shanghai().isoformat(timespec="seconds"),
                warnings=tuple(warnings),
            )
            key = (item.name, item.region, item.status, item.fetched_at, item.warnings)
            if key not in self._health_keys:
                self._health_keys.add(key)
                self.health.append(item)
        if row_count <= 0:
            if _status_from_frame(row_count, warnings) in {"timeout", "failed"}:
                self.failed += 1
            return
        self.successful += 1
        self.raw_rows += row_count

    def record_failure(self, exc: BaseException, *, name: str, region: str) -> None:
        self.attempted += 1
        self.failed += 1
        status = _status_from_exception(exc)
        item = NewsSourceHealth(
            name=name,
            region=_normalize_region(region),
            status=status,
            fetched_at=now_shanghai().isoformat(timespec="seconds"),
            warnings=(str(exc),),
        )
        key = (item.name, item.region, item.status, item.fetched_at, item.warnings)
        if key not in self._health_keys:
            self._health_keys.add(key)
            self.health.append(item)


@dataclass(frozen=True)
class _NewsFetchOutcome:
    frame: Any = field(default_factory=pd.DataFrame)
    error: BaseException | None = None
    timed_out: bool = False


Fetcher = Callable[[str, int], pd.DataFrame]
_AKSHARE_NEWS: NewsSource | None = None
_CATALYST_CACHE_VERSION = 1


@dataclass(frozen=True)
class _CachedCatalystReport:
    report: CatalystReport
    cached_at: datetime
    age_seconds: float


def _get_akshare_news_source() -> NewsSource:
    global _AKSHARE_NEWS
    if _AKSHARE_NEWS is None:
        _AKSHARE_NEWS = build_default_news_source()
    return _AKSHARE_NEWS


def _resolve_catalyst_cache_path(path: str) -> Path | None:
    configured = str(path or "").strip()
    if not configured:
        return None
    raw = Path(configured).expanduser()
    if raw.is_absolute():
        return raw.resolve(strict=False)
    root = Path(os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
    return (root / raw).resolve(strict=False)


def _catalyst_cache_key(cfg: NewsCatalystConfig) -> str:
    normalized_symbols = tuple(
        sorted(str(symbol).strip() for symbol in cfg.symbols if str(symbol).strip())
    )
    payload = {
        "symbols": normalized_symbols,
        "max_symbol_news": int(cfg.max_symbol_news),
        "max_global_news": int(cfg.max_global_news),
        "max_events": int(cfg.max_events),
        "min_confidence": float(cfg.min_confidence),
        "max_news_age_days": int(cfg.max_news_age_days),
        "allow_undated_news": bool(cfg.allow_undated_news),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return tuple(result)
    return ()


def _serialize_catalyst_report(report: CatalystReport) -> dict[str, Any]:
    return {
        "date": report.date,
        "generated_at": report.generated_at,
        "source_status": report.source_status,
        "event_status": report.news_status,
        "raw_news_count": report.raw_news_count,
        "stale_news_count": report.stale_news_count,
        "undated_news_count": report.undated_news_count,
        "future_news_count": report.future_news_count,
        "region_statuses": [
            {
                "region": item.region,
                "status": item.status,
                "source_count": item.source_count,
                "successful_sources": item.successful_sources,
                "row_count": item.row_count,
            }
            for item in report.region_statuses
        ],
        "warnings": list(report.warnings),
        "events": [
            {
                "title": event.title,
                "summary": event.summary,
                "source": event.source,
                "published_at": event.published_at,
                "source_fetched_at": event.source_fetched_at,
                "symbol": event.symbol,
                "name": event.name,
                "impact": event.impact,
                "category": event.category,
                "weight": event.weight,
                "confidence": event.confidence,
                "source_count": event.source_count,
                "verification": event.verification,
                "source_quality_label": event.source_quality_label,
                "source_quality_score": event.source_quality_score,
                "inference": event.inference,
                "llm_review": event.llm_review,
                "url": event.url,
                "source_region": event.source_region,
                "affected_sectors": list(event.affected_sectors),
                "affected_symbols": list(event.affected_symbols),
                "transmission_hypothesis": event.transmission_hypothesis,
                "time_horizon": event.time_horizon,
                "supporting_evidence": list(event.supporting_evidence),
                "contradicting_evidence": list(event.contradicting_evidence),
                "transmission_path": list(event.transmission_path),
                "validation_signals": list(event.validation_signals),
                "invalidation_signals": list(event.invalidation_signals),
            }
            for event in report.events
        ],
        "source_statuses": [
            {
                "name": item.name,
                "region": item.region,
                "status": item.status,
                "attempted": item.attempted,
                "successful": item.successful,
                "row_count": item.row_count,
                "fetched_at": item.fetched_at,
                "warnings": list(item.warnings),
            }
            for item in report.source_statuses
        ],
    }


def _deserialize_catalyst_report(payload: Any) -> CatalystReport | None:
    if not isinstance(payload, dict):
        return None
    try:
        events = tuple(
            CatalystEvent(
                title=str(item.get("title", "")).strip(),
                summary=str(item.get("summary", "")).strip(),
                source=str(item.get("source", "")).strip(),
                published_at=str(item.get("published_at", "")).strip(),
                source_fetched_at=str(item.get("source_fetched_at", "")).strip(),
                symbol=str(item.get("symbol", "")).strip(),
                name=str(item.get("name", "")).strip(),
                impact=str(item.get("impact", "neutral")).strip() or "neutral",
                category=str(item.get("category", "消息")).strip() or "消息",
                weight=int(item.get("weight", 1) or 1),
                confidence=float(item.get("confidence", 0.0) or 0.0),
                source_count=int(item.get("source_count", 1) or 1),
                verification=str(item.get("verification", "待证实")).strip()
                or "待证实",
                source_quality_label=str(
                    item.get("source_quality_label", "普通来源")
                ).strip()
                or "普通来源",
                source_quality_score=int(item.get("source_quality_score", 1) or 1),
                inference=str(item.get("inference", "")).strip(),
                llm_review=str(item.get("llm_review", "")).strip(),
                url=str(item.get("url", "")).strip(),
                source_region=str(item.get("source_region", "mixed")).strip()
                or "mixed",
                affected_sectors=_text_tuple(item.get("affected_sectors", ())),
                affected_symbols=_text_tuple(item.get("affected_symbols", ())),
                transmission_hypothesis=str(
                    item.get("transmission_hypothesis", "")
                ).strip(),
                time_horizon=str(item.get("time_horizon", "")).strip(),
                supporting_evidence=_text_tuple(item.get("supporting_evidence", ())),
                contradicting_evidence=_text_tuple(
                    item.get("contradicting_evidence", ())
                ),
                transmission_path=_text_tuple(item.get("transmission_path", ())),
                validation_signals=_text_tuple(item.get("validation_signals", ())),
                invalidation_signals=_text_tuple(
                    item.get("invalidation_signals", ())
                ),
            )
            for item in tuple(payload.get("events", ()) or ())
            if isinstance(item, dict)
        )
        source_statuses = tuple(
            NewsSourceHealth(
                name=str(item.get("name", "")).strip(),
                region=_normalize_region(str(item.get("region", "mixed"))),
                status=_normalize_source_status(str(item.get("status", "failed"))),
                attempted=int(item.get("attempted", 1) or 1),
                successful=int(item.get("successful", 0) or 0),
                row_count=int(item.get("row_count", 0) or 0),
                fetched_at=str(item.get("fetched_at", "")).strip(),
                warnings=tuple(
                    str(warning).strip()
                    for warning in tuple(item.get("warnings", ()) or ())
                    if str(warning).strip()
                ),
            )
            for item in tuple(payload.get("source_statuses", ()) or ())
            if isinstance(item, dict)
        )
        region_statuses = tuple(
            NewsRegionStatus(
                region=str(item.get("region", "")).strip(),
                status=str(item.get("status", "unavailable")).strip() or "unavailable",
                source_count=int(item.get("source_count", 0) or 0),
                successful_sources=int(item.get("successful_sources", 0) or 0),
                row_count=int(item.get("row_count", 0) or 0),
            )
            for item in tuple(payload.get("region_statuses", ()) or ())
            if isinstance(item, dict)
            and str(item.get("region", "")).strip() in {"domestic", "international"}
            and str(item.get("status", "unavailable")).strip()
            in {"ok", "partial", "empty", "timeout", "failed", "unavailable"}
        )
        return CatalystReport(
            date=str(payload.get("date", "")).strip(),
            generated_at=str(payload.get("generated_at", "")).strip(),
            events=events,
            source_status=str(payload.get("source_status", "")).strip(),
            warnings=tuple(
                str(item).strip()
                for item in tuple(payload.get("warnings", ()) or ())
                if str(item).strip()
            ),
            source_statuses=source_statuses,
            event_status=str(payload.get("event_status", "")).strip(),
            raw_news_count=int(payload.get("raw_news_count", 0) or 0),
            stale_news_count=int(payload.get("stale_news_count", 0) or 0),
            undated_news_count=int(payload.get("undated_news_count", 0) or 0),
            future_news_count=int(payload.get("future_news_count", 0) or 0),
            region_statuses=region_statuses,
        )
    except (TypeError, ValueError):
        return None


def serialize_catalyst_report(report: CatalystReport) -> dict[str, Any]:
    """Return the structured report used by runtime and dashboard consumers."""
    return _serialize_catalyst_report(report)


def deserialize_catalyst_report(payload: Any) -> CatalystReport | None:
    """Restore a structured report without accepting untyped runtime state."""
    return _deserialize_catalyst_report(payload)


def load_catalyst_report_artifact(
    path: str | Path,
    *,
    expected_date: str = "",
    max_age_seconds: float = 0.0,
) -> CatalystReport | None:
    """Load a recent, date-matched report written by the news radar."""
    resolved = _resolve_catalyst_cache_path(str(path))
    if resolved is None or not resolved.exists():
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    report = _deserialize_catalyst_report(payload)
    if report is None:
        return None
    if expected_date and report.date != expected_date:
        return None
    try:
        generated_at = datetime.fromisoformat(report.generated_at)
    except ValueError:
        return None
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=_SHANGHAI_TZ)
    else:
        generated_at = generated_at.astimezone(_SHANGHAI_TZ)
    age_seconds = (now_shanghai() - generated_at).total_seconds()
    if age_seconds < 0:
        return None
    if max_age_seconds > 0 and age_seconds > float(max_age_seconds):
        return None
    return report


def _read_catalyst_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _CATALYST_CACHE_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _CATALYST_CACHE_VERSION, "entries": {}}
    if not isinstance(payload, dict):
        return {"version": _CATALYST_CACHE_VERSION, "entries": {}}
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    return {
        "version": int(
            payload.get("version", _CATALYST_CACHE_VERSION) or _CATALYST_CACHE_VERSION
        ),
        "entries": entries,
    }


def _load_cached_catalyst_report(
    cfg: NewsCatalystConfig,
) -> _CachedCatalystReport | None:
    cache_path = _resolve_catalyst_cache_path(cfg.cache_path)
    if cache_path is None:
        return None
    cache_key = _catalyst_cache_key(cfg)
    with advisory_lock(cache_path):
        payload = _read_catalyst_cache(cache_path)
    entry = payload.get("entries", {}).get(cache_key)
    if not isinstance(entry, dict):
        return None
    report = _deserialize_catalyst_report(entry.get("report"))
    if report is None:
        return None
    cached_at_raw = str(entry.get("cached_at", "")).strip()
    try:
        cached_at = datetime.fromisoformat(cached_at_raw)
    except ValueError:
        return None
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=_SHANGHAI_TZ)
    else:
        cached_at = cached_at.astimezone(_SHANGHAI_TZ)
    age_seconds = (now_shanghai() - cached_at).total_seconds()
    if age_seconds < 0:
        return None
    return _CachedCatalystReport(
        report=report,
        cached_at=cached_at,
        age_seconds=age_seconds,
    )


def _store_cached_catalyst_report(
    cfg: NewsCatalystConfig,
    report: CatalystReport,
) -> None:
    cache_path = _resolve_catalyst_cache_path(cfg.cache_path)
    if cache_path is None:
        return
    cache_key = _catalyst_cache_key(cfg)
    with advisory_lock(cache_path):
        payload = _read_catalyst_cache(cache_path)
        entries = dict(payload.get("entries", {}) or {})
        entries[cache_key] = {
            "cached_at": now_shanghai().isoformat(timespec="seconds"),
            "report": _serialize_catalyst_report(report),
        }
        atomic_write_text(
            cache_path,
            json.dumps(
                {
                    "version": _CATALYST_CACHE_VERSION,
                    "entries": entries,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


def _cache_warning_line(age_seconds: float) -> str:
    if age_seconds < 60:
        return f"消息缓存回退: 使用 {int(age_seconds)} 秒前摘要"
    age_minutes = age_seconds / 60
    if age_minutes < 60:
        return f"消息缓存回退: 使用 {int(age_minutes)} 分钟前摘要"
    age_hours = age_minutes / 60
    return f"消息缓存回退: 使用 {age_hours:.1f} 小时前摘要"


def _stale_cache_limit_seconds(cfg: NewsCatalystConfig) -> float:
    return max(0.0, float(cfg.max_stale_cache_age_seconds))


def _stale_cache_is_allowed(
    cfg: NewsCatalystConfig,
    cached: _CachedCatalystReport,
) -> bool:
    limit_seconds = _stale_cache_limit_seconds(cfg)
    # A short outage may use a bounded same-day snapshot, but never carry
    # yesterday's news across midnight into a live decision.
    return (
        limit_seconds > 0
        and cached.age_seconds <= limit_seconds
        and cached.report.date == today_shanghai().isoformat()
    )


def _stale_cache_rejected_warning(
    cfg: NewsCatalystConfig,
    cached: _CachedCatalystReport,
) -> str:
    if cached.report.date != today_shanghai().isoformat():
        return (
            "消息缓存过期: 已拒绝跨自然日缓存"
            f"（缓存日期 {cached.report.date or 'unknown'}）"
        )
    limit_minutes = _stale_cache_limit_seconds(cfg) / 60
    age_minutes = cached.age_seconds / 60
    if age_minutes >= 60:
        age_text = f"{age_minutes / 60:.1f} 小时"
    else:
        age_text = f"{int(age_minutes)} 分钟"
    limit_text = (
        f"{int(limit_minutes)} 分钟"
        if limit_minutes < 60
        else f"{limit_minutes / 60:.1f} 小时"
    )
    return f"消息缓存过期: 已拒绝使用 {age_text}前摘要（上限 {limit_text}）"


def _report_from_stale_cache(
    cached: _CachedCatalystReport,
    *,
    fetch_warnings: Sequence[str],
) -> CatalystReport:
    warning_line = _cache_warning_line(cached.age_seconds)
    warnings = tuple(
        _dedupe_texts(
            (
                warning_line,
                *tuple(fetch_warnings),
                *tuple(cached.report.warnings),
            )
        )
    )[:5]
    status = cached.report.source_status
    if status == "ok":
        status = "partial"
    generated_at = now_shanghai()
    return CatalystReport(
        date=generated_at.date().isoformat(),
        generated_at=generated_at.isoformat(timespec="seconds"),
        events=cached.report.events,
        source_status=status,
        warnings=warnings,
        source_statuses=cached.report.source_statuses,
        event_status="stale_cache",
        raw_news_count=cached.report.raw_news_count,
        stale_news_count=cached.report.stale_news_count,
        undated_news_count=cached.report.undated_news_count,
        future_news_count=cached.report.future_news_count,
    )


POSITIVE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    (
        "GPT|AI safety|agentic AI|AI investments|AI innovation|"
        "foundry|EPYC|Instinct|Gaudi|processor|semiconductor|"
        "data center|AI infrastructure|数据中心|人工智能|"
        "(?:AMD|Intel).*(?:AI|data center|processor|semiconductor|foundry|"
        "Instinct|EPYC|Gaudi|launch|introduces|unveil)",
        "AI/半导体技术动态",
        3,
    ),
    (
        "(?:发布|推出|上市|量产|交付|商业化).*"
        "(?:新品|新产品|新型号|新一代)|"
        "(?:新品|新产品|新型号|新一代).*"
        "(?:发布|推出|上市|量产|交付|商业化)",
        "新品/产品发布",
        4,
    ),
    (
        "利率|通胀|inflation|liquidity|discount rate|rate meetings|"
        "流动性|货币政策",
        "宏观流动性",
        3,
    ),
    (
        "IPO|public markets|上市|注册制|资本市场|market access",
        "资本市场制度",
        3,
    ),
    (
        "(?:pcb|覆铜板|铜箔|玻纤布|树脂|基材|电子材料).*(?:涨价|提价|"
        "价格上调|报价上调|缺货|供不应求|供应紧张)|"
        "(?:涨价|提价|价格上调|报价上调|缺货|供不应求|供应紧张).*"
        "(?:pcb|覆铜板|铜箔|玻纤布|树脂|基材|电子材料)",
        "电子材料涨价/缺货",
        5,
    ),
    (
        "(?:hbm|dram|nand|存储芯片|内存|存储).*(?:涨价|提价|价格上涨|价格上调|"
        "报价上调|缺货|供不应求|供应紧张)|"
        "(?:涨价|提价|价格上涨|价格上调|报价上调|缺货|供不应求|供应紧张).*"
        "(?:hbm|dram|nand|存储芯片|内存|存储)",
        "存储涨价/缺货",
        5,
    ),
    (
        "晶圆.*(涨价|缺货|满产)|半导体.*(缺货|涨价)|"
        "光刻胶|硅片|先进封装.*(订单|扩产|涨价)",
        "半导体供需催化",
        5,
    ),
    (
        "(?:光模块|1\\.6T|800G|服务器|交换机|液冷|数据中心).*"
        "(?:订单|扩产|出货|资本开支|建设)",
        "AI算力基础设施",
        5,
    ),
    (
        "(?:锂矿|碳酸锂|氢氧化锂|锂电材料|电解液|正极|负极).*"
        "(?:涨价|提价|报价|缺货|库存|排产)|"
        "(?:涨价|提价|报价|缺货|库存|排产).*"
        "(?:锂矿|碳酸锂|氢氧化锂|锂电材料|电解液|正极|负极)",
        "锂电供需催化",
        5,
    ),
    (
        "(?:稀土|氧化镨钕|钕铁硼|永磁|磁材).*"
        "(?:涨价|报价|供给|配额|订单)|"
        "(?:涨价|报价|供给|配额|订单).*"
        "(?:稀土|氧化镨钕|钕铁硼|永磁|磁材)",
        "稀土/磁材供需",
        5,
    ),
    (
        "(?:变压器|电力设备|储能系统).*(?:招标|中标|订单|扩产)|"
        "算力.*(用电|电力需求)",
        "电网与储能订单",
        4,
    ),
    (
        "航运|集运|运价|港口拥堵|红海|海运.*(涨|上升|中断|绕行|拥堵)",
        "航运运价催化",
        4,
    ),
    ("涨价|提价|价格上调|报价上调|缺货|供不应求", "涨价/供需催化", 5),
    ("扩产受限|停产|限产|供给收缩|库存低位|排产紧张", "供给收缩", 4),
    (
        "政策支持|补贴|国常会|发改委|工信部|行动方案|指导意见|以旧换新|设备更新",
        "政策催化",
        4,
    ),
    (
        "中标|大单|订单|签订合同|采购|定点|销量放量|出货放量|需求放量",
        "订单/需求验证",
        4,
    ),
    ("业绩预增|扭亏|超预期|利润增长|收入增长", "业绩催化", 4),
    ("回购|增持|并购|重组|注入|战略合作", "资本运作", 3),
    (
        "NVIDIA|英伟达|Physical AI|physical ai|具身智能|humanoid robot|robotics",
        "科技催化",
        3,
    ),
    (
        "NASA.*(?:launch|rocket|satellite|spacecraft|mission)|SpaceX|"
        "space mission|commercial space|satellite|launch vehicle|rocket|"
        "商业航天|卫星互联网|低轨卫星",
        "资本运作",
        3,
    ),
)

GLOBAL_CROSS_MARKET_PATTERNS: tuple[tuple[str, str, Impact, int], ...] = (
    (
        "美股大涨|纳斯达克.*(大涨|反弹|新高)|标普.*(大涨|反弹|新高)|"
        "nasdaq.*(rally|surge|record|jumps)|s&p 500.*(rally|record|jumps)|"
        "risk-on|tech stocks rally|stock futures rise",
        "外盘风险偏好",
        "positive",
        4,
    ),
    (
        "战争|地缘|冲突|袭击|空袭|导弹|中东|停火破裂|"
        r"\bwar\b|\bgeopolitical\b|\battack\b|\bairstrike\b|\bmissile\b|"
        r"\bmiddle east\b|\bdefense stocks\b",
        "地缘冲突",
        "negative",
        5,
    ),
    (
        "油价大涨|油价飙升|原油大涨|布伦特原油|wti|"
        "crude oil.*(rises|jumps|surges|rally)|brent.*(rises|jumps|surges)|"
        "opec.*(cut|cuts)|oil prices.*(rise|jump|surge)",
        "油价冲击",
        "positive",
        4,
    ),
)

NEGATIVE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("减持|清仓|套现|解禁|质押|爆仓", "股东/筹码风险", 5),
    (
        "立案|调查|处罚|问询函|监管处罚|监管问询|监管重拳|反垄断|拆分|诉讼|仲裁|听证",
        "监管/合规风险",
        5,
    ),
    ("事故|停工|停产|召回|安全隐患|污染", "经营事故", 5),
    ("制裁|限制|禁令|断供|关税|出口管制", "外部冲击", 4),
    ("业绩下滑|亏损|不及预期|预亏|暴雷", "业绩风险", 4),
    ("价格下跌|降价|需求疲弱|库存高企|产能过剩", "供需转弱", 4),
    (
        "(?:光模块|服务器|交换机|液冷|数据中心).*"
        "(?:订单取消|砍单|延迟)|"
        "订单.*(?:取消|砍单|延迟)",
        "算力订单风险",
        5,
    ),
    (
        "(?:锂矿|碳酸锂|锂电材料|电解液|正极|负极).*"
        "(?:价格下跌|库存高企|减产)|"
        "(?:价格下跌|库存高企|减产).*"
        "(?:锂矿|碳酸锂|锂电材料|电解液|正极|负极)",
        "锂电供需转弱",
        5,
    ),
    (
        "(?:稀土|钕铁硼|磁材).*"
        "(?:价格下跌|需求疲弱|库存高企)|"
        "(?:价格下跌|需求疲弱|库存高企).*"
        "(?:稀土|钕铁硼|磁材)",
        "稀土需求转弱",
        5,
    ),
)

_AUTHORITATIVE_SOURCE_TOKENS: tuple[str, ...] = (
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
    "NVIDIADeveloper",
    "NVIDIA Newsroom",
    "AMD-PressReleases",
    "AMD Press Releases",
    "Intel-PressReleases",
    "Intel Press Releases",
    "OpenAI-News",
    "OpenAI News",
)

_MEDIA_SOURCE_TOKENS: tuple[str, ...] = (
    "新华社",
    "央视",
    "证券报",
    "财联社",
    "东财",
    "同花顺",
    "新浪",
    "路透",
    "彭博",
    "Reuters",
    "Bloomberg",
    "MarketWatch",
)

_SOURCE_BY_URL_TOKEN: tuple[tuple[str, str], ...] = (
    ("federalreserve.gov", "Federal Reserve"),
    ("sec.gov", "SEC"),
    ("ecb.europa.eu", "ECB"),
    ("nasa.gov", "NASA"),
    ("nvidianews.nvidia.com", "NVIDIA Newsroom"),
    ("nvidia.com", "NVIDIA"),
    ("ir.amd.com", "AMD Press Releases"),
    ("intc.com", "Intel Press Releases"),
    ("openai.com", "OpenAI News"),
    ("marketwatch.com", "MarketWatch"),
    ("dowjones.io", "MarketWatch"),
    ("reuters.com", "Reuters"),
    ("bloomberg.com", "Bloomberg"),
    ("10jqka.com.cn", "同花顺"),
    ("eastmoney.com", "东财"),
    ("futunn.com", "富途"),
    ("cls.cn", "财联社"),
    ("cnstock.com", "证券报"),
    ("xinhua", "新华社"),
    ("cctv", "央视"),
    ("cninfo.com.cn", "巨潮公告"),
    ("sse.com.cn", "上交所公告"),
    ("szse.cn", "深交所公告"),
)

# These are deterministic relevance tags, not a forecast model.  They describe
# the first places to look after a catalyst is classified; price/volume
# confirmation remains outside this module.
_SECTOR_TAG_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "pcb",
            "覆铜板",
            "铜箔",
            "玻纤布",
            "树脂",
            "基材",
            "电子材料",
        ),
        ("PCB", "覆铜板", "铜箔", "电子材料", "先进封装"),
    ),
    (
        ("hbm", "dram", "nand", "存储芯片", "内存", "存储涨价"),
        ("存储", "半导体", "先进封装", "AI算力"),
    ),
    (
        (
            "晶圆",
            "光刻胶",
            "硅片",
            "半导体设备",
            "刻蚀",
            "薄膜沉积",
            "先进封装",
        ),
        ("半导体设备", "半导体材料", "先进封装", "半导体"),
    ),
    (
        ("spacex", "space x", "starlink", "商业航天", "卫星", "低轨", "火箭"),
        ("商业航天", "卫星互联网", "军工电子"),
    ),
    (
        ("physical ai", "具身智能", "robotics", "humanoid", "英伟达", "nvidia"),
        ("具身智能", "机器人", "AI算力", "半导体"),
    ),
    (
        ("纳斯达克", "nasdaq", "美股大涨", "risk-on", "风险偏好"),
        ("AI算力", "半导体", "高beta成长"),
    ),
    (
        ("战争", "war", "地缘", "冲突", "袭击", "middle east", "中东"),
        ("黄金", "军工", "油气", "航运"),
    ),
    (
        ("油价", "原油", "brent", "wti", "crude", "opec"),
        ("油气", "油服", "煤化工", "资源品"),
    ),
    (
        ("存储", "hbm", "dram", "nand", "半导体", "芯片", "先进封装"),
        ("存储", "半导体材料", "先进封装", "PCB"),
    ),
    (
        ("设备更新", "以旧换新", "国常会", "补贴", "政策支持"),
        ("设备更新", "工程机械", "汽车家电"),
    ),
    (
        ("光模块", "1.6t", "800g", "服务器", "交换机", "液冷", "数据中心"),
        ("光模块", "服务器", "交换机", "液冷", "AI算力"),
    ),
    (
        ("锂矿", "碳酸锂", "氢氧化锂", "锂电", "电解液", "正极", "负极"),
        ("锂资源", "锂电材料", "电池", "储能", "新能源汽车"),
    ),
    (
        ("稀土", "氧化镨钕", "磁材", "钕铁硼", "永磁"),
        ("稀土资源", "磁性材料", "机器人", "新能源汽车", "风电"),
    ),
    (
        ("电网", "变压器", "电力设备", "储能系统", "电力需求"),
        ("电网设备", "变压器", "储能", "电力自动化", "AI算力"),
    ),
    (
        ("航运", "集运", "运价", "港口拥堵", "红海", "海运"),
        ("航运", "港口", "油运", "跨境物流", "油气"),
    ),
)

# These are short-horizon causal chains, not price forecasts.  They make the
# first verification target explicit so a headline cannot become a stock tip
# merely because it contains a fashionable keyword.
_TRANSMISSION_CHAIN_RULES: tuple[
    tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        ("pcb", "覆铜板", "铜箔", "玻纤布", "电子材料"),
        ("原材料/覆铜板价格", "PCB厂商报价与订单", "高频通信/服务器板需求"),
        ("相关品种报价继续上调", "板块成交额放大且强于大盘"),
        ("现货价格未跟涨", "放量冲高后跌回消息前"),
    ),
    (
        ("hbm", "dram", "nand", "存储芯片", "内存", "存储"),
        ("存储现货价格", "模组/封测与渠道库存", "AI服务器与终端需求"),
        ("现货或厂商报价连续上调", "存储产业链出现扩散"),
        ("价格仅为单点传闻", "库存/需求数据反向"),
    ),
    (
        ("晶圆", "光刻胶", "硅片", "半导体设备", "先进封装"),
        ("设备/材料订单", "晶圆厂稼动率", "下游芯片交付"),
        ("订单或产能利用率被公告/数据确认", "相关环节相对强度延续"),
        ("只有概念标题无订单", "成交无法承接"),
    ),
    (
        ("physical ai", "具身智能", "robotics", "humanoid", "英伟达", "nvidia"),
        ("平台/模型发布", "机器人本体与传感器", "控制器/算力/伺服"),
        ("客户发布产品或订单落地", "机器人产业链成交扩散"),
        ("仅开发者演示无商业化", "相关标的高开低走"),
    ),
    (
        ("spacex", "space x", "商业航天", "卫星", "低轨", "火箭"),
        ("发射/卫星订单", "卫星制造与地面设备", "军工电子/通信应用"),
        ("发射计划或订单有官方来源", "商业航天板块相对强度扩散"),
        ("上市传闻未有文件", "板块冲高回落"),
    ),
    (
        ("战争", "war", "地缘", "冲突", "袭击", "中东"),
        ("避险需求", "黄金/贵金属", "军工与能源"),
        ("黄金与军工相对强度同步", "事件持续且有权威来源"),
        ("停火或风险溢价回落", "金价/军工冲高回落"),
    ),
    (
        ("光模块", "1.6t", "800g", "服务器", "交换机", "液冷", "数据中心"),
        ("海外算力资本开支", "光模块/服务器订单", "上游芯片与散热交付"),
        ("公司订单、扩产或出货被公告确认", "光模块与服务器成交扩散"),
        ("只有产品发布没有订单", "订单兑现或板块成交明显转弱"),
    ),
    (
        ("锂矿", "碳酸锂", "氢氧化锂", "锂电", "电解液", "正极", "负极"),
        ("资源报价", "材料厂库存与排产", "电池/储能需求"),
        ("现货报价连续变化且产业链库存同步", "资源与材料环节相对强度扩散"),
        ("仅期货异动未传导现货", "库存上升或下游减产"),
    ),
    (
        ("稀土", "氧化镨钕", "磁材", "钕铁硼", "永磁"),
        ("稀土氧化物报价", "磁材厂成本与订单", "机器人/汽车/风电需求"),
        ("报价与磁材企业订单同时确认", "下游应用环节跟随放量"),
        ("仅政策预期没有报价确认", "磁材价格和订单不跟随"),
    ),
    (
        ("电网", "变压器", "电力设备", "储能系统", "电力需求"),
        ("电力投资/算力用电", "变压器与电网设备订单", "储能及运维交付"),
        ("招标、中标或排产数据确认", "电网设备内部扩散且强于大盘"),
        ("只有政策口号无招标", "订单延期或板块冲高回落"),
    ),
    (
        ("航运", "集运", "运价", "港口拥堵", "红海", "海运"),
        ("航线受阻/运力变化", "运价与港口周转", "航运公司利润弹性"),
        ("运价指数连续上行且公司运力受益", "航运板块相对强度延续"),
        ("运价单日脉冲", "航线恢复或运价快速回落"),
    ),
)

_CATEGORY_TIME_HORIZONS: dict[str, str] = {
    "AI/半导体技术动态": "隔夜-5日",
    "新品/产品发布": "当日-3日",
    "宏观流动性": "隔夜-5日",
    "资本市场制度": "隔夜-5日",
    "科技催化": "隔夜-3日",
    "资本运作": "当日-3日",
    "外盘风险偏好": "隔夜-2日",
    "地缘冲突": "当日-3日",
    "油价冲击": "当日-3日",
    "政策催化": "1-5日",
    "订单/需求验证": "当日-3日",
    "涨价/供需催化": "当日-5日",
    "供给收缩": "当日-5日",
    "AI算力基础设施": "隔夜-5日",
    "锂电供需催化": "当日-5日",
    "稀土/磁材供需": "当日-5日",
    "电网与储能订单": "1-5日",
    "航运运价催化": "当日-5日",
    "业绩催化": "1-5日",
}


def _source_quality_from_source(
    source: str,
    *,
    source_count: int = 1,
) -> tuple[str, int]:
    clean = str(source or "").strip()
    score = 1
    if _contains_source_token(clean, _AUTHORITATIVE_SOURCE_TOKENS):
        score = 4
    elif _contains_source_token(
        clean, ("新华社", "央视", "国常会", "发改委", "工信部")
    ):
        score = 3
    elif _contains_source_token(clean, _MEDIA_SOURCE_TOKENS):
        score = 2
    if int(source_count) >= 2:
        score = max(score, 3)
    label = {
        4: "高价值来源",
        3: "多源/权威媒体",
        2: "主流媒体",
    }.get(score, "普通来源")
    return label, score


def _contains_source_token(source: str, tokens: Sequence[str]) -> bool:
    clean = str(source or "").casefold()
    return any(str(token).casefold() in clean for token in tokens)


def _fetch_news_frames_parallel(
    symbols: Sequence[str],
    *,
    symbol_fetcher: Fetcher,
    global_fetcher: Callable[[int], pd.DataFrame],
    max_symbol_news: int,
    max_global_news: int,
    timeout_seconds: float,
    isolate_process: bool = False,
) -> dict[str, _NewsFetchOutcome]:
    """Fetch all news scopes against one deadline.

    A slow candidate must not consume a separate timeout before the global
    feed or the other candidates get a chance to return.  Workers may still
    be unwinding after the deadline, so the executor is deliberately not
    used as a context manager (which would wait for those workers).
    """

    unique_symbols = tuple(dict.fromkeys(str(symbol).strip() for symbol in symbols))
    timeout = float(timeout_seconds)
    fetches: dict[str, Callable[[], pd.DataFrame]] = {
        symbol: lambda symbol=symbol: symbol_fetcher(symbol, max_symbol_news)
        for symbol in unique_symbols
    }
    fetches["__global__"] = lambda: global_fetcher(max_global_news)
    outcomes = _run_fetchers_with_deadline(
        fetches,
        timeout_seconds=timeout,
        isolate_process=isolate_process,
    )
    return {
        key: replace(
            outcome,
            frame=(
                outcome.frame
                if isinstance(outcome.frame, pd.DataFrame)
                else pd.DataFrame()
            ),
        )
        for key, outcome in outcomes.items()
    }


def _run_fetchers_with_deadline(
    fetches: dict[str, Callable[[], pd.DataFrame]],
    *,
    timeout_seconds: float,
    isolate_process: bool = False,
) -> dict[str, _NewsFetchOutcome]:
    """Run independent fetches without registering exit-blocking futures.

    ``ThreadPoolExecutor.shutdown(wait=False)`` still leaves its worker threads
    registered in ``concurrent.futures``' interpreter-exit hook.  A network
    source that ignores its request timeout can therefore keep the whole CLI
    alive after the deadline.  These daemon workers are deliberately detached:
    the caller receives a bounded result and the process can publish its
    partial/observation snapshot without waiting for an uncooperative source.
    """

    if isolate_process:
        return _run_fetchers_in_processes(
            fetches,
            timeout_seconds=timeout_seconds,
        )

    outcomes: dict[str, _NewsFetchOutcome] = {}
    lock = threading.Lock()

    def run_one(key: str, fetch: Callable[[], pd.DataFrame]) -> None:
        try:
            result = fetch()
            outcome = _NewsFetchOutcome(frame=result)
        except BaseException as exc:
            outcome = _NewsFetchOutcome(error=exc)
        with lock:
            outcomes[key] = outcome

    workers = [
        threading.Thread(
            target=run_one,
            args=(key, fetch),
            name=f"aqsp-news-{key}",
            daemon=True,
        )
        for key, fetch in fetches.items()
    ]
    for worker in workers:
        worker.start()

    deadline = monotonic() + max(0.1, float(timeout_seconds))
    for worker in workers:
        worker.join(max(0.0, deadline - monotonic()))

    for key in fetches:
        if key not in outcomes:
            outcomes[key] = _NewsFetchOutcome(
                error=TimeoutError(f"消息源超过 {float(timeout_seconds):.1f}s 未返回"),
                timed_out=True,
            )
    return outcomes


def _process_fetcher_entry(
    key: str,
    fetch: Callable[[], pd.DataFrame],
    result_queue: Any,
) -> None:
    """Run an untrusted network adapter outside the parent interpreter."""
    try:
        result_queue.put((key, _NewsFetchOutcome(frame=fetch())))
    except BaseException as exc:
        result_queue.put(
            (
                key,
                _NewsFetchOutcome(
                    error=RuntimeError(f"{type(exc).__name__}: {exc}")
                ),
            )
        )


def _run_fetchers_in_processes(
    fetches: dict[str, Callable[[], pd.DataFrame]],
    *,
    timeout_seconds: float,
) -> dict[str, _NewsFetchOutcome]:
    """Bound third-party adapters without leaving native threads at exit."""
    try:
        context = multiprocessing.get_context("fork")
    except ValueError:
        # Windows has no fork context; retain the daemon-thread fallback there.
        return _run_fetchers_with_deadline(
            fetches,
            timeout_seconds=timeout_seconds,
            isolate_process=False,
        )

    result_queue = context.Queue()
    workers = {
        key: context.Process(
            target=_process_fetcher_entry,
            args=(key, fetch, result_queue),
            name=f"aqsp-news-process-{key}",
        )
        for key, fetch in fetches.items()
    }
    for worker in workers.values():
        worker.daemon = True
        worker.start()

    deadline = monotonic() + max(0.1, float(timeout_seconds))
    for worker in workers.values():
        worker.join(max(0.0, deadline - monotonic()))

    outcomes: dict[str, _NewsFetchOutcome] = {}
    while True:
        try:
            key, outcome = result_queue.get_nowait()
        except Exception:
            break
        if isinstance(key, str) and isinstance(outcome, _NewsFetchOutcome):
            outcomes[key] = outcome

    for key, worker in workers.items():
        if worker.is_alive():
            worker.terminate()
            worker.join(0.5)
        if key not in outcomes:
            outcomes[key] = _NewsFetchOutcome(
                error=TimeoutError(
                    f"消息源超过 {float(timeout_seconds):.1f}s 未返回"
                ),
                timed_out=True,
            )
        worker.close()
    result_queue.close()
    return outcomes


def build_catalyst_report(
    *,
    symbols: Sequence[str] = (),
    symbol_names: dict[str, str] | None = None,
    fetch_symbol_news: Fetcher | None = None,
    fetch_global_news: Callable[[int], pd.DataFrame] | None = None,
    config: NewsCatalystConfig | None = None,
) -> CatalystReport:
    cfg = config or NewsCatalystConfig(symbols=tuple(symbols))
    effective_symbols = tuple(symbols or cfg.symbols)
    if tuple(cfg.symbols) != effective_symbols:
        cfg = replace(cfg, symbols=effective_symbols)
    cached_report = _load_cached_catalyst_report(cfg)
    if (
        cached_report is not None
        and float(cfg.cache_ttl_seconds) > 0
        and cached_report.age_seconds <= float(cfg.cache_ttl_seconds)
        and cached_report.report.source_status == "ok"
        and cached_report.report.date == today_shanghai().isoformat()
        and cached_report.report.news_status != "stale_cache"
    ):
        return cached_report.report
    names = symbol_names or {}
    warnings: list[str] = []
    rows: list[CatalystEvent] = []
    source_stats = _SourceStats()
    stale_news_count = 0
    undated_news_count = 0
    future_news_count = 0
    recent_news_count = 0

    symbol_fetcher = fetch_symbol_news or (
        lambda symbol, limit: _akshare_symbol_news(
            symbol,
            limit,
            timeout_seconds=cfg.source_timeout_seconds,
        )
    )
    global_fetcher = fetch_global_news or (
        lambda limit: _akshare_global_news(
            limit,
            timeout_seconds=cfg.source_timeout_seconds,
        )
    )
    anchor_day = today_shanghai()

    fetches = _fetch_news_frames_parallel(
        tuple(symbols or cfg.symbols),
        symbol_fetcher=symbol_fetcher,
        global_fetcher=global_fetcher,
        max_symbol_news=cfg.max_symbol_news,
        max_global_news=cfg.max_global_news,
        timeout_seconds=cfg.source_timeout_seconds,
        isolate_process=bool(cfg.isolate_external_sources),
    )

    for symbol in tuple(symbols or cfg.symbols):
        outcome = fetches.get(symbol, _NewsFetchOutcome())
        if outcome.error is not None:
            exc = outcome.error
            source_stats.record_failure(
                exc,
                name=f"{symbol}:symbol_news",
                region="domestic",
            )
            warnings.append(f"{symbol} 个股新闻获取失败: {exc}")
            continue
        df = outcome.frame
        frame_warnings = _frame_warnings(df, prefix=f"{symbol} 个股新闻")
        source_stats.record_frame(
            df,
            frame_warnings,
            name=f"{symbol}:symbol_news",
            region="domestic",
        )
        warnings.extend(frame_warnings)
        symbol_rows, stale_count, undated_count, future_count = (
            _filter_recent_news_rows(
                _sorted_news_rows(_iter_news_rows(df)),
                today=anchor_day,
                max_age_days=cfg.max_news_age_days,
                allow_undated=cfg.allow_undated_news,
                limit=cfg.max_symbol_news,
            )
        )
        if stale_count > 0:
            warnings.append(f"{symbol} 个股新闻: 已过滤 {stale_count} 条过期消息")
            stale_news_count += stale_count
        if undated_count > 0:
            warnings.append(f"{symbol} 个股新闻: 已过滤 {undated_count} 条无日期消息")
            undated_news_count += undated_count
        if future_count > 0:
            warnings.append(
                f"{symbol} 个股新闻: 已过滤 {future_count} 条未来时间戳消息"
            )
            future_news_count += future_count
        recent_news_count += len(symbol_rows)
        rows.extend(
            _events_from_rows(
                symbol_rows,
                symbol=symbol,
                name=names.get(symbol, ""),
            )
        )

    global_outcome = fetches.get("__global__", _NewsFetchOutcome())
    if global_outcome.error is not None:
        exc = global_outcome.error
        source_stats.record_failure(
            exc,
            name="global_news",
            region="international",
        )
        warnings.append(f"全市场快讯获取失败: {exc}")
        global_df = pd.DataFrame()
    else:
        global_df = global_outcome.frame
        frame_warnings = _frame_warnings(global_df, prefix="全市场快讯")
        source_stats.record_frame(
            global_df,
            frame_warnings,
            name="global_news",
            region="international",
        )
        warnings.extend(frame_warnings)
    warnings = list(_dedupe_texts(warnings))
    (
        global_rows,
        global_stale_count,
        global_undated_count,
        global_future_count,
    ) = _filter_recent_news_rows(
        _sorted_news_rows(_iter_news_rows(global_df)),
        today=anchor_day,
        max_age_days=cfg.max_news_age_days,
        allow_undated=cfg.allow_undated_news,
        limit=cfg.max_global_news,
    )
    if global_stale_count > 0:
        warnings.append(f"全市场快讯: 已过滤 {global_stale_count} 条过期消息")
        stale_news_count += global_stale_count
    if global_undated_count > 0:
        warnings.append(f"全市场快讯: 已过滤 {global_undated_count} 条无日期消息")
        undated_news_count += global_undated_count
    if global_future_count > 0:
        warnings.append(f"全市场快讯: 已过滤 {global_future_count} 条未来时间戳消息")
        future_news_count += global_future_count
    recent_news_count += len(global_rows)
    rows.extend(_events_from_rows(global_rows))

    deduped = _merge_events(rows)
    pre_ranked = tuple(
        sorted(
            deduped,
            key=_event_rank_key,
            reverse=True,
        )
    )
    reviewed = _review_events(
        pre_ranked,
        enable_llm=cfg.enable_llm_review,
        timeout_seconds=cfg.llm_timeout_seconds,
        max_events=cfg.max_llm_review_events,
    )
    filtered = [item for item in reviewed if item.confidence >= cfg.min_confidence]
    ranked = sorted(
        filtered,
        key=_event_rank_key,
        reverse=True,
    )
    status = _report_source_status(source_stats.health)
    event_status = _event_result_status(
        source_status=status,
        has_events=bool(ranked),
        raw_news_count=source_stats.raw_rows,
        recent_news_count=recent_news_count,
        stale_news_count=stale_news_count,
        future_news_count=future_news_count,
    )
    report = CatalystReport(
        date=today_shanghai().isoformat(),
        generated_at=now_shanghai().isoformat(timespec="seconds"),
        events=_select_diverse_events(ranked, cfg.max_events),
        source_status=status,
        warnings=tuple(warnings[:5]),
        source_statuses=tuple(source_stats.health),
        event_status=event_status,
        raw_news_count=source_stats.raw_rows,
        stale_news_count=stale_news_count,
        undated_news_count=undated_news_count,
        future_news_count=future_news_count,
    )
    if report.source_status == "ok":
        _store_cached_catalyst_report(cfg, report)
        return report
    if (
        cached_report is not None
        and cfg.allow_stale_cache_on_failure
        and _stale_cache_is_allowed(cfg, cached_report)
    ):
        return _report_from_stale_cache(
            cached_report,
            fetch_warnings=report.warnings,
        )
    if cached_report is not None and cfg.allow_stale_cache_on_failure:
        return CatalystReport(
            date=report.date,
            generated_at=report.generated_at,
            events=report.events,
            source_status=report.source_status,
            warnings=tuple(
                _dedupe_texts(
                    (
                        _stale_cache_rejected_warning(cfg, cached_report),
                        *report.warnings,
                    )
                )
            )[:5],
            source_statuses=report.source_statuses,
            event_status=report.news_status,
            raw_news_count=report.raw_news_count,
            stale_news_count=report.stale_news_count,
            undated_news_count=report.undated_news_count,
            future_news_count=report.future_news_count,
        )
    return report


def format_catalyst_notification(report: CatalystReport) -> str:
    has_events = bool(report.events)
    has_warnings = bool(report.warnings)
    if has_events:
        lead = report.events[0]
        lead_line = lead.inference or (
            f"{_event_target(lead)}｜{lead.category}｜{_short_text(lead.title, 36)}"
        )
    elif report.source_status == "failed":
        lead_line = "无有效结论：消息源失败"
    elif report.source_status == "timeout":
        lead_line = "无有效结论：消息源超时"
    elif report.source_status == "partial":
        lead_line = "无强事件：部分消息源可用"
    else:
        lead_line = "无强事件"

    lines = [
        f"# 消息面雷达-{report.date}｜{_report_title_status(report)}",
        "",
        "## 结论",
        "",
        f"- {lead_line}",
        f"- 数据状态: {_source_status_label(report.source_status)}",
        f"- 事件状态: {_event_status_label(report.news_status)}",
        "",
        "## 事件",
        "",
    ]
    if not report.events:
        if report.source_status == "failed":
            lines.append("- 无可靠消息面结果")
        else:
            lines.append("- 未筛出高影响消息")
    else:
        for index, event in enumerate(report.events, start=1):
            lines.extend(_event_card_lines(index, event))
            lines.append("")

    lines.extend(
        [
            "## 状态",
            "",
            f"- 状态: {report.source_status}",
            f"- 高影响事件: {len(report.events)}",
            f"- 来源明细: {_source_status_digest(report.source_statuses)}",
            f"- 告警: {'有' if has_warnings else '无'}",
        ]
    )
    if report.warnings:
        lines.append(
            "- 告警: "
            + "；".join(
                _safe_warning(item) for item in _display_warnings(report.warnings)
            )
        )
    return compact_notification_markdown(
        normalize_research_tone("\n".join(lines)),
        max_section_items=8,
    )


def _source_status_digest(statuses: Sequence[NewsSourceHealth]) -> str:
    if not statuses:
        return "国内=unavailable；国际=unavailable"
    grouped: dict[str, list[str]] = {}
    for item in statuses:
        grouped.setdefault(item.region, []).append(item.status)
    source_digest = "；".join(
        f"{region}={'、'.join(_dedupe_texts(values))}"
        for region, values in sorted(grouped.items())
    )
    region_statuses = _region_statuses_from_sources(statuses)
    region_digest = "；".join(
        f"{item.region}={item.status}"
        for item in region_statuses
        if item.region not in grouped
    )
    return "；".join(item for item in (source_digest, region_digest) if item)


def _source_status_label(status: str) -> str:
    return {
        "ok": "可用",
        "partial": "部分可用",
        "empty": "无数据",
        "timeout": "超时",
        "failed": "失败",
    }.get(status, status or "未知")


def _event_status_label(status: str) -> str:
    return {
        "high_impact": "已筛出高影响事件",
        "no_high_impact": "抓取成功但未筛出高影响事件",
        "stale_only": "仅发现旧新闻，已排除",
        "no_valid_news": "无可用新闻记录",
        "source_failed": "来源失败，无有效事件",
        "stale_cache": "来源失败，使用受限旧缓存",
    }.get(status, status or "未知")


def _report_source_status(
    source_statuses: Sequence[NewsSourceHealth],
) -> str:
    statuses = tuple(item.status for item in source_statuses)
    if not statuses:
        return "failed"
    if all(status == "ok" for status in statuses):
        return "ok"
    if any(status == "ok" for status in statuses):
        return "partial"
    if any(status == "partial" for status in statuses):
        return "partial"
    if all(status == "empty" for status in statuses):
        return "empty"
    if all(status == "timeout" for status in statuses):
        return "timeout"
    return "failed"


def _region_statuses_from_sources(
    source_statuses: Sequence[NewsSourceHealth],
) -> tuple[NewsRegionStatus, ...]:
    """Aggregate existing per-source health without changing source semantics."""

    result: list[NewsRegionStatus] = []
    for region in ("domestic", "international"):
        items = tuple(
            item
            for item in source_statuses
            if item.region == region or item.region == "mixed"
        )
        statuses = tuple(item.status for item in items)
        if not statuses:
            status: NewsRegionStatusValue = "unavailable"
        elif all(value == "ok" for value in statuses):
            status = "ok"
        elif any(value in {"ok", "partial"} for value in statuses):
            status = "partial"
        elif all(value == "empty" for value in statuses):
            status = "empty"
        elif all(value == "timeout" for value in statuses):
            status = "timeout"
        else:
            status = "failed"
        result.append(
            NewsRegionStatus(
                region=region,
                status=status,
                source_count=len(items),
                successful_sources=sum(
                    1 for item in items if item.status in {"ok", "partial"}
                ),
                row_count=sum(max(0, int(item.row_count)) for item in items),
            )
        )
    return tuple(result)


def _event_result_status(
    *,
    source_status: str,
    has_events: bool,
    raw_news_count: int,
    recent_news_count: int,
    stale_news_count: int,
    future_news_count: int = 0,
) -> CatalystResultStatus:
    if has_events:
        return "high_impact"
    if source_status in {"failed", "timeout"} and raw_news_count <= 0:
        return "source_failed"
    if (
        raw_news_count > 0
        and recent_news_count <= 0
        and (stale_news_count > 0 or future_news_count > 0)
    ):
        return "stale_only"
    if source_status == "ok":
        return "no_high_impact"
    return "no_valid_news"


def _report_title_status(report: CatalystReport) -> str:
    if report.events:
        lead = report.events[0]
        return f"{lead.category}{'/利空' if lead.impact == 'negative' else ''}"
    if report.news_status == "stale_only":
        return "旧新闻已排除"
    if report.news_status == "stale_cache":
        return "旧缓存降级"
    if report.source_status == "failed":
        return "抓取失败"
    if report.source_status == "timeout":
        return "抓取超时"
    if report.source_status == "partial":
        return "部分消息"
    return "无强催化"


def _event_card_lines(index: int, event: CatalystEvent) -> list[str]:
    impact = {"positive": "利好", "negative": "利空", "neutral": "中性"}[event.impact]
    target = _event_target(event)
    title = _short_text(event.title, 42)
    lines = [
        f"- {index}. {impact} | {_inline(target)} | {_inline(event.category)}",
        f"- 结果: {title}",
        f"- 结论: {_inline(event.inference or _event_impact_summary(event))}",
        f"- 影响: {_event_impact_summary(event)}",
        f"- 来源: {_inline(event.source)} | 质量 {_inline(event.source_quality_label)}（{event.source_quality_score}/4） | 区域 {_inline(event.source_region)}",
    ]
    if event.published_at:
        lines.append(f"- 时间: {_inline(event.published_at)}")
    if event.url:
        lines.append(f"- 原文: {_inline(event.url)}")
    if event.source_fetched_at:
        lines.append(f"- 抓取: {_inline(event.source_fetched_at)}")
    return lines


def _events_from_rows(
    rows: Iterable[dict[str, str]],
    *,
    symbol: str = "",
    name: str = "",
) -> list[CatalystEvent]:
    events: list[CatalystEvent] = []
    for row in rows:
        title = row.get("title", "")
        summary = row.get("summary", "")
        content = row.get("content", "")
        event = _classify_title(title, summary=summary, content=content)
        if event is None:
            continue
        category, impact, weight, _reason = event
        source = row.get("source", "")
        source_region = _normalize_region(row.get("source_region", "mixed"))
        source_quality_label, source_quality_score = _source_quality_from_source(source)
        target_name = name or _name_from_title(title)
        affected_symbols = _event_symbols(
            symbol=symbol,
            title=title,
            summary=summary,
            row_symbols=row.get("affected_symbols", ""),
        )
        affected_sectors = _event_sectors(
            title=title,
            summary=summary,
            category=category,
            row=row,
        )
        transmission_hypothesis = _build_transmission_hypothesis(
            title=title,
            category=category,
            impact=impact,
            target=(f"{symbol} {target_name}".strip() or "全市场"),
            sectors=affected_sectors,
        )
        events.append(
            CatalystEvent(
                title=title,
                source=source,
                published_at=row.get("published_at", ""),
                source_fetched_at=row.get("source_fetched_at", ""),
                symbol=symbol,
                name=target_name,
                impact=impact,
                category=category,
                weight=weight,
                confidence=_base_confidence(row),
                verification=_verification_label(row),
                source_quality_label=source_quality_label,
                source_quality_score=source_quality_score,
                inference=_build_event_inference(
                    impact=impact,
                    category=category,
                    target=(
                        f"{symbol} {name}".strip()
                        or _name_from_title(title)
                        or "市场/行业"
                    ),
                ),
                url=row.get("url", ""),
                source_region=source_region,
                affected_sectors=affected_sectors,
                affected_symbols=affected_symbols,
                transmission_hypothesis=transmission_hypothesis,
                time_horizon=_CATEGORY_TIME_HORIZONS.get(category, "当日-3日"),
                supporting_evidence=_event_supporting_evidence(
                    title=title,
                    source=source,
                    verification=_verification_label(row),
                    context=_news_context_snippet(summary, content),
                ),
                contradicting_evidence=_text_tuple(
                    row.get("contradicting_evidence", "")
                ),
                transmission_path=_transmission_path(title, affected_sectors),
                validation_signals=_transmission_signals(
                    title, affected_sectors, positive=True
                ),
                invalidation_signals=_transmission_signals(
                    title, affected_sectors, positive=False
                ),
                summary=_news_context_snippet(summary, content),
            )
        )
    return events


def _event_symbols(
    *,
    symbol: str,
    title: str,
    summary: str = "",
    row_symbols: str = "",
) -> tuple[str, ...]:
    values: list[str] = []
    for value in (symbol, row_symbols):
        for item in re.findall(r"(?<!\d)\d{6}(?!\d)", str(value or "")):
            if item not in values:
                values.append(item)
    for item in re.findall(r"(?<!\d)\d{6}(?!\d)", str(title or "")):
        if item not in values:
            values.append(item)
    for item in match_news_entities(title, summary).symbols:
        if item not in values:
            values.append(item)
    return tuple(values)


def _event_sectors(
    *,
    title: str,
    summary: str = "",
    category: str,
    row: dict[str, str],
) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("sector", "industry", "affected_sectors"):
        for value in _text_tuple(row.get(key, "")):
            if value not in values:
                values.append(value)
    entity_resolution = match_news_entities(title, summary)
    for sector in entity_resolution.sectors:
        if sector not in values:
            values.append(sector)
    text = " ".join(
        (str(title or ""), str(summary or ""), str(category or ""))
    ).casefold()
    for keywords, sectors in _SECTOR_TAG_RULES:
        if any(keyword.casefold() in text for keyword in keywords):
            for sector in sectors:
                if sector not in values:
                    values.append(sector)
    if not values and category and category != "消息":
        values.append(category)
    return tuple(values[:8])


def _build_transmission_hypothesis(
    *,
    title: str,
    category: str,
    impact: Impact,
    target: str,
    sectors: tuple[str, ...],
) -> str:
    direction = (
        "抬升" if impact == "positive" else "压低" if impact == "negative" else "扰动"
    )
    sector_text = "、".join(sectors[:3]) or "相关行业"
    path = _transmission_path(title, sectors)
    if path:
        return f"{' -> '.join(path)}；先{direction}短线关注度，再等价格与成交确认（{category}）。"
    return (
        f"{title}通过改变{sector_text}预期，先{direction}{target}的短线关注度，"
        f"再观察板块扩散与价格成交确认（{category}）。"
    )


def _transmission_rule(
    title: str,
    sectors: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None:
    title_text = str(title or "").casefold()
    title_rule = next(
        (
            rule
            for rule in _TRANSMISSION_CHAIN_RULES
            if any(keyword.casefold() in title_text for keyword in rule[0])
        ),
        None,
    )
    if title_rule is not None:
        return title_rule
    sector_text = " ".join(str(item) for item in sectors).casefold()
    return next(
        (
            rule
            for rule in _TRANSMISSION_CHAIN_RULES
            if any(keyword.casefold() in sector_text for keyword in rule[0])
        ),
        None,
    )


def _transmission_path(title: str, sectors: tuple[str, ...]) -> tuple[str, ...]:
    rule = _transmission_rule(title, sectors)
    return () if rule is None else rule[1]


def _transmission_signals(
    title: str,
    sectors: tuple[str, ...],
    *,
    positive: bool,
) -> tuple[str, ...]:
    rule = _transmission_rule(title, sectors)
    if rule is None:
        return ()
    return rule[2] if positive else rule[3]


def _event_supporting_evidence(
    *,
    title: str,
    source: str,
    verification: str,
    context: str = "",
) -> tuple[str, ...]:
    source_text = source or "来源未标注"
    evidence = [f"{source_text}: {title}", f"来源验证: {verification}"]
    if context:
        evidence.append(f"正文证据: {context}")
    return tuple(evidence)


def _event_target(event: CatalystEvent) -> str:
    target = f"{event.symbol} {event.name}".strip()
    return target or _name_from_title(event.title) or "市场/行业"


def _name_from_title(title: str) -> str:
    import re

    clean = str(title or "").strip()
    match = re.match(r"^([\u4e00-\u9fffA-Za-z0-9]{2,12})[:：]", clean)
    if not match:
        return ""
    name = match.group(1)
    if name in {"消息人士", "市场消息", "快讯"} or name.startswith("据"):
        return ""
    return name


def _classify_title(
    title: str,
    *,
    summary: str = "",
    content: str = "",
) -> tuple[str, Impact, int, str] | None:
    clean = str(title or "").strip()
    if not clean:
        return None
    import re

    evidence = _news_evidence_text(clean, summary=summary, content=content)

    for pattern, category, impact, weight in GLOBAL_CROSS_MARKET_PATTERNS:
        if re.search(pattern, evidence, flags=re.IGNORECASE):
            return (
                category,
                impact,
                weight,
                "",
            )
    if _is_market_price_action_noise(evidence):
        return None
    if _is_non_actionable_discipline_news(evidence):
        return None
    if _is_non_actionable_price_hike_noise(evidence):
        return None
    for pattern, category, weight in NEGATIVE_PATTERNS:
        if re.search(pattern, evidence, flags=re.IGNORECASE):
            return (
                category,
                "negative",
                weight,
                "",
            )
    for pattern, category, weight in POSITIVE_PATTERNS:
        if re.search(pattern, evidence, flags=re.IGNORECASE):
            return (
                category,
                "positive",
                weight,
                "",
            )
    return None


def _news_evidence_text(title: str, *, summary: str = "", content: str = "") -> str:
    """Combine bounded title, abstract, and body evidence for rule matching."""

    parts = [str(title or "").strip()]
    for value in (summary, content):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text[:2000])
    return "\n".join(parts)


def _news_context_snippet(summary: str, content: str) -> str:
    for value in (summary, content):
        text = " ".join(str(value or "").split())
        if text:
            return text[:240]
    return ""


def _is_market_price_action_noise(title: str) -> bool:
    import re

    price_action = re.search(
        r"ETF|指数|盘中|涨超|涨逾|涨幅|大涨|领涨|转跌|收涨|成交|放量冲击|"
        r"涨停|封板|冲板|拉升|走强|异动",
        title,
    )
    if not price_action:
        return False
    if re.search(r"传闻|网传|市场消息|受.*刺激|受.*利好", title):
        return True
    fundamental = re.search(
        r"公告|交易所|涨价|提价|报价上调|价格上调|缺货|供不应求|政策支持|补贴|"
        r"中标|订单|签订合同|业绩预增|回购|增持|并购|重组|减持|立案|调查|"
        r"处罚|事故|停产|制裁|出口管制|预亏|亏损",
        title,
    )
    return fundamental is None


def _is_non_actionable_discipline_news(title: str) -> bool:
    import re

    if not re.search(r"纪律审查|监察调查|严重违纪违法", title):
        return False
    listed_context = re.search(
        r"上市公司|股份有限公司|证券|股票|公告|交易所|证监|董监高|实控人|控股股东",
        title,
    )
    return listed_context is None


def _is_non_actionable_price_hike_noise(title: str) -> bool:
    import re

    if not re.search(r"涨价|提价|价格上调|报价上调", title):
        return False
    if re.search(
        r"监管|反垄断|拆分|众议员|议员|国会|听证|处罚|调查|诉讼|关税|制裁",
        title,
    ):
        return True
    return False


def _iter_news_rows(df: pd.DataFrame) -> Iterable[dict[str, str]]:
    if df is None or df.empty:
        return ()
    rows: list[dict[str, str]] = []
    for row in df.to_dict(orient="records"):
        title = _first_text(
            row, ("新闻标题", "公告标题", "标题", "title", "内容", "摘要")
        )
        if not title:
            continue
        url = _first_text(
            row,
            (
                "新闻链接",
                "链接",
                "原文",
                "原文地址",
                "新闻网址",
                "新闻URL",
                "source_url",
                "news_url",
                "article_url",
                "href",
                "link",
                "url",
                "URL",
                "公告链接",
                "公告网址",
                "网址",
                "原文链接",
            ),
        )
        published_at = _first_text(
            row,
            (
                "发布时间",
                "时间",
                "date",
                "日期",
                "公告日期",
                "公告时间",
                "发布日期",
                "发稿时间",
                "published_at",
                "published",
                "publishedAt",
                "pubtime",
                "publish_time",
                "pub_time",
                "pubDate",
                "display_time",
            ),
        )
        url = _normalize_news_url(url)
        source = _first_text(
            row,
            (
                "文章来源",
                "媒体",
                "source",
                "来源",
                "source_name",
                "publisher",
                "公告类型",
            ),
        ) or _source_from_url(url)
        source_region = _first_text(
            row,
            ("source_region", "region", "market", "市场", "source_group"),
        )
        sector = _first_text(row, ("sector", "行业", "板块"))
        industry = _first_text(row, ("industry", "所属行业"))
        affected_sectors = _first_text(
            row,
            ("affected_sectors", "影响板块", "相关行业"),
        )
        affected_symbols = _first_text(
            row,
            ("affected_symbols", "影响标的", "相关股票", "symbols"),
        )
        contradicting_evidence = _first_text(
            row,
            ("contradicting_evidence", "反向证据", "利空证据"),
        )
        summary = _first_text(
            row,
            ("summary", "摘要", "新闻摘要", "description", "描述"),
        )
        content = _first_text(
            row,
            (
                "content",
                "新闻内容",
                "正文",
                "内容",
                "body",
                "article_body",
            ),
        )
        source_group = _first_text(row, ("source_group", "source_type"))
        # RSS title-only items are leads, not auditable messages. Do not let a
        # headline keyword manufacture a catalyst without an abstract/body.
        if not source or (source_group == "rss" and not (summary or content)):
            continue
        summary = summary or content or title
        rows.append(
            {
                "title": title,
                "summary": summary,
                "content": content,
                "source": source,
                "published_at": _normalize_news_published_at(
                    published_at or _fallback_published_at(title, url)
                ),
                "source_fetched_at": str(
                    getattr(df, "attrs", {}).get("aqsp_fetched_at", "") or ""
                ),
                "url": url,
                "source_region": source_region or _region_from_source(source),
                "sector": sector,
                "industry": industry,
                "affected_sectors": affected_sectors,
                "affected_symbols": affected_symbols,
                "contradicting_evidence": contradicting_evidence,
            }
        )
    return tuple(rows)


def _first_text(row: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = row.get(key)
        text = "" if value is None else str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _normalize_news_url(value: str) -> str:
    """Keep only traceable web URLs; malformed values must not be shown as evidence."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _normalize_news_published_at(value: str) -> str:
    """Require a traceable publication timestamp in Shanghai time when known."""

    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_published_datetime(text)
    if parsed is None:
        return text
    # Preserve already Shanghai-qualified display strings; normalize naive or
    # other-zone values so serialized output carries an explicit offset.
    if parsed.utcoffset() == timedelta(hours=8) and re.search(
        r"[+-]\d{2}:?\d{2}$", text
    ):
        return text
    return parsed.isoformat(timespec="seconds")


def _source_from_url(url: str) -> str:
    clean = str(url or "").lower()
    if not clean:
        return ""
    for token, source in _SOURCE_BY_URL_TOKEN:
        if token in clean:
            return source
    return ""


def _region_from_source(source: str) -> str:
    text = str(source or "").casefold()
    if any(
        token in text
        for token in (
            "federalreserve",
            "federal reserve",
            "sec",
            "ecb",
            "nasa",
            "marketwatch",
            "nvidia",
            "amd",
            "intel",
            "openai",
            "reuters",
            "bloomberg",
        )
    ):
        return "international"
    if any(token in text for token in ("美联储", "美国", "欧洲央行", "海外")):
        return "international"
    return "domestic"


def _normalize_region(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"domestic", "cn", "china", "国内"}:
        return "domestic"
    if normalized in {"international", "global", "overseas", "海外"}:
        return "international"
    return "mixed"


def _merge_source_regions(left: str, right: str) -> str:
    regions = {_normalize_region(left), _normalize_region(right)}
    if len(regions) == 1:
        return regions.pop()
    return "mixed"


def _normalize_source_status(value: str) -> NewsSourceStatus:
    normalized = str(value or "").strip().casefold()
    if normalized in {"ok", "empty", "timeout", "partial", "failed"}:
        return normalized  # type: ignore[return-value]
    return "failed"


def _status_from_exception(exc: BaseException) -> NewsSourceStatus:
    text = str(exc).casefold()
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    return "failed"


def _status_from_frame(
    row_count: int,
    warnings: Sequence[str],
) -> NewsSourceStatus:
    if row_count > 0:
        return "partial" if warnings else "ok"
    if any(
        "timeout" in str(item).casefold()
        or "timed out" in str(item).casefold()
        or "超时" in str(item)
        for item in warnings
    ):
        return "timeout"
    if warnings and any(
        "失败" in str(item) or "error" in str(item).casefold() for item in warnings
    ):
        return "failed"
    return "empty"


def _source_health_from_frame(df: pd.DataFrame) -> tuple[NewsSourceHealth, ...]:
    payload = (
        getattr(df, "attrs", {}).get("aqsp_source_health", ()) if df is not None else ()
    )
    if not payload:
        return ()
    health: list[NewsSourceHealth] = []
    for item in tuple(payload):
        if isinstance(item, NewsSourceHealth):
            health.append(item)
            continue
        if not isinstance(item, dict):
            continue
        health.append(
            NewsSourceHealth(
                name=str(item.get("name", "")).strip(),
                region=_normalize_region(str(item.get("region", "mixed"))),
                status=_normalize_source_status(str(item.get("status", "failed"))),
                attempted=int(item.get("attempted", 1) or 1),
                successful=int(item.get("successful", 0) or 0),
                row_count=int(item.get("row_count", 0) or 0),
                fetched_at=str(item.get("fetched_at", "")).strip(),
                warnings=tuple(
                    str(warning).strip()
                    for warning in tuple(item.get("warnings", ()) or ())
                    if str(warning).strip()
                ),
            )
        )
    return tuple(health)


def _fallback_published_at(title: str, url: str) -> str:
    published_day = _parse_published_day(title) or _parse_published_day(url)
    if published_day is None:
        return ""
    return datetime.combine(published_day, datetime.min.time()).replace(
        tzinfo=_SHANGHAI_TZ
    ).isoformat(timespec="seconds")


def _select_diverse_events(
    events: Sequence[CatalystEvent],
    limit: int,
) -> tuple[CatalystEvent, ...]:
    """Select ranked events while maximizing publisher, theme, and region diversity.

    Source names often identify a publication rather than a publisher (for
    example, ``NVIDIA Developer`` and ``NVIDIA Newsroom``).  Treating those
    strings as unrelated sources lets one publisher fill the whole snapshot.
    The first event remains the highest-ranked event; subsequent slots prefer
    new publisher families and new supply-chain themes before falling back to
    the deterministic rank order.
    """
    target = max(0, int(limit))
    if target == 0:
        return ()
    ranked = tuple(events)
    if not ranked:
        return ()

    def source_groups(event: CatalystEvent) -> frozenset[str]:
        return frozenset(
            _source_diversity_key(part)
            for part in _source_names(event.source)
            if part.strip()
        ) or frozenset({"unknown"})

    def theme_groups(event: CatalystEvent) -> frozenset[str]:
        sectors = tuple(
            f"sector:{str(item).strip().casefold()}"
            for item in event.affected_sectors
            if str(item).strip()
        )
        if sectors:
            return frozenset(sectors)
        chain = tuple(
            f"chain:{str(item).strip().casefold()}"
            for item in event.transmission_path
            if str(item).strip()
        )
        if chain:
            return frozenset(chain[:2])
        category = str(event.category).strip().casefold()
        return (
            frozenset({f"category:{category}"})
            if category and category != "消息"
            else frozenset()
        )

    def region_groups(event: CatalystEvent) -> frozenset[str]:
        region = _normalize_region(event.source_region)
        return frozenset({region}) if region != "mixed" else frozenset()

    selected: list[CatalystEvent] = [ranked[0]]
    remaining = list(ranked[1:])
    seen_sources = set(source_groups(selected[0]))
    seen_themes = set(theme_groups(selected[0]))
    seen_regions = set(region_groups(selected[0]))

    while remaining and len(selected) < target:
        def selection_key(
            event: CatalystEvent,
        ) -> tuple[int, int, int, tuple[int, int, int, float, int]]:
            sources = source_groups(event)
            themes = theme_groups(event)
            regions = region_groups(event)
            return (
                int(not sources.intersection(seen_sources)),
                int(bool(themes) and not themes.intersection(seen_themes)),
                int(bool(regions) and not regions.intersection(seen_regions)),
                _event_rank_key(event),
            )

        best = max(remaining, key=selection_key)
        remaining.remove(best)
        selected.append(best)
        seen_sources.update(source_groups(best))
        seen_themes.update(theme_groups(best))
        seen_regions.update(region_groups(best))
    return tuple(selected)


def _source_diversity_key(source: str) -> str:
    """Normalize publication labels to stable publisher families."""
    normalized = re.sub(r"\s+", " ", str(source or "").strip().casefold())
    if not normalized:
        return "unknown"
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("nvidia", ("nvidia", "英伟达")),
        ("reuters", ("reuters", "路透")),
        ("bloomberg", ("bloomberg", "彭博")),
        ("marketwatch", ("marketwatch",)),
        ("amd", ("amd",)),
        ("intel", ("intel",)),
        ("openai", ("openai",)),
        ("eastmoney", ("eastmoney", "东方财富", "东财")),
        ("cls", ("财联社",)),
        ("xinhua", ("新华社",)),
        ("cctv", ("央视",)),
    )
    for family, tokens in aliases:
        if any(token in normalized for token in tokens):
            return family
    return normalized


def _merge_events(events: Sequence[CatalystEvent]) -> tuple[CatalystEvent, ...]:
    merged: list[CatalystEvent] = []
    for event in events:
        existing_index = next(
            (
                index
                for index, existing in enumerate(merged)
                if _events_can_merge(existing, event)
            ),
            None,
        )
        if existing_index is None:
            merged.append(event)
            continue
        existing = merged[existing_index]
        merged_source = "、".join(_dedupe_texts((existing.source, event.source)))
        merged_sources = _source_names(existing.source, event.source)
        source_families = {
            _source_diversity_key(source)
            for source in merged_sources
            if source.strip()
        }
        merged_source_count = max(1, len(source_families))
        is_new_source = _source_diversity_key(event.source) not in {
            _source_diversity_key(source) for source in _source_names(existing.source)
        }
        source_quality_label, source_quality_score = _source_quality_from_source(
            merged_source,
            source_count=merged_source_count,
        )
        primary_event = _newer_event(existing, event)
        merged_sectors = _text_tuple(
            (*existing.affected_sectors, *event.affected_sectors)
        )
        merged_symbols = _text_tuple(
            (*existing.affected_symbols, *event.affected_symbols)
        )
        merged_target = (
            (existing.symbol or event.symbol) + " " + (existing.name or event.name)
        ).strip() or "市场/行业"
        merged[existing_index] = CatalystEvent(
            title=existing.title
            if len(existing.title) <= len(event.title)
            else event.title,
            source=merged_source,
            published_at=primary_event.published_at or event.published_at,
            source_fetched_at=primary_event.source_fetched_at
            or existing.source_fetched_at
            or event.source_fetched_at,
            symbol=existing.symbol or event.symbol,
            name=existing.name or event.name,
            impact=existing.impact,
            category=existing.category,
            # Repeated rows from one publisher are transport duplication, not
            # corroboration. Only a genuinely new publisher family adds weight.
            weight=max(existing.weight, event.weight) + int(is_new_source),
            confidence=min(
                1.0,
                max(existing.confidence, event.confidence)
                + (0.18 if is_new_source else 0.0),
            ),
            source_count=merged_source_count,
            verification="多源交叉"
            if merged_source_count >= 2
            else existing.verification,
            source_quality_label=source_quality_label,
            source_quality_score=source_quality_score,
            inference=_build_event_inference(
                impact=existing.impact,
                category=existing.category,
                target=merged_target,
            ),
            url=primary_event.url or existing.url or event.url,
            source_region=_merge_source_regions(
                existing.source_region,
                event.source_region,
            ),
            affected_sectors=merged_sectors,
            affected_symbols=merged_symbols,
            transmission_hypothesis=_build_transmission_hypothesis(
                title=existing.title
                if len(existing.title) <= len(event.title)
                else event.title,
                category=existing.category,
                impact=existing.impact,
                target=merged_target,
                sectors=merged_sectors,
            ),
            time_horizon=existing.time_horizon or event.time_horizon,
            supporting_evidence=_text_tuple(
                (*existing.supporting_evidence, *event.supporting_evidence)
            ),
            contradicting_evidence=_text_tuple(
                (*existing.contradicting_evidence, *event.contradicting_evidence)
            ),
            transmission_path=existing.transmission_path or event.transmission_path,
            validation_signals=_text_tuple(
                (*existing.validation_signals, *event.validation_signals)
            ),
            invalidation_signals=_text_tuple(
                (*existing.invalidation_signals, *event.invalidation_signals)
            ),
            summary=existing.summary or event.summary,
        )
    return tuple(merged)


def _newer_event(left: CatalystEvent, right: CatalystEvent) -> CatalystEvent:
    """Choose the event carrying the newest auditable publication timestamp."""

    left_dt = _parse_published_datetime(left.published_at)
    right_dt = _parse_published_datetime(right.published_at)
    if left_dt is None and right_dt is not None:
        return right
    if right_dt is None or left_dt is not None and left_dt >= right_dt:
        return left
    return right


def _source_names(*sources: str) -> tuple[str, ...]:
    names: list[str] = []
    for source in sources:
        for name in str(source or "").split("、"):
            clean = name.strip()
            if clean and clean not in names:
                names.append(clean)
    return tuple(names)


def _review_events(
    events: Sequence[CatalystEvent],
    *,
    enable_llm: bool,
    timeout_seconds: float,
    max_events: int,
) -> tuple[CatalystEvent, ...]:
    if not enable_llm or not events:
        return tuple(events)
    from aqsp.utils.llm_safe import llm_call_or_fallback

    reviewed: list[CatalystEvent] = []
    review_limit = max(0, max_events)
    for event in events[:review_limit]:
        prompt = (
            "判断下面新闻标题是否属于短线高影响事件。"
            "只输出一行：可信度=0-100; 影响=利好/利空/中性。"
            "标题党、传闻、缺少原始来源时降低可信度。\n"
            f"标题: {event.title}\n来源: {event.source}\n类型: {event.category}\n"
        )
        fallback = f"可信度={event.confidence:.0%}; 影响={event.impact}"
        result = llm_call_or_fallback(
            prompt=prompt,
            fallback=fallback,
            enable_llm=True,
            caller="news_catalyst_review",
            timeout_s=max(1.0, timeout_seconds),
        )
        reviewed.append(
            CatalystEvent(
                **{
                    **event.__dict__,
                    "llm_review": result.text[:160],
                    "verification": event.verification,
                }
            )
        )
    reviewed.extend(events[review_limit:])
    return tuple(reviewed)


def _base_confidence(row: dict[str, str]) -> float:
    title = row.get("title", "")
    source = row.get("source", "")
    confidence = 0.38
    if _contains_source_token(source, _AUTHORITATIVE_SOURCE_TOKENS):
        confidence += 0.28
    elif _contains_source_token(source, _MEDIA_SOURCE_TOKENS):
        confidence += 0.12
    if any(token in title for token in ("据悉", "传", "网传", "市场消息", "消息人士")):
        confidence -= 0.18
    if row.get("url"):
        confidence += 0.08
    return max(0.05, min(0.95, confidence))


def _verification_label(row: dict[str, str]) -> str:
    source = row.get("source", "")
    if _contains_source_token(source, _AUTHORITATIVE_SOURCE_TOKENS):
        return "接近原始来源"
    if _contains_source_token(source, _MEDIA_SOURCE_TOKENS):
        return "媒体来源"
    return "待证实"


def _events_can_merge(left: CatalystEvent, right: CatalystEvent) -> bool:
    left_title = _normalized_title_key(left.title)
    right_title = _normalized_title_key(right.title)
    if left.symbol and right.symbol and left.symbol != right.symbol:
        return False
    if left_title and left_title == right_title:
        return True
    if left.impact != right.impact or left.category != right.category:
        return False
    left_target = _event_target(left)
    right_target = _event_target(right)
    if left_target == "市场/行业" or right_target == "市场/行业":
        return False
    if left_target != right_target:
        return False
    return _title_overlap_ratio(left_title, right_title) >= 0.62


def _normalized_title_key(title: str) -> str:
    text = str(title or "").casefold()
    text = re.sub(
        r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}年第\d+次", "", text
    )
    text = re.sub(r"召开|定于|公司|股份|购买资产|募集配套资金", "", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)
    return text[:80]


def _title_overlap_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_tokens = set(left)
    right_tokens = set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(
        1, min(len(left_tokens), len(right_tokens))
    )


def _dedupe_texts(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return tuple(out)


def _parse_llm_confidence(text: str) -> float | None:
    match = re.search(r"可信度\s*[=:：]\s*(\d{1,3})", text)
    if not match:
        return None
    value = max(0, min(100, int(match.group(1))))
    return value / 100


def _parse_published_day(raw: str) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    iso_like = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_like)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
        return parsed.astimezone(_SHANGHAI_TZ).date()
    except ValueError:
        pass
    try:
        parsed = pd.to_datetime(text, errors="raise")
        if isinstance(parsed, pd.Timestamp):
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(_SHANGHAI_TZ)
            else:
                parsed = parsed.tz_convert(_SHANGHAI_TZ)
            return parsed.date()
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(iso_like[:10])
    except ValueError:
        pass
    match = re.search(r"\b(20\d{2})[-/_](\d{1,2})[-/_](\d{1,2})\b", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    compact = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", text)
    if compact:
        try:
            return date(
                int(compact.group(1)),
                int(compact.group(2)),
                int(compact.group(3)),
            )
        except ValueError:
            return None
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_published_datetime(raw: str) -> datetime | None:
    """Parse a publication timestamp and normalize it to Asia/Shanghai."""

    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            candidate = pd.to_datetime(text, errors="raise")
        except (TypeError, ValueError):
            return None
        if not isinstance(candidate, pd.Timestamp):
            return None
        parsed = candidate.to_pydatetime()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
    return parsed.astimezone(_SHANGHAI_TZ)


def _row_published_datetime(row: dict[str, str]) -> datetime | None:
    published_at = str(row.get("published_at", "") or "").strip()
    parsed = _parse_published_datetime(published_at)
    if parsed is not None:
        return parsed
    published_day = _row_published_day(row)
    if published_day is None:
        return None
    return datetime.combine(published_day, datetime.min.time()).replace(
        tzinfo=_SHANGHAI_TZ
    )


def _filter_recent_news_rows(
    rows: Iterable[dict[str, str]],
    *,
    today: date,
    max_age_days: int,
    allow_undated: bool,
    limit: int | None = None,
    now: datetime | None = None,
) -> tuple[tuple[dict[str, str], ...], int, int, int]:
    age_limit = max(0, int(max_age_days))
    filtered: list[dict[str, str]] = []
    stale_count = 0
    undated_count = 0
    future_count = 0
    observed_at = now or now_shanghai()
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=_SHANGHAI_TZ)
    else:
        observed_at = observed_at.astimezone(_SHANGHAI_TZ)
    for row in rows:
        published_day = _row_published_day(row)
        if published_day is None:
            if not allow_undated:
                undated_count += 1
                continue
            filtered.append(row)
            if limit is not None and len(filtered) >= max(0, limit):
                break
            continue
        published_at = _row_published_datetime(row)
        if published_at is not None and published_at > observed_at:
            future_count += 1
            if published_day > today:
                stale_count += 1
            continue
        if published_day > today:
            stale_count += 1
            continue
        if (today - published_day).days > age_limit:
            stale_count += 1
            continue
        filtered.append(row)
        if limit is not None and len(filtered) >= max(0, limit):
            break
    return tuple(filtered), stale_count, undated_count, future_count


def _event_recency_rank(event: CatalystEvent) -> int:
    published_day = _row_published_day(
        {"published_at": event.published_at, "title": event.title, "url": event.url}
    )
    if published_day is None:
        return -999999
    return published_day.toordinal()


def _event_rank_key(event: CatalystEvent) -> tuple[int, int, int, float, int]:
    return (
        int(event.weight),
        int(event.source_quality_score),
        _event_recency_rank(event),
        float(event.confidence),
        int(event.source_count),
    )


def _build_event_inference(*, impact: Impact, category: str, target: str) -> str:
    if impact == "negative":
        return f"{target} 风险抬升，短线回避 {category} 方向。"
    if category in {"涨价/供需催化", "供给收缩"}:
        return f"{target} 催化强化，短线关注度上升。"
    if category in {"订单/需求验证", "业绩催化", "资本运作", "政策催化"}:
        return f"{target} 交易催化明确，短线偏强。"
    return f"{target} 关注度抬升，等待后续确认。"


def _event_impact_summary(event: CatalystEvent) -> str:
    if event.impact == "negative":
        return "短线偏空"
    if event.impact == "positive":
        return "短线偏多"
    return "中性观察"


def _inline(value: object) -> str:
    """把字段压成单行文本，去掉换行；保留竖线无害（不再走表格）。"""
    return str(value or "").replace("\n", " ").strip() or "-"


def _safe_warning(value: object) -> str:
    text = _inline(value).replace("<", "＜").replace(">", "＞")
    lower = text.lower()
    if (
        "httpsconnectionpool" in lower
        or "remote end closed connection" in lower
        or "connection aborted" in lower
        or "max retries exceeded" in lower
        or "read timed out" in lower
        or "timed out" in lower
    ):
        return "部分消息源超时或连接中断，已降级使用其它来源"
    return text[:120] + ("..." if len(text) > 120 else "")


def _display_warnings(warnings: Sequence[str], limit: int = 3) -> tuple[str, ...]:
    displayed: list[str] = []
    timeout_seen = False
    for warning in warnings:
        text = _safe_warning(warning)
        if "消息源超过" in text or "超时" in text or "连接中断" in text:
            if timeout_seen:
                continue
            text = "部分消息源超时或连接中断，已降级使用其它来源"
            timeout_seen = True
        if text and text not in displayed:
            displayed.append(text)
        if len(displayed) >= limit:
            break
    return tuple(displayed)


def _short_text(value: object, max_chars: int) -> str:
    text = _inline(value)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _frame_warnings(df: pd.DataFrame, *, prefix: str) -> list[str]:
    warnings = (
        getattr(df, "attrs", {}).get("aqsp_warnings", ()) if df is not None else ()
    )
    result = [f"{prefix}: {warning}" for warning in warnings]
    result.extend(
        f"{prefix}: {warning}" for warning in _news_quality_warnings(df)
    )
    return result


def _news_quality_warnings(df: pd.DataFrame) -> tuple[str, ...]:
    """Report malformed source rows without treating a reachable source as down."""

    if df is None or df.empty:
        return ()
    rows = df.to_dict(orient="records")
    is_rss = any(
        _first_text(row, ("source_group", "source_type")) == "rss"
        for row in rows
    )
    if not is_rss:
        return ()
    missing_summary = 0
    missing_source = 0
    for row in rows:
        title = _first_text(row, ("标题", "title", "新闻标题"))
        if not title:
            continue
        source = _first_text(
            row,
            (
                "文章来源",
                "source",
                "来源",
                "source_name",
                "publisher",
            ),
        )
        summary = _first_text(
            row,
            ("summary", "摘要", "新闻摘要", "description", "描述", "content"),
        )
        missing_source += int(not source)
        missing_summary += int(not summary)
    warnings: list[str] = []
    if missing_summary:
        warnings.append(f"{missing_summary} 条消息缺少摘要，已过滤")
    if missing_source:
        warnings.append(f"{missing_source} 条消息缺少来源，已过滤")
    return tuple(warnings)


def _call_fetcher_with_timeout(
    fetch: Callable[[], pd.DataFrame],
    *,
    timeout_seconds: float,
) -> pd.DataFrame:
    if threading.current_thread() is threading.main_thread() and hasattr(
        signal,
        "SIGALRM",
    ):
        return _call_fetcher_with_signal_timeout(
            fetch,
            timeout_seconds=timeout_seconds,
        )
    result = _run_fetchers_with_deadline(
        {"single": fetch},
        timeout_seconds=float(timeout_seconds),
    )["single"]
    if result.error is not None:
        raise result.error
    return result.frame


def _call_fetcher_with_signal_timeout(
    fetch: Callable[[], pd.DataFrame],
    *,
    timeout_seconds: float,
) -> pd.DataFrame:
    timeout = max(0.1, float(timeout_seconds))

    def _raise_timeout(_signum, _frame) -> None:
        raise TimeoutError(f"消息源超过 {timeout_seconds:.1f}s 未返回")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        result = fetch()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    if result is None:
        return pd.DataFrame()
    return result


def _fetch_optional_frame(
    fetch: Callable[[], pd.DataFrame], timeout_seconds: float
) -> tuple[pd.DataFrame, str]:
    try:
        return _call_fetcher_with_timeout(fetch, timeout_seconds=timeout_seconds), ""
    except Exception as exc:
        return pd.DataFrame(), str(exc)


def _akshare_symbol_news(
    symbol: str,
    limit: int,
    *,
    timeout_seconds: float = 6.0,
) -> pd.DataFrame:
    akshare_news = _get_akshare_news_source()
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    fetched, warning = _fetch_optional_frame(
        lambda: akshare_news.fetch_symbol_news(symbol),
        timeout_seconds=timeout_seconds,
    )
    if warning:
        warnings.append(warning)
    for df in fetched if isinstance(fetched, list) else []:
        if df is not None and not df.empty:
            frames.append(df.head(limit))
    if not frames:
        empty = pd.DataFrame()
        empty.attrs["aqsp_warnings"] = tuple(warnings)
        health = getattr(akshare_news, "last_health", ())
        if health:
            empty.attrs["aqsp_source_health"] = tuple(health)
        return empty
    result = pd.concat(frames, ignore_index=True).head(limit * 3)
    result.attrs["aqsp_warnings"] = _merge_frame_warnings(frames, warnings)[:3]
    result.attrs["aqsp_source_health"] = _merge_frame_source_health(
        frames,
        getattr(akshare_news, "last_health", ()),
    )
    return result


def _akshare_global_news(
    limit: int,
    *,
    timeout_seconds: float = 6.0,
) -> pd.DataFrame:
    akshare_news = _get_akshare_news_source()
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    per_source_limit = max(2, min(5, limit))
    fetched, warning = _fetch_optional_frame(
        akshare_news.fetch_global_news,
        timeout_seconds=timeout_seconds,
    )
    if warning:
        warnings.append(warning)
    for df in fetched if isinstance(fetched, list) else []:
        if df is not None and not df.empty:
            frames.append(_prioritize_news_frame(df).head(per_source_limit))
    if not frames:
        empty = pd.DataFrame()
        empty.attrs["aqsp_warnings"] = tuple(warnings)
        health = getattr(akshare_news, "last_health", ())
        if health:
            empty.attrs["aqsp_source_health"] = tuple(health)
        return empty
    result = _prioritize_news_frame(pd.concat(frames, ignore_index=True)).head(limit)
    result.attrs["aqsp_warnings"] = _merge_frame_warnings(frames, warnings)[:3]
    result.attrs["aqsp_source_health"] = _merge_frame_source_health(
        frames,
        getattr(akshare_news, "last_health", ()),
    )
    return result


def _merge_frame_warnings(
    frames: Sequence[pd.DataFrame],
    warnings: Sequence[str],
) -> tuple[str, ...]:
    merged = list(warnings)
    for frame in frames:
        merged.extend(getattr(frame, "attrs", {}).get("aqsp_warnings", ()))
    return _dedupe_texts(merged)


def _merge_frame_source_health(
    frames: Sequence[pd.DataFrame],
    fallback: Sequence[NewsSourceHealth],
) -> tuple[NewsSourceHealth, ...]:
    merged: list[NewsSourceHealth] = []
    for frame in frames:
        merged.extend(_source_health_from_frame(frame))
    if not merged:
        merged.extend(fallback)
    deduped: list[NewsSourceHealth] = []
    seen: set[tuple[str, str, str]] = set()
    for item in merged:
        key = (item.name, item.region, item.status)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return tuple(deduped)


def _row_published_day(row: dict[str, str]) -> date | None:
    published_day = _parse_published_day(row.get("published_at", ""))
    if published_day is not None:
        return published_day
    title_day = _parse_published_day(row.get("title", ""))
    if title_day is not None:
        return title_day
    return _parse_published_day(row.get("url", ""))


def _sorted_news_rows(rows: Iterable[dict[str, str]]) -> tuple[dict[str, str], ...]:
    materialized = tuple(rows)
    return tuple(
        sorted(
            materialized,
            key=lambda row: (
                _row_published_day(row) is not None,
                (_row_published_day(row) or date.min).toordinal(),
            ),
            reverse=True,
        )
    )


def _prioritize_news_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    def priority(row: dict[str, Any]) -> int:
        source_text = _first_text(
            row, ("文章来源", "媒体", "source", "来源", "公告类型")
        )
        title = _first_text(
            row, ("新闻标题", "公告标题", "标题", "title", "内容", "摘要")
        )
        blob = f"{source_text} {title}"
        if any(token in blob for token in _AUTHORITATIVE_SOURCE_TOKENS):
            return 0
        if any(
            token in blob for token in ("新华社", "央视", "国常会", "发改委", "工信部")
        ):
            return 1
        if any(token in blob for token in _MEDIA_SOURCE_TOKENS):
            return 2
        return 3

    rows = df.to_dict(orient="records")
    def published_day(row: dict[str, Any]) -> date:
        raw = _first_text(
            row,
            (
                "发布时间",
                "时间",
                "date",
                "日期",
                "公告日期",
                "公告时间",
                "发布日期",
                "发稿时间",
                "published_at",
                "pub_time",
                "pubDate",
                "display_time",
            ),
        )
        return _parse_published_day(raw) or date.min

    ordered = sorted(
        enumerate(rows),
        key=lambda item: (priority(item[1]), -published_day(item[1]).toordinal(), item[0]),
    )
    result = pd.DataFrame([row for _, row in ordered])
    result.attrs.update(getattr(df, "attrs", {}))
    return result
