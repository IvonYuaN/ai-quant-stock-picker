"""Persist the first valid candidate computation for each trading session."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from aqsp.core.runtime import runtime_data_root
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.core.types import PickResult
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


def candidate_snapshot_path() -> Path:
    """Return the private runtime path for candidate computation snapshots."""
    configured = str(os.environ.get("AQSP_CANDIDATE_COMPUTATION_PATH", "") or "").strip()
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
        raise CandidateSnapshotError("候选增量只能记录当前交易日，过期/未来记录拒绝写入")
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
        existing = []
        if file_path.exists():
            existing = [
                line
                for line in file_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        existing.append(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False))
        atomic_write_text(file_path, "\n".join(existing) + "\n")


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
        raise CandidateSnapshotError("只允许读取当前交易日的候选冻结，历史快照不得冒充今日")
    file_path = Path(path) if path is not None else candidate_snapshot_path()
    if not file_path.exists():
        return None

    with advisory_lock(file_path):
        records = _read_records(file_path)
    matching = [record for record in records if record.get("session_date") == session_date]
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
) -> CandidateComputationSnapshot:
    """Freeze the first complete scored list, including a valid empty list."""
    session = _parse_canonical_date(session_date, "候选冻结交易日")
    if session.isoformat() != _current_trading_session_date():
        raise CandidateSnapshotError("候选冻结只能写入当前交易日，过期/未来日期拒绝写入")
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
        strategy_weights={str(key): float(value) for key, value in strategy_weights.items()},
        resolved_symbols=tuple(str(symbol).strip() for symbol in resolved_symbols),
        resolved_symbol_count=len(resolved_symbols),
        min_price=float(min_price),
        max_price=float(max_price),
        min_avg_amount=float(min_avg_amount),
        screened_picks=tuple(picks),
    )
    _validate_snapshot(candidate)

    with advisory_lock(file_path):
        records = _read_records(file_path)
        matching = [record for record in records if record.get("session_date") == session_date]
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
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CandidateSnapshotError(
                f"候选冻结文件第 {line_number} 行 JSON 损坏，拒绝复算覆盖"
            ) from exc
        if not isinstance(record, dict):
            raise CandidateSnapshotError(
                f"候选冻结文件第 {line_number} 行结构无效"
            )
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
            screened_picks=tuple(_parse_pick(item) for item in record["screened_picks"]),
            final_picks=tuple(_parse_pick(item) for item in record["final_picks"]),
            finalized=bool(record["finalized"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise CandidateSnapshotError("候选冻结数据结构无效，拒绝使用") from exc
    _validate_snapshot(snapshot)
    return snapshot


def _serialize_pick(pick: PickResult) -> dict[str, Any]:
    return {field.name: _to_json_value(getattr(pick, field.name)) for field in fields(PickResult)}


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
    if not pick.symbol.strip() or not isinstance(pick.name, str) or not pick.name.strip():
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
        return _to_json_value({item.name: getattr(value, item.name) for item in fields(value)})
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
