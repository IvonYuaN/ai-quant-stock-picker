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

# 保存候选回测参数：可复用命名配置按租户隔离，revision CAS + 审计，配置与证据分离。
_CONFIG_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_SAVED_CONFIGS = 50
# 只把这些「回放参数」写入可复用配置。任何运行结果 / gate / OOS 证据 / 净值曲线
# 都不属于配置，白名单天然把它们排除，保证「配置与证据分离」。
_CONFIG_PARAM_KEYS: tuple[str, ...] = (
    "strategy", "strategy_hash", "symbols", "symbol", "mode", "universe_mode",
    "universe_as_of", "universe_hash", "position_sizing", "initial_capital",
    "signal_start", "signal_end", "outcome_as_of", "policy", "source",
    "price_mode", "max_runtime_seconds",
)
_REQUIRED_POLICY_KEYS: tuple[str, ...] = (
    "commission_rate", "stamp_tax_rate", "slippage",
    "fallback_limit_main_pct", "fallback_limit_growth_pct",
    "fallback_limit_bse_pct", "fallback_limit_st_pct", "stop_loss_pct",
    "max_hold_days", "max_positions", "max_exposure_pct",
    "max_single_position_pct", "min_cash_reserve_pct",
    "thresholds_version", "fill_rule",
)


class StrategyBacktestError(ValueError):
    """Invalid, unavailable, or tenant-inaccessible custom strategy replay."""


class StrategyBacktestConflict(StrategyBacktestError):
    """A tenant replay is active or its retained task limit is reached."""


class StrategyBacktestConfigConflict(StrategyBacktestError):
    """A saved config revision changed after the editor read it."""


class StrategyBacktestCoverageError(StrategyBacktestError):
    """One-click rerun as-of coverage re-verification failed before any run existed."""

    def __init__(
        self,
        message: str,
        *,
        coverage_gaps: list[dict[str, Any]],
        market_as_of: str | None = None,
    ) -> None:
        super().__init__(message)
        self.coverage_gaps = coverage_gaps
        self.market_as_of = market_as_of


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


def _config_root() -> Path:
    # _root() 已按租户切分目录，saved-configs 子目录同样受租户隔离。
    return _root() / "saved-configs"


def _config_path(config_id: str) -> Path:
    if not _CONFIG_ID_RE.fullmatch(config_id):
        raise StrategyBacktestError("回测配置标识无效")
    return _config_root() / f"{config_id}.json"


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
    strict_coverage: bool = False,
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
    coverage_gaps = [
        {
            "symbol": symbol,
            "latest_as_of": stale.get(symbol),
            "required_as_of": outcome_as_of.isoformat(),
            "reason": "missing" if stale.get(symbol) is None else "stale",
        }
        for symbol in symbols
        if symbol in stale
    ]
    if strict_coverage and stale:
        # 一键复测：不允许像普通组合回放那样静默丢弃陈旧标的，缺覆盖即明确失败。
        details = ", ".join(
            f"{symbol}:{actual or '无数据'}"
            for symbol, actual in list(sorted(stale.items()))[:10]
        )
        suffix = "等" if len(stale) > 10 else ""
        raise StrategyBacktestCoverageError(
            f"一键复测前置校验失败：{len(stale)} 个标的未覆盖结果数据日 "
            f"{outcome_as_of.isoformat()} 的 as-of：{details}{suffix}；未创建回放任务",
            coverage_gaps=coverage_gaps,
            market_as_of=market_latest.isoformat(),
        )
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
        "coverage_gaps": coverage_gaps,
    }


