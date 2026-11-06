"""Bounded, tenant-scoped daily scans over enabled condition-signal definitions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

import pandas as pd
import portfolio as pf
import tenant
from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data.source_factory import build_data_source
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

import signal_definitions


_LOCK = threading.RLock()


@dataclass(frozen=True)
class SignalScanLimits:
    max_signals: int = 5
    max_symbols: int = 20
    max_calendar_days: int = 120
    max_result_hits: int = 500
    max_retained_runs: int = 20


LIMITS = SignalScanLimits()


class SignalScanConflict(ValueError):
    """A bounded scan cannot be stored without explicit user cleanup."""


def _root() -> Path:
    tenant_id = tenant.current_tenant.get()
    root = Path(pf.CACHE_DIR)
    if tenant_id != "local":
        root = root / "users" / tenant_id
    return root


def _store_path() -> Path:
    return _root() / "signal_scan_runs.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("信号扫描记录不可读取，已停止写入以保护原记录") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("items"), list)
        or any(not isinstance(item, dict) for item in payload["items"])
    ):
        raise ValueError("信号扫描记录格式无效，已停止写入以保护原记录")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
    )


def list_runs(*, limit: int = LIMITS.max_retained_runs) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= LIMITS.max_retained_runs:
        raise ValueError(f"limit 必须在 1 到 {LIMITS.max_retained_runs} 之间")
    with _LOCK:
        rows = _load(_store_path())["items"]
    return sorted(rows, key=lambda row: str(row.get("generated_at", "")), reverse=True)[:limit]


def delete_run(run_id: str) -> bool:
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        raise ValueError("信号扫描记录不存在")
    path = _store_path()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        existing = next((row for row in payload["items"] if row.get("run_id") == run_id), None)
        if existing is None:
            return False
        payload["items"].remove(existing)
        _write(path, payload)
        return True


def _parse_request(
    *, signal_ids: list[str], symbols: list[str], start: str, end: str
) -> tuple[list[str], list[str], date, date]:
    clean_signal_ids = list(dict.fromkeys(str(item).strip() for item in signal_ids if str(item).strip()))
    clean_symbols = list(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
    if not 1 <= len(clean_signal_ids) <= LIMITS.max_signals:
        raise ValueError(f"每次需选择 1 到 {LIMITS.max_signals} 个 entry/both 信号")
    if not 1 <= len(clean_symbols) <= LIMITS.max_symbols:
        raise ValueError(f"每次需提供 1 到 {LIMITS.max_symbols} 个六位 A 股代码")
    if any(len(item) != 6 or not item.isdigit() for item in clean_symbols):
        raise ValueError("symbols 必须是六位数字 A 股代码")
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("扫描日期必须使用 YYYY-MM-DD") from exc
    if start_date > end_date:
        raise ValueError("扫描开始日不能晚于结束日")
    if (end_date - start_date).days > LIMITS.max_calendar_days:
        raise ValueError(f"单次扫描区间最多 {LIMITS.max_calendar_days} 个自然日")
    if end_date > latest_completed_trading_day(now_shanghai()):
        raise ValueError("扫描结束日不能晚于最近已完成交易日")
    return clean_signal_ids, clean_symbols, start_date, end_date


def _definitions(signal_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): item for item in signal_definitions.list_signals()}
    selected: list[dict[str, Any]] = []
    for signal_id in signal_ids:
        definition = by_id.get(signal_id)
        if definition is None:
            raise ValueError(f"条件信号不存在或已移出当前租户：{signal_id}")
        if definition.get("enabled") is not True:
            raise ValueError(f"条件信号已停用：{definition.get('name') or signal_id}")
        if definition.get("kind") not in {"entry", "both"}:
            raise ValueError(f"退出信号不能用于 entry 扫描：{definition.get('name') or signal_id}")
        selected.append(definition)
    return selected


def _pit_records(source: Any, symbol: str, frame: pd.DataFrame) -> dict[date, Any]:
    dates = [date.fromisoformat(str(value)[:10]) for value in frame["date"]]
    response = source.get_point_in_time_adjustment_factors([symbol], dates)
    return response.get(symbol, {}) if isinstance(response, dict) else {}


def _required_pit_dates(
    definition: dict[str, Any], frame: pd.DataFrame, start: date, end: date
) -> set[date]:
    offsets = signal_definitions.price_factor_offsets(definition)
    if not offsets:
        return set()
    dates = [date.fromisoformat(str(value)[:10]) for value in frame["date"]]
    result_indexes = [index for index, day in enumerate(dates) if start <= day <= end]
    required: set[date] = set()
    for index in result_indexes:
        for offset in offsets:
            prior_index = index - offset
            if prior_index >= 0:
                required.add(dates[index])
                required.add(dates[prior_index])
    return required


def _persist(run: dict[str, Any]) -> None:
    path = _store_path()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        items = payload["items"]
        if len(items) >= LIMITS.max_retained_runs:
            raise SignalScanConflict(
                f"扫描记录已达 {LIMITS.max_retained_runs} 条上限；请先显式删除旧记录"
            )
        items.append(run)
        _write(path, payload)


def _assert_capacity() -> None:
    with _LOCK:
        if len(_load(_store_path())["items"]) >= LIMITS.max_retained_runs:
            raise SignalScanConflict(
                f"扫描记录已达 {LIMITS.max_retained_runs} 条上限；请先显式删除旧记录"
            )


def run_signal_scan(
    *, signal_ids: list[str], symbols: list[str], start: str, end: str
) -> dict[str, Any]:
    """Run one raw daily entry-signal scan and persist its tenant-local evidence."""
    clean_signal_ids, clean_symbols, start_date, end_date = _parse_request(
        signal_ids=signal_ids, symbols=symbols, start=start, end=end
    )
    definitions = _definitions(clean_signal_ids)
    _assert_capacity()
    source = build_data_source("sqlite_db", cache=None)
    source_id = str(getattr(source, "name", ""))
    mode_fn = getattr(source, "price_mode", None)
    price_mode = str(mode_fn()) if callable(mode_fn) else "unknown"
    if source_id != "sqlite_db" or price_mode != "raw":
        raise DataError("条件信号扫描要求可核验的 sqlite_db raw 不复权数据源")

    market_as_of = source.get_latest_daily_date()
    market_day = market_as_of.isoformat() if market_as_of else None
    blockers: list[dict[str, str]] = []
    if market_as_of is None or market_as_of < end_date:
        message = f"市场日线最新数据 {market_day or '缺失'} 早于请求结束日 {end_date.isoformat()}"
        blockers.extend({"symbol": symbol, "reason": message} for symbol in clean_symbols)
        return _finish_scan(
            definitions, clean_signal_ids, clean_symbols, start_date, end_date,
            source_id, price_mode, market_day, [], blockers, scanned_symbols=0,
        )

    latest_by_symbol = source.get_latest_daily_dates(clean_symbols)
    fresh_symbols: list[str] = []
    for symbol in clean_symbols:
        actual = latest_by_symbol.get(symbol)
        if actual is None or actual < end_date:
            blockers.append({
                "symbol": symbol,
                "reason": f"该标的数据截至 {actual.isoformat() if actual else '缺失'}，早于请求结束日 {end_date.isoformat()}",
            })
        else:
            fresh_symbols.append(symbol)

    max_lookback = max(signal_definitions.max_lookback(item) for item in definitions)
    warmup_start = start_date - timedelta(days=max(90, max_lookback * 3))
    panel: dict[str, pd.DataFrame] = {}
    if fresh_symbols:
        try:
            frames = source.fetch_daily(fresh_symbols, warmup_start, end_date, adjust="")
        except (DataError, OSError) as exc:
            blockers.extend({"symbol": symbol, "reason": f"raw 日线覆盖不可用：{exc}"} for symbol in fresh_symbols)
            frames = {}
        for symbol in fresh_symbols:
            frame = frames.get(symbol)
            if frame is None or frame.empty:
                blockers.append({"symbol": symbol, "reason": "raw 日线没有返回该标的数据"})
                continue
            frame = frame.copy()
            parsed = pd.to_datetime(frame.get("date"), errors="coerce")
            if parsed.isna().any():
                blockers.append({"symbol": symbol, "reason": "raw 日线包含无效日期"})
                continue
            frame["date"] = parsed.dt.strftime("%Y-%m-%d")
            frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
            if frame["date"].duplicated().any():
                blockers.append({"symbol": symbol, "reason": "raw 日线包含重复交易日"})
                continue
            actual_day = date.fromisoformat(str(frame["date"].iloc[-1]))
            if actual_day < end_date:
                blockers.append({
                    "symbol": symbol,
                    "reason": f"请求结束日实际无日线：最新 {actual_day.isoformat()}，请求 {end_date.isoformat()}",
                })
                continue
            frame["raw_close"] = pd.to_numeric(frame.get("raw_close", frame["close"]), errors="coerce")
            frame["prev_close"] = pd.to_numeric(frame["close"], errors="coerce").shift(1)
            panel[symbol] = frame

    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for symbol, frame in panel.items():
        if not any(signal_definitions.price_factor_offsets(item) for item in definitions):
            factors: dict[date, Any] = {}
        else:
            try:
                factors = _pit_records(source, symbol, frame)
            except (DataError, OSError) as exc:
                blockers.append({"symbol": symbol, "reason": f"PIT 因子读取失败：{exc}"})
                continue
            frame["adj_factor"] = [
                factors[item].value if item in factors else float("nan")
                for item in (date.fromisoformat(str(value)) for value in frame["date"])
            ]
        for definition in definitions:
            required_dates = _required_pit_dates(definition, frame, start_date, end_date)
            missing_dates = sorted(day for day in required_dates if day not in factors)
            if missing_dates:
                blockers.append({
                    "symbol": symbol,
                    "reason": f"{definition['name']} 缺 PIT 复权因子：{', '.join(day.isoformat() for day in missing_dates[:5])}",
                })
                continue
            try:
                evaluated = signal_definitions.evaluate_signal_series(definition, frame)
            except (DataError, ValueError) as exc:
                blockers.append({"symbol": symbol, "reason": f"{definition['name']} 无法求值：{exc}"})
                continue
            for index, is_hit in enumerate(evaluated.tolist()):
                day = str(frame["date"].iloc[index])
                if not start_date.isoformat() <= day <= end_date.isoformat() or not is_hit:
                    continue
                key = (symbol, day)
                current = hits.setdefault(key, {"symbol": symbol, "date": day, "raw_close": float(frame["raw_close"].iloc[index]), "signals": []})
                current["signals"].append({
                    "id": definition["id"], "name": definition["name"],
                    "kind": definition["kind"], "revision": definition["revision"],
                })

    ordered_hits = [
        {**row, "signal_ids": [signal["id"] for signal in row["signals"]]}
        for _key, row in sorted(hits.items(), key=lambda item: (item[0][1], item[0][0]))
    ]
    return _finish_scan(
        definitions, clean_signal_ids, clean_symbols, start_date, end_date,
        source_id, price_mode, market_day, ordered_hits, blockers,
        scanned_symbols=len(panel),
    )


def _finish_scan(
    definitions: list[dict[str, Any]],
    signal_ids: list[str],
    symbols: list[str],
    start: date,
    end: date,
    source: str,
    price_mode: str,
    market_as_of: str | None,
    hits: list[dict[str, Any]],
    blockers: list[dict[str, str]],
    *,
    scanned_symbols: int,
) -> dict[str, Any]:
    truncated = len(hits) > LIMITS.max_result_hits
    status = "blocked" if scanned_symbols == 0 else "partial" if blockers else "ready"
    run = {
        "run_id": uuid4().hex,
        "generated_at": now_shanghai().isoformat(),
        "status": status,
        "signal_ids": signal_ids,
        "signals": [
            {key: item[key] for key in ("id", "name", "kind", "revision")}
            for item in definitions
        ],
        "signal_hashes": {
            str(item["id"]): hashlib.sha256(
                json.dumps(
                    {
                        "id": item["id"],
                        "kind": item["kind"],
                        "conditions": item["conditions"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            for item in definitions
        },
        "symbols": symbols,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "market_as_of": market_as_of,
        "source": source,
        "price_mode": price_mode,
        "requested_symbols": len(symbols),
        "scanned_symbols": scanned_symbols,
        "hit_count": len(hits),
        "hits_truncated": truncated,
        "hits": hits[:LIMITS.max_result_hits],
        "blockers": blockers,
        "advisory_only": True,
        "promotion_authorized": False,
        "formal_ledger_written": False,
    }
    _persist(run)
    return run
