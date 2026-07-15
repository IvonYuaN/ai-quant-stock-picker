from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.core.types import RunMetadata
from aqsp.core.errors import DataError
from aqsp.models import PickResult
from aqsp.ratings import is_tradable_rating
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


TERMINAL_PREDICTION_STATUSES = frozenset(
    {
        "validated",
        "not_executable",
        "watch_only",
        "closed",
    }
)

FORMAL_LEDGER_WORKLOADS = frozenset(
    {
        "live_short",
        "intraday",
        "midday",
        "daily",
        "morning_breakout",
        "closing_premium",
        "closing_review",
        "briefing",
        "scheduled",
    }
)


@dataclass(frozen=True)
class ValidationSummary:
    checked: int
    wins: int
    avg_return_pct: float
    avg_excess_pct: float
    skipped_not_executable: int = 0
    not_executable_reasons: dict[str, int] | None = None
    strategy_not_executable_rates: dict[str, float] | None = None


@dataclass(frozen=True)
class ExecutionConfig:
    horizon_days: int = 3
    fee_bps: float = 3.0
    slippage_bps: float = 20.0
    benchmark_symbol: str = "000300"
    limit_up_pct: float = 0.10
    limit_down_pct: float = 0.10


def _normalize_strategies(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        raw = parsed if isinstance(parsed, list) else [value]
    elif not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def execution_config_from_thresholds(
    thresholds: object | None = None,
    *,
    horizon_days: int = 3,
    benchmark_symbol: str = "000300",
) -> ExecutionConfig:
    if thresholds is None:
        from aqsp.strategies.thresholds import load_thresholds

        thresholds = load_thresholds()
    execution = getattr(thresholds, "execution")
    main_limit = float(execution.fallback_limit_main_pct)
    return ExecutionConfig(
        horizon_days=horizon_days,
        fee_bps=float(execution.commission_rate) * 10000.0,
        slippage_bps=float(execution.slippage) * 10000.0,
        benchmark_symbol=benchmark_symbol,
        limit_up_pct=main_limit,
        limit_down_pct=main_limit,
    )


def read_ledger(path: str | Path) -> list[dict]:
    ledger = Path(path)
    if not ledger.exists():
        return []
    rows = []
    for lineno, line in enumerate(
        ledger.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            # 单行损坏（写入中断/磁盘满）不应让整个账本读取崩溃，
            # 跳过坏行并警告，保住其余有效记录。
            import logging

            logging.getLogger("aqsp.ledger").warning(
                "账本 %s 第 %d 行 JSON 损坏，已跳过: %s", ledger, lineno, exc
            )
    return rows


def ledger_rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def write_ledger(path: str | Path, rows: list[dict]) -> None:
    ledger = Path(path)
    text = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows
    )
    atomic_write_text(ledger, (text + "\n") if text else "")


def append_predictions(
    path: str | Path,
    picks: list[PickResult],
    execution: ExecutionConfig | None = None,
    thresholds_version: str = "",
    regime: str = "",
    northbound_flow_5d_z: float = 0.0,
    margin_balance_change_5d: float = 0.0,
    run_metadata: RunMetadata | None = None,
) -> None:
    _validate_formal_run_metadata(run_metadata)
    with advisory_lock(path):
        execution = execution or execution_config_from_thresholds()
        rows = read_ledger(path)
        row_index_by_key = {_prediction_key(row): idx for idx, row in enumerate(rows)}
        now = now_shanghai().isoformat(timespec="seconds")
        for pick in picks:
            strategies = list(pick.strategies)
            signal_day_group = pick.date
            row_key = (pick.date, pick.symbol, thresholds_version, regime, "next_open")
            prediction_fields = {
                "signal_date": pick.date,
                "symbol": pick.symbol,
                "name": pick.name,
                "signal_close": pick.close,
                "intended_entry": "next_open",
                "score": pick.score,
                "rating": pick.rating,
                "position": pick.position,
                "portfolio_action": str(pick.metrics.get("portfolio_action", "")),
                "candidate_status": str(pick.metrics.get("candidate_status", "") or ""),
                "candidate_blocker": str(
                    pick.metrics.get("candidate_blocker", "") or ""
                ),
                "candidate_next_step": str(
                    pick.metrics.get("candidate_next_step", "") or ""
                ),
                "candidate_review_window": str(
                    pick.metrics.get("candidate_review_window", "") or ""
                ),
                "candidate_review_priority": str(
                    pick.metrics.get("candidate_review_priority", "") or ""
                ),
                "data_quality_status": str(
                    pick.metrics.get("data_quality_status", "") or ""
                ),
                "data_quality_alerts": list(
                    pick.metrics.get("data_quality_alerts", ()) or ()
                ),
                "entry_type": pick.entry_type,
                "ideal_buy": pick.ideal_buy,
                "strategies": strategies,
                "reasons": list(pick.reasons),
                "risks": list(pick.risks),
                "stop_loss": pick.stop_loss,
                "stop_method": str(pick.metrics.get("stop_method", "")),
                "take_profit": pick.take_profit,
                "adjusted_score": pick.adjusted_score,
                "recommended_adjustment": pick.recommended_adjustment,
                "debate_consensus": pick.debate_consensus,
                "debate_id": str(pick.metrics.get("debate_id", "") or ""),
                "debate_disagreement_score": float(
                    pick.metrics.get("debate_disagreement_score", 0.0) or 0.0
                ),
                "debate_final_vote": dict(
                    pick.metrics.get("debate_final_vote", {}) or {}
                ),
                "debate_active_roles": list(
                    pick.metrics.get("debate_active_roles", ()) or ()
                ),
                "debate_active_role_summary": str(
                    pick.metrics.get("debate_active_role_summary", "") or ""
                ),
                "debate_role_selection_summary": str(
                    pick.metrics.get("debate_role_selection_summary", "") or ""
                ),
                "debate_role_selection_plan": str(
                    pick.metrics.get("debate_role_selection_plan", "") or ""
                ),
                "debate_research_verdict": str(
                    pick.metrics.get("debate_research_verdict", "") or ""
                ),
                "debate_primary_risk_gate": str(
                    pick.metrics.get("debate_primary_risk_gate", "") or ""
                ),
                "debate_next_trigger": str(
                    pick.metrics.get("debate_next_trigger", "") or ""
                ),
                "support_points": list(pick.metrics.get("support_points", ()) or ()),
                "opposition_points": list(
                    pick.metrics.get("opposition_points", ()) or ()
                ),
                "watch_items": list(pick.metrics.get("watch_items", ()) or ()),
                "role_reliability_lines": list(
                    pick.metrics.get("role_reliability_lines", ()) or ()
                ),
                "debate_historical_context_note": str(
                    pick.metrics.get("debate_historical_context_note", "") or ""
                ),
                "debate_historical_context_bucket": str(
                    pick.metrics.get("debate_historical_context_bucket", "") or ""
                ),
                "debate_historical_context_sample_count": int(
                    pick.metrics.get("debate_historical_context_sample_count", 0) or 0
                ),
                "debate_historical_context_accuracy": float(
                    pick.metrics.get("debate_historical_context_accuracy", 0.0) or 0.0
                ),
                "confidence": pick.confidence,
                "regime_score": pick.regime_score,
                "strategy_weight_snapshot": pick.metrics.get(
                    "strategy_weight_snapshot", {}
                ),
                "composite_score_raw": pick.metrics.get("composite_score_raw", 0.0),
                "composite_score_normalized": pick.metrics.get(
                    "composite_score_normalized", 0.0
                ),
                "base_score_before_composite": pick.metrics.get(
                    "base_score_before_composite", 0.0
                ),
                "final_score_after_composite": pick.metrics.get(
                    "final_score_after_composite", pick.score
                ),
                "deterministic_score": float(
                    pick.metrics.get("deterministic_score", pick.score) or pick.score
                ),
                "deterministic_score_unchanged": bool(
                    pick.metrics.get("deterministic_score_unchanged", True)
                ),
                "advisory_only": bool(pick.metrics.get("advisory_only", True)),
                "sector": str(pick.metrics.get("sector", "") or ""),
                "industry": str(pick.metrics.get("industry", "") or ""),
                "cross_market_primary_theme": str(
                    pick.metrics.get("cross_market_primary_theme", "") or ""
                ),
                "cross_market_linkage_basis": str(
                    pick.metrics.get("cross_market_linkage_basis", "") or ""
                ),
                "cross_market_action": str(
                    pick.metrics.get("cross_market_action", "") or ""
                ),
                "cross_market_strength": str(
                    pick.metrics.get("cross_market_strength", "") or ""
                ),
                "cross_market_lead_window": str(
                    pick.metrics.get("cross_market_lead_window", "") or ""
                ),
                "cross_market_observation_window": str(
                    pick.metrics.get("cross_market_observation_window", "") or ""
                ),
                "cross_market_priority_score": int(
                    pick.metrics.get("cross_market_priority_score", 0) or 0
                ),
                "cross_market_themes": list(
                    pick.metrics.get("cross_market_themes", ()) or ()
                ),
                "cross_market_rule_ids": list(
                    pick.metrics.get("cross_market_rule_ids", ()) or ()
                ),
                "cross_market_first_order_targets": list(
                    pick.metrics.get("cross_market_first_order_targets", ()) or ()
                ),
                "cross_market_second_order_targets": list(
                    pick.metrics.get("cross_market_second_order_targets", ()) or ()
                ),
                "cross_market_pressure_targets": list(
                    pick.metrics.get("cross_market_pressure_targets", ()) or ()
                ),
                "cross_market_execution_watchpoints": list(
                    pick.metrics.get("cross_market_execution_watchpoints", ()) or ()
                ),
                "cross_market_transmission_path": list(
                    pick.metrics.get("cross_market_transmission_path", ()) or ()
                ),
                "cross_market_validation_signals": list(
                    pick.metrics.get("cross_market_validation_signals", ()) or ()
                ),
                "cross_market_invalidation_signals": list(
                    pick.metrics.get("cross_market_invalidation_signals", ()) or ()
                ),
                "cross_market_chain_summary": str(
                    pick.metrics.get("cross_market_chain_summary", "") or ""
                ),
                "cross_market_support_event_count": int(
                    pick.metrics.get("cross_market_support_event_count", 0) or 0
                ),
                "cross_market_conflict_event_count": int(
                    pick.metrics.get("cross_market_conflict_event_count", 0) or 0
                ),
                "cross_market_evidence_stack_summary": str(
                    pick.metrics.get("cross_market_evidence_stack_summary", "") or ""
                ),
                "cross_market_summaries": list(
                    pick.metrics.get("cross_market_summaries", ()) or ()
                ),
                "news_catalyst_judgement": str(
                    pick.metrics.get("news_catalyst_judgement", "") or ""
                ),
                "news_catalyst_priority_score": int(
                    pick.metrics.get("news_catalyst_priority_score", 0) or 0
                ),
                "news_catalyst_support_count": int(
                    pick.metrics.get("news_catalyst_support_count", 0) or 0
                ),
                "news_catalyst_oppose_count": int(
                    pick.metrics.get("news_catalyst_oppose_count", 0) or 0
                ),
                "news_catalyst_review_count": int(
                    pick.metrics.get("news_catalyst_review_count", 0) or 0
                ),
                "news_catalyst_supports": list(
                    pick.metrics.get("news_catalyst_supports", ()) or ()
                ),
                "news_catalyst_opposes": list(
                    pick.metrics.get("news_catalyst_opposes", ()) or ()
                ),
                "news_catalyst_needs_review": list(
                    pick.metrics.get("news_catalyst_needs_review", ()) or ()
                ),
                "news_catalyst_lead": str(
                    pick.metrics.get("news_catalyst_lead", "") or ""
                ),
                "news_catalyst_source": str(
                    pick.metrics.get("news_catalyst_source", "") or ""
                ),
                "news_catalyst_url": str(
                    pick.metrics.get("news_catalyst_url", "") or ""
                ),
                "horizon_days": execution.horizon_days,
                "fee_bps": execution.fee_bps,
                "slippage_bps": execution.slippage_bps,
                "benchmark_symbol": execution.benchmark_symbol,
                "limit_up_pct": execution.limit_up_pct,
                "limit_down_pct": execution.limit_down_pct,
                "thresholds_version": thresholds_version,
                "regime_at_signal": regime,
                "signal_day_group": signal_day_group,
                "northbound_flow_5d_z": northbound_flow_5d_z,
                "margin_balance_change_5d": margin_balance_change_5d,
                **run_metadata_fields(run_metadata),
            }

            existing_idx = row_index_by_key.get(row_key)
            if existing_idx is not None:
                existing_row = rows[existing_idx]
                if _is_terminal_prediction_row(existing_row):
                    continue
                preserved_status = str(existing_row.get("status", "") or "pending")
                rows[existing_idx] = {
                    **existing_row,
                    **prediction_fields,
                    "status": preserved_status,
                }
                continue

            rows.append(
                {
                    "id": uuid4().hex,
                    "created_at": now,
                    **prediction_fields,
                    "status": "pending",
                }
            )
            row_index_by_key[row_key] = len(rows) - 1
        write_ledger(path, rows)


def append_run_event(
    path: str | Path,
    *,
    event_date: str,
    status: str,
    reason: str,
    run_metadata: RunMetadata | None = None,
    details: dict[str, object] | None = None,
) -> None:
    _validate_formal_run_metadata(run_metadata)
    with advisory_lock(path):
        rows = read_ledger(path)
        row_key = (
            event_date,
            "__RUN__",
            status,
            str(run_metadata.task_id if run_metadata else ""),
        )
        existing_keys = {
            (
                str(row.get("signal_date", "")),
                str(row.get("symbol", "")),
                str(row.get("status", "")),
                str(row.get("run_task_id", "")),
            )
            for row in rows
        }
        if row_key in existing_keys:
            return
        now = now_shanghai().isoformat(timespec="seconds")
        rows.append(
            {
                "id": uuid4().hex,
                "created_at": now,
                "signal_date": event_date,
                "signal_day_group": event_date,
                "symbol": "__RUN__",
                "name": "run_event",
                "status": status,
                "reason": reason,
                "event_type": status,
                **(details or {}),
                **run_metadata_fields(run_metadata),
            }
        )
        write_ledger(path, rows)


def run_metadata_fields(metadata: RunMetadata | None) -> dict[str, object]:
    if metadata is None:
        return {}
    return {
        "run_requested_source": metadata.requested_source,
        "run_actual_source": metadata.actual_source,
        "run_source_freshness_tier": metadata.source_freshness_tier,
        "run_source_coverage_tier": metadata.source_coverage_tier,
        "run_source_local_status": metadata.source_local_status,
        "run_source_health_label": metadata.source_health_label,
        "run_source_health_message": metadata.source_health_message,
        "run_fallback_used": metadata.fallback_used,
        "run_explicit_symbol_count": metadata.explicit_symbol_count,
        "run_resolved_symbol_count": metadata.resolved_symbol_count,
        "run_fetched_frame_count": metadata.fetched_frame_count,
        "run_screened_count": metadata.screened_count,
        "run_final_count": metadata.final_count,
        "task_id": metadata.task_id,
        "run_task_id": metadata.task_id,
        "run_max_universe": metadata.max_universe,
        "run_min_price": metadata.min_price,
        "run_max_price": metadata.max_price,
        "run_min_avg_amount": metadata.min_avg_amount,
        "run_online_factors_enabled": metadata.online_factors_enabled,
        "run_data_latest_trade_date": metadata.data_latest_trade_date,
        "run_data_lag_days": metadata.data_lag_days,
        "run_circuit_breaker_triggered": metadata.circuit_breaker_triggered,
        "run_circuit_breaker_reason": metadata.circuit_breaker_reason,
        "run_market_context_overview": metadata.market_context_overview,
        "run_market_context_lines": list(metadata.market_context_lines),
        "run_intraday_coverage_status": metadata.intraday_coverage_status,
        "run_intraday_missing_symbols": list(metadata.intraday_missing_symbols),
        "thresholds_version": metadata.thresholds_version,
        "run_workload": metadata.workload,
    }


def _validate_formal_run_metadata(metadata: RunMetadata | None) -> None:
    """Fail closed when a production workload lacks provenance evidence.

    Historical/backtest callers intentionally omit metadata. Production callers
    opt into this gate through ``RunMetadata.workload`` so old replay fixtures
    remain readable without weakening the live ledger contract.
    """
    if metadata is None or metadata.workload not in FORMAL_LEDGER_WORKLOADS:
        return

    required = {
        "requested_source": metadata.requested_source,
        "actual_source": metadata.actual_source,
        "source_freshness_tier": metadata.source_freshness_tier,
        "source_coverage_tier": metadata.source_coverage_tier,
        "source_local_status": metadata.source_local_status,
        "source_health_label": metadata.source_health_label,
        "thresholds_version": metadata.thresholds_version,
        "data_latest_trade_date": metadata.data_latest_trade_date,
    }
    missing = tuple(name for name, value in required.items() if not str(value).strip())
    if missing:
        raise DataError(
            "正式 ledger 缺少 provenance: "
            + ", ".join(missing)
            + f" (workload={metadata.workload})"
        )
    if metadata.data_lag_days < 0:
        raise DataError("正式 ledger 的 data_lag_days 不能为负数")


def _prediction_key(row: dict) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("signal_date", "")),
        str(row.get("symbol", "")),
        str(row.get("thresholds_version", "")),
        str(row.get("regime_at_signal", "")),
        str(row.get("intended_entry", "next_open")),
    )