def _create_run(
    config: dict[str, Any],
    *,
    strategy_id: str,
    extra_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a frozen config.json + state.json and launch one isolated worker.

    ``config`` must already carry ``config_hash``. Every run-state field is derived
    from the frozen config, so a rerun can never reuse an old result or gate.
    """
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
        atomic_write_text(
            run_dir / "config.json",
            json.dumps(config, ensure_ascii=False, sort_keys=True) + "\n",
        )
        state_path = run_dir / "state.json"
        policy = config["policy"]
        strategy = config["strategy"]
        state: dict[str, Any] = {
            "run_id": run_id,
            "config_hash": config["config_hash"],
            "strategy_id": strategy_id,
            "strategy_revision": strategy["revision"],
            "strategy_hash": config["strategy_hash"],
            "symbol": config.get("symbol"),
            "symbols": config["symbols"],
            "mode": config["mode"],
            "universe_mode": config["universe_mode"],
            "universe_as_of": config.get("universe_as_of"),
            "universe_hash": config.get("universe_hash"),
            "universe_symbol_count": len(config["symbols"]),
            "position_sizing": config["position_sizing"],
            "initial_capital": config.get("initial_capital"),
            "signal_start": config["signal_start"],
            "signal_end": config["signal_end"],
            "outcome_as_of": config["outcome_as_of"],
            "market_as_of": config["market_as_of"],
            "available_symbols": config["available_symbols"],
            "stale_symbols": config["stale_symbols"],
            "max_runtime_seconds": config["max_runtime_seconds"],
            "source": config["source"],
            "price_mode": config["price_mode"],
            "thresholds_version": policy["thresholds_version"],
            "fill_rule": policy["fill_rule"],
            "status": "starting",
            "phase": "queued",
            "message": "用户策略回放已排队；结果与 composite Walk-forward 分离",
            "created_at": now_shanghai().isoformat(timespec="seconds"),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
            "pid": None,
        }
        if extra_state:
            state.update(extra_state)
        _write(state_path, state)
        worker = Path(__file__).with_name("strategy_backtest_worker.py")
        args = [
            sys.executable, str(worker), "--run-id", run_id,
            "--state-path", str(state_path), "--tenant-id", tenant.current_tenant.get(),
        ]
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
    return _create_run(config, strategy_id=strategy_id)


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


# ---------------------------------------------------------------------------
# 保存候选回测参数（可复用命名配置）与一键复测
# ---------------------------------------------------------------------------

def _validate_saved_params(config: dict[str, Any]) -> dict[str, Any]:
    """Whitelist replay parameters out of a frozen run config.

    Anything outside ``_CONFIG_PARAM_KEYS`` — results, gate conclusions, OOS
    evidence, equity curves — is deliberately dropped. This is the config /
    evidence separation boundary: a saved config never carries a run outcome.
    """
    if not isinstance(config, dict):
        raise StrategyBacktestError("回测配置必须是对象")
    params = {key: config[key] for key in _CONFIG_PARAM_KEYS if key in config}
    required = ("strategy", "strategy_hash", "symbols", "signal_start",
                "signal_end", "outcome_as_of", "mode", "policy")
    missing = [key for key in required if key not in params]
    if missing:
        raise StrategyBacktestError(f"回测配置缺少必要参数：{', '.join(missing)}")
    strategy = params["strategy"]
    if not isinstance(strategy, dict) or not all(
        key in strategy for key in ("id", "revision", "expression")
    ):
        raise StrategyBacktestError("回测配置中的策略定义无效")
    if not isinstance(params["strategy_hash"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", params["strategy_hash"]
    ):
        raise StrategyBacktestError("策略 hash 无效")
    symbols = params["symbols"]
    if (
        not isinstance(symbols, list)
        or not 1 <= len(symbols) <= MAX_SYMBOLS
        or any(not isinstance(item, str) or not _SYMBOL_RE.fullmatch(item) for item in symbols)
    ):
        raise StrategyBacktestError(f"回测配置标的需为 1 到 {MAX_SYMBOLS} 个六位 A 股代码")
    if params["mode"] not in {"single", "portfolio"}:
        raise StrategyBacktestError("回放模式仅支持 single 或 portfolio")
    if params.get("universe_mode", "explicit") not in {"explicit", "local_history"}:
        raise StrategyBacktestError("标的池仅支持显式代码或本机历史行情池")
    if params.get("position_sizing", "equal") not in {"equal", "score_weight"}:
        raise StrategyBacktestError("仓位分配仅支持 equal 或 score_weight")
    for key in ("signal_start", "signal_end", "outcome_as_of"):
        try:
            date.fromisoformat(params[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise StrategyBacktestError("回测配置日期必须使用 YYYY-MM-DD") from exc
    policy = params["policy"]
    if not isinstance(policy, dict) or any(key not in policy for key in _REQUIRED_POLICY_KEYS):
        raise StrategyBacktestError("回测配置成本/组合约束快照不完整")
    return params


def save_config(
    *,
    name: str,
    config: dict[str, Any],
    config_id: str | None = None,
    expected_revision: int | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    """Save or update a named, tenant-scoped replay config (CAS + audit).

    ``config`` is a frozen run config (``config.json``). Only whitelisted replay
    parameters are persisted; run results / gate / evidence are never stored.
    """
    clean_name = name.strip()
    if not 1 <= len(clean_name) <= 120:
        raise StrategyBacktestError("配置名称必须为 1 到 120 个字符")
    params = _validate_saved_params(config)
    params_sha256 = hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if config_id is None:
        config_id = uuid4().hex[:16]
    path = _config_path(config_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = now_shanghai().isoformat(timespec="seconds")
    with advisory_lock(path.with_suffix(".lock")):
        existing = _read(path)
        if existing and expected_revision is None:
            raise StrategyBacktestConfigConflict("配置已存在；更新请带上 expected_revision")
        if existing and expected_revision != existing.get("revision"):
            raise StrategyBacktestConfigConflict("配置已被其他操作更新，请重新读取")
        if not existing and expected_revision is not None:
            raise StrategyBacktestConfigConflict("配置不存在或已移出当前租户")
        if not existing and len(list(_config_root().glob("*.json"))) >= MAX_SAVED_CONFIGS:
            raise StrategyBacktestConfigConflict(
                f"当前租户最多保存 {MAX_SAVED_CONFIGS} 份回测配置；请先删除不再需要的配置"
            )
        if existing:
            record = existing
            record["revision"] = int(record["revision"]) + 1
            record["name"] = clean_name
            record["updated_at"] = now
            action = "updated"
        else:
            record = {
                "schema_version": 1,
                "config_id": config_id,
                "revision": 1,
                "origin": "saved_from_run" if source_run_id else "manual",
                "source_run_id": source_run_id,
                "created_at": now,
                "updated_at": now,
                "audit": [],
                "last_rerun": None,
                "name": clean_name,
            }
            action = "created"
        for key, value in params.items():
            record[key] = value
        record["params_sha256"] = params_sha256
        audit = list(record.get("audit") or [])
        audit.append({
            "action": action,
            "at": now,
            "revision": record["revision"],
            "params_sha256": params_sha256,
        })
        record["audit"] = audit[-50:]
        atomic_write_text(
            path,
            json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        )
    return dict(record)


def save_config_from_run(
    *,
    run_id: str,
    name: str,
    config_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Save a COMPLETED run's frozen config as a reusable named config.

    The completed run's result/gate/evidence live in separate files and are
    never copied. Only config.json is read, and it is whitelist-filtered again.
    """
    run_dir = _run_dir(run_id)
    state = _read(run_dir / "state.json")
    if not state:
        raise StrategyBacktestError("回测任务不存在或已移出当前租户")
    if state.get("status") != "completed":
        raise StrategyBacktestError("只能保存已完成（completed）的策略回测配置")
    config = _read(run_dir / "config.json")
    if not config:
        raise StrategyBacktestError("回测任务缺少冻结配置，无法保存")
    return save_config(
        name=name,
        config=config,
        config_id=config_id,
        expected_revision=expected_revision,
        source_run_id=run_id,
    )


def get_config(config_id: str) -> dict[str, Any] | None:
    record = _read(_config_path(config_id))
    return record or None


def list_configs(limit: int = MAX_SAVED_CONFIGS) -> list[dict[str, Any]]:
    if not 1 <= limit <= MAX_SAVED_CONFIGS:
        raise StrategyBacktestError(f"limit 必须在 1 到 {MAX_SAVED_CONFIGS} 之间")
    root = _config_root()
    if not root.is_dir():
        return []
    records = []
    for path in root.glob("*.json"):
        record = _read(path)
        if record.get("config_id"):
            records.append(record)
    return sorted(
        records, key=lambda item: str(item.get("updated_at", "")), reverse=True
    )[:limit]


def delete_config(config_id: str, expected_revision: int) -> bool:
    path = _config_path(config_id)
    with advisory_lock(path.with_suffix(".lock")):
        record = _read(path)
        if not record:
            return False
        if record.get("revision") != expected_revision:
            raise StrategyBacktestConfigConflict("配置已被其他操作更新，请重新读取")
        try:
            path.unlink()
        except FileNotFoundError:
            return False
    return True


def _verify_rerun(record: dict[str, Any]) -> dict[str, Any]:
    """Re-verify per-symbol as-of coverage against a FRESH data source.

    Returns a structured verdict. It never reuses a saved preflight, result,
    or gate; a coverage gap or unavailable source becomes a terminal rejection.
    """
    symbols = [str(item) for item in record["symbols"]]
    try:
        source = build_data_source("sqlite_db", cache=None)
    except Exception as exc:  # 数据源不可用时明确失败，绝不静默降级
        return {
            "ok": False, "coverage_gaps": [], "market_as_of": None,
            "reason": f"行情数据源不可用：{exc}", "preflight": None,
        }
    try:
        mode_fn = getattr(source, "price_mode", None)
        price_mode = str(mode_fn()) if callable(mode_fn) else "unknown"
        if getattr(source, "name", "") != "sqlite_db" or price_mode != "raw":
            raise StrategyBacktestError("一键复测要求 sqlite_db raw 不复权数据源")
        preflight = _preflight(
            source,
            symbols,
            mode=str(record["mode"]),
            signal_start=date.fromisoformat(record["signal_start"]),
            signal_end=date.fromisoformat(record["signal_end"]),
            outcome_as_of=date.fromisoformat(record["outcome_as_of"]),
            strict_coverage=True,
        )
    except StrategyBacktestCoverageError as exc:
        return {
            "ok": False, "coverage_gaps": exc.coverage_gaps,
            "market_as_of": exc.market_as_of, "reason": str(exc), "preflight": None,
        }
    except StrategyBacktestError as exc:
        return {
            "ok": False, "coverage_gaps": [], "market_as_of": None,
            "reason": str(exc), "preflight": None,
        }
    except (OSError, RuntimeError) as exc:
        return {
            "ok": False, "coverage_gaps": [], "market_as_of": None,
            "reason": f"行情数据核验不可用：{exc}", "preflight": None,
        }
    return {
        "ok": True, "coverage_gaps": [], "market_as_of": preflight["market_as_of"],
        "reason": "", "preflight": preflight,
    }


def _build_rerun_config(
    record: dict[str, Any], preflight: dict[str, Any]
) -> dict[str, Any]:
    """Rebuild a fresh config.json from saved params + freshly verified coverage."""
    config: dict[str, Any] = {
        "strategy": record["strategy"],
        "strategy_hash": record["strategy_hash"],
        "symbol": record.get("symbol"),
        "symbols": list(record["symbols"]),
        "mode": record["mode"],
        "universe_mode": record.get("universe_mode", "explicit"),
        "universe_as_of": record.get("universe_as_of"),
        "universe_hash": record.get("universe_hash", ""),
        "position_sizing": record.get("position_sizing", "equal"),
        "initial_capital": record.get("initial_capital"),
        "signal_start": record["signal_start"],
        "signal_end": record["signal_end"],
        "outcome_as_of": record["outcome_as_of"],
        "market_as_of": preflight["market_as_of"],
        "available_symbols": preflight["available_symbols"],
        "stale_symbols": preflight["stale_symbols"],
        "max_runtime_seconds": record.get("max_runtime_seconds", MAX_RUNTIME_SECONDS),
        "source": record.get("source", "sqlite_db"),
        "price_mode": record.get("price_mode", "raw"),
        "policy": record["policy"],
    }
    config["config_hash"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return config


def _started_outcome(
    record: dict[str, Any], run_state: dict[str, Any], config_id: str
) -> dict[str, Any]:
    return {
        "outcome": "started",
        "config_id": config_id,
        "config_revision": record["revision"],
        "run_id": run_state["run_id"],
        "run": run_state,
        "coverage_gaps": [],
        "market_as_of": run_state["market_as_of"],
        "message": "已从保存配置创建全新回放任务；旧结果/gate/证据不被继承，不自动重跑",
        "terminal": False,
        "source_run_id": record.get("source_run_id"),
    }


def _rejected_outcome(
    record: dict[str, Any], verification: dict[str, Any], config_id: str
) -> dict[str, Any]:
    return {
        "outcome": "rejected",
        "config_id": config_id,
        "config_revision": record["revision"],
        "run_id": None,
        "run": None,
        "coverage_gaps": verification["coverage_gaps"],
        "market_as_of": verification["market_as_of"],
        "message": verification["reason"] or "一键复测前置校验失败，未创建回放任务",
        "terminal": True,
        "source_run_id": record.get("source_run_id"),
    }


def _append_rerun_audit(record: dict[str, Any], outcome: dict[str, Any]) -> None:
    """Persist a terminal, readable rerun outcome; never auto-retries."""
    now = now_shanghai().isoformat(timespec="seconds")
    event = {
        "action": "rerun_started" if outcome["outcome"] == "started" else "rerun_rejected",
        "at": now,
        "revision": outcome["config_revision"],
        "outcome": outcome["outcome"],
        "run_id": outcome["run_id"],
        "terminal": outcome["terminal"],
        "message": outcome["message"],
        "coverage_gaps": outcome["coverage_gaps"],
    }
    audit = list(record.get("audit") or [])
    audit.append(event)
    record["audit"] = audit[-50:]
    record["last_rerun"] = {
        key: outcome[key]
        for key in (
            "outcome", "run_id", "message", "terminal", "coverage_gaps",
            "market_as_of", "config_revision",
        )
    }
    record["last_rerun"]["at"] = now
    record["last_rerun_at"] = now


def rerun_from_config(
    *, config_id: str, expected_revision: int | None = None
) -> dict[str, Any]:
    """One-click rerun: create a NEW run from a saved config.

    Hard rules:
      * Re-verify every symbol's SQLite as-of coverage up to the saved end date
        using a fresh data source BEFORE creating any run.
      * Missing / stale / unavailable coverage -> terminal rejection, NO run is
        created, NO silent degradation, NO reuse of old results, NO auto-rerun.
      * On success the new run uses the FROZEN strategy/policy/params; its
        config/state carry no result/gate inherited from the source run.
      * The config itself is never deleted, so the failure path is recoverable.
    """
    path = _config_path(config_id)
    with advisory_lock(path.with_suffix(".lock")):
        record = _read(path)
        if not record:
            raise StrategyBacktestError("回测配置不存在或已移出当前租户")
        if expected_revision is not None and expected_revision != record.get("revision"):
            raise StrategyBacktestConfigConflict("配置已被其他操作更新，请重新读取")
        verification = _verify_rerun(record)
        if verification["ok"]:
            config = _build_rerun_config(record, verification["preflight"])
            strategy_id = str(record["strategy"].get("id", ""))
            run_state = _create_run(
                config,
                strategy_id=strategy_id,
                extra_state={
                    "rerun_of": record.get("source_run_id"),
                    "saved_config_id": config_id,
                    "saved_config_revision": record["revision"],
                },
            )
            outcome = _started_outcome(record, run_state, config_id)
        else:
            outcome = _rejected_outcome(record, verification, config_id)
        _append_rerun_audit(record, outcome)
        atomic_write_text(
            path,
            json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        )
    return outcome
