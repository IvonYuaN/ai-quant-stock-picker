"""Special-strategy and intraday overlay helpers extracted from ``cli.py``.

Contains runtime source workload guards, special-strategy ledger/freshness
checks, special-strategy run metadata construction, special-strategy runtime
readiness checks, intraday frame fetching with overlay, intraday coverage
inspection, intraday source summarization, forced observation for partial
coverage, protection observation boundary application, and relevant missing
symbol filtering.

All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from typing import Any

import pandas as pd

from aqsp.cli_regime_helpers import _detect_runtime_regime
from aqsp.cli_runtime_source_helpers import (
    _build_sqlite_db_source,
    _fetch_frames_for_cli_with_metadata,
    _get_source,
)
from aqsp.core.errors import DataError, MissingDataError
from aqsp.core.time import (
    get_previous_trading_day,
    now_shanghai,
    today_shanghai,
)
from aqsp.core.types import RunMetadata
from aqsp.data import IntradayService, fetch_with_source
from aqsp.data.source_health import describe_source_health
from aqsp.data.source_readiness import (
    source_role_for_workload,
    source_supports_workload,
    workload_guard_message,
)
from aqsp.freshness import assert_fresh_data, latest_trade_date
from aqsp.models import PickResult

LOGGER = logging.getLogger(__name__)


def _runtime_source_workload_allowed(
    source_name: str,
    *,
    workload: str,
) -> tuple[bool, str]:
    if source_supports_workload(source_name, workload):  # type: ignore[arg-type]
        return True, ""
    return False, workload_guard_message(source_name, workload)  # type: ignore[arg-type]


def _runtime_actual_source_workload_allowed(
    requested_source: str,
    actual_source: str,
    *,
    workload: str,
) -> tuple[bool, str]:
    resolved_actual = (actual_source or requested_source).strip()
    allowed, reason = _runtime_source_workload_allowed(
        resolved_actual,
        workload=workload,
    )
    if workload == "live_short" and resolved_actual not in {
        "auto",
        "local_first",
        "online_first",
        "multi",
    }:
        role = source_role_for_workload(resolved_actual, "live_short")
        if role != "realtime":
            reason = (
                f"数据源 {resolved_actual} 仅可作为 {role or 'unknown'} 层，"
                "不能形成正式 live_short 候选"
            )
            requested = requested_source.strip()
            if requested and requested != resolved_actual:
                return False, f"请求源 {requested} 实际落到 {resolved_actual}；{reason}"
            return False, reason
    if allowed:
        return True, ""
    requested = requested_source.strip()
    if requested and requested != resolved_actual:
        return False, f"请求源 {requested} 实际落到 {resolved_actual}；{reason}"
    return False, reason


def _special_strategy_ledger_write_allowed(
    frames: dict[str, pd.DataFrame],
    *,
    max_data_lag_days: int,
) -> tuple[bool, str]:
    from aqsp.core.time import is_trading_day

    today = today_shanghai()
    if not is_trading_day(today):
        return False, f"{today.isoformat()} 非交易日"
    try:
        latest = assert_fresh_data(
            frames,
            max_data_lag_days,
            workload="live_short",
        )
    except Exception as exc:
        return False, f"数据新鲜度未通过: {exc}"
    return True, f"latest={latest.isoformat()}"


def _special_strategy_run_metadata(
    *,
    requested_source: str,
    actual_source: str,
    frames: dict[str, pd.DataFrame],
    thresholds_version: str,
    task_id: str,
) -> RunMetadata:
    """Build the same provenance envelope used by the main runtime ledger."""
    from aqsp.cli import _runtime_data_lag_days, _source_runtime_metadata

    latest = latest_trade_date(frames)
    freshness_tier, coverage_tier, source_local_status = _source_runtime_metadata(
        actual_source,
        latest_trade_day=latest,
    )
    health_label, health_message, fallback_used = describe_source_health(
        requested_source,
        actual_source,
    )
    return RunMetadata(
        requested_source=requested_source,
        actual_source=actual_source,
        source_freshness_tier=freshness_tier,
        source_coverage_tier=coverage_tier,
        source_local_status=source_local_status,
        source_health_label=health_label,
        source_health_message=health_message,
        fallback_used=fallback_used,
        explicit_symbol_count=len(frames),
        resolved_symbol_count=len(frames),
        fetched_frame_count=len(frames),
        screened_count=0,
        final_count=0,
        min_price=0.0,
        max_price=0.0,
        min_avg_amount=0.0,
        online_factors_enabled=False,
        thresholds_version=thresholds_version,
        data_latest_trade_date=latest.isoformat() if latest is not None else "",
        data_lag_days=_runtime_data_lag_days(latest),
        regime="",
        task_id=task_id,
        workload="live_short",
    )


def _special_strategy_runtime_ready(
    *,
    strategy: Any,
    frames: dict[str, pd.DataFrame],
    benchmark_symbol: str | None,
) -> tuple[bool, str, str]:
    threshold_config = getattr(strategy, "mb", None) or getattr(strategy, "cfg", None)
    if threshold_config is not None and not bool(
        getattr(threshold_config, "enabled", True)
    ):
        return False, "", "策略已禁用"
    regime = _detect_runtime_regime(
        frames,
        benchmark_symbol=benchmark_symbol,
        thresholds=getattr(strategy, "thresholds", None),
    )
    required = tuple(getattr(strategy, "regime_required", ()) or ())
    if required and regime not in required:
        regime_text = regime or "unknown"
        return False, regime, f"市场状态不匹配: {regime_text} not in {required}"
    return True, regime, regime or "ok"


def _fetch_special_strategy_frames(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    days: int = 250,
    intraday_period: str = "5",
) -> tuple[dict[str, pd.DataFrame], str]:
    allowed, reason = _runtime_source_workload_allowed(
        source_name,
        workload="live_short",
    )
    if not allowed:
        raise DataError(reason)
    target_day = today_shanghai()
    try:
        frames, actual_source = _fetch_frames_for_cli_with_metadata(
            source_name,
            symbols,
            benchmark_symbol=benchmark_symbol,
            days=days,
            workload="live_short",
        )
    except DataError as live_error:
        frames = _fetch_intraday_historical_base(
            symbols,
            benchmark_symbol=benchmark_symbol,
            days=days,
            target_day=target_day,
        )
        actual_source = source_name
        LOGGER.warning(
            "盘中在线日线不可用，使用 sqlite 历史底座等待实时覆盖: %s", live_error
        )
    else:
        actual_allowed, actual_reason = _runtime_actual_source_workload_allowed(
            source_name,
            actual_source,
            workload="live_short",
        )
        if not actual_allowed:
            raise DataError(actual_reason)
        frames = {
            symbol: frame
            for symbol, frame in frames.items()
            if not frame.empty
            and "date" in frame.columns
            and pd.to_datetime(frame["date"], errors="coerce").max().date()
            >= target_day
        }
    if not frames:
        frames = _fetch_intraday_historical_base(
            symbols,
            benchmark_symbol=benchmark_symbol,
            days=days,
            target_day=target_day,
        )
        actual_source = source_name
        LOGGER.warning("盘中在线日线为空，使用 sqlite 历史底座等待实时覆盖")
    if not frames:
        raise MissingDataError(symbols[0], reason="无法获取历史日线数据")
    data_source = _get_source(source_name)
    intraday_service = IntradayService(data_source)
    overlay_symbols = list(symbols)
    if benchmark_symbol and benchmark_symbol not in overlay_symbols:
        overlay_symbols.append(benchmark_symbol)
    overlay = intraday_service.merge_intraday_bar_into_daily_with_coverage(
        frames,
        overlay_symbols,
        period=intraday_period,
        target_date=today_shanghai(),
        index_symbols=(benchmark_symbol,) if benchmark_symbol else (),
    )
    coverage = {
        "status": "complete" if overlay.complete else "partial",
        "requested_symbols": overlay.requested_symbols,
        "covered_symbols": overlay.covered_symbols,
        "missing_symbols": overlay.missing_symbols,
    }
    # A failed intraday overlay must not erase a valid live daily batch. Keep
    # the daily frames, expose the overlay gap, and let the downstream quality
    # boundary downgrade affected candidates explicitly.
    # Preserve the prior completed-day base for symbols without a live bar so
    # they can be explicitly downgraded to observation-only downstream.
    output_frames = {**frames, **overlay.frames}
    for frame in output_frames.values():
        frame.attrs["intraday_overlay_coverage"] = coverage
    return output_frames, _intraday_actual_source(overlay.frames, actual_source)


def _fetch_intraday_historical_base(
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    days: int,
    target_day: date,
) -> dict[str, pd.DataFrame]:
    """Load only completed historical bars for a live intraday overlay fallback."""
    historical_source = _build_sqlite_db_source(cache=None)
    base = fetch_with_source(
        historical_source,
        symbols,
        days=days,
        benchmark_symbol=benchmark_symbol,
        end_date=get_previous_trading_day(target_day),
    )
    fetched_at = now_shanghai().isoformat()
    for frame in base.values():
        frame.attrs.update(
            {
                "source_name": "sqlite_db",
                "source": "sqlite_db",
                "workload": "historical_base",
                "fetched_at": fetched_at,
                "timestamp_source": "sqlite_trade_date",
                "freshness": "historical",
            }
        )
    return base


def _intraday_overlay_coverage(
    frames: dict[str, pd.DataFrame],
) -> tuple[str, tuple[str, ...]]:
    for frame in frames.values():
        coverage = frame.attrs.get("intraday_overlay_coverage")
        if not isinstance(coverage, dict):
            continue
        missing = tuple(
            str(item) for item in coverage.get("missing_symbols", ()) if str(item)
        )
        status = str(coverage.get("status", "partial" if missing else "complete"))
        return status, missing
    return "not_available", ()


def _intraday_actual_source(
    frames: dict[str, pd.DataFrame],
    fallback: str,
) -> str:
    """Summarize the sources that supplied the current-day overlay."""
    sources = {
        str(frame.attrs.get("source_name", "") or "").strip()
        for frame in frames.values()
        if str(frame.attrs.get("source_name", "") or "").strip()
    }
    if len(sources) == 1:
        return next(iter(sources))
    if len(sources) > 1:
        return "multi"
    return fallback


def _force_intraday_observation(
    picks: list[PickResult],
    *,
    missing_symbols: tuple[str, ...],
    benchmark_symbol: str = "000300",
) -> list[PickResult]:
    """Keep deterministic scores visible while forbidding partial-live recommendations."""
    if not picks or not missing_symbols:
        return picks
    benchmark = str(benchmark_symbol or "000300").strip()
    benchmark_missing = benchmark in missing_symbols
    observed: list[PickResult] = []
    for pick in picks:
        pick_missing = tuple(
            symbol for symbol in missing_symbols if symbol == str(pick.symbol).strip()
        )
        if not benchmark_missing and not pick_missing:
            observed.append(pick)
            continue
        reason_symbols = (benchmark,) if benchmark_missing else pick_missing
        reason = "盘中覆盖不完整，缺少: " + "、".join(reason_symbols)
        metrics = dict(pick.metrics)
        alerts = tuple(metrics.get("data_quality_alerts", ()) or ())
        metrics.update(
            {
                "observation_only": True,
                "intraday_coverage_status": "partial",
                "intraday_missing_symbols": missing_symbols,
                "data_quality_status": "critical",
                "data_quality_alerts": tuple(dict.fromkeys((*alerts, reason))),
                "candidate_status": "盘中覆盖观察",
                "candidate_blocker": reason,
                "candidate_review_priority": "low",
                "candidate_next_step": "补齐全部盘中报价后，再重新评估纸面复核",
                "candidate_review_window": "盘中数据覆盖完整后",
                "portfolio_action": "observation_only",
            }
        )
        risks = tuple(dict.fromkeys((*pick.risks, reason)))
        observed.append(replace(pick, metrics=metrics, risks=risks))
    return observed


def _apply_protection_observation_boundary(
    picks: list[PickResult],
    *,
    reason: str,
) -> list[PickResult]:
    """Keep research candidates intact while limiting only paper actions.

    Portfolio protection is not evidence about the current quote or signal. It
    must therefore not rewrite a candidate into a low-priority observation or
    hide its deterministic research qualification.
    """
    if not picks:
        return picks
    clean_reason = str(reason or "组合保护已触发").strip()
    observed: list[PickResult] = []
    for pick in picks:
        metrics = dict(pick.metrics)
        if str(metrics.get("quality_gate_action", "clean")) == "blocked":
            observed.append(pick)
            continue
        alerts = tuple(metrics.get("quality_gate_reasons", ()) or ())
        is_recommendation = pick.rating in {
            "strong_buy_candidate",
            "buy_candidate",
        }
        metrics.update(
            {
                "observation_only": True,
                "paper_review_eligible": False,
                # Circuit-breaker state is a portfolio-action constraint. It
                # must not downgrade a fresh, deterministic research signal.
                "research_recommendation": is_recommendation,
                "candidate_status": ("实时推荐" if is_recommendation else "实时观察"),
                "candidate_next_step": ("按实时信号继续复核；组合保护仅限制纸面动作"),
                "candidate_review_window": "当前盘中窗口",
                "quality_gate_reasons": alerts,
                "portfolio_action": "observation_only",
            }
        )
        risks = tuple(
            dict.fromkeys((*pick.risks, f"组合保护仅限制纸面动作: {clean_reason}"))
        )
        observed.append(replace(pick, metrics=metrics, risks=risks))
    return observed


def _relevant_intraday_missing_symbols(
    picks: list[PickResult],
    *,
    missing_symbols: tuple[str, ...],
    benchmark_symbol: str,
) -> tuple[str, ...]:
    """Only candidate or benchmark gaps can block a live recommendation batch."""
    candidate_symbols = {str(pick.symbol).strip() for pick in picks if pick.symbol}
    benchmark = str(benchmark_symbol or "").strip()
    return tuple(
        symbol
        for symbol in missing_symbols
        if symbol in candidate_symbols or (benchmark and symbol == benchmark)
    )