def _is_terminal_prediction_row(row: dict) -> bool:
    return str(row.get("status", "") or "pending") in TERMINAL_PREDICTION_STATUSES


def _fallback_limit_pct(row: dict) -> float:
    explicit = row.get("limit_up_pct") or row.get("limit_down_pct")
    if explicit:
        try:
            value = float(explicit)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value

    symbol = str(row.get("symbol", ""))
    name = str(row.get("name", "")).upper()
    try:
        from aqsp.strategies.thresholds import load_thresholds

        execution = load_thresholds().execution
    except Exception:
        return 0.10

    if "ST" in name:
        return float(execution.fallback_limit_st_pct)
    if symbol.startswith(("300", "301", "688", "689")):
        return float(execution.fallback_limit_growth_pct)
    if symbol.startswith(("8", "4")):
        return float(execution.fallback_limit_bse_pct)
    return float(execution.fallback_limit_main_pct)


def _check_executable(
    entry_bar: pd.Series, prev_close: float, row: dict
) -> tuple[bool, str]:
    if prev_close is None:
        return False, "missing_prev_close"
    try:
        prev_close_value = float(prev_close)
    except (TypeError, ValueError):
        return False, "missing_prev_close"
    if not math.isfinite(prev_close_value) or prev_close_value <= 0:
        return False, "missing_prev_close"
    open_price = float(entry_bar.get("open") or 0)
    if open_price <= 0:
        return False, "no_open_price"

    suspended_flag = entry_bar.get("suspended")
    if suspended_flag is True or (
        isinstance(suspended_flag, (int, float)) and suspended_flag
    ):
        return False, "suspended_or_no_trade"

    volume = entry_bar.get("volume")
    if volume is not None:
        try:
            if float(volume) <= 0:
                return False, "suspended_or_no_trade"
        except (TypeError, ValueError):
            pass

    high = float(entry_bar.get("high") or open_price)
    low = float(entry_bar.get("low") or open_price)

    bar_limit_up = entry_bar.get("limit_up")
    bar_limit_down = entry_bar.get("limit_down")
    try:
        bar_limit_up = float(bar_limit_up) if bar_limit_up is not None else 0.0
    except (TypeError, ValueError):
        bar_limit_up = 0.0
    try:
        bar_limit_down = float(bar_limit_down) if bar_limit_down is not None else 0.0
    except (TypeError, ValueError):
        bar_limit_down = 0.0

    if bar_limit_up > 0:
        limit_up_price = bar_limit_up
    else:
        limit_up_pct = _fallback_limit_pct(row)
        # 涨跌停价四舍五入到分（A股交易所规则），与 source.apply_limit_suspended_adj 口径一致
        limit_up_price = round(prev_close_value * (1 + limit_up_pct), 2)

    if bar_limit_down > 0:
        limit_down_price = bar_limit_down
    else:
        limit_down_pct = _fallback_limit_pct(row)
        limit_down_price = round(prev_close_value * (1 - limit_down_pct), 2)

    if open_price >= limit_up_price * 0.999 and high <= open_price * 1.0001:
        return False, "limit_up_at_open"
    if open_price <= limit_down_price * 1.001 and low >= open_price * 0.9999:
        return False, "limit_down_at_open"
    return True, ""


