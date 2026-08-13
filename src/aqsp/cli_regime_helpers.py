"""Regime detection and execution summary helpers extracted from ``cli.py``.

Contains synthetic regime frame construction, runtime regime detection,
base/regime score blending, runtime weight snapshots, T+1 blocker and market
context summary augmentation, execution summary lines, audit action resolution,
execution preview construction, run decision logging, and sector concentration
checks with runtime hints.

All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from typing import Any

import pandas as pd

from aqsp.briefing.debate import DebateResult
from aqsp.cli_candidate_helpers import _candidate_blocker_map, _candidate_review_map
from aqsp.cli_runtime_catalyst_helpers import _is_high_frequency_task
from aqsp.core.time import now_shanghai
from aqsp.core.types import RunMetadata
from aqsp.models import PickResult
from aqsp.presentation import format_symbol_name
from aqsp.regime import (
    build_synthetic_regime_frame,
    detect_runtime_regime,
    detect_runtime_regime_context,
    format_runtime_regime_lines,
)

LOGGER = logging.getLogger(__name__)

# Keep the intraday catalyst fan-out bounded even when the final screen limit
# is large. These are resource guards, not scoring or recommendation thresholds.
_INTRADAY_CATALYST_PREVIEW_MIN = 3
_INTRADAY_CATALYST_PREVIEW_MAX = 5


def _build_synthetic_regime_frame(
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    return build_synthetic_regime_frame(frames)


def _detect_runtime_regime(
    frames: dict[str, pd.DataFrame],
    *,
    benchmark_symbol: str | None,
    thresholds: Any | None = None,
) -> str:
    return detect_runtime_regime(
        frames,
        benchmark_symbol=benchmark_symbol,
        thresholds=thresholds,
    )


def _runtime_regime_market_context_lines(
    frames: dict[str, pd.DataFrame],
    *,
    benchmark_symbol: str | None,
    thresholds: Any | None = None,
) -> tuple[str, ...]:
    return format_runtime_regime_lines(
        detect_runtime_regime_context(
            frames,
            benchmark_symbol=benchmark_symbol,
            thresholds=thresholds,
        )
    )


def _blend_base_and_regime_scores(
    *,
    base_score: float,
    regime_score: float,
    thresholds: Any,
) -> float:
    composite = thresholds.composite
    base_weight = float(composite.base_blend_weight)
    regime_weight = float(composite.regime_blend_weight)
    total_weight = base_weight + regime_weight
    if total_weight <= 0:
        return round(base_score, 2)
    blended = (base_score * base_weight + regime_score * regime_weight) / total_weight
    return round(blended, 2)


def _runtime_weight_snapshot(
    *,
    thresholds: Any,
    regime: str,
    strategy_weights: dict[str, float],
    strategy_weight_reasons: dict[str, str],
) -> dict[str, Any]:
    composite = thresholds.composite
    return {
        "source": "runtime_strategy_mix",
        "regime": regime,
        "strategy_weights": {
            str(key): round(float(value), 6)
            for key, value in sorted(strategy_weights.items())
        },
        "strategy_weight_reasons": {
            str(key): str(value)
            for key, value in sorted(strategy_weight_reasons.items())
        },
        "base_blend_weight": float(composite.base_blend_weight),
        "regime_blend_weight": float(composite.regime_blend_weight),
        "thresholds_version": str(thresholds.version),
    }


def _attach_runtime_weight_snapshot(
    picks: list[PickResult],
    *,
    thresholds: Any,
    regime: str,
    strategy_weights: dict[str, float],
    strategy_weight_reasons: dict[str, str],
) -> list[PickResult]:
    snapshot = _runtime_weight_snapshot(
        thresholds=thresholds,
        regime=regime,
        strategy_weights=strategy_weights,
        strategy_weight_reasons=strategy_weight_reasons,
    )
    return [
        replace(
            pick,
            metrics={
                **pick.metrics,
                "strategy_weight_snapshot": snapshot,
            },
        )
        for pick in picks
    ]


def _augment_summary_with_t1_blockers(
    summary: Any | None,
    *,
    removed_symbols: list[str],
    removed_name_map: dict[str, str],
) -> Any | None:
    if summary is None or not removed_symbols:
        return summary

    removed_displays = tuple(
        format_symbol_name(symbol, removed_name_map.get(symbol, ""))
        for symbol in removed_symbols
    )
    hotspot = "T+1 持仓约束：昨日已买标的今日不纳入纸面复核名单"
    blockers = tuple(
        f"{display}: T+1 持仓约束，昨日已买，今日仅保留观察"
        for display in removed_displays
    )
    existing_watchlist = tuple(getattr(summary, "watchlist", ()) or ())
    existing_hotspots = tuple(getattr(summary, "action_hotspots", ()) or ())
    existing_blockers = tuple(getattr(summary, "execution_blockers", ()) or ())
    merged_watchlist = tuple(
        dict.fromkeys(existing_watchlist + removed_displays).keys()
    )[:5]
    merged_hotspots = tuple(dict.fromkeys(existing_hotspots + (hotspot,)).keys())[:3]
    merged_blockers = tuple(dict.fromkeys(existing_blockers + blockers).keys())[:5]
    note = str(getattr(summary, "allocation_note", "") or "")
    t1_note = (
        f"T+1 限制：昨日已买 {len(removed_symbols)} 只"
        f"（{'、'.join(removed_symbols[:3])}）仅保留观察"
    )
    merged_note = f"{note}；{t1_note}" if note else t1_note
    return replace(
        summary,
        watchlist=merged_watchlist,
        action_hotspots=merged_hotspots,
        execution_blockers=merged_blockers,
        allocation_note=merged_note,
    )


def _augment_summary_with_market_context(
    summary: Any | None,
    *,
    market_context: Any | None,
) -> Any | None:
    if summary is None or market_context is None:
        return summary

    from aqsp.market_context import combine_cross_market_overview

    combined = combine_cross_market_overview(
        str(getattr(summary, "cross_market_overview", "") or ""),
        market_context,
    )
    if not combined:
        return summary
    return replace(summary, cross_market_overview=combined)


def _market_context_preview_count(
    limit: int,
    total: int,
    *,
    task_id: str = "",
) -> int:
    if total <= 0:
        return 0
    if not _is_high_frequency_task(task_id):
        return min(total, max(int(limit) * 2, 6))
    bounded_limit = max(0, int(limit))
    preview_count = min(
        max(bounded_limit, _INTRADAY_CATALYST_PREVIEW_MIN),
        _INTRADAY_CATALYST_PREVIEW_MAX,
    )
    return min(total, preview_count)


def _build_execution_summary_line(
    tradable: list[PickResult],
    portfolio_summary: Any | None,
) -> str:
    has_allocations = bool(getattr(portfolio_summary, "allocations", ()) or ())
    if tradable and has_allocations:
        top = tradable[0]
        return (
            f"🎯 **优先纸面复核**: {top.symbol} {top.name} | 评分 {top.score:.0f} | "
            f"观察参考 {top.ideal_buy} / 防守 {top.stop_loss} / 目标 {top.take_profit}"
        )
    watchlist = tuple(getattr(portfolio_summary, "watchlist", ()) or ())
    blockers = tuple(getattr(portfolio_summary, "execution_blockers", ()) or ())
    if watchlist:
        names = "、".join(watchlist[:2])
        return f"👀 **今日无纸面复核对象**，转入继续观察名单：{names}"
    if tradable:
        top = tradable[0]
        return (
            f"👀 **首位观察**: {top.symbol} {top.name} | 评分 {top.score:.0f} | "
            "等待 PM 阻塞解除"
        )
    if blockers:
        return "👀 **今日无纸面复核对象**，受纸面约束影响，暂仅观察。"
    return "👀 **今日无纸面复核对象**，仅观察。等待更强信号。"


def _resolve_audit_action(
    pick: PickResult,
    *,
    allocation_symbols: set[str],
) -> str:
    if pick.symbol in allocation_symbols:
        return "PAPER_REVIEW"
    return "SKIP"


def _build_execution_preview(
    pick: PickResult,
    *,
    frame: pd.DataFrame,
    action: str,
) -> dict[str, Any]:
    if action != "PAPER_REVIEW" or frame.empty:
        return {}

    recent_frame = frame.tail(20).copy()
    if "volume" not in recent_frame.columns or "close" not in recent_frame.columns:
        return {}

    avg_daily_volume = float(recent_frame["volume"].fillna(0).mean() or 0.0)
    estimated_price = float(pick.ideal_buy or pick.close or 0.0)
    if avg_daily_volume <= 0 or estimated_price <= 0:
        return {}

    from aqsp.execution.executor import ExecutionCoordinator

    coordinator = ExecutionCoordinator()
    plan = coordinator.plan_execution(
        symbol=pick.symbol,
        target_shares=100,
        avg_daily_volume=avg_daily_volume,
        estimated_price=estimated_price,
        is_sell=False,
    )
    return {
        "board_lot_shares": 100,
        "estimated_amount": round(estimated_price * 100, 2),
        "estimated_total_cost": round(plan.estimated_total_cost, 4),
        "estimated_cost_rate_pct": round(plan.estimated_cost_rate, 4),
        "twap_order_count": len(plan.twap_plan.orders),
        "plan_valid": bool(plan.is_valid),
        "validation_errors": list(plan.validation_errors),
    }


def _log_run_decisions(
    *,
    picks: list[PickResult],
    frames: dict[str, pd.DataFrame],
    debate_results: list[DebateResult],
    portfolio_summary: Any | None,
    circuit_breaker_triggered: bool,
    regime: str,
    run_metadata: RunMetadata,
) -> None:
    if not picks:
        return

    from aqsp.audit.trade_logger import TradeDecisionLog, TradeLogger

    allocation_symbols = {
        str(item.symbol)
        for item in tuple(getattr(portfolio_summary, "allocations", ()) or ())
    }
    blocker_map = _candidate_blocker_map(portfolio_summary)
    review_map = _candidate_review_map(portfolio_summary)
    debate_by_symbol = {result.symbol: result for result in debate_results}
    trade_logger = TradeLogger(log_dir=os.getenv("AQSP_TRADE_LOG_DIR", "logs/trades"))
    timestamp = now_shanghai()

    for pick in picks:
        action = _resolve_audit_action(
            pick,
            allocation_symbols=allocation_symbols,
        )
        review_meta = review_map.get(pick.symbol, {})
        blocker = blocker_map.get(pick.symbol, "")
        debate = debate_by_symbol.get(pick.symbol)
        execution_preview = _build_execution_preview(
            pick,
            frame=frames.get(pick.symbol, pd.DataFrame()),
            action=action,
        )
        reason_parts = [
            f"PM裁决 {str(pick.metrics.get('portfolio_action', '') or 'keep')}",
            f"评级 {pick.rating}",
        ]
        candidate_status = str(pick.metrics.get("candidate_status", "") or "").strip()
        if candidate_status:
            reason_parts.append(f"状态 {candidate_status}")
        if blocker:
            reason_parts.append(f"阻塞 {blocker}")

        context: dict[str, Any] = {
            "thresholds_version": run_metadata.thresholds_version,
            "signal_date": pick.date,
            "requested_source": run_metadata.requested_source,
            "actual_source": run_metadata.actual_source,
            "source_health_label": run_metadata.source_health_label,
            "source_health_message": run_metadata.source_health_message,
            "data_latest_trade_date": run_metadata.data_latest_trade_date,
            "data_lag_days": run_metadata.data_lag_days,
            "portfolio_action": str(pick.metrics.get("portfolio_action", "") or "keep"),
            "candidate_status": candidate_status,
            "candidate_blocker": blocker,
            "candidate_next_step": str(review_meta.get("next_step", "") or ""),
            "candidate_review_window": str(review_meta.get("review_window", "") or ""),
            "candidate_review_priority": str(review_meta.get("priority", "") or ""),
            "intended_entry": pick.entry_type,
            "ideal_buy": pick.ideal_buy,
            "stop_loss": pick.stop_loss,
            "take_profit": pick.take_profit,
            "paper_position": pick.position,
            "run_task_id": run_metadata.task_id,
        }
        if execution_preview:
            context["paper_execution_preview"] = execution_preview
        if debate is not None:
            context["debate_consensus"] = debate.final_consensus
            context["debate_adjustment"] = debate.recommended_adjustment
            context["debate_disagreement_score"] = debate.disagreement_score

        trade_logger.log_decision(
            TradeDecisionLog(
                timestamp=timestamp,
                symbol=pick.symbol,
                name=pick.name,
                action=action,
                score=float(pick.score),
                strategies=list(pick.strategies),
                debate_summary=(
                    str(debate.final_consensus)
                    if debate is not None
                    else "no_debate_attached"
                ),
                risk_check_passed=(
                    action == "PAPER_REVIEW"
                    and not circuit_breaker_triggered
                    and not blocker
                ),
                regime=regime or "unknown",
                reason="；".join(reason_parts),
                context=context,
            )
        )

    from aqsp.audit.decision_chain import append_decision_record, new_decision_record

    evidence_ids = tuple(
        sorted(
            {
                str(item)
                for pick in picks
                for item in tuple(pick.metrics.get("artifact_ids", ()) or ())
                if str(item).strip()
            }
        )
    )
    advisory_ids = tuple(
        sorted(result.debate_id for result in debate_results if result.debate_id)
    )
    append_decision_record(
        os.getenv("AQSP_DECISION_AUDIT_PATH", "data/audit/decision-chain.jsonl"),
        new_decision_record(
            run_id=f"{run_metadata.task_id or 'scheduled'}:{run_metadata.data_latest_trade_date}",
            thresholds_version=run_metadata.thresholds_version,
            regime=regime or "unknown",
            source=run_metadata.actual_source,
            candidates=tuple(
                {
                    "symbol": pick.symbol,
                    "score": round(float(pick.score), 4),
                    "deterministic_score": round(
                        float(
                            pick.metrics.get("deterministic_score", pick.score)
                            or pick.score
                        ),
                        4,
                    ),
                    "deterministic_score_unchanged": bool(
                        pick.metrics.get("deterministic_score_unchanged", True)
                    ),
                    "advisory_only": bool(pick.metrics.get("advisory_only", True)),
                    "rating": pick.rating,
                    "position": pick.position,
                    "strategies": tuple(pick.strategies),
                }
                for pick in picks
            ),
            evidence_ids=evidence_ids,
            advisory_ids=advisory_ids,
        ),
    )


def _check_sector_concentration_with_runtime_hints(
    symbols: list[str],
    *,
    max_concentration: float | None = None,
    sector_map: dict[str, str] | None = None,
    industry_map: dict[str, str] | None = None,
):
    from aqsp.portfolio.sector_check import check_sector_concentration

    try:
        return check_sector_concentration(
            symbols,
            max_concentration=max_concentration
            if max_concentration is not None
            else 0.4,
            sector_map=sector_map,
            industry_map=industry_map,
        )
    except TypeError:
        return check_sector_concentration(symbols)
