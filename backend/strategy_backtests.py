"""Tenant-scoped custom-strategy replay tasks, separate from composite Walk-forward."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
from typing import Any
from uuid import uuid4

import tenant
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data.source_factory import build_data_source
from aqsp.strategies.thresholds import load_thresholds
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

import strategy_definitions


_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SYMBOL_RE = re.compile(r"^\d{6}$")
MAX_SYMBOLS = 10_000
MAX_RANGE_DAYS = 365 * 3
MAX_RUNTIME_SECONDS = 300
MAX_RETAINED_RUNS = 20
MAX_REPORT_BYTES = 2 * 1024 * 1024
TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out", "unknown"})
_LOCK = threading.RLock()


class StrategyBacktestError(ValueError):
    """Invalid, unavailable, or tenant-inaccessible custom strategy replay."""


class StrategyBacktestConflict(StrategyBacktestError):
    """A tenant replay is active or its retained task limit is reached."""


def _root() -> Path:
    configured = os.environ.get("VR_DATA_DIR", "").strip()
    root = Path(configured).expanduser().resolve() if configured else Path.home() / ".vibe-research"
    tenant_id = tenant.current_tenant.get()
    if tenant_id != "local":
        if not re.fullmatch(r"[uk]_[0-9a-f]{20}", tenant_id):
            raise StrategyBacktestError("租户身份无效")
        root = root / "users" / tenant_id
    return root / "strategy-backtests"


def _run_dir(run_id: str) -> Path:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise StrategyBacktestError("策略回测任务不存在")
    return _root() / run_id


def _state_path(run_id: str) -> Path:
    return _run_dir(run_id) / "state.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
    return bool(run_id and run_id in command and "strategy_backtest_worker.py" in command)


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in {"pid", "worker_args"}}


def _policy_snapshot() -> dict[str, Any]:
    thresholds = load_thresholds()
    execution = thresholds.execution
    risk = thresholds.risk
    return {
        "thresholds_version": str(thresholds.version),
        "commission_rate": float(execution.commission_rate),
        "stamp_tax_rate": float(execution.stamp_tax_rate),
        "slippage": float(execution.slippage),
        "fallback_limit_main_pct": float(execution.fallback_limit_main_pct),
        "fallback_limit_growth_pct": float(execution.fallback_limit_growth_pct),
        "fallback_limit_bse_pct": float(execution.fallback_limit_bse_pct),
        "fallback_limit_st_pct": float(execution.fallback_limit_st_pct),
        "stop_loss_pct": float(risk.single_stock_stop_pct),
        "max_hold_days": int(risk.max_holding_days),
        "max_positions": int(risk.max_positions),
        "max_exposure_pct": float(1.0 - risk.min_cash_reserve),
        "max_single_position_pct": float(risk.max_single_position_pct),
        "min_cash_reserve_pct": float(risk.min_cash_reserve),
        "fill_rule": "signal_close_next_open",
    }


def _preflight(
    source: Any,
    symbols: list[str],
    *,
    mode: str,
    signal_start: date,
    signal_end: date,
    outcome_as_of: date,
) -> dict[str, Any]:
    if outcome_as_of > latest_completed_trading_day(now_shanghai()):
        raise StrategyBacktestError("结果数据日不能晚于最近已完成交易日")
    if outcome_as_of - signal_start > timedelta(days=MAX_RANGE_DAYS):
        raise StrategyBacktestError("单次策略回放区间最多三年")
    if not signal_start <= signal_end <= outcome_as_of:
        raise StrategyBacktestError("必须满足 signal_start ≤ signal_end ≤ outcome_as_of")
    market_latest = source.get_latest_daily_date()
    if market_latest is None or outcome_as_of > market_latest:
        raise StrategyBacktestError("结果数据日超出本机 raw SQLite 最新市场日线")
    available_days = set(source.get_available_daily_dates(signal_start, outcome_as_of))
    if signal_start not in available_days or signal_end not in available_days or outcome_as_of not in available_days:
        raise StrategyBacktestError("入场区间和结果数据日必须是本机 SQLite 已有交易日")
    latest_by_symbol = source.get_latest_daily_dates(symbols, as_of=outcome_as_of)
    stale = {
        symbol: (latest_by_symbol.get(symbol).isoformat() if latest_by_symbol.get(symbol) else None)
        for symbol in symbols
        if latest_by_symbol.get(symbol) != outcome_as_of
    }
    if stale and mode == "single":
        details = ", ".join(
            f"{symbol}:{actual or '无数据'}"
            for symbol, actual in list(sorted(stale.items()))[:10]
        )
        suffix = "等" if len(stale) > 10 else ""
        raise StrategyBacktestError(
            f"组合中 {len(stale)} 个标的未覆盖指定结果数据日 {outcome_as_of.isoformat()}：{details}{suffix}"
        )
    available_symbols = [symbol for symbol in symbols if symbol not in stale]
    if not available_symbols:
        raise StrategyBacktestError("所选标的都没有覆盖指定结果数据日的日线")
    return {
        "market_as_of": market_latest.isoformat(),
        "available_symbols": available_symbols,
        "stale_symbols": stale,
    }


def start_run(
    *, strategy_id: str, symbols: list[str] | None, signal_start: str, signal_end: str,
    outcome_as_of: str, mode: str = "single", initial_capital: float | None = None,
    position_sizing: str = "equal", universe_mode: str = "explicit",
) -> dict[str, Any]:
    if mode not in {"single", "portfolio"}:
        raise StrategyBacktestError("回放模式仅支持 single 或 portfolio")
    if universe_mode not in {"explicit", "local_history"}:
        raise StrategyBacktestError("标的池仅支持显式代码或本机历史行情池")
    if universe_mode == "local_history" and mode != "portfolio":
        raise StrategyBacktestError("本机历史行情池只适用于策略组合")
    if mode == "portfolio" and (initial_capital is None or initial_capital <= 0):
        raise StrategyBacktestError("策略组合回放必须设置正数初始资金")
    if position_sizing not in {"equal", "score_weight"}:
        raise StrategyBacktestError("仓位分配仅支持 equal 或 score_weight")
    if mode == "single" and position_sizing != "equal":
        raise StrategyBacktestError("评分加权只适用于策略组合回放")
    strategy = strategy_definitions.get_strategy(strategy_id)
    if strategy is None:
        raise StrategyBacktestError("策略不存在或已移出当前租户")
    if strategy.get("enabled") is not True:
        raise StrategyBacktestError("策略已停用，不能启动回放")
    if position_sizing == "score_weight" and not str(strategy.get("score_expression", "")).strip():
        raise StrategyBacktestError("评分加权需要先为策略设置数值评分表达式")
    try:
        start_day = date.fromisoformat(signal_start)
        end_day = date.fromisoformat(signal_end)
        outcome_day = date.fromisoformat(outcome_as_of)
    except ValueError as exc:
        raise StrategyBacktestError("日期必须使用 YYYY-MM-DD") from exc
    source = build_data_source("sqlite_db", cache=None)
    mode_fn = getattr(source, "price_mode", None)
    price_mode = str(mode_fn()) if callable(mode_fn) else "unknown"
    if getattr(source, "name", "") != "sqlite_db" or price_mode != "raw":
        raise StrategyBacktestError("用户策略回放要求 sqlite_db raw 不复权数据源")
    if universe_mode == "local_history":
        list_symbols = getattr(source, "get_symbols_with_daily_bars", None)
        if not callable(list_symbols):
            raise StrategyBacktestError("当前 SQLite 数据源不支持按历史 raw 行构造标的池")
        symbols = list_symbols(start_day, end_day)
        if not symbols:
            raise StrategyBacktestError("入场信号区间内本机 raw SQLite 没有可回放 A 股日线")
    normalized_symbols = sorted(set(str(item).strip() for item in (symbols or [])))
    if not normalized_symbols or len(normalized_symbols) > MAX_SYMBOLS:
        raise StrategyBacktestError(f"组合回放需有 1 到 {MAX_SYMBOLS} 个 A 股代码")
    if any(not _SYMBOL_RE.fullmatch(item) for item in normalized_symbols):
        raise StrategyBacktestError("每个标的都必须是六位 A 股代码")
    if mode == "single" and len(normalized_symbols) != 1:
        raise StrategyBacktestError("单标回放只能选择一个 A 股代码")
    preflight = _preflight(
        source, normalized_symbols, mode=mode,
        signal_start=start_day, signal_end=end_day, outcome_as_of=outcome_day
    )
    market_as_of = str(preflight["market_as_of"])
    policy = _policy_snapshot()
    config = {
        "strategy": strategy,
        "strategy_hash": strategy["expression_sha256"],
        "symbol": normalized_symbols[0] if mode == "single" else None,
        "symbols": normalized_symbols,
        "mode": mode,
        "universe_mode": universe_mode,
        "universe_as_of": end_day.isoformat() if universe_mode == "local_history" else None,
        "universe_hash": hashlib.sha256("\n".join(normalized_symbols).encode()).hexdigest(),
        "position_sizing": position_sizing,
        "initial_capital": float(initial_capital) if initial_capital is not None else None,
        "signal_start": start_day.isoformat(),
        "signal_end": end_day.isoformat(),
        "outcome_as_of": outcome_day.isoformat(),
        "market_as_of": market_as_of,
        "available_symbols": preflight["available_symbols"],
        "stale_symbols": preflight["stale_symbols"],
        "max_runtime_seconds": MAX_RUNTIME_SECONDS,
        "source": "sqlite_db",
        "price_mode": "raw",
        "policy": policy,
    }
    config["config_hash"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with advisory_lock(root / ".start.lock"):
        state_paths = list(root.glob("*/state.json"))
        if len(state_paths) >= MAX_RETAINED_RUNS:
            raise StrategyBacktestConflict(
                f"已保存 {MAX_RETAINED_RUNS} 条用户策略回放；请显式删除旧任务"
            )
        for path in state_paths:
            previous = _read(path)
            if previous.get("status") in {"starting", "running", "cancelling"}:
                if _active_process(previous):
                    raise StrategyBacktestConflict("当前租户已有策略回放运行")
                previous.update({
                    "status": "unknown", "phase": "unknown",
                    "message": "回放进程已不存在；结果需要人工核对，不会自动续跑",
                    "updated_at": now_shanghai().isoformat(timespec="seconds"),
                })
                _write(path, previous)
        run_id = uuid4().hex
        run_dir = root / run_id
        run_dir.mkdir(mode=0o700)
        atomic_write_text(run_dir / "config.json", json.dumps(config, ensure_ascii=False, sort_keys=True) + "\n")
        state_path = run_dir / "state.json"
        state: dict[str, Any] = {
            "run_id": run_id,
            "config_hash": config["config_hash"],
            "strategy_id": strategy_id,
            "strategy_revision": strategy["revision"],
            "strategy_hash": strategy["expression_sha256"],
            "symbol": normalized_symbols[0] if mode == "single" else None,
            "symbols": normalized_symbols,
            "mode": mode,
            "universe_mode": universe_mode,
            "universe_as_of": config["universe_as_of"],
            "universe_hash": config["universe_hash"],
            "universe_symbol_count": len(normalized_symbols),
            "position_sizing": position_sizing,
            "initial_capital": float(initial_capital) if initial_capital is not None else None,
            "signal_start": config["signal_start"],
            "signal_end": config["signal_end"],
            "outcome_as_of": config["outcome_as_of"],
            "market_as_of": market_as_of,
            "available_symbols": preflight["available_symbols"],
            "stale_symbols": preflight["stale_symbols"],
            "max_runtime_seconds": MAX_RUNTIME_SECONDS,
            "source": "sqlite_db",
            "price_mode": "raw",
            "thresholds_version": policy["thresholds_version"],
            "fill_rule": policy["fill_rule"],
            "status": "starting",
            "phase": "queued",
            "message": "用户策略回放已排队；结果与 composite Walk-forward 分离",
            "created_at": now_shanghai().isoformat(timespec="seconds"),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
            "pid": None,
        }
        _write(state_path, state)
        worker = Path(__file__).with_name("strategy_backtest_worker.py")
        args = [sys.executable, str(worker), "--run-id", run_id, "--state-path", str(state_path), "--tenant-id", tenant.current_tenant.get()]
        try:
            process = subprocess.Popen(
                args, cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            state.update({"status": "failed", "phase": "failed", "message": "策略回放 worker 无法启动"})
            _write(state_path, state)
            raise StrategyBacktestError("策略回放任务无法启动") from exc
        state.update({"pid": process.pid, "status": "running", "phase": "preparing", "message": "正在准备 raw 日线与 PIT 因子"})
        _write(state_path, state)
    return _public(state)


def get_run(run_id: str) -> dict[str, Any] | None:
    path = _state_path(run_id)
    state = _read(path)
    if not state:
        return None
    if state.get("status") in {"starting", "running", "cancelling"} and not _active_process(state):
        state.update({"status": "unknown", "phase": "unknown", "message": "回放进程已停止；结果需人工核对"})
        _write(path, state)
    result = _read(path.parent / "result.json")
    if result:
        state["result"] = result
    return _public(state)


def list_runs(limit: int = MAX_RETAINED_RUNS) -> list[dict[str, Any]]:
    if not 1 <= limit <= MAX_RETAINED_RUNS:
        raise StrategyBacktestError(f"limit 必须在 1 到 {MAX_RETAINED_RUNS} 之间")
    root = _root()
    if not root.is_dir():
        return []
    values = [get_run(path.parent.name) for path in root.glob("*/state.json")]
    return sorted((item for item in values if item), key=lambda item: item.get("created_at", ""), reverse=True)[:limit]


def cancel_run(run_id: str) -> dict[str, Any]:
    path = _state_path(run_id)
    state = _read(path)
    if not state:
        raise StrategyBacktestError("策略回放任务不存在")
    if state.get("status") not in {"starting", "running", "cancelling"}:
        return _public(state)
    if not _active_process(state):
        state.update({"status": "unknown", "phase": "unknown", "message": "回放进程已停止"})
        _write(path, state)
        return _public(state)
    state.update({"status": "cancelling", "phase": "cancelling", "message": "正在停止策略回放"})
    _write(path, state)
    os.kill(state["pid"], signal.SIGTERM)
    return _public(state)


def report_text(run_id: str) -> str | None:
    run = get_run(run_id)
    if run is None or run.get("status") != "completed":
        return None
    path = _run_dir(run_id) / "report.md"
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        raise StrategyBacktestError("回放报告缺失或超过读取上限")
    return path.read_text(encoding="utf-8")


def delete_run(run_id: str) -> bool:
    run_dir = _run_dir(run_id)
    state = get_run(run_id)
    if state is None:
        return False
    if state.get("status") not in TERMINAL:
        raise StrategyBacktestConflict("运行中的策略回放不能删除")
    shutil.rmtree(run_dir)
    return True