def validate_predictions(
    path: str | Path, frames: dict[str, pd.DataFrame]
) -> ValidationSummary:
    rows = read_ledger(path)
    checked = 0
    wins = 0
    skipped = 0
    not_executable_reasons: dict[str, int] = {}
    strategy_attempts: dict[str, int] = {}
    strategy_not_executable: dict[str, int] = {}
    returns: list[float] = []
    excess_returns: list[float] = []

    for row in rows:
        if row.get("status") != "pending":
            continue
        if not is_tradable_rating(row.get("rating")):
            row["status"] = "watch_only"
            continue
        strategies = _normalize_strategies(row.get("strategies"))
        symbol = str(row.get("symbol", ""))
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        missing_columns = {"date", "open", "close"} - set(frame.columns)
        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise DataError(
                f"账本校验数据缺少必需列: symbol={symbol}, columns={missing_text}"
            )
        frame = frame.sort_values("date").reset_index(drop=True)
        signal_date = row.get("signal_date", row.get("pick_date", ""))
        future = frame[frame["date"] > signal_date]
        if future.empty:
            continue
        entry_bar = future.iloc[0]
        for strategy in strategies:
            strategy_attempts[strategy] = strategy_attempts.get(strategy, 0) + 1

        signal_rows = frame[frame["date"] <= signal_date]
        prev_close = (
            float(signal_rows.iloc[-1]["close"])
            if not signal_rows.empty
            else float(row.get("signal_close") or 0)
        )
        executable, reason = _check_executable(entry_bar, prev_close, row)
        if not executable:
            row["status"] = "not_executable"
            row["entry_date"] = str(entry_bar["date"])
            row["not_executable_reason"] = reason
            skipped += 1
            not_executable_reasons[reason] = not_executable_reasons.get(reason, 0) + 1
            for strategy in strategies:
                strategy_not_executable[strategy] = (
                    strategy_not_executable.get(strategy, 0) + 1
                )
            continue

        horizon = int(row.get("horizon_days") or 1)
        if len(future) < horizon:
            continue

        eval_window = future.iloc[:horizon]
        entry_price = float(entry_bar["open"]) * (
            1 + float(row.get("slippage_bps") or 0) / 10000
        )
        fee_pct = float(row.get("fee_bps") or 0) / 100
        exit_bar, exit_price, exit_reason = _resolve_exit(eval_window, row)
        ret = (exit_price - entry_price) / entry_price * 100 - fee_pct
        benchmark_ret = _benchmark_return(frames, row, entry_bar, exit_bar)
        excess = ret - benchmark_ret if benchmark_ret is not None else None
        row["status"] = "validated"
        row["entry_date"] = str(entry_bar["date"])
        row["entry_price"] = round(entry_price, 4)
        row["exit_date"] = str(exit_bar["date"])
        row["exit_price"] = round(exit_price, 4)
        row["exit_reason"] = exit_reason
        row["return_pct"] = round(ret, 4)
        row["benchmark_return_pct"] = (
            round(benchmark_ret, 4) if benchmark_ret is not None else None
        )
        row["excess_return_pct"] = round(excess, 4) if excess is not None else None
        row["win"] = ret > 0
        checked += 1
        wins += 1 if ret > 0 else 0
        returns.append(ret)
        if excess is not None:
            excess_returns.append(excess)

    write_ledger(path, rows)
    avg = sum(returns) / len(returns) if returns else 0.0
    avg_excess = sum(excess_returns) / len(excess_returns) if excess_returns else 0.0
    return ValidationSummary(
        checked=checked,
        wins=wins,
        avg_return_pct=round(avg, 4),
        avg_excess_pct=round(avg_excess, 4),
        skipped_not_executable=skipped,
        not_executable_reasons=dict(sorted(not_executable_reasons.items())),
        strategy_not_executable_rates={
            strategy: round(strategy_not_executable.get(strategy, 0) / attempts, 4)
            for strategy, attempts in sorted(strategy_attempts.items())
            if attempts > 0 and strategy_not_executable.get(strategy, 0) > 0
        },
    )


