from __future__ import annotations

import argparse
import ast
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from aqsp.cli import (
    _build_debate_coordinator,
    _candidate_debate_fingerprint,
    _merge_debate_records,
    _read_retained_debates,
    _resolve_pick_debate_roles,
    _write_debate_records,
    serialize_debate_result,
)
from aqsp.config import load_debate_runtime_config
from aqsp.core.time import now_shanghai
from aqsp.models import PickResult
from aqsp.strategies.thresholds import load_thresholds
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


DEFAULT_MAX_CANDIDATES = 5
DEBATE_RETENTION_DAYS = 30
DEFAULT_STATUS_PATH = Path("data/backfill_intraday_debate_status.json")
DEFAULT_LOCK_PATH = Path("data/backfill_intraday_debate.lock")
DEFAULT_STALE_LOCK_SECONDS = 30 * 60
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_STALE = "stale"
CANDIDATE_PENDING = "pending"
CANDIDATE_RUNNING = "running"
CANDIDATE_SUCCEEDED = "succeeded"
CANDIDATE_FAILED = "failed"
DEFAULT_MAX_ATTEMPTS = 2


def _float_value(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        value = str(row.get(key, "") or "").strip()
        return default if not value else float(value)
    except ValueError:
        return default


def _split_texts(value: str) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    separators = ("；", ";", "|")
    for separator in separators:
        if separator in text:
            return tuple(part.strip() for part in text.split(separator) if part.strip())
    return (text,)


_TUPLE_METRIC_FIELDS = {
    "cross_market_themes",
    "cross_market_rule_ids",
    "cross_market_first_order_targets",
    "cross_market_second_order_targets",
    "cross_market_pressure_targets",
    "cross_market_execution_watchpoints",
    "cross_market_transmission_path",
    "cross_market_validation_signals",
    "cross_market_invalidation_signals",
    "cross_market_summaries",
    "news_catalyst_supports",
    "news_catalyst_opposes",
    "news_catalyst_needs_review",
    "support_points",
    "opposition_points",
    "watch_items",
    "role_reliability_lines",
}

_INT_METRIC_FIELDS = {
    "cross_market_priority_score",
    "cross_market_source_quality_score",
    "cross_market_support_event_count",
    "cross_market_conflict_event_count",
    "news_catalyst_priority_score",
    "news_catalyst_support_count",
    "news_catalyst_oppose_count",
    "news_catalyst_review_count",
}

_FLOAT_METRIC_FIELDS = {
    "debate_disagreement_score",
    "debate_historical_context_accuracy",
    "composite_score_raw",
    "composite_score_normalized",
    "base_score_before_composite",
    "final_score_after_composite",
}


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value in ("", None):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    if text[:1] in "[(":
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
            return _text_tuple(parsed)
    return _split_texts(text)


def _metric_value(key: str, value: str) -> Any:
    if key in _TUPLE_METRIC_FIELDS:
        return _text_tuple(value)
    if key in _INT_METRIC_FIELDS:
        return int(_float_value({key: value}, key, 0.0))
    if key in _FLOAT_METRIC_FIELDS:
        return _float_value({key: value}, key, 0.0)
    return value


def _pick_from_row(row: dict[str, str]) -> PickResult:
    close = _float_value(row, "close")
    metrics: dict[str, Any] = {
        key: _metric_value(key, value)
        for key, value in row.items()
        if key
        and value not in ("", None)
        and key
        not in {
            "symbol",
            "name",
            "date",
            "close",
            "score",
            "rating",
            "entry_type",
            "ideal_buy",
            "stop_loss",
            "take_profit",
            "position",
            "strategies",
            "reasons",
            "risks",
        }
    }
    return PickResult(
        symbol=str(row.get("symbol", "") or "").zfill(6),
        name=str(row.get("name", "") or ""),
        date=str(row.get("date", "") or ""),
        close=close,
        score=_float_value(row, "score"),
        rating=str(row.get("rating", "") or "watch"),
        entry_type=str(row.get("entry_type", "") or "intraday_observation"),
        ideal_buy=_float_value(row, "ideal_buy", close),
        stop_loss=_float_value(row, "stop_loss", close),
        take_profit=_float_value(row, "take_profit", close),
        position=str(row.get("position", "") or ""),
        strategies=_split_texts(str(row.get("strategies", "") or "")),
        reasons=_split_texts(str(row.get("reasons", "") or "")),
        risks=_split_texts(str(row.get("risks", "") or "")),
        metrics=metrics,
        adjusted_score=_float_value(row, "adjusted_score"),
        recommended_adjustment=str(row.get("recommended_adjustment", "") or "keep"),
        debate_consensus=str(row.get("debate_consensus", "") or ""),
        confidence=_float_value(row, "confidence"),
        regime_score=_float_value(row, "regime_score"),
    )


def _dedupe_lines(lines: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return tuple(output[:11])


def _load_run_market_context_lines(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if str(row.get("symbol", "") or "").strip() != "__RUN__":
                continue
            lines = list(_text_tuple(row.get("run_market_context_lines", "")))
            overview = str(row.get("run_market_context_overview", "") or "").strip()
            if overview:
                lines.insert(0, f"运行上下文: {overview}")
            return _dedupe_lines(lines)
    return ()


def _market_context_lines_for_pick(
    pick: PickResult,
    *,
    run_market_context_lines: tuple[str, ...],
) -> tuple[str, ...]:
    metrics = pick.metrics or {}
    lines = list(run_market_context_lines)
    lines.extend(_text_tuple(metrics.get("cross_market_summaries")))

    theme = str(metrics.get("cross_market_primary_theme", "") or "").strip()
    action = str(metrics.get("cross_market_action", "") or "").strip()
    evidence = str(metrics.get("cross_market_evidence_stack_summary", "") or "").strip()
    if theme:
        parts = [theme]
        if action:
            parts.append(f"动作 {action}")
        if evidence:
            parts.append(f"证据 {evidence}")
        lines.append("跨市主线: " + "｜".join(parts))

    chain = str(metrics.get("cross_market_chain_summary", "") or "").strip()
    if chain:
        lines.append(f"传导推演[{theme or pick.symbol}]: {chain}")

    path = _text_tuple(metrics.get("cross_market_transmission_path"))
    if path:
        lines.append("传导路径: " + " -> ".join(path[:4]))

    validation = _text_tuple(metrics.get("cross_market_validation_signals"))
    invalidation = _text_tuple(metrics.get("cross_market_invalidation_signals"))
    watchpoints = _text_tuple(metrics.get("cross_market_execution_watchpoints"))
    if validation:
        lines.append(f"确认信号: {validation[0]}")
    if invalidation:
        lines.append(f"失效条件: {invalidation[0]}")
    if watchpoints:
        lines.append(f"先看: {watchpoints[0]}")

    quality = str(metrics.get("cross_market_source_quality_label", "") or "").strip()
    if quality:
        lines.append(f"来源质量: {quality}")

    lead = str(metrics.get("news_catalyst_lead", "") or "").strip()
    if lead:
        lines.append(f"消息催化: {lead}")
    supports = _text_tuple(metrics.get("news_catalyst_supports"))
    opposes = _text_tuple(metrics.get("news_catalyst_opposes"))
    if supports:
        lines.append(f"消息支持: {supports[0]}")
    if opposes:
        lines.append(f"消息压力: {opposes[0]}")

    return _dedupe_lines(lines)


def _context_quality_for_pick(
    pick: PickResult,
    market_context_lines: tuple[str, ...],
) -> tuple[str, str]:
    metrics = pick.metrics or {}
    has_structured_context = bool(
        market_context_lines
        or str(metrics.get("cross_market_primary_theme", "") or "").strip()
        or str(metrics.get("news_catalyst_lead", "") or "").strip()
    )
    if has_structured_context:
        return "structured_context", ""
    return "thin_context", "盘中候选缺少跨市/消息结构化上下文，仅按技术候选回填委员会。"


def _debate_frame(pick: PickResult) -> tuple[pd.DataFrame, str]:
    metrics = pick.metrics or {}
    values = {
        key: _float_value({key: str(metrics.get(key, ""))}, key)
        for key in ("open", "high", "low", "volume", "amount")
    }
    if all(values[key] > 0 for key in values):
        return (
            pd.DataFrame(
                [
                    {
                        "date": pick.date,
                        "open": values["open"],
                        "high": values["high"],
                        "low": values["low"],
                        "close": float(pick.close),
                        "volume": values["volume"],
                        "amount": values["amount"],
                    }
                ]
            ),
            "runtime_ohlcv",
        )
    close = float(pick.close or pick.ideal_buy or 0.0)
    prev_close = close * 0.995 if close else 0.0
    return (
        pd.DataFrame(
            [
                {
                    "date": pick.date,
                    "open": prev_close,
                    "high": max(prev_close, close),
                    "low": min(prev_close, close),
                    "close": prev_close,
                    "volume": 1.0,
                    "amount": 1.0,
                },
                {
                    "date": pick.date,
                    "open": prev_close,
                    "high": max(prev_close, close),
                    "low": min(prev_close, close),
                    "close": close,
                    "volume": 1.0,
                    "amount": 1.0,
                },
            ]
        ),
        "synthetic_context",
    )


def _reconsideration_evidence(
    pick: PickResult,
    frame: pd.DataFrame,
    data_context: str,
) -> tuple[str, ...]:
    """为二轮复议提供可审计的当期证据，不改变原始候选数据。"""
    if data_context == "runtime_ohlcv" and not frame.empty:
        latest = frame.iloc[-1]
        return (
            "第2轮复议新证据: 真实盘中OHLCV "
            f"open={float(latest['open']):.4f}, high={float(latest['high']):.4f}, "
            f"low={float(latest['low']):.4f}, close={float(latest['close']):.4f}, "
            f"volume={float(latest['volume']):.4f}, amount={float(latest['amount']):.4f}",
        )
    return (
        "第2轮复议新证据: 当前候选缺少完整盘中OHLCV，"
        f"仅使用 synthetic_context；标的 {pick.symbol} 结论不得视为真实行情验证。",
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_state_key(pick: PickResult, task_id: str) -> str:
    return "|".join(
        (
            pick.symbol,
            pick.date,
            task_id,
            _candidate_debate_fingerprint(pick),
        )
    )


def _candidate_state(
    pick: PickResult,
    *,
    task_id: str,
    max_attempts: int,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = previous or {}
    return {
        "state_key": _candidate_state_key(pick, task_id),
        "symbol": pick.symbol,
        "name": pick.name,
        "candidate_fingerprint": _candidate_debate_fingerprint(pick),
        "task_id": task_id,
        "status": CANDIDATE_PENDING,
        "attempts": 0,
        "max_attempts": max_attempts,
        "retryable": False,
        "last_error": str(previous.get("last_error", "") or ""),
        "previous_status": str(previous.get("status", "") or ""),
        "previous_attempts": int(previous.get("attempts", 0) or 0),
        "responsible_roles": [],
        "responsible_agents": [],
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    }


def _update_candidate_state(
    state: dict[str, Any],
    *,
    status: str,
    error: str = "",
    retryable: bool = False,
    **updates: Any,
) -> None:
    state.update(
        {
            "status": status,
            "last_error": error[:500],
            "retryable": retryable,
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
            **updates,
        }
    )


def _write_status(
    path: Path,
    *,
    status: str,
    run_id: str,
    task_id: str,
    input_csv: Path,
    output_path: Path,
    candidate_count: int,
    succeeded_count: int,
    failed_candidates: list[dict[str, str]],
    started_at: str,
    detail: str = "",
    stale_recovered: bool = False,
    skipped: bool = False,
    candidate_states: list[dict[str, Any]] | None = None,
) -> None:
    updated_at = now_shanghai().isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "schema_version": "v2",
        "run_id": run_id,
        "status": status,
        "task_id": task_id,
        "pid": os.getpid(),
        "input_csv": str(input_csv),
        "output_path": str(output_path),
        "candidate_count": candidate_count,
        "succeeded_count": succeeded_count,
        "failed_count": sum(
            1
            for item in (candidate_states or ())
            if str(item.get("status", "")) == CANDIDATE_FAILED
        )
        or len(failed_candidates),
        "failed_candidates": failed_candidates,
        "candidate_states": candidate_states or [],
        "started_at": started_at,
        "updated_at": updated_at,
        "detail": detail,
        "stale_recovered": stale_recovered,
        "skipped": skipped,
    }
    if status in {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_STALE}:
        payload["completed_at"] = updated_at
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _lock_meta_path(lock_path: Path) -> Path:
    return lock_path / "meta.json"


def _pid_active(value: object) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False
    return True


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_shanghai().tzinfo)
    return parsed


def _lock_is_stale(
    payload: dict[str, Any],
    *,
    now: datetime,
    stale_lock_seconds: int,
) -> bool:
    if not payload:
        return True
    if not _pid_active(payload.get("pid")):
        return True
    heartbeat = _parse_timestamp(payload.get("updated_at")) or _parse_timestamp(
        payload.get("started_at")
    )
    if heartbeat is None:
        return True
    return (now - heartbeat).total_seconds() > stale_lock_seconds


def _remove_stale_lock(lock_path: Path) -> None:
    meta_path = _lock_meta_path(lock_path)
    meta_path.unlink(missing_ok=True)
    lock_path.rmdir()


def _acquire_run_lock(
    lock_path: Path,
    *,
    run_id: str,
    stale_lock_seconds: int,
) -> tuple[bool, bool, str]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stale_recovered = False
    try:
        lock_path.mkdir()
    except FileExistsError:
        existing = _read_json_object(_lock_meta_path(lock_path))
        if not _lock_is_stale(
            existing,
            now=now_shanghai(),
            stale_lock_seconds=stale_lock_seconds,
        ):
            pid = existing.get("pid", "unknown")
            return False, False, f"active backfill lock already held: pid={pid}"
        try:
            _remove_stale_lock(lock_path)
        except OSError as exc:
            return False, False, f"stale backfill lock cannot be removed: {exc}"
        stale_recovered = True
        try:
            lock_path.mkdir()
        except FileExistsError:
            return False, stale_recovered, "backfill lock was reacquired concurrently"

    meta = {
        "pid": os.getpid(),
        "run_id": run_id,
        "started_at": now_shanghai().isoformat(timespec="seconds"),
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    }
    atomic_write_text(
        _lock_meta_path(lock_path),
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
    )
    return True, stale_recovered, ""


def _touch_run_lock(lock_path: Path, run_id: str) -> None:
    meta_path = _lock_meta_path(lock_path)
    payload = _read_json_object(meta_path)
    if payload.get("run_id") != run_id or payload.get("pid") != os.getpid():
        raise RuntimeError("backfill lock ownership changed")
    payload["updated_at"] = now_shanghai().isoformat(timespec="seconds")
    atomic_write_text(
        meta_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def _release_run_lock(lock_path: Path, run_id: str) -> None:
    payload = _read_json_object(_lock_meta_path(lock_path))
    if payload.get("run_id") != run_id or payload.get("pid") != os.getpid():
        return
    try:
        _remove_stale_lock(lock_path)
    except OSError:
        return


def _running_status_is_stale(
    payload: dict[str, Any],
    *,
    now: datetime,
    stale_lock_seconds: int,
) -> bool:
    if str(payload.get("status") or "").strip() != STATUS_RUNNING:
        return False
    return _lock_is_stale(
        payload,
        now=now,
        stale_lock_seconds=stale_lock_seconds,
    )


def _persist_debate_update(
    output_path: Path,
    *,
    cutoff: str,
    payload: dict[str, Any],
) -> None:
    key = "|".join(
        (
            str(payload.get("symbol", "")),
            str(payload.get("related_signal_date", "")),
            str(payload.get("task_id", "")),
            str(payload.get("candidate_fingerprint", "")),
        )
    )
    with advisory_lock(output_path):
        records = _read_retained_debates(output_path, cutoff)
        _merge_debate_records(records, {key: payload})
        _write_debate_records(output_path, records)


def _responsibility_snapshot(coordinator: Any) -> list[dict[str, str]]:
    agents = getattr(coordinator, "agents", ()) or ()
    agent_ids = {
        agent.role: str(agent.agent_id)
        for agent in agents
        if getattr(agent, "role", None) is not None
        and str(getattr(agent, "agent_id", "") or "").strip()
    }
    tracker = getattr(coordinator, "tracker", None)
    getter = getattr(tracker, "get_agent_responsibilities", None)
    if callable(getter):
        return [item.to_dict() for item in getter(agent_ids)]
    return [
        {
            "role": role.value,
            "role_label": role.value,
            "agent_id": agent_id,
            "responsibility": "本轮规则讨论责任人",
        }
        for role, agent_id in agent_ids.items()
    ]


def load_intraday_picks(path: Path, limit: int) -> list[PickResult]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        rows = [
            row
            for row in csv.DictReader(file)
            if str(row.get("symbol", "") or "").strip()
            and str(row.get("symbol", "") or "").strip() != "__RUN__"
            and (
                "quality_gate_action" not in row
                or (
                    str(row.get("quality_gate_action") or "").strip() == "clean"
                    and str(row.get("paper_review_eligible") or "").lower() == "true"
                )
            )
        ]
    picks = [_pick_from_row(row) for row in rows]
    picks.sort(key=lambda pick: float(pick.score or 0.0), reverse=True)
    return picks[:limit]


def _run_candidate_debate(
    pick: PickResult,
    *,
    runtime: Any,
    coordinator: Any,
    default_roles: tuple[str, ...],
    thresholds_version: str,
    run_market_context_lines: tuple[str, ...],
    today: str,
    created_at: str,
    task_id: str,
) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    market_context_lines = _market_context_lines_for_pick(
        pick,
        run_market_context_lines=run_market_context_lines,
    )
    context_quality, context_warning = _context_quality_for_pick(
        pick,
        market_context_lines,
    )
    roles = _resolve_pick_debate_roles(
        runtime,
        pick=pick,
        market_context_lines=market_context_lines,
    )
    active_coordinator = (
        coordinator
        if roles == default_roles
        else _build_debate_coordinator(
            runtime,
            thresholds_version=thresholds_version,
            regime="intraday",
            data_source="intraday_latest_csv",
            roles_override=roles,
        )
    )
    frame, data_context = _debate_frame(pick)
    requested_rounds = max(1, int(runtime.max_rounds))
    reconsideration_evidence = _reconsideration_evidence(pick, frame, data_context)
    debate_context_lines = market_context_lines
    if requested_rounds >= 2:
        debate_context_lines += reconsideration_evidence
    result = active_coordinator.run_debate(
        pick,
        frame,
        signal_date=pick.date or today,
        market_context_lines=debate_context_lines,
    )
    payload = serialize_debate_result(result)
    payload.setdefault("market_context_lines", list(market_context_lines))
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        rounds = []
    if requested_rounds >= 2 and len(rounds) < 2:
        raise ValueError(
            "debate runtime requires a second round, but coordinator returned fewer than 2 rounds"
        )

    payload["debate_rounds_requested"] = requested_rounds
    payload["debate_rounds_completed"] = len(rounds)
    if requested_rounds >= 2:
        payload["debate_reconsideration"] = {
            "round_num": 2,
            "basis": "round_1_counterarguments_and_current_context",
            "new_evidence": list(reconsideration_evidence),
        }
    payload["debate_context_quality"] = context_quality
    payload["debate_data_context"] = data_context
    if context_warning:
        payload["debate_context_warning"] = context_warning
    if data_context == "synthetic_context":
        payload["debate_data_context_warning"] = (
            "当前 CSV 缺少完整盘中 OHLCV，委员会仅作解释性参考。"
        )
    payload["related_signal_date"] = pick.date or today
    payload["debate_date"] = today
    payload["created_at"] = created_at
    payload["task_id"] = task_id
    payload["candidate_created_at"] = created_at
    payload["candidate_signal_date"] = pick.date or today
    payload["candidate_fingerprint"] = _candidate_debate_fingerprint(pick)
    payload["deterministic_score"] = float(pick.score)
    payload["deterministic_score_unchanged"] = bool(
        getattr(result, "deterministic_score_unchanged", True)
    ) and float(getattr(result, "original_score", pick.score)) == float(pick.score)
    payload["advisory_adjusted_score"] = float(
        getattr(result, "adjusted_score", pick.score) or pick.score
    )
    payload["advisory_only"] = True
    payload["adjusted_score_is_advisory"] = True
    responsibilities = _responsibility_snapshot(active_coordinator)
    payload["debate_responsibilities"] = responsibilities
    return payload, result, responsibilities


def run_backfill(
    *,
    input_csv: Path,
    output_path: Path,
    task_id: str,
    max_candidates: int,
    force: bool,
    status_path: Path = DEFAULT_STATUS_PATH,
    lock_path: Path = DEFAULT_LOCK_PATH,
    stale_lock_seconds: int = DEFAULT_STALE_LOCK_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    input_csv = Path(input_csv)
    output_path = Path(output_path)
    status_path = Path(status_path)
    lock_path = Path(lock_path)
    stale_lock_seconds = max(1, int(stale_lock_seconds))
    max_attempts = max(1, int(max_attempts))
    run_id = uuid4().hex
    started_at = now_shanghai().isoformat(timespec="seconds")
    failed_candidates: list[dict[str, str]] = []
    succeeded_count = 0

    previous_status = _read_json_object(status_path)
    if _running_status_is_stale(
        previous_status,
        now=now_shanghai(),
        stale_lock_seconds=stale_lock_seconds,
    ):
        _write_status(
            status_path,
            status=STATUS_STALE,
            run_id=str(previous_status.get("run_id") or run_id),
            task_id=str(previous_status.get("task_id") or task_id),
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=int(previous_status.get("candidate_count") or 0),
            succeeded_count=int(previous_status.get("succeeded_count") or 0),
            failed_candidates=[],
            started_at=str(previous_status.get("started_at") or started_at),
            detail="previous running status was stale and auto-repaired",
            stale_recovered=True,
        )

    try:
        runtime = load_debate_runtime_config(task_id=task_id)
        # 盘中主链可关闭全局 debate，但后台回填用 --force 独立运行规则讨论。
        # LLM 仍由 AQSP_DEBATE_ENABLE_LLM / 角色配置单独控制。
        if not runtime.enabled and not force:
            _write_status(
                status_path,
                status=STATUS_SUCCEEDED,
                run_id=run_id,
                task_id=task_id,
                input_csv=input_csv,
                output_path=output_path,
                candidate_count=0,
                succeeded_count=0,
                failed_candidates=[],
                started_at=started_at,
                detail="debate runtime disabled; backfill skipped",
                skipped=True,
            )
            print("debate backfill skipped: debate runtime disabled")
            return 0

        picks = load_intraday_picks(
            input_csv,
            limit=max(1, min(max_candidates, runtime.max_candidates)),
        )
    except Exception as exc:
        failed_candidates.append(
            {
                "symbol": "__RUN__",
                "name": "",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        )
        _write_status(
            status_path,
            status=STATUS_FAILED,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=0,
            succeeded_count=0,
            failed_candidates=failed_candidates,
            started_at=started_at,
            detail="backfill setup failed",
        )
        print(f"debate backfill failed during setup: {exc}")
        return 0

    if not picks:
        _write_status(
            status_path,
            status=STATUS_SUCCEEDED,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=0,
            succeeded_count=0,
            failed_candidates=[],
            started_at=started_at,
            detail=f"no candidates in {input_csv}",
        )
        print(f"debate backfill skipped: no candidates in {input_csv}")
        return 0

    previous_states = {
        str(item.get("state_key", "")): item
        for item in (_read_json_object(status_path).get("candidate_states", []) or [])
        if isinstance(item, dict) and str(item.get("state_key", ""))
    }
    candidate_states = [
        _candidate_state(
            pick,
            task_id=task_id,
            max_attempts=max_attempts,
            previous=previous_states.get(_candidate_state_key(pick, task_id)),
        )
        for pick in picks
    ]

    acquired, stale_recovered, lock_detail = _acquire_run_lock(
        lock_path,
        run_id=run_id,
        stale_lock_seconds=stale_lock_seconds,
    )
    if not acquired:
        _write_status(
            status_path,
            status=STATUS_FAILED,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=len(picks),
            succeeded_count=0,
            failed_candidates=[],
            started_at=started_at,
            detail=lock_detail,
            candidate_states=candidate_states,
        )
        print(f"debate backfill failed: {lock_detail}")
        return 0

    if stale_recovered:
        _write_status(
            status_path,
            status=STATUS_STALE,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=len(picks),
            succeeded_count=0,
            failed_candidates=[],
            started_at=started_at,
            detail="stale backfill lock auto-recovered",
            stale_recovered=True,
            candidate_states=candidate_states,
        )

    now = now_shanghai().isoformat(timespec="seconds")
    today = now_shanghai().date().isoformat()
    cutoff = (now_shanghai().date() - timedelta(days=DEBATE_RETENTION_DAYS)).isoformat()
    try:
        _write_status(
            status_path,
            status=STATUS_RUNNING,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=len(picks),
            succeeded_count=0,
            failed_candidates=failed_candidates,
            started_at=started_at,
            detail="backfill running",
            stale_recovered=stale_recovered,
            candidate_states=candidate_states,
        )
        thresholds = load_thresholds()
        coordinator = _build_debate_coordinator(
            runtime,
            thresholds_version=thresholds.version,
            regime="intraday",
            data_source="intraday_latest_csv",
        )
        default_roles = tuple(runtime.roles)
        run_market_context_lines = _load_run_market_context_lines(input_csv)

        for pick, state in zip(picks, candidate_states):
            responsibility_snapshot: list[dict[str, str]] = []
            for attempt in range(1, max_attempts + 1):
                _touch_run_lock(lock_path, run_id)
                state["attempts"] = attempt
                _update_candidate_state(
                    state,
                    status=CANDIDATE_RUNNING,
                    retryable=attempt < max_attempts,
                )
                _write_status(
                    status_path,
                    status=STATUS_RUNNING,
                    run_id=run_id,
                    task_id=task_id,
                    input_csv=input_csv,
                    output_path=output_path,
                    candidate_count=len(picks),
                    succeeded_count=succeeded_count,
                    failed_candidates=failed_candidates,
                    started_at=started_at,
                    detail=f"candidate {pick.symbol} attempt {attempt}/{max_attempts}",
                    stale_recovered=stale_recovered,
                    candidate_states=candidate_states,
                )
                try:
                    payload, result, responsibility_snapshot = _run_candidate_debate(
                        pick,
                        runtime=runtime,
                        coordinator=coordinator,
                        default_roles=default_roles,
                        thresholds_version=thresholds.version,
                        run_market_context_lines=run_market_context_lines,
                        today=today,
                        created_at=now,
                        task_id=task_id,
                    )
                    _persist_debate_update(
                        output_path,
                        cutoff=cutoff,
                        payload=payload,
                    )
                    succeeded_count += 1
                    state["responsible_roles"] = [
                        item["role"] for item in responsibility_snapshot
                    ]
                    state["responsible_agents"] = responsibility_snapshot
                    _update_candidate_state(
                        state,
                        status=CANDIDATE_SUCCEEDED,
                        error="",
                        retryable=False,
                        completed_at=now_shanghai().isoformat(timespec="seconds"),
                    )
                    _write_status(
                        status_path,
                        status=STATUS_RUNNING,
                        run_id=run_id,
                        task_id=task_id,
                        input_csv=input_csv,
                        output_path=output_path,
                        candidate_count=len(picks),
                        succeeded_count=succeeded_count,
                        failed_candidates=failed_candidates,
                        started_at=started_at,
                        detail="backfill running",
                        stale_recovered=stale_recovered,
                        candidate_states=candidate_states,
                    )
                    print(
                        f"debate backfilled: {pick.symbol} {pick.name} "
                        f"{result.recommended_adjustment} disagreement={result.disagreement_score:.2f}"
                    )
                    break
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"[:500]
                    if attempt < max_attempts:
                        _update_candidate_state(
                            state,
                            status=CANDIDATE_PENDING,
                            error=error,
                            retryable=True,
                        )
                        _write_status(
                            status_path,
                            status=STATUS_RUNNING,
                            run_id=run_id,
                            task_id=task_id,
                            input_csv=input_csv,
                            output_path=output_path,
                            candidate_count=len(picks),
                            succeeded_count=succeeded_count,
                            failed_candidates=failed_candidates,
                            started_at=started_at,
                            detail=f"candidate {pick.symbol} failed; retrying",
                            stale_recovered=stale_recovered,
                            candidate_states=candidate_states,
                        )
                        continue

                    _update_candidate_state(
                        state,
                        status=CANDIDATE_FAILED,
                        error=error,
                        retryable=True,
                    )
                    failed_candidates.append(
                        {
                            "symbol": pick.symbol,
                            "name": pick.name,
                            "candidate_fingerprint": state["candidate_fingerprint"],
                            "attempts": str(attempt),
                            "retryable": "true",
                            "error": error,
                        }
                    )
                    _write_status(
                        status_path,
                        status=STATUS_RUNNING,
                        run_id=run_id,
                        task_id=task_id,
                        input_csv=input_csv,
                        output_path=output_path,
                        candidate_count=len(picks),
                        succeeded_count=succeeded_count,
                        failed_candidates=failed_candidates,
                        started_at=started_at,
                        detail="one or more candidates failed; continuing",
                        stale_recovered=stale_recovered,
                        candidate_states=candidate_states,
                    )
                    print(f"debate backfill failed: {pick.symbol} {pick.name}: {exc}")
                    break

        final_status = STATUS_FAILED if failed_candidates else STATUS_SUCCEEDED
        detail = (
            f"completed with {len(failed_candidates)} candidate failures"
            if failed_candidates
            else "backfill completed"
        )
        _write_status(
            status_path,
            status=final_status,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=len(picks),
            succeeded_count=succeeded_count,
            failed_candidates=failed_candidates,
            started_at=started_at,
            detail=detail,
            stale_recovered=stale_recovered,
            candidate_states=candidate_states,
        )
        return succeeded_count
    except Exception as exc:
        failed_candidates.append(
            {
                "symbol": "__RUN__",
                "name": "",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        )
        _write_status(
            status_path,
            status=STATUS_FAILED,
            run_id=run_id,
            task_id=task_id,
            input_csv=input_csv,
            output_path=output_path,
            candidate_count=len(picks),
            succeeded_count=succeeded_count,
            failed_candidates=failed_candidates,
            started_at=started_at,
            detail="backfill aborted before all candidates completed",
            stale_recovered=stale_recovered,
            candidate_states=candidate_states,
        )
        return succeeded_count
    finally:
        _release_run_lock(lock_path, run_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill non-blocking multi-agent debates for intraday candidates."
    )
    parser.add_argument("--input-csv", default="reports/intraday_latest.csv")
    parser.add_argument("--output", default="data/debate_results.jsonl")
    parser.add_argument("--task-id", default="intraday")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    parser.add_argument(
        "--stale-lock-seconds",
        type=int,
        default=DEFAULT_STALE_LOCK_SECONDS,
    )
    args = parser.parse_args(argv)

    count = run_backfill(
        input_csv=Path(args.input_csv),
        output_path=Path(args.output),
        task_id=args.task_id,
        max_candidates=args.max_candidates,
        force=args.force,
        status_path=Path(args.status_path),
        lock_path=Path(args.lock_path),
        stale_lock_seconds=args.stale_lock_seconds,
        max_attempts=args.max_attempts,
    )
    status = _read_json_object(Path(args.status_path))
    print(
        f"debate backfill completed: {count} records "
        f"status={status.get('status', 'unknown')}"
    )
    if status.get("status") == "failed" and not status.get("skipped"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
