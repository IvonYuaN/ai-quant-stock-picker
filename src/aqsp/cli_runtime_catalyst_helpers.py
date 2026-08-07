"""Runtime catalyst & market-context helpers extracted from ``cli.py``.

Contains high-frequency task classification, catalyst cache/artifact
configuration, runtime catalyst report building, and market-context
payload loading — all driven by ``task_id`` (intraday / midday / daily).

All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from aqsp.config import load_debate_runtime_config
from aqsp.core.time import today_shanghai
from aqsp.goal_switches import goal_switch_enabled
from aqsp.models import PickResult

LOGGER = logging.getLogger(__name__)

_INTRADAY_CATALYST_THREAD_MODES = frozenset({"thread", "in_process", "same_process"})


def _is_high_frequency_task(task_id: str) -> bool:
    return str(task_id or "").strip().lower() in {"intraday", "midday"}


def _runtime_catalyst_isolate_external_sources(task_id: str) -> bool:
    """Keep fork isolation by default; allow an explicit HF thread fallback."""
    if sys.platform == "darwin":
        return False
    if not _is_high_frequency_task(task_id):
        return True

    mode = (
        str(os.getenv("AQSP_INTRADAY_CATALYST_FETCH_MODE", "process") or "process")
        .strip()
        .lower()
    )
    if mode in _INTRADAY_CATALYST_THREAD_MODES:
        return False
    return True


def _requires_intraday_overlay_task(task_id: str) -> bool:
    return str(task_id or "").strip().lower() in {"intraday", "midday", "live_short"}


def _effective_live_short_max_data_lag_days(
    configured_days: int,
    *,
    requires_live_short_source: bool,
    csv_path: str,
    as_of_raw: str = "",
) -> int:
    lag_days = max(0, int(configured_days))
    if not requires_live_short_source or str(csv_path or "").strip() or as_of_raw:
        return lag_days
    return min(lag_days, 1)


def _should_build_market_context(task_id: str) -> bool:
    normalized_task_id = str(task_id or "").strip().lower()
    if not _is_high_frequency_task(normalized_task_id):
        return True
    return goal_switch_enabled("live_short_runtime", default=True)


def _runtime_realtime_cross_market_payload(task_id: str) -> dict | None:
    """Load live macro context only for explicitly enabled short-term runs."""
    if not _is_high_frequency_task(task_id):
        return None
    enabled = str(os.getenv("AQSP_MARKET_CONTEXT_LIVE_SOURCE", "false")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    try:
        from aqsp.data.market_context_source import fetch_live_market_context_payload

        return fetch_live_market_context_payload(
            timeout_seconds=_market_context_source_timeout_seconds(task_id)
        )
    except Exception as exc:
        LOGGER.warning("实时跨市场上下文获取失败，保留不可用状态: %s", exc)
        return None


def _market_context_source_timeout_seconds(task_id: str) -> float:
    if _is_high_frequency_task(task_id):
        return 1.0
    return 4.0


def _runtime_catalyst_cache_path(task_id: str) -> str:
    configured = str(os.getenv("AQSP_CATALYST_REPORT_CACHE_PATH", "") or "").strip()
    if configured:
        return configured
    if _is_high_frequency_task(task_id):
        return "data/runtime/catalyst_report_cache.json"
    return ""


def _runtime_catalyst_cache_ttl_seconds(task_id: str) -> float:
    configured = str(
        os.getenv("AQSP_CATALYST_REPORT_CACHE_TTL_SECONDS", "") or ""
    ).strip()
    if configured:
        try:
            return max(0.0, float(configured))
        except ValueError:
            return 0.0
    if _is_high_frequency_task(task_id):
        return 120.0
    return 0.0


def _runtime_catalyst_allow_stale_cache_on_failure(task_id: str) -> bool:
    configured = str(
        os.getenv("AQSP_CATALYST_REPORT_ALLOW_STALE_CACHE", "") or ""
    ).strip()
    if configured.lower() in {"1", "true", "yes", "on"}:
        return True
    if configured.lower() in {"0", "false", "no", "off"}:
        return False
    return _is_high_frequency_task(task_id)


def _runtime_catalyst_max_stale_cache_age_seconds(task_id: str) -> float:
    configured = str(
        os.getenv("AQSP_CATALYST_REPORT_MAX_STALE_CACHE_AGE_SECONDS", "") or ""
    ).strip()
    if configured:
        try:
            return max(0.0, float(configured))
        except ValueError:
            return 30 * 60
    if _is_high_frequency_task(task_id):
        return 30 * 60
    return 0.0


def _runtime_catalyst_max_news_age_days(task_id: str) -> int:
    configured = str(
        os.getenv("AQSP_CATALYST_REPORT_MAX_NEWS_AGE_DAYS", "") or ""
    ).strip()
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            return 30
    if _is_high_frequency_task(task_id):
        return 5
    return 30


def _runtime_news_artifact_path(task_id: str) -> str:
    configured = str(os.getenv("AQSP_NEWS_JSON_OUTPUT", "") or "").strip()
    if configured:
        return configured
    return (
        "data/runtime/news_catalysts_latest.json"
        if _is_high_frequency_task(task_id)
        else ""
    )


def _runtime_news_artifact_max_age_seconds(task_id: str) -> float:
    configured = str(
        os.getenv("AQSP_NEWS_RUNTIME_ARTIFACT_MAX_AGE_SECONDS", "") or ""
    ).strip()
    if configured:
        try:
            return max(0.0, float(configured))
        except ValueError:
            return 0.0
    return 6 * 60 * 60 if _is_high_frequency_task(task_id) else 0.0


def _build_runtime_catalyst_report(
    picks: list[PickResult],
    *,
    task_id: str,
) -> Any | None:
    if not picks:
        return None

    from aqsp.news.catalysts import NewsCatalystConfig, build_catalyst_report

    source_timeout_seconds = _market_context_source_timeout_seconds(task_id)
    cache_path = _runtime_catalyst_cache_path(task_id)
    cache_ttl_seconds = _runtime_catalyst_cache_ttl_seconds(task_id)
    max_stale_cache_age_seconds = _runtime_catalyst_max_stale_cache_age_seconds(task_id)
    max_news_age_days = _runtime_catalyst_max_news_age_days(task_id)
    symbols = tuple(pick.symbol for pick in picks)
    symbol_names = {pick.symbol: pick.name for pick in picks}
    return build_catalyst_report(
        symbols=symbols,
        symbol_names=symbol_names,
        config=NewsCatalystConfig(
            symbols=symbols,
            max_symbol_news=3,
            max_global_news=6,
            max_events=4,
            max_news_age_days=max_news_age_days,
            source_timeout_seconds=source_timeout_seconds,
            enable_llm_review=False,
            cache_path=cache_path,
            cache_ttl_seconds=cache_ttl_seconds,
            max_stale_cache_age_seconds=max_stale_cache_age_seconds,
            allow_stale_cache_on_failure=_runtime_catalyst_allow_stale_cache_on_failure(
                task_id
            ),
            isolate_external_sources=_runtime_catalyst_isolate_external_sources(
                task_id
            ),
        ),
    )


def _filter_catalyst_report_for_symbols(
    report: Any | None,
    symbols: tuple[str, ...],
) -> Any | None:
    if report is None:
        return None

    from aqsp.news.catalysts import CatalystReport

    allowed_symbols = {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
    if not allowed_symbols:
        return report

    events = tuple(
        event
        for event in getattr(report, "events", ())
        if not getattr(event, "symbol", "")
        or getattr(event, "symbol", "") in allowed_symbols
    )
    return CatalystReport(
        date=str(getattr(report, "date", "")).strip(),
        generated_at=str(getattr(report, "generated_at", "")).strip(),
        events=events,
        source_status=str(getattr(report, "source_status", "")).strip(),
        warnings=tuple(getattr(report, "warnings", ()) or ()),
        source_statuses=tuple(getattr(report, "source_statuses", ()) or ()),
        event_status=str(getattr(report, "event_status", "") or ""),
        raw_news_count=int(getattr(report, "raw_news_count", 0) or 0),
        stale_news_count=int(getattr(report, "stale_news_count", 0) or 0),
        undated_news_count=int(getattr(report, "undated_news_count", 0) or 0),
        future_news_count=int(getattr(report, "future_news_count", 0) or 0),
    )


def _load_runtime_market_context_catalyst_report(
    *,
    preview_report: Any | None,
    preview_symbols: tuple[str, ...],
    picks: list[PickResult],
    task_id: str,
) -> Any | None:
    from aqsp.news.catalysts import load_catalyst_report_artifact

    debate_runtime = load_debate_runtime_config(task_id=task_id)
    target_picks = picks[: max(1, int(debate_runtime.max_candidates))]
    if not target_picks:
        return None

    target_symbols = tuple(pick.symbol for pick in target_picks)
    artifact_path = _runtime_news_artifact_path(task_id)
    if artifact_path:
        artifact = load_catalyst_report_artifact(
            artifact_path,
            expected_date=today_shanghai().isoformat(),
            max_age_seconds=_runtime_news_artifact_max_age_seconds(task_id),
        )
        if artifact is not None:
            return _filter_catalyst_report_for_symbols(artifact, target_symbols)
    if preview_report is not None and set(target_symbols).issubset(
        set(preview_symbols)
    ):
        return _filter_catalyst_report_for_symbols(preview_report, target_symbols)
    return _build_runtime_catalyst_report(target_picks, task_id=task_id)