def strategy_weights_from_ledger(
    path: str | Path,
    min_independent_signal_days: int = 30,
    weight_floor: float = 0.65,
    weight_ceiling: float = 1.45,
) -> dict[str, float]:
    groups: dict[str, dict[str, list[float]]] = {}
    for row in read_ledger(path):
        if str(row.get("status") or "").strip() != "validated":
            continue
        ret = float(
            row.get("excess_return_pct")
            if row.get("excess_return_pct") is not None
            else row.get("return_pct") or 0
        )
        signal_date = row.get("signal_date") or row.get("signal_day_group") or ""
        for strategy in _normalize_strategies(row.get("strategies")):
            groups.setdefault(strategy, {}).setdefault(signal_date, []).append(ret)

    weights: dict[str, float] = {}
    for strategy, date_groups in groups.items():
        group_avgs = [sum(rets) / len(rets) for rets in date_groups.values()]
        if len(group_avgs) < min_independent_signal_days:
            continue
        win_rate = sum(1 for r in group_avgs if r > 0) / len(group_avgs)
        avg_ret = sum(group_avgs) / len(group_avgs)
        weights[strategy] = round(
            max(
                weight_floor,
                min(weight_ceiling, 1 + (win_rate - 0.5) * 0.7 + avg_ret / 20),
            ),
            3,
        )
    return weights


