"""Tenant-scoped persistent user backtest runs over the AQSP walk-forward engine."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from typing import Any
from uuid import uuid4

from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data.source_factory import resolve_sqlite_db_path
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text

import tenant

TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out", "unknown"})
MAX_SYMBOLS = 20
MAX_RANGE_DAYS = 365 * 3
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_RETAINED_RUNS = 20
SYMBOL_RE = re.compile(r"^\d{6}$")


class BacktestRunError(ValueError):
    """Invalid or unavailable user backtest run."""


class BacktestRunConflict(BacktestRunError):
    """A run is already active for this tenant."""


def _user_data_root() -> Path:
    configured = os.environ.get("VR_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".vibe-research"


def _tenant_root() -> Path:
    root = _user_data_root()
    tenant_id = tenant.current_tenant.get()
    if tenant_id != "local":
        if not re.fullmatch(r"[uk]_[0-9a-f]{20}", tenant_id):
            raise BacktestRunError("租户身份无效")
        root = root / "users" / tenant_id
    return root / "backtest-runs"


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise BacktestRunError("回测任务不存在")
    return _tenant_root() / run_id


def _state_path(run_id: str) -> Path:
    return _run_dir(run_id) / "state.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    with advisory_lock(path.with_suffix(".lock")):
        atomic_write_text(path, json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n")


def _validate_request(
    *, strategy_id: str, symbols: list[str], start: str, end: str,
    train_days: int, test_days: int, horizon_days: int,
) -> dict[str, Any]:
    if strategy_id != "composite":
        raise BacktestRunError("当前仅开放已注册的 composite Walk-forward 策略")
    clean_symbols = list(dict.fromkeys(item.strip() for item in symbols if item.strip()))
    if not clean_symbols or len(clean_symbols) > MAX_SYMBOLS or any(not SYMBOL_RE.fullmatch(item) for item in clean_symbols):
        raise BacktestRunError(f"标的需为 1–{MAX_SYMBOLS} 个六位 A 股代码")
    try:
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end)
    except ValueError as exc:
        raise BacktestRunError("回测日期必须使用 YYYY-MM-DD") from exc
    if end_day < start_day:
        raise BacktestRunError("结束日期不能早于开始日期")
    if (end_day - start_day).days > MAX_RANGE_DAYS:
        raise BacktestRunError("单次用户回测范围最多三年")
    if end_day > latest_completed_trading_day(now_shanghai()):
        raise BacktestRunError("结束日期不能超过最近已完成交易日")
    if not 20 <= train_days <= 1000 or not 5 <= test_days <= 250 or not 1 <= horizon_days <= 60:
        raise BacktestRunError("训练/测试/持有窗口超出允许范围")
    return {
        "strategy_id": strategy_id,
        "symbols": clean_symbols,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "train_days": train_days,
        "test_days": test_days,
        "horizon_days": horizon_days,
        "source": "sqlite_db",
        "price_mode": "raw + point-in-time enrichment",
        "engine": "builtin",
        "window_mode": "rolling_recent",
        "grid_profile": "stable_plus",
    }


def _active_process(state: dict[str, Any]) -> bool:
    pid = state.get("pid")
    run_id = state.get("run_id")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(run_id and run_id in command and "backtest_run_worker.py" in command)


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in {"pid", "worker_args"}}


def _latest_sqlite_data_asof(symbols: list[str], requested_end: str) -> tuple[str, dict[str, str]]:
    db_path = resolve_sqlite_db_path()
    if not db_path or not Path(db_path).is_file():
        raise BacktestRunError("本机 raw SQLite 行情库不可用，无法启动回测")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    end_key = date.fromisoformat(requested_end).strftime("%Y%m%d")
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            market_row = connection.execute(
                """
                SELECT MAX(d.trade_date)
                FROM daily_qfq AS d
                JOIN stocks AS s ON s.ts_code = d.ts_code
                WHERE d.trade_date <= ? AND s.ts_code GLOB '??????.??'
                """,
                (end_key,),
            ).fetchone()
            market_key = str(market_row[0] or "") if market_row else ""
            per_symbol: dict[str, str] = {}
            for symbol in symbols:
                rows = connection.execute(
                    "SELECT ts_code FROM stocks WHERE ts_code LIKE ? ORDER BY ts_code",
                    (f"{symbol}.%",),
                ).fetchall()
                ts_codes = [str(row[0]) for row in rows]
                if len(ts_codes) != 1:
                    per_symbol[symbol] = ""
                    continue
                row = connection.execute(
                    "SELECT MAX(trade_date) FROM daily_qfq WHERE ts_code = ? AND trade_date <= ?",
                    (ts_codes[0], end_key),
                ).fetchone()
                per_symbol[symbol] = str(row[0] or "") if row else ""
    except (OSError, sqlite3.Error) as exc:
        raise BacktestRunError("无法只读核验 SQLite 行情库的实际数据日") from exc
    if not re.fullmatch(r"\d{8}", market_key):
        raise BacktestRunError("SQLite 行情库没有可用于回测的日线数据")
    stale = {
        symbol: value
        for symbol, value in per_symbol.items()
        if value != market_key
    }
    if stale:
        details = ", ".join(
            f"{symbol}: {date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat() if value else '无数据'}"
            for symbol, value in sorted(stale.items())
        )
        market_asof = date(int(market_key[:4]), int(market_key[4:6]), int(market_key[6:8])).isoformat()
        raise BacktestRunError(
            f"所选标的未覆盖结束日前市场最新交易日 {market_asof}（{details}）；请缩短区间或先完成数据刷新"
        )
    market_asof = date(int(market_key[:4]), int(market_key[4:6]), int(market_key[6:8])).isoformat()
    symbol_asof = {
        symbol: date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
        for symbol, value in per_symbol.items()
    }
    return market_asof, symbol_asof


def start_run(
    *, strategy_id: str, symbols: list[str], start: str, end: str,
    train_days: int = 120, test_days: int = 30, horizon_days: int = 3,
) -> dict[str, Any]:
    config = _validate_request(
        strategy_id=strategy_id, symbols=symbols, start=start, end=end,
        train_days=train_days, test_days=test_days, horizon_days=horizon_days,
    )
    data_asof, symbol_asof = _latest_sqlite_data_asof(config["symbols"], config["end"])
    config["data_as_of"] = data_asof
    config["symbol_data_asof"] = symbol_asof
    root = _tenant_root()
    root.mkdir(parents=True, exist_ok=True)
    with advisory_lock(root / ".start.lock"):
        retained = [path for path in root.glob("*/state.json") if path.parent.is_dir()]
        if len(retained) >= MAX_RETAINED_RUNS:
            raise BacktestRunConflict("已保存 20 个回测任务；请删除不再需要的终态任务后再启动")
        for path in root.glob("*/state.json"):
            previous = _read(path)
            if previous.get("status") in {"starting", "running"}:
                if _active_process(previous):
                    raise BacktestRunConflict("当前租户已有回测任务运行")
                previous.update({
                    "status": "unknown",
                    "phase": "unknown",
                    "message": "任务进程已不存在；结果需人工核对，不会自动续跑",
                    "updated_at": now_shanghai().isoformat(timespec="seconds"),
                })
                _write_state(path, previous)
        run_id = uuid4().hex
        run_dir = root / run_id
        run_dir.mkdir(mode=0o700)
        state_path = run_dir / "state.json"
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        state: dict[str, Any] = {
            "run_id": run_id,
            "config_hash": config_hash,
            "config": config,
            "status": "starting",
            "phase": "queued",
            "progress": None,
            "message": "回测任务已排队",
            "created_at": now_shanghai().isoformat(timespec="seconds"),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
            "pid": None,
        }
        _write_state(state_path, state)
        worker = Path(__file__).with_name("backtest_run_worker.py")
        args = [
            sys.executable, str(worker), "--run-id", run_id,
            "--state-path", str(state_path), "--project-root", str(Path(__file__).resolve().parents[1]),
        ]
        try:
            process = subprocess.Popen(
                args, cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
            )
        except OSError as exc:
            state.update({"status": "failed", "phase": "failed", "message": "回测 worker 无法启动", "finished_at": now_shanghai().isoformat(timespec="seconds")})
            _write_state(state_path, state)
            raise BacktestRunError("回测任务无法启动") from exc
        state["pid"] = process.pid
        state["status"] = "running"
        state["phase"] = "preparing"
        state["message"] = "正在准备 raw SQLite 日线与 PIT 输入"
        _write_state(state_path, state)
    return _public(state)


def get_run(run_id: str) -> dict[str, Any] | None:
    path = _state_path(run_id)
    state = _read(path)
    if not state:
        return None
    if state.get("status") in {"starting", "running", "cancelling"} and not _active_process(state):
        state.update({
            "status": "unknown", "phase": "unknown",
            "message": "任务进程已不存在；结果需人工核对，不会自动续跑",
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
        })
        _write_state(path, state)
    log_path = path.parent / "run.log"
    try:
        with log_path.open("rb") as handle:
            handle.seek(max(0, log_path.stat().st_size - 8192))
            state["log_tail"] = handle.read().decode("utf-8", errors="replace")[-4000:]
    except OSError:
        state["log_tail"] = ""
    report_path = path.parent / "report.md"
    if state.get("status") == "completed" and report_path.is_file():
        state["report_available"] = report_path.stat().st_size <= MAX_REPORT_BYTES
    return _public(state)


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    if not 1 <= limit <= 50:
        raise BacktestRunError("任务列表 limit 必须为 1–50")
    root = _tenant_root()
    if not root.is_dir():
        return []
    runs = []
    for path in root.glob("*/state.json"):
        state = get_run(path.parent.name)
        if state:
            runs.append(state)
    return sorted(runs, key=lambda item: str(item.get("created_at", "")), reverse=True)[:limit]


def cancel_run(run_id: str) -> dict[str, Any] | None:
    path = _state_path(run_id)
    state = _read(path)
    if not state:
        return None
    if state.get("status") not in {"starting", "running", "cancelling"}:
        return _public(state)
    if state.get("status") == "cancelling":
        return _public(state)
    if not _active_process(state):
        return get_run(run_id)
    pid = int(state["pid"])
    state.update({
        "status": "cancelling", "phase": "cancelling", "progress": None,
        "message": "正在停止回测并回收 worker；完成前不可删除任务",
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    })
    _write_state(path, state)
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return get_run(run_id)
    return _public(state)


def report_text(run_id: str) -> str | None:
    state = _read(_state_path(run_id))
    if not state or state.get("status") != "completed":
        return None
    path = _run_dir(run_id) / "report.md"
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise BacktestRunError("报告超出下载大小上限")
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def delete_run(run_id: str) -> bool:
    run_dir = _run_dir(run_id)
    state = _read(run_dir / "state.json")
    if not state:
        return False
    if state.get("status") not in TERMINAL:
        raise BacktestRunConflict("运行中的任务不能删除；请先取消或等待终态")
    root = _tenant_root().resolve()
    if run_dir.is_symlink() or run_dir.resolve().parent != root:
        raise BacktestRunError("回测任务路径无效")
    with advisory_lock(root / ".start.lock"):
        shutil.rmtree(run_dir)
    return True


def build_worker_command(config: dict[str, Any], run_dir: Path) -> list[str]:
    """Build a fixed CLI invocation; request values never become option names or paths."""
    if config.get("strategy_id") != "composite" or config.get("source") != "sqlite_db":
        raise BacktestRunError("回测任务配置不受支持")
    return [
        sys.executable, "-m", "aqsp", "walkforward",
        "--symbols", ",".join(config["symbols"]),
        "--start", config["start"], "--end", config["end"],
        "--source", "sqlite_db", "--engine", "builtin",
        "--window-mode", "rolling_recent", "--train-days", str(config["train_days"]),
        "--test-days", str(config["test_days"]), "--horizon-days", str(config["horizon_days"]),
        "--grid-cscv", "--grid-profile", "stable_plus",
        "--report", str(run_dir / "report.md"), "--gate-path", str(run_dir / "gate.json"),
    ]
