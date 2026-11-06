"""Persistent, tenant-scoped full-universe daily strategy scans."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any
from uuid import uuid4

import pandas as pd
import portfolio as pf
import tenant
from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data.source_factory import build_data_source
from aqsp.indicators import enrich_indicators
from aqsp.research.strategy_expression import compile_strategy_expression
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

import strategy_definitions


_LOCK = threading.RLock()
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_A_SHARE_PREFIXES = (
    "600", "601", "603", "605", "688", "689",
    "000", "001", "002", "003", "300", "301",
    "4", "8", "92",
)
_PRICE_COLUMNS = ("open", "high", "low", "close")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out", "unknown"})
_CANCEL_REQUESTED = False


@dataclass(frozen=True)
class StrategyScanLimits:
    max_universe_symbols: int = 10_000
    batch_size: int = 100
    max_retained_runs: int = 20
    max_result_hits: int = 500
    calendar_days_per_trading_bar: int = 4
    max_runtime_seconds: int = 300


LIMITS = StrategyScanLimits()


class StrategyScanError(ValueError):
    """A strategy scan cannot be started or read safely."""


class StrategyScanConflict(StrategyScanError):
    """A scan is already active or the retained task limit has been reached."""


def _root() -> Path:
    tenant_id = tenant.current_tenant.get()
    root = Path(pf.CACHE_DIR)
    if tenant_id != "local":
        if not re.fullmatch(r"[uk]_[0-9a-f]{20}", tenant_id):
            raise StrategyScanError("租户身份无效")
        root = root / "users" / tenant_id
    return root / "strategy-scans"


def _run_dir(run_id: str) -> Path:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise StrategyScanError("策略扫描任务不存在")
    return _root() / run_id


def _state_path(run_id: str) -> Path:
    return _run_dir(run_id) / "state.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, state: dict[str, Any]) -> None:
    with advisory_lock(path.with_suffix(".lock")):
        atomic_write_text(
            path,
            json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        )


def _active_process(state: dict[str, Any]) -> bool:
    pid = state.get("pid")
    run_id = state.get("run_id")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(run_id and run_id in command and "strategy_scan_worker.py" in command)


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in {"pid", "worker_args"}}


def _universe(source: Any, *, reference_day: date) -> tuple[list[str], date]:
    from aqsp.data.daily_universe import latest_available_daily_day

    candidates = {
        str(item).strip()
        for item in source.get_available_symbols()
        if len(str(item).strip()) == 6
        and str(item).strip().isdigit()
        and str(item).strip().startswith(_A_SHARE_PREFIXES)
    }
    candidates = {
        symbol
        for symbol in candidates
        if "ST" not in source.get_symbol_name(symbol).upper().replace(" ", "")
        and "退" not in source.get_symbol_name(symbol)
    }
    try:
        baseline_day = latest_available_daily_day(source, not_after=reference_day)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StrategyScanError("无法核验 A 股 universe 覆盖基准") from exc
    if baseline_day is None:
        raise StrategyScanError("raw SQLite 找不到可核验的 A 股覆盖基准日")
    symbols = sorted(
        source.get_symbols_with_daily_coverage(
            sorted(candidates),
            baseline_day,
            baseline_day,
            min_rows=1,
            min_coverage_ratio=1.0,
        )
    )
    if not symbols:
        raise StrategyScanError("raw SQLite 覆盖基准日没有可扫描的 A 股标的")
    if len(symbols) > LIMITS.max_universe_symbols:
        raise StrategyScanError(
            f"raw SQLite A 股 universe 超过 {LIMITS.max_universe_symbols} 个标的上限"
        )
    return symbols, baseline_day


def _source() -> Any:
    source = build_data_source("sqlite_db", cache=None)
    mode_fn = getattr(source, "price_mode", None)
    price_mode = str(mode_fn()) if callable(mode_fn) else "unknown"
    if getattr(source, "name", "") != "sqlite_db" or price_mode != "raw":
        raise DataError("全市场策略扫描要求可核验的 sqlite_db raw 不复权数据源")
    return source


def _latest_scan_day(source: Any, requested: str | None) -> tuple[date, date]:
    market_day = source.get_latest_daily_date()
    if market_day is None:
        raise StrategyScanError("raw SQLite 没有日线数据")
    completed_day = latest_completed_trading_day(now_shanghai())
    if market_day > completed_day:
        raise StrategyScanError("raw SQLite 最新日线晚于最近已完成交易日")
    if requested is None:
        scan_day = market_day
    else:
        try:
            scan_day = date.fromisoformat(requested)
        except ValueError as exc:
            raise StrategyScanError("扫描日期必须使用 YYYY-MM-DD") from exc
    if scan_day > completed_day:
        raise StrategyScanError("扫描日期不能晚于最近已完成交易日")
    available_days = source.get_available_daily_dates(scan_day, scan_day)
    if scan_day not in available_days:
        raise StrategyScanError(f"请求扫描日 {scan_day.isoformat()} 不在本机 SQLite 交易日中")
    if scan_day > market_day:
        raise StrategyScanError("扫描日期晚于 raw SQLite 最新日线")
    return scan_day, market_day


def start_run(*, strategy_id: str, as_of: str | None = None) -> dict[str, Any]:
    strategy = strategy_definitions.get_strategy(strategy_id)
    if strategy is None:
        raise StrategyScanError("策略不存在或已移出当前租户")
    if strategy.get("enabled") is not True:
        raise StrategyScanError("策略已停用，不能启动扫描")
    compiled = compile_strategy_expression(str(strategy.get("expression", "")))
    source = _source()
    scan_day, market_day = _latest_scan_day(source, as_of)
    symbols, universe_as_of = _universe(source, reference_day=scan_day)
    tenant_id = tenant.current_tenant.get()
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with advisory_lock(root / ".start.lock"):
        state_paths = sorted(root.glob("*/state.json"))
        if len(state_paths) >= LIMITS.max_retained_runs:
            raise StrategyScanConflict(
                f"当前租户已保存 {LIMITS.max_retained_runs} 次扫描；请显式删除旧任务"
            )
        for path in state_paths:
            previous = _read(path)
            if previous.get("status") in {"starting", "running", "cancelling"}:
                if _active_process(previous):
                    raise StrategyScanConflict("当前租户已有全市场策略扫描运行")
                previous.update(
                    {
                        "status": "unknown",
                        "phase": "unknown",
                        "message": "扫描进程已不存在；结果需人工核对，不会自动续跑",
                        "updated_at": now_shanghai().isoformat(timespec="seconds"),
                    }
                )
                _write(path, previous)

        run_id = uuid4().hex
        run_dir = root / run_id
        run_dir.mkdir(mode=0o700)
        config = {
            "strategy": strategy,
            "strategy_hash": strategy["expression_sha256"],
            "lookback_bars": compiled.max_lookback,
            "as_of": scan_day.isoformat(),
            "market_as_of": market_day.isoformat(),
            "universe_as_of": universe_as_of.isoformat(),
            "source": "sqlite_db",
            "price_mode": "raw",
            "symbols": symbols,
        }
        config_path = run_dir / "config.json"
        atomic_write_text(
            config_path,
            json.dumps(config, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        )
        state_path = run_dir / "state.json"
        config_hash = hashlib.sha256(
            json.dumps(
                {key: value for key, value in config.items() if key != "symbols"}
                | {"universe_hash": hashlib.sha256("\n".join(symbols).encode()).hexdigest()},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        state: dict[str, Any] = {
            "run_id": run_id,
            "config_hash": config_hash,
            "strategy_id": strategy_id,
            "strategy_revision": strategy["revision"],
            "strategy_hash": strategy["expression_sha256"],
            "lookback_bars": compiled.max_lookback,
            "as_of": scan_day.isoformat(),
            "market_as_of": market_day.isoformat(),
            "universe_as_of": universe_as_of.isoformat(),
            "source": "sqlite_db",
            "price_mode": "raw",
            "universe_size": len(symbols),
            "processed_symbols": 0,
            "hit_count": 0,
            "status": "starting",
            "coverage_status": None,
            "message": "全市场策略扫描已排队",
            "created_at": now_shanghai().isoformat(timespec="seconds"),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
            "pid": None,
        }
        _write(state_path, state)
        worker = Path(__file__).with_name("strategy_scan_worker.py")
        args = [
            sys.executable,
            str(worker),
            "--run-id",
            run_id,
            "--state-path",
            str(state_path),
            "--tenant-id",
            tenant_id,
        ]
        try:
            process = subprocess.Popen(
                args,
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            state.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "message": "策略扫描 worker 无法启动",
                    "updated_at": now_shanghai().isoformat(timespec="seconds"),
                }
            )
            _write(state_path, state)
            raise StrategyScanError("策略扫描任务无法启动") from exc
        state.update(
            {
                "pid": process.pid,
                "status": "running",
                "phase": "scanning",
                "message": "正在读取 raw 日线、PIT 因子并计算覆盖",
                "updated_at": now_shanghai().isoformat(timespec="seconds"),
            }
        )
        _write(state_path, state)
    return _public(state)


def get_run(run_id: str, *, include_result: bool = True) -> dict[str, Any] | None:
    path = _state_path(run_id)
    state = _read(path)
    if not state:
        return None
    if state.get("status") in {"starting", "running", "cancelling"} and not _active_process(state):
        state.update(
            {
                "status": "unknown",
                "phase": "unknown",
                "message": "扫描进程已不存在；结果需人工核对，不会自动续跑",
                "updated_at": now_shanghai().isoformat(timespec="seconds"),
            }
        )
        _write(path, state)
    public = _public(state)
    result_path = path.parent / "result.json"
    result = _read(result_path) if include_result else {}
    if result:
        public["result"] = result
    return public


def list_runs(*, limit: int = LIMITS.max_retained_runs) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= LIMITS.max_retained_runs:
        raise ValueError(f"limit 必须在 1 到 {LIMITS.max_retained_runs} 之间")
    root = _root()
    if not root.is_dir():
        return []
    items = [get_run(path.parent.name, include_result=False) for path in root.glob("*/state.json")]
    return sorted(
        (item for item in items if item),
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )[:limit]


def cancel_run(run_id: str) -> dict[str, Any]:
    path = _state_path(run_id)
    state = _read(path)
    if not state:
        raise StrategyScanError("策略扫描任务不存在")
    if state.get("status") not in {"starting", "running", "cancelling"}:
        return _public(state)
    if not _active_process(state):
        state.update({"status": "unknown", "phase": "unknown", "message": "扫描进程已不存在"})
        _write(path, state)
        return _public(state)
    state.update(
        {
            "status": "cancelling",
            "phase": "cancelling",
            "message": "正在停止当前扫描批次",
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
        }
    )
    _write(path, state)
    os.kill(state["pid"], signal.SIGTERM)
    return _public(state)


def delete_run(run_id: str) -> bool:
    run_dir = _run_dir(run_id)
    state = get_run(run_id)
    if state is None:
        return False
    if state.get("status") not in _TERMINAL_STATUSES:
        raise StrategyScanError("运行中的策略扫描不能删除")
    shutil.rmtree(run_dir)
    return True


def export_csv(run_id: str, *, view: str = "hits") -> str:
    run = get_run(run_id)
    if run is None or not isinstance(run.get("result"), dict):
        raise StrategyScanError("策略扫描结果暂不可用")
    result = run["result"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    if view == "hits":
        writer.writerow(["date", "symbol", "name", "raw_close"])
        for item in result.get("hits", []):
            writer.writerow([item["date"], item["symbol"], item["name"], item["raw_close"]])
    elif view == "coverage":
        writer.writerow(["symbol", "status", "data_as_of"])
        for item in result.get("coverage", []):
            writer.writerow([item["symbol"], item["status"], item.get("data_as_of") or ""])
    else:
        raise ValueError("view 必须是 hits 或 coverage")
    return stream.getvalue()


def _set_state(path: Path, state: dict[str, Any], **updates: Any) -> dict[str, Any]:
    state.update(updates)
    state["updated_at"] = now_shanghai().isoformat(timespec="seconds")
    _write(path, state)
    return state


def _factor_values(
    frame: pd.DataFrame, records: dict[date, Any] | None
) -> dict[date, float] | None:
    dates = [date.fromisoformat(str(value)[:10]) for value in frame["date"]]
    if not isinstance(records, dict):
        return None
    values: dict[date, float] = {}
    for day in dates:
        record = records.get(day)
        if record is None:
            return None
        try:
            value = float(record.value)
        except (AttributeError, TypeError, ValueError):
            return None
        if not pd.notna(value) or value <= 0:
            return None
        values[day] = value
    return values


def _scan_symbol(
    *, source: Any, symbol: str, frame: pd.DataFrame | None, scan_day: date,
    expression: Any, strategy: dict[str, Any], pit_records: dict[date, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if frame is None or frame.empty:
        return {"symbol": symbol, "status": "missing_data", "data_as_of": None}, None
    ordered = frame.copy()
    parsed = pd.to_datetime(ordered.get("date"), errors="coerce")
    if parsed.isna().any():
        return {"symbol": symbol, "status": "invalid_dates", "data_as_of": None}, None
    ordered["date"] = parsed.dt.strftime("%Y-%m-%d")
    ordered = ordered.sort_values("date", kind="stable").reset_index(drop=True)
    if ordered["date"].duplicated().any():
        return {"symbol": symbol, "status": "duplicate_dates", "data_as_of": None}, None
    actual_day = date.fromisoformat(str(ordered["date"].iloc[-1]))
    if actual_day != scan_day:
        return {"symbol": symbol, "status": "stale", "data_as_of": actual_day.isoformat()}, None
    if len(ordered) < expression.max_lookback:
        return {"symbol": symbol, "status": "insufficient_history", "data_as_of": actual_day.isoformat()}, None
    ordered = ordered.tail(expression.max_lookback + 1).reset_index(drop=True)
    ordered["raw_close"] = pd.to_numeric(ordered.get("raw_close", ordered["close"]), errors="coerce")
    if expression.requires_pit_price_basis:
        factors = _factor_values(ordered, pit_records)
        if not factors:
            return {"symbol": symbol, "status": "missing_pit", "data_as_of": actual_day.isoformat()}, None
        target_factor = factors[scan_day]
        scale = [factors[date.fromisoformat(str(value))] / target_factor for value in ordered["date"]]
        for column in _PRICE_COLUMNS:
            ordered[column] = pd.to_numeric(ordered[column], errors="coerce") * scale
    featured = enrich_indicators(ordered)
    target_row = featured.iloc[-1]
    for field in expression.fields:
        if field not in featured.columns or pd.isna(target_row[field]):
            return {"symbol": symbol, "status": "missing_feature", "data_as_of": actual_day.isoformat()}, None
        if not math.isfinite(float(target_row[field])):
            return {"symbol": symbol, "status": "invalid_feature", "data_as_of": actual_day.isoformat()}, None
    values = expression.evaluate(featured)
    if values.empty or pd.isna(values.iloc[-1]):
        return {"symbol": symbol, "status": "insufficient_history", "data_as_of": actual_day.isoformat()}, None
    row = featured.iloc[-1]
    coverage = {"symbol": symbol, "status": "matched" if bool(values.iloc[-1]) else "no_match", "data_as_of": actual_day.isoformat()}
    if not bool(values.iloc[-1]):
        return coverage, None
    raw_close = float(row["raw_close"])
    if not math.isfinite(raw_close):
        return {"symbol": symbol, "status": "invalid_raw_close", "data_as_of": actual_day.isoformat()}, None
    hit = {
        "date": scan_day.isoformat(),
        "symbol": symbol,
        "name": source.get_symbol_name(symbol),
        "raw_close": raw_close,
        "strategy_id": strategy["id"],
        "strategy_revision": strategy["revision"],
        "expression_fields": {
            field: float(row[field])
            for field in expression.fields
            if field in featured.columns and pd.notna(row[field]) and math.isfinite(float(row[field]))
        },
    }
    return coverage, hit


def execute_run(run_id: str, state_path: Path, tenant_id: str) -> int:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = False
    tenant.current_tenant.set(tenant_id)
    signal.signal(signal.SIGTERM, _request_cancel)
    run_dir = state_path.parent
    wait_deadline = time.monotonic() + 10
    state: dict[str, Any] = {}
    while time.monotonic() < wait_deadline:
        state = _read(state_path)
        if state.get("pid") == os.getpid() and state.get("run_id") == run_id:
            break
        time.sleep(0.05)
    else:
        return 2
    config = _read(run_dir / "config.json")
    if config.get("run_id") not in {None, run_id}:
        return 2
    strategy = config.get("strategy")
    if not isinstance(strategy, dict):
        return 2
    expression_text = str(strategy.get("expression", ""))
    if strategy_definitions.strategy_expression_hash(
        expression_text, str(strategy.get("exit_expression", ""))
    ) != config.get("strategy_hash"):
        return 2
    expression = compile_strategy_expression(expression_text)
    scan_day = date.fromisoformat(str(config["as_of"]))
    symbols = config.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        return 2
    source = _source()
    state = _set_state(state_path, state, status="running", phase="scanning", message="正在逐批扫描 A 股 universe")
    coverage: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    total = len(symbols)
    calendar_days = max(90, expression.max_lookback * LIMITS.calendar_days_per_trading_bar)
    warmup_start = scan_day - timedelta(days=calendar_days)
    started = time.monotonic()
    for offset in range(0, total, LIMITS.batch_size):
        if time.monotonic() - started >= LIMITS.max_runtime_seconds:
            state = _set_state(
                state_path,
                state,
                status="timed_out",
                phase="timed_out",
                message="全市场扫描超过 5 分钟资源上限；保留已处理计数，不发布不完整命中列表",
                finished_at=now_shanghai().isoformat(timespec="seconds"),
            )
            return 1
        if _CANCEL_REQUESTED:
            state = _set_state(state_path, state, status="cancelled", phase="cancelled", message="用户取消了全市场扫描")
            return 0
        batch = symbols[offset : offset + LIMITS.batch_size]
        try:
            latest_dates = source.get_latest_daily_dates(batch)
        except (DataError, OSError):
            latest_dates = {}
        eligible: list[str] = []
        preflight: dict[str, dict[str, Any]] = {}
        for symbol in batch:
            latest = latest_dates.get(symbol)
            if latest is None:
                preflight[symbol] = {"symbol": symbol, "status": "missing_data", "data_as_of": None}
            elif latest < scan_day:
                preflight[symbol] = {"symbol": symbol, "status": "stale", "data_as_of": latest.isoformat()}
            elif latest > scan_day:
                eligible.append(symbol)
            else:
                eligible.append(symbol)
        try:
            frames = source.fetch_daily(eligible, warmup_start, scan_day, adjust="") if eligible else {}
        except (DataError, OSError):
            frames = {}
        batch_pit: dict[str, dict[date, Any]] = {}
        if expression.requires_pit_price_basis and frames:
            all_dates: set[date] = set()
            for frame in frames.values():
                if frame is None or frame.empty or "date" not in frame:
                    continue
                parsed_dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
                all_dates.update(item.date() for item in parsed_dates)
            if all_dates:
                try:
                    response = source.get_point_in_time_adjustment_factors(eligible, sorted(all_dates))
                    batch_pit = response if isinstance(response, dict) else {}
                except (DataError, OSError):
                    batch_pit = {}
        for symbol in batch:
            if symbol in preflight:
                coverage.append(preflight[symbol])
                continue
            try:
                item, hit = _scan_symbol(
                    source=source,
                    symbol=str(symbol),
                    frame=frames.get(str(symbol)),
                    scan_day=scan_day,
                    expression=expression,
                    strategy=strategy,
                    pit_records=batch_pit.get(str(symbol)),
                )
            except (DataError, OSError, ValueError):
                item, hit = {"symbol": str(symbol), "status": "evaluation_error", "data_as_of": None}, None
            coverage.append(item)
            if hit is not None and len(hits) < LIMITS.max_result_hits:
                hits.append(hit)
        processed = min(offset + len(batch), total)
        counts: dict[str, int] = {}
        for item in coverage:
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        state = _set_state(
            state_path,
            state,
            processed_symbols=processed,
            hit_count=counts.get("matched", 0),
            coverage_counts=counts,
            message=f"已扫描 {processed}/{total} 个标的",
        )
    counts: dict[str, int] = {}
    for item in coverage:
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1
    blocked = sum(count for status, count in counts.items() if status not in {"matched", "no_match"})
    result_status = "ready" if blocked == 0 else "partial"
    result = {
        "status": result_status,
        "as_of": scan_day.isoformat(),
        "market_as_of": config["market_as_of"],
        "universe_as_of": config["universe_as_of"],
        "source": "sqlite_db",
        "price_mode": "raw",
        "universe_size": total,
        "processed_symbols": len(coverage),
        "hit_count": counts.get("matched", 0),
        "hits_truncated": counts.get("matched", 0) > LIMITS.max_result_hits,
        "hits": hits,
        "coverage_counts": counts,
        "coverage": coverage,
        "strategy_id": strategy["id"],
        "strategy_revision": strategy["revision"],
        "strategy_hash": config["strategy_hash"],
    }
    atomic_write_text(
        run_dir / "result.json",
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
    )
    state = _set_state(
        state_path,
        state,
        status="completed",
        phase="complete",
        coverage_status=result_status,
        processed_symbols=len(coverage),
        hit_count=counts.get("matched", 0),
        coverage_counts=counts,
        hits_truncated=result["hits_truncated"],
        message="扫描完成" if result_status == "ready" else "扫描完成；部分标的数据缺失、陈旧或缺少 PIT 因子",
        finished_at=now_shanghai().isoformat(timespec="seconds"),
    )
    return 0


def _request_cancel(_signum: int, _frame: object) -> None:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True