def _resolve_exit(window: pd.DataFrame, row: dict) -> tuple[pd.Series, float, str]:
    stop_loss = float(row.get("stop_loss") or 0)
    take_profit = float(row.get("take_profit") or 0)
    slippage = float(row.get("slippage_bps") or 0) / 10000
    for bar in window.itertuples(index=False, name="PriceBar"):
        bar_close = float(getattr(bar, "close"))
        low = float(getattr(bar, "low", bar_close))
        high = float(getattr(bar, "high", bar_close))
        if stop_loss > 0 and low <= stop_loss:
            return pd.Series(bar._asdict()), stop_loss * (1 - slippage), "stop_loss"
        if take_profit > 0 and high >= take_profit:
            return pd.Series(bar._asdict()), take_profit * (1 - slippage), "take_profit"
    last = window.iloc[-1]
    return last, float(last["close"]) * (1 - slippage), "horizon_close"


def _benchmark_return(
    frames: dict[str, pd.DataFrame],
    row: dict,
    entry_bar: pd.Series,
    exit_bar: pd.Series,
) -> float | None:
    benchmark = frames.get(str(row.get("benchmark_symbol") or ""))
    if benchmark is None or benchmark.empty:
        return None
    benchmark = benchmark.sort_values("date").reset_index(drop=True)
    entry_rows = benchmark[benchmark["date"] >= entry_bar["date"]]
    exit_rows = benchmark[benchmark["date"] <= exit_bar["date"]]
    if entry_rows.empty or exit_rows.empty:
        return None
    entry = float(entry_rows.iloc[0]["open"])
    exit_price = float(exit_rows.iloc[-1]["close"])
    if entry <= 0:
        return None
    return (exit_price - entry) / entry * 100
