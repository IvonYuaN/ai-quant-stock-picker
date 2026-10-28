"""Persist the first valid candidate computation for each trading session."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.core.runtime import runtime_data_root
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.core.types import PickResult
from aqsp.portfolio.parameter_snapshot import (
    build_parameter_snapshot,
    parse_parameter_snapshot,
)
from aqsp.strategies.thresholds import Thresholds
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

_SCHEMA_VERSION = 2


class CandidateSnapshotError(ValueError):
    """A session snapshot exists but cannot safely be reused."""


@dataclass(frozen=True)
class CandidateIncrement:
    """A separately persisted, evidence-backed intraday change."""

    session_date: str
    observed_at: str
    request_fingerprint: str
    symbol: str
    status: str
    reason: str
    pick: PickResult | None = None


@dataclass(frozen=True)
class CandidateComputationSnapshot:
    session_date: str
    created_at: str
    request_fingerprint: str
    source: str
    data_latest_trade_date: str
    thresholds_version: str
    regime: str
    strategy_weights: dict[str, float]
    resolved_symbols: tuple[str, ...]
    resolved_symbol_count: int
    min_price: float
    max_price: float
    min_avg_amount: float
    screened_picks: tuple[PickResult, ...]
    final_picks: tuple[PickResult, ...] = ()
    finalized: bool = False
    thresholds_fingerprint: str = ""


def candidate_snapshot_path() -> Path:
    """Return the private runtime path for candidate computation snapshots."""
    configured = str(
        os.environ.get("AQSP_CANDIDATE_COMPUTATION_PATH", "") or ""
    ).strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = runtime_data_root() / path
        return path
    return runtime_data_root() / "candidate_computations.jsonl"


def candidate_increment_path() -> Path:
    """Return the append-only path for intraday additions or invalidations."""
    configured = str(os.environ.get("AQSP_CANDIDATE_INCREMENT_PATH", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = runtime_data_root() / path
        return path
    return runtime_data_root() / "candidate_computation_increments.jsonl"


def candidate_batch_snapshot_path(request_fingerprint: str) -> Path:
    """Keep rotating scan identities separate from the one daily display roster."""
    digest = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()
    root = candidate_snapshot_path()
    return root.parent / f"{root.stem}_batches" / f"{digest}.jsonl"


def candidate_close_snapshot_path() -> Path:
    """Isolate closing computations while retaining same-day identity checks.

    Unlike rotating batch paths, this path does not include the request hash:
    changing the closing pool/config must still fail rather than start over.
    The intraday roster remains display evidence, never a closing-score cache.
    """
    root = candidate_snapshot_path()
    return root.with_name(f"{root.stem}_close{root.suffix}")


def _candidate_parameter_fingerprints(
    snapshot: CandidateComputationSnapshot,
    additional_picks: list[PickResult] | None = None,
    thresholds: Thresholds | None = None,
) -> set[str]:
    fingerprints: set[str] = set()
    for pick in (
        *snapshot.screened_picks,
        *snapshot.final_picks,
        *(additional_picks or []),
    ):
        weights = pick.metrics.get("strategy_weight_snapshot", {})
        if not isinstance(weights, dict):
            raise CandidateSnapshotError("候选参数快照无效，拒绝混用")
        for payload in (
            weights.get("parameter_snapshot"),
            pick.metrics.get("parameter_snapshot"),
        ):
            try:
                identity = parse_parameter_snapshot(payload)
            except (TypeError, ValueError) as exc:
                raise CandidateSnapshotError("候选参数快照无效，拒绝混用") from exc
            if identity is not None:
                if identity.version != snapshot.thresholds_version:
                    raise CandidateSnapshotError("候选参数版本冲突，拒绝混用")
                if thresholds is not None:
                    if weights.get("parameter_snapshot") is None:
                        raise CandidateSnapshotError("候选参数缺少原运行描述，拒绝混用")
                    runtime_weights = {
                        key: value
                        for key, value in weights.items()
                        if key != "parameter_snapshot"
                    }
                    runtime_weights["strategy_weights"] = dict(snapshot.strategy_weights)
                    if build_parameter_snapshot(thresholds, runtime_weights) != identity:
                        raise CandidateSnapshotError("候选真实参数指纹冲突，拒绝混用")
                fingerprints.add(identity.fingerprint)
    if len(fingerprints) > 1:
        raise CandidateSnapshotError("候选真实参数指纹冲突，拒绝混用")
    return fingerprints


def _validate_session_parameters(
    stable: CandidateComputationSnapshot,
    batch: CandidateComputationSnapshot,
    final_picks: list[PickResult] | None,
    thresholds: Thresholds | None,
) -> None:
    # Symbols rotate; source fallbacks and regime labels are observations.
    # Actual screening inputs must still agree with the stable roster.
    if thresholds is not None and thresholds.version != stable.thresholds_version:
        raise CandidateSnapshotError("当日候选参数版本冲突，拒绝混用")
    for field in (
        "thresholds_version",
        "min_price",
        "max_price",
        "min_avg_amount",
        "strategy_weights",
    ):
        if getattr(stable, field) != getattr(batch, field):
            raise CandidateSnapshotError(f"当日候选参数冲突: {field}，拒绝混用")
    current_config = (
        candidate_request_fingerprint(thresholds.to_dict())
        if thresholds is not None
        else batch.thresholds_fingerprint
    )
    if stable.thresholds_fingerprint and current_config != stable.thresholds_fingerprint:
        raise CandidateSnapshotError("当日候选真实参数指纹冲突，拒绝混用")
    if batch.thresholds_fingerprint and current_config != batch.thresholds_fingerprint:
        raise CandidateSnapshotError("当前批次真实参数指纹冲突，拒绝混用")
    incoming = _candidate_parameter_fingerprints(batch, final_picks, thresholds)
    # Rebuilding each original runtime description with today's thresholds
    # verifies config identity without treating regime/reason text as config.
    # Without thresholds, a changed fingerprint cannot safely be explained.
    prior_snapshots = [stable]
    # An initially empty daily roster has no per-pick identity. Later additions
    # are equally frozen evidence and must constrain all subsequent batches.
    added = _session_added_picks(stable)
    prior_snapshots.extend(
        replace(stable, screened_picks=(), final_picks=(pick,))
        for pick in added
    )
    has_prior_identity = bool(stable.thresholds_fingerprint)
    for prior in prior_snapshots:
        original = _candidate_parameter_fingerprints(prior, thresholds=thresholds)
        has_prior_identity = has_prior_identity or bool(original)
        if thresholds is None and original and incoming and incoming != original:
            raise CandidateSnapshotError("当日候选真实参数指纹冲突，拒绝混用")
    if (
        not has_prior_identity
        and not stable.screened_picks
        and not stable.final_picks
        and final_picks
    ):
        raise CandidateSnapshotError("旧空候选缺少真实参数身份，无法核对，拒绝追加")


def _session_added_picks(stable: CandidateComputationSnapshot) -> list[PickResult]:
    path = candidate_increment_path()
    with advisory_lock(path):
        increments = _read_records(path)
    picks: list[PickResult] = []
    for record in increments:
        if (
            record.get("session_date") != stable.session_date
            or record.get("request_fingerprint") != stable.request_fingerprint
        ):
            continue
        payload = {key: value for key, value in record.items() if key != "checksum"}
        if record.get("checksum") != _checksum(payload):
            raise CandidateSnapshotError("候选增量校验失败")
        if record.get("status") == "added":
            pick = _parse_pick(record.get("pick"))
            _validate_pick(pick)
            picks.append(pick)
    return picks


def session_candidate_view(
    batch: CandidateComputationSnapshot | None = None,
    *,
    final_picks: list[PickResult] | None = None,
    thresholds: Thresholds | None = None,
) -> list[PickResult] | None:
    """Retain the daily roster; append proven batch discoveries without reordering.

    ``None`` means no finalized session view yet (provisional caller may use its
    current batch). Existing v2 snapshots are retained with their original
    identity; they are display evidence only, never proof of a new batch scan.
    Absence from another batch is not evidence of invalidation.
    ``thresholds`` verifies actual config against each original runtime
    fingerprint while allowing source/regime observations to evolve.
    """
    session_date = (
        batch.session_date if batch is not None else _current_trading_session_date()
    )
    path = candidate_snapshot_path()
    with advisory_lock(path):
        records = _read_records(path)
        matches = [item for item in records if item.get("session_date") == session_date]
        if len(matches) > 1:
            raise CandidateSnapshotError("当日候选冻结记录重复，拒绝覆盖")
        stable = _parse_snapshot(matches[0]) if matches else None
        if stable is not None and batch is not None:
            _validate_session_parameters(stable, batch, final_picks, thresholds)
        if stable is None:
            if final_picks is None or batch is None:
                return None
            _validate_session_parameters(batch, batch, final_picks, thresholds)
            stable = replace(batch, final_picks=tuple(final_picks), finalized=True)
            _validate_snapshot(stable)
            records.append(_serialize_snapshot(stable))
            _write_records(path, records)
        elif not stable.finalized:
            # Only the original computation may complete its interrupted
            # finalization. Screened candidates have not passed final gates.
            if (
                batch is None
                or final_picks is None
                or batch.request_fingerprint != stable.request_fingerprint
                or not batch.finalized
            ):
                raise CandidateSnapshotError(
                    "原交易日候选尚未完成最终筛选，等待同身份完成后再发布"
                )
            stable = replace(stable, final_picks=tuple(final_picks), finalized=True)
            _validate_snapshot(stable)
            records[records.index(matches[0])] = _serialize_snapshot(stable)
            _write_records(path, records)

        if final_picks is not None:
            frozen_symbols = {pick.symbol for pick in stable.final_picks}
            for pick in final_picks:
                if pick.symbol not in frozen_symbols:
                    record_candidate_increment(
                        CandidateIncrement(
                            session_date=stable.session_date,
                            observed_at=now_shanghai().isoformat(timespec="seconds"),
                            request_fingerprint=stable.request_fingerprint,
                            symbol=pick.symbol,
                            status="added",
                            reason="后续真实扫描批次发现；保留首次候选与原顺序",
                            pick=pick,
                        )
                    )
        result = list(stable.final_picks)
        seen = {pick.symbol for pick in result}
        for pick in _session_added_picks(stable):
            if pick.symbol not in seen:
                result.append(
                    replace(
                        pick,
                        metrics={
                            **pick.metrics,
                            "candidate_increment_status": "added",
                            "candidate_status": "盘中新增观察",
                        },
                    )
                )
                seen.add(pick.symbol)
        return result


def _candidate_quote_evidence(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    *,
    session_date: str,
    missing_symbols: tuple[str, ...],
    benchmark_symbol: str = "",
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    missing = set(missing_symbols)
    for symbol in symbols:
        frame = frames.get(symbol)
        if symbol == benchmark_symbol or frame is None or frame.empty:
            continue
        dates = pd.to_datetime(frame.get("date"), errors="coerce")
        if dates is None or dates.dropna().empty:
            continue
        latest = dates.max()
        current_time = now_shanghai()
        latest_at = (
            latest.tz_localize(current_time.tzinfo)
            if latest.tzinfo is None
            else latest.tz_convert(current_time.tzinfo)
        )
        attrs = frame.attrs
        source = str(attrs.get("source_name") or attrs.get("source") or "")
        timestamp_source = str(attrs.get("timestamp_source") or "")
        fetched_at = str(attrs.get("fetched_at") or "")
        try:
            received = _parse_aware_datetime(fetched_at, "行情取得时间")
            close = float(frame.loc[dates.idxmax(), "close"])
        except (CandidateSnapshotError, TypeError, ValueError, KeyError):
            continue
        if (
            symbol in missing
            or latest_at.date().isoformat() != session_date
            or latest_at > current_time
            or not source
            or not timestamp_source
            or received > now_shanghai()
            or received.astimezone(now_shanghai().tzinfo).date().isoformat()
            != session_date
            or attrs.get("cache_fallback")
            or str(attrs.get("freshness", "")).lower()
            in {"cached", "stale", "historical"}
            or not math.isfinite(close)
            or close <= 0
        ):
            continue
        evidence.append(
            {
                "symbol": symbol,
                "source": source,
                "source_time": latest_at.date().isoformat(),
                "source_time_precision": "day",
                "timestamp_source": timestamp_source,
                "fetched_at": fetched_at,
                "close": close,
            }
        )
    return evidence


def refresh_candidate_observations(
    picks: list[PickResult],
    frames: dict[str, pd.DataFrame],
    *,
    missing_symbols: tuple[str, ...] = (),
) -> list[PickResult]:
    """Refresh quote evidence without rescoring or changing frozen thresholds.

    A rotating batch does not refresh symbols outside that batch. Those rows
    explicitly remain unknown rather than inheriting the aggregate run tier.
    """
    evidence = {
        item["symbol"]: item
        for item in _candidate_quote_evidence(
            [pick.symbol for pick in picks],
            frames,
            session_date=_current_trading_session_date(),
            missing_symbols=missing_symbols,
        )
    }
    result = []
    for pick in picks:
        quote = evidence.get(pick.symbol, {})
        # technical_source belongs to the scored bar. Quote receipt metadata
        # below must never replace or backfill that original research basis.
        metrics = {
            **pick.metrics,
            "data_source": quote.get("source", ""),
            "data_fetched_at": quote.get("fetched_at", ""),
            "data_timestamp_source": quote.get("timestamp_source", ""),
            "data_source_time": quote.get("source_time", ""),
            "data_source_time_precision": quote.get("source_time_precision", ""),
            "freshness": "fresh" if quote else "unknown",
        }
        result.append(
            replace(pick, close=float(quote.get("close", pick.close)), metrics=metrics)
        )
    return result


def write_candidate_scan_receipt(
    output_csv: str,
    *,
    session_date: str,
    request_fingerprint: str,
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    missing_symbols: tuple[str, ...],
    benchmark_symbol: str,
) -> None:
    """Write bounded evidence of this fetch, never candidate-count coverage.

    Source time retains its available daily precision; fetched_at is recorded
    separately and never substituted for a vendor time. No historical bars are
    serialized. The run-local path prevents reuse of another run's receipt.
    """
    requested = list(dict.fromkeys(symbols))
    evidence = _candidate_quote_evidence(
        requested,
        frames,
        session_date=session_date,
        missing_symbols=missing_symbols,
        benchmark_symbol=benchmark_symbol,
    )
    covered = {item["symbol"] for item in evidence}
    payload = {
        "schema_version": 1,
        "session_date": session_date,
        "observed_at": now_shanghai().isoformat(timespec="seconds"),
        "request_fingerprint": request_fingerprint,
        "requested_symbols": requested,
        "scanned_symbols": requested,
        "fetched_symbols": [
            symbol
            for symbol in requested
            if symbol != benchmark_symbol
            and symbol in frames
            and not frames[symbol].empty
        ],
        "valid_quotes": evidence,
        "missing_symbols": [symbol for symbol in requested if symbol not in covered],
        "supplemental_fetched_symbols": [
            symbol
            for symbol, frame in frames.items()
            if symbol not in requested
            and symbol != benchmark_symbol
            and not frame.empty
        ],
    }
    atomic_write_text(
        output_csv + ".scan.json",
        json.dumps(
            {**payload, "checksum": _checksum(payload)},
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )


def validate_candidate_scan_receipt(
    output_csv: str,
    *,
    expected_symbols: list[str],
    minimum_valid_ratio: float,
) -> int:
    """Require current-run evidence for exactly the selected scan membership."""
    records = _read_records(Path(output_csv + ".scan.json"))
    if len(records) != 1:
        raise CandidateSnapshotError("盘中批次缺少唯一扫描凭据")
    record = records[0]
    payload = {key: value for key, value in record.items() if key != "checksum"}
    if record.get("checksum") != _checksum(payload):
        raise CandidateSnapshotError("盘中扫描凭据校验失败")
    expected = list(dict.fromkeys(expected_symbols))
    if (
        not expected
        or len(expected) != len(expected_symbols)
        or record.get("requested_symbols") != expected
        or record.get("scanned_symbols") != expected
    ):
        raise CandidateSnapshotError("盘中扫描凭据与当前批次标的不符")
    session = _current_trading_session_date()
    if record.get("session_date") != session:
        raise CandidateSnapshotError("盘中扫描凭据不属于当前交易日")
    if not record.get("request_fingerprint"):
        raise CandidateSnapshotError("盘中扫描凭据缺少请求身份")
    quotes = record.get("valid_quotes", [])
    valid = [item.get("symbol") for item in quotes]
    fetched = record.get("fetched_symbols", [])
    if (
        len(set(valid)) != len(valid)
        or not set(valid).issubset(expected)
        or not set(valid).issubset(fetched)
        or not set(fetched).issubset(expected)
    ):
        raise CandidateSnapshotError("盘中扫描覆盖代码无效")
    for quote in quotes:
        received = _parse_aware_datetime(quote.get("fetched_at", ""), "行情取得时间")
        if (
            quote.get("source_time") != session
            or not quote.get("source")
            or not quote.get("timestamp_source")
            or received > now_shanghai()
            or received.astimezone(now_shanghai().tzinfo).date().isoformat() != session
        ):
            raise CandidateSnapshotError("盘中扫描缺少有效的同日来源证据")
    minimum = max(1, math.ceil(len(expected) * minimum_valid_ratio))
    if len(valid) < minimum:
        raise CandidateSnapshotError(
            f"盘中批次有效报价低于最低覆盖: {len(valid)}/{len(expected)}，最低 {minimum}"
        )
    return len(expected)


def candidate_freeze_enabled() -> bool:
    """Return whether a process has an explicit persistent runtime data root.

    Production entrypoints always set ``AQSP_RUNTIME_DATA_ROOT``.  Keeping a
    bare developer invocation ephemeral prevents a local command or test from
    accidentally reusing another process's fallback snapshot in ``/tmp``.
    """
    return bool(
        str(os.environ.get("AQSP_RUNTIME_DATA_ROOT", "") or "").strip()
        or str(os.environ.get("AQSP_CANDIDATE_COMPUTATION_PATH", "") or "").strip()
    )


def record_candidate_increment(
    increment: CandidateIncrement,
    path: str | Path | None = None,
) -> None:
    """Persist an explicitly supplied incremental observation separately.

    The scoring snapshot is never edited here.  Callers must provide a reason
    and a trusted input result; missing incremental coverage remains unknown.
    """
    if increment.status not in {"added", "invalidated"}:
        raise CandidateSnapshotError("候选增量状态必须是 added 或 invalidated")
    if not increment.symbol.strip() or not increment.reason.strip():
        raise CandidateSnapshotError("候选增量缺少标的或可复核原因")
    if not increment.request_fingerprint:
        raise CandidateSnapshotError("候选增量缺少冻结配置身份")
    session_date = _parse_canonical_date(increment.session_date, "候选增量交易日")
    if session_date.isoformat() != _current_trading_session_date():
        raise CandidateSnapshotError(
            "候选增量只能记录当前交易日，过期/未来记录拒绝写入"
        )
    observed_at = _parse_aware_datetime(increment.observed_at, "候选增量观察时间")
    if observed_at > now_shanghai():
        raise CandidateSnapshotError("候选增量观察时间不能晚于当前时间")
    if increment.pick is not None:
        if increment.pick.symbol != increment.symbol:
            raise CandidateSnapshotError("候选增量 PickResult 与代码不一致")
        _validate_pick(increment.pick)
    elif increment.status == "added":
        raise CandidateSnapshotError("新增候选必须附带同代码的完整 PickResult")
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "session_date": increment.session_date,
        "observed_at": increment.observed_at,
        "request_fingerprint": increment.request_fingerprint,
        "symbol": increment.symbol,
        "status": increment.status,
        "reason": increment.reason,
        "pick": _serialize_pick(increment.pick) if increment.pick is not None else None,
    }
    record = {**payload, "checksum": _checksum(payload)}
    file_path = Path(path) if path is not None else candidate_increment_path()
    with advisory_lock(file_path):
        existing = _read_records(file_path)
        identity = ("session_date", "request_fingerprint", "symbol", "status")
        for previous in existing:
            if all(previous.get(key) == record.get(key) for key in identity):
                previous_payload = {
                    key: value for key, value in previous.items() if key != "checksum"
                }
                if previous.get("checksum") != _checksum(previous_payload):
                    raise CandidateSnapshotError("候选增量校验失败，拒绝覆盖")
                return
        existing.append(record)
        _write_records(file_path, existing)


def candidate_request_fingerprint(request: dict[str, Any]) -> str:
    """Hash normalized scoring inputs without persisting potentially sensitive values."""
    normalized = _to_json_value(request)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_candidate_snapshot(
    session_date: str,
    request_fingerprint: str,
    path: str | Path | None = None,
) -> CandidateComputationSnapshot | None:
    """Load a validated same-session scoring snapshot; reject stale identities."""
    _parse_canonical_date(session_date, "候选冻结交易日")
    if session_date != _current_trading_session_date():
        raise CandidateSnapshotError(
            "只允许读取当前交易日的候选冻结，历史快照不得冒充今日"
        )
    file_path = Path(path) if path is not None else candidate_snapshot_path()
    if not file_path.exists():
        return None

    with advisory_lock(file_path):
        records = _read_records(file_path)
    matching = [
        record for record in records if record.get("session_date") == session_date
    ]
    if not matching:
        return None
    if len(matching) != 1:
        raise CandidateSnapshotError(
            f"当日候选冻结记录重复: {session_date}，拒绝覆盖或猜测"
        )

    record = matching[0]
    snapshot = _parse_snapshot(record)
    if snapshot.request_fingerprint != request_fingerprint:
        raise CandidateSnapshotError(
            f"当日候选冻结配置身份冲突: {session_date}，拒绝混用参数与股票池"
        )
    return snapshot


def freeze_scored_candidates(
    *,
    session_date: str,
    request_fingerprint: str,
    source: str,
    data_latest_trade_date: str,
    thresholds_version: str,
    regime: str,
    strategy_weights: dict[str, float],
    resolved_symbols: list[str],
    min_price: float,
    max_price: float,
    min_avg_amount: float,
    picks: list[PickResult],
    path: str | Path | None = None,
    thresholds: Thresholds | None = None,
) -> CandidateComputationSnapshot:
    """Freeze the first complete scored list, including a valid empty list."""
    session = _parse_canonical_date(session_date, "候选冻结交易日")
    if session.isoformat() != _current_trading_session_date():
        raise CandidateSnapshotError(
            "候选冻结只能写入当前交易日，过期/未来日期拒绝写入"
        )
    file_path = Path(path) if path is not None else candidate_snapshot_path()
    created = now_shanghai().isoformat(timespec="seconds")
    candidate = CandidateComputationSnapshot(
        session_date=session.isoformat(),
        created_at=created,
        request_fingerprint=request_fingerprint,
        source=source,
        data_latest_trade_date=data_latest_trade_date,
        thresholds_version=thresholds_version,
        regime=regime,
        strategy_weights={
            str(key): float(value) for key, value in strategy_weights.items()
        },
        resolved_symbols=tuple(str(symbol).strip() for symbol in resolved_symbols),
        resolved_symbol_count=len(resolved_symbols),
        min_price=float(min_price),
        max_price=float(max_price),
        min_avg_amount=float(min_avg_amount),
        screened_picks=tuple(picks),
        thresholds_fingerprint=(
            candidate_request_fingerprint(thresholds.to_dict())
            if thresholds is not None
            else ""
        ),
    )
    _validate_snapshot(candidate)

    with advisory_lock(file_path):
        records = _read_records(file_path)
        matching = [
            record for record in records if record.get("session_date") == session_date
        ]
        if matching:
            if len(matching) != 1:
                raise CandidateSnapshotError(
                    f"当日候选冻结记录重复: {session_date}，拒绝覆盖或猜测"
                )
            existing = _parse_snapshot(matching[0])
            if existing.request_fingerprint != request_fingerprint:
                raise CandidateSnapshotError(
                    f"当日候选冻结配置身份冲突: {session_date}，拒绝混用参数与股票池"
                )
            return existing
        records.append(_serialize_snapshot(candidate))
        _write_records(file_path, records)
    return candidate


def freeze_final_candidates(
    snapshot: CandidateComputationSnapshot,
    picks: list[PickResult],
    path: str | Path | None = None,
) -> CandidateComputationSnapshot:
    """Freeze the first final roster and order for a session."""
    if snapshot.session_date != _current_trading_session_date():
        raise CandidateSnapshotError("候选正式列表只能冻结当前交易日")
    file_path = Path(path) if path is not None else candidate_snapshot_path()
    with advisory_lock(file_path):
        records = _read_records(file_path)
        matches = [
            (index, record)
            for index, record in enumerate(records)
            if record.get("session_date") == snapshot.session_date
        ]
        if len(matches) != 1:
            raise CandidateSnapshotError(
                f"候选评分快照缺失或重复: {snapshot.session_date}"
            )
        index, record = matches[0]
        existing = _parse_snapshot(record)
        if existing.request_fingerprint != snapshot.request_fingerprint:
            raise CandidateSnapshotError(
                f"当日候选冻结配置身份冲突: {snapshot.session_date}"
            )
        if existing.finalized:
            return existing
        finalized = CandidateComputationSnapshot(
            **{
                **existing.__dict__,
                "final_picks": tuple(picks),
                "finalized": True,
            }
        )
        _validate_snapshot(finalized)
        records[index] = _serialize_snapshot(finalized)
        _write_records(file_path, records)
    return finalized


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CandidateSnapshotError(
                f"候选冻结文件第 {line_number} 行 JSON 损坏，拒绝复算覆盖"
            ) from exc
        if not isinstance(record, dict):
            raise CandidateSnapshotError(f"候选冻结文件第 {line_number} 行结构无效")
        records.append(record)
    return records


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    normalized = sorted(records, key=lambda item: str(item.get("session_date", "")))
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for record in normalized
    )
    atomic_write_text(path, content)


def _serialize_snapshot(snapshot: CandidateComputationSnapshot) -> dict[str, Any]:
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "session_date": snapshot.session_date,
        "created_at": snapshot.created_at,
        "request_fingerprint": snapshot.request_fingerprint,
        "source": snapshot.source,
        "data_latest_trade_date": snapshot.data_latest_trade_date,
        "thresholds_version": snapshot.thresholds_version,
        "regime": snapshot.regime,
        "strategy_weights": snapshot.strategy_weights,
        "resolved_symbols": list(snapshot.resolved_symbols),
        "resolved_symbol_count": snapshot.resolved_symbol_count,
        "min_price": snapshot.min_price,
        "max_price": snapshot.max_price,
        "min_avg_amount": snapshot.min_avg_amount,
        "screened_picks": [_serialize_pick(pick) for pick in snapshot.screened_picks],
        "final_picks": [_serialize_pick(pick) for pick in snapshot.final_picks],
        "finalized": snapshot.finalized,
    }
    if snapshot.thresholds_fingerprint:
        payload["thresholds_fingerprint"] = snapshot.thresholds_fingerprint
    return {**payload, "checksum": _checksum(payload)}


def _parse_snapshot(record: dict[str, Any]) -> CandidateComputationSnapshot:
    checksum = str(record.get("checksum", "") or "")
    payload = {key: value for key, value in record.items() if key != "checksum"}
    if record.get("schema_version") != _SCHEMA_VERSION or not checksum:
        raise CandidateSnapshotError("候选冻结版本缺失或不兼容，拒绝复算覆盖")
    if checksum != _checksum(payload):
        raise CandidateSnapshotError("候选冻结校验失败，拒绝使用损坏评分结果")
    try:
        snapshot = CandidateComputationSnapshot(
            session_date=str(record["session_date"]),
            created_at=str(record["created_at"]),
            request_fingerprint=str(record["request_fingerprint"]),
            source=str(record["source"]),
            data_latest_trade_date=str(record["data_latest_trade_date"]),
            thresholds_version=str(record["thresholds_version"]),
            regime=str(record["regime"]),
            strategy_weights={
                str(key): float(value)
                for key, value in dict(record["strategy_weights"]).items()
            },
            resolved_symbols=tuple(str(item) for item in record["resolved_symbols"]),
            resolved_symbol_count=int(record["resolved_symbol_count"]),
            min_price=float(record["min_price"]),
            max_price=float(record["max_price"]),
            min_avg_amount=float(record["min_avg_amount"]),
            screened_picks=tuple(
                _parse_pick(item) for item in record["screened_picks"]
            ),
            final_picks=tuple(_parse_pick(item) for item in record["final_picks"]),
            finalized=bool(record["finalized"]),
            thresholds_fingerprint=str(record.get("thresholds_fingerprint", "")),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise CandidateSnapshotError("候选冻结数据结构无效，拒绝使用") from exc
    _validate_snapshot(snapshot)
    return snapshot


def _serialize_pick(pick: PickResult) -> dict[str, Any]:
    return {
        field.name: _to_json_value(getattr(pick, field.name))
        for field in fields(PickResult)
    }


def _parse_pick(value: Any) -> PickResult:
    if not isinstance(value, dict):
        raise TypeError("candidate must be an object")
    expected = {field.name for field in fields(PickResult)}
    if set(value) != expected:
        raise ValueError("candidate fields are incomplete or incompatible")
    values = dict(value)
    for key in ("strategies", "reasons", "risks"):
        values[key] = tuple(str(item) for item in values.get(key, ()))
    values["metrics"] = dict(values.get("metrics", {}))
    return PickResult(**values)


def _validate_snapshot(snapshot: CandidateComputationSnapshot) -> None:
    if snapshot.thresholds_fingerprint and (
        len(snapshot.thresholds_fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in snapshot.thresholds_fingerprint)
    ):
        raise CandidateSnapshotError("候选冻结真实参数指纹无效")
    day = _parse_canonical_date(snapshot.session_date, "候选冻结交易日")
    created = _parse_aware_datetime(snapshot.created_at, "候选冻结创建时间")
    latest = _parse_canonical_date(snapshot.data_latest_trade_date, "候选冻结行情日期")
    if day > today_shanghai():
        raise CandidateSnapshotError("候选冻结交易日不能晚于当前日期")
    if created > now_shanghai():
        raise CandidateSnapshotError("候选冻结创建时间不能晚于当前时间")
    if latest > day:
        raise CandidateSnapshotError("候选冻结行情日期晚于所属交易日")
    if created.astimezone(now_shanghai().tzinfo).date() < day:
        raise CandidateSnapshotError("候选冻结创建时间早于所属交易日")
    if (
        not snapshot.request_fingerprint
        or not snapshot.thresholds_version
        or not snapshot.source.strip()
    ):
        raise CandidateSnapshotError("候选冻结缺少配置身份或阈值版本")
    if (
        snapshot.resolved_symbol_count != len(snapshot.resolved_symbols)
        or snapshot.resolved_symbol_count <= 0
        or any(not symbol for symbol in snapshot.resolved_symbols)
        or len(set(snapshot.resolved_symbols)) != len(snapshot.resolved_symbols)
    ):
        raise CandidateSnapshotError("候选冻结股票池计数无效")
    if not all(
        math.isfinite(value)
        for value in (snapshot.min_price, snapshot.max_price, snapshot.min_avg_amount)
    ):
        raise CandidateSnapshotError("候选冻结筛选参数无效")
    if snapshot.min_price > snapshot.max_price:
        raise CandidateSnapshotError("候选冻结价格区间无效")
    if any(not math.isfinite(value) for value in snapshot.strategy_weights.values()):
        raise CandidateSnapshotError("候选冻结策略权重无效")
    for pick in (*snapshot.screened_picks, *snapshot.final_picks):
        _validate_pick(pick)


def _validate_pick(pick: PickResult) -> None:
    if not isinstance(pick, PickResult):
        raise CandidateSnapshotError("候选冻结包含非 PickResult 数据")
    if (
        not pick.symbol.strip()
        or not isinstance(pick.name, str)
        or not pick.name.strip()
    ):
        raise CandidateSnapshotError("候选冻结包含缺少代码或名称的 PickResult")
    _parse_canonical_date(pick.date, "候选信号日期")
    if not isinstance(pick.metrics, dict):
        raise CandidateSnapshotError("候选冻结包含无效的 PickResult metrics")
    for value in (
        pick.close,
        pick.score,
        pick.ideal_buy,
        pick.stop_loss,
        pick.take_profit,
        pick.adjusted_score,
        pick.confidence,
        pick.regime_score,
    ):
        if not math.isfinite(float(value)):
            raise CandidateSnapshotError("候选冻结包含非有限 PickResult 数值")
    _serialize_pick(pick)


def _parse_canonical_date(value: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CandidateSnapshotError(f"{label}无效") from exc
    if parsed.isoformat() != value:
        raise CandidateSnapshotError(f"{label}必须使用 YYYY-MM-DD 格式")
    return parsed


def _parse_aware_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CandidateSnapshotError(f"{label}无效") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateSnapshotError(f"{label}必须包含时区")
    return parsed


def _current_trading_session_date() -> str:
    from aqsp.portfolio.snapshot import snapshot_trading_date

    return snapshot_trading_date(today_shanghai().isoformat())


def _checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _to_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("non-finite floats cannot be frozen")
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _to_json_value(value.value)
    if is_dataclass(value):
        return _to_json_value(
            {item.name: getattr(value, item.name) for item in fields(value)}
        )
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        normalized_items = [_to_json_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(
                normalized_items,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
        return normalized_items
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _to_json_value(item())
        except (TypeError, ValueError):
            pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _to_json_value(tolist())
        except (TypeError, ValueError):
            pass
    raise TypeError(f"unsupported candidate snapshot value: {type(value).__name__}")
