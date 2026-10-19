#!/usr/bin/env python3
"""Backfill baostock daily data for the local sqlite source.

The legacy server updater only requested today's bar. If a symbol missed one or
more trading days, it stayed stale forever. This updater starts from each symbol's latest stored trade_date + 1 day by default.
Use --start-date with --fill-history-gaps for production raw backfills that must
repair symbols with partial recent rows without refetching complete symbols.
Use --force-from-start only for a clean rebuild after taking a database backup.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # __file__ is scripts/data/update_sqlite_daily.py -> parents[2] is the repo
    # root, whose ``src`` package holds ``aqsp``. (parents[1] == scripts/src,
    # which is NOT on the package path, so the old insert silently failed and
    # only worked when PYTHONPATH happened to be set by the caller.)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aqsp.core.time import (
    get_previous_trading_day,
    is_trading_day,
    now_shanghai,
    today_shanghai,
)
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import atomic_write_text

_QUERY_RETRY_LIMIT = 2
_QUERY_RETRY_BASE_SLEEP_SECONDS = 0.2
# Per-symbol query retry is capped so a throttled source cannot make a single
# symbol block for minutes. The real protection against the baostock rate-limit
# death-spiral lives at the process level (see consecutive-failure cooldown below).
_QUERY_RETRY_MAX_SLEEP_SECONDS = 5.0

# Initial login is retried with exponential backoff instead of dying on the
# first throttled attempt.
# 🔴 2026-10-13 修正（#378）：旧注释称「baostock sessions stay valid for the whole
# process, so we never re-login」——**实测不成立**：长跑（~1.5h+）中会话会断裂
# （Broken pipe），其后每次查询都失败，同会话重试=死循环（runner 实测连续失败
# 292 次、占 flock 8.5h）。⇒ 连接类失败必须在进程级**重新登录**（见主循环
# error_code == "exception" 分支），并按 _SESSION_RELOGIN_LIMIT 封顶。
_LOGIN_RETRY_LIMIT = 5
_LOGIN_RETRY_BASE_SECONDS = 2.0
_LOGIN_RETRY_MAX_SECONDS = 60.0

# 单次运行内允许的最大「断线重登录」次数。封顶理由：上游真死时无限重登录
# 会把限流恶化；耗尽后靠末尾 require_target_coverage 的 fail-closed 收尾。
_SESSION_RELOGIN_LIMIT = 5

# 🔴 2026-10-14 修正（#378 二次修复）：**真实故障路径不抛异常**。
# 证据链（baostock 0.9.3 源码）：
#   socketutil.send_msg() 的 `except Exception: print(...)` **只打印不 re-raise**
#   （打印的正是现场那两行 "[Errno 32] Broken pipe" / "接收数据异常，请稍后再试。"）
#   ⇒ 返回 None；history.py 见 None 则返回 error_code=BSERR_RECVSOCK_FAIL 的
#   ResultData（**不抛异常**）⇒ 我们 `rs.error_code != "0"` 拿到的是**错误码字符串**。
# 因此只认 error_code == "exception" 的判据**永不触发**（首版修复实测无效）。
_SESSION_ERROR_CODES = frozenset(
    {
        "10002007",  # BSERR_RECVSOCK_FAIL：网络接收错误（socket 断裂的主路径）
        "10002001",  # BSERR_SOCKET_ERR：网络错误
        "10001001",  # BSERR_NO_LOGIN：用户未登录（会话被服务端清掉）
    }
)

# 🔴 2026-10-19（#402）：**回退路径的总预算**。#379 只给「重新登录」设了上限
# （_SESSION_RELOGIN_LIMIT），但超限后的「纯冷却重试」没有任何总量约束 ⇒ 上游
# 持续故障时日更死循环：10-14~10-16 实测连续失败从 286 涨到 677、占 flock
# 2.5 天，直到周六推库把库换掉才以 malformed 崩溃——**终止条件是意外，不是
# 设计**。现在按两个维度封顶，任一超限即 fail-closed 退出（释放 flock，让
# 下一次日更接管；与覆盖门一致的哲学：宁可不更新，不要霸锁）：
#   ① 累计失败符号数（默认 50）：健康全市场运行的失败量级在个位数；
#   ② 最长无进展时长（默认 1800s）：任何一次成功查询（含 0 行的停牌空响应——
#      它证明上游活着）即重置；卡死在 recv 或持续失败都会触发。
# 默认值非 0 ⇒ 现有 cron（不显式传参）自动获得保护，无需改 cron 行。
_TOTAL_FAILURE_BUDGET_DEFAULT = 50
_NO_PROGRESS_BUDGET_SECONDS_DEFAULT = 1800.0


def _looks_like_session_failure(error_code: str, consecutive_failures: int) -> bool:
    """判断本次失败是否可能源于**会话失效**（而非业务性失败）。

    - 库抛传输异常 ⇒ 真（重试层耗尽后 error_code == "exception"）；
    - baostock 传输/会话类错误码 ⇒ 真（**真实主路径**，见上方证据链）；
    - 防御兜底：任何来源的连续失败达到冷却阈值 ⇒ 真——未知错误码也能自愈，
      重登录有 _SESSION_RELOGIN_LIMIT 封顶，代价极低。
    """
    if error_code == "exception":
        return True
    if error_code in _SESSION_ERROR_CODES:
        return True
    return consecutive_failures >= _RATE_LIMIT_COOLDOWN_THRESHOLD

# Global rate-limit guard: once this many symbols fail in a row we assume the
# upstream IP is throttled and pause (exponential, capped) before continuing.
_RATE_LIMIT_COOLDOWN_THRESHOLD = 3
_RATE_LIMIT_COOLDOWN_BASE_SECONDS = 5.0
_RATE_LIMIT_COOLDOWN_MAX_SECONDS = 300.0


def _exponential_delay(attempt: int, base_seconds: float, max_seconds: float) -> float:
    """Capped exponential backoff in seconds. attempt is 1-based."""
    if base_seconds <= 0:
        return 0.0
    delay = base_seconds * (2 ** max(0, attempt - 1))
    return min(float(max_seconds), float(delay))


def _cooldown_seconds(
    consecutive_failures: int,
    threshold: int,
    base_seconds: float,
    max_seconds: float,
) -> float:
    """Process-level cooldown once consecutive symbol failures cross threshold."""
    if base_seconds <= 0 or consecutive_failures < threshold:
        return 0.0
    over = consecutive_failures - threshold + 1
    return _exponential_delay(over, base_seconds, max_seconds)


@dataclass(frozen=True)
class UpdateSummary:
    updated_rows: int
    skipped_symbols: int
    failed_symbols: int
    target_day: date
    price_mode: str
    target_day_symbol_count: int
    total_symbols: int
    raw_max_trade_date: date | None = None
    coverage_error: str | None = None
    processed_symbols: int = 0
    budget_exhausted: bool = False
    # #402：fail-closed 中止的原因（总失败预算 / 无进展预算触发）；None = 未被
    # 预算中止。与 budget_exhausted 的区别：后者含「排程批次正常跑完时长预算」
    # （调用方 coordinator 自己校验覆盖度），前者一律是失败，main() 必须返回 1。
    budget_abort_reason: str | None = None
    already_current_symbols: int = 0
    empty_response_symbols: int = 0
    # #378：本次运行发生「断线重登录」的次数（连接类失败后重建会话）。
    session_relogins: int = 0


def _parse_trade_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _to_compact(d: date) -> str:
    return d.strftime("%Y%m%d")


def _next_calendar_day(d: date) -> date:
    return d + timedelta(days=1)


def _target_trade_day(raw: str) -> date:
    if raw:
        target = date.fromisoformat(raw)
    else:
        current = today_shanghai()
        target = (
            current if is_trading_day(current) else get_previous_trading_day(current)
        )
    if not is_trading_day(target):
        target = get_previous_trading_day(target)
    return target


def _normalize_requested_symbol(raw: str) -> str:
    text = str(raw).strip().upper()
    if not text:
        return ""
    if "." in text:
        code, market = text.split(".", 1)
        if market in {"SH", "SZ"}:
            return f"{code}.{market}"
        if code in {"SH", "SZ"}:
            return f"{market}.{code}"
    return text


def _requested_ts_codes(symbols: tuple[str, ...]) -> list[str]:
    """Return explicit batch symbols in the database's canonical format."""
    normalized: set[str] = set()
    for item in symbols:
        requested = _normalize_requested_symbol(item)
        if not requested:
            continue
        if "." in requested:
            normalized.add(requested)
        elif requested.startswith("6"):
            normalized.add(f"{requested}.SH")
        elif requested.startswith(("0", "3")):
            normalized.add(f"{requested}.SZ")
    return sorted(normalized)


def _bs_code(ts_code: str) -> str:
    code, market = ts_code.split(".")
    return f"{market.lower()}.{code}"


def _is_a_share_bs_code(code: str) -> bool:
    return re.match(r"(sh\.60|sz\.00|sz\.30|sh\.68)\d+", code) is not None


def _load_baostock() -> Any:
    try:
        import baostock as bs  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on server env
        raise SystemExit("baostock is required for sqlite daily update") from exc
    return bs


def _login_baostock_session(
    bs: Any,
    *,
    retry_limit: int = _LOGIN_RETRY_LIMIT,
    base_seconds: float = _LOGIN_RETRY_BASE_SECONDS,
    max_seconds: float = _LOGIN_RETRY_MAX_SECONDS,
) -> None:
    last_msg = ""
    for attempt in range(1, max(1, retry_limit) + 1):
        login = bs.login()
        if str(getattr(login, "error_code", "")) == "0":
            return
        last_msg = getattr(login, "error_msg", "")
        if attempt < retry_limit:
            delay = _exponential_delay(attempt, base_seconds, max_seconds)
            print(
                f"[WARN] baostock 登录失败 (第{attempt}次): {last_msg}; "
                f"{delay:.1f}s 后重试",
                flush=True,
            )
            if delay > 0:
                time.sleep(delay)
    raise RuntimeError(f"Baostock login failed after {retry_limit} attempts: {last_msg}")


def _logout_baostock_session(bs: Any) -> None:
    try:
        bs.logout()
    except Exception:
        return None
    return None


def _exception_supports_retry(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, BrokenPipeError, ConnectionError)):
        return True
    if not isinstance(exc, OSError):
        return False
    text = str(exc).lower()
    return "broken pipe" in text or "connection reset" in text or "timed out" in text


def _query_history_rows_with_retry(
    *,
    bs: Any,
    ts_code: str,
    fetch_start_day: date,
    target_day: date,
    price_mode: str,
    timeout_seconds: float,
    retry_limit: int = _QUERY_RETRY_LIMIT,
    retry_sleep_seconds: float = _QUERY_RETRY_BASE_SLEEP_SECONDS,
    deadline: float | None = None,
) -> tuple[str, list[list[str]]]:
    attempts = max(1, retry_limit + 1)
    for attempt in range(1, attempts + 1):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return "timeout", []
        try:
            error_code, rows = _query_history_rows(
                bs=bs,
                ts_code=ts_code,
                fetch_start_day=fetch_start_day,
                target_day=target_day,
                price_mode=price_mode,
                timeout_seconds=(
                    timeout_seconds
                    if remaining is None
                    else min(timeout_seconds, remaining)
                ),
            )
        except Exception as exc:
            if not _exception_supports_retry(exc) or attempt >= attempts:
                if _exception_supports_retry(exc):
                    return "exception", []
                raise
            # Reuse the session across retries; only the process-level cooldown
            # (in update_sqlite_daily) backs off when the upstream IP is throttled.
            if retry_sleep_seconds > 0:
                delay = _exponential_delay(attempt, retry_sleep_seconds, _QUERY_RETRY_MAX_SLEEP_SECONDS)
                if deadline is not None:
                    delay = min(delay, max(0.0, deadline - time.monotonic()))
                if delay > 0:
                    time.sleep(delay)
            continue
        if error_code == "0":
            return error_code, rows
        if attempt >= attempts:
            return error_code, rows
        # Reuse the session across retries; never re-login on a transient failure.
        if retry_sleep_seconds > 0:
            delay = _exponential_delay(attempt, retry_sleep_seconds, _QUERY_RETRY_MAX_SLEEP_SECONDS)
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.monotonic()))
                if delay > 0:
                    time.sleep(delay)
    return "exception", []


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stocks (ts_code TEXT PRIMARY KEY, name TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_qfq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close_qfq REAL,
            volume INTEGER,
            amount REAL,
            open_qfq REAL,
            high_qfq REAL,
            low_qfq REAL,
            close REAL,
            UNIQUE(ts_code, trade_date)
        )
        """
    )
    conn.commit()


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 30000")
    journal_mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    if not journal_mode or str(journal_mode[0]).lower() != "wal":
        raise sqlite3.OperationalError("failed to enable WAL mode for sqlite updater")
    conn.execute("PRAGMA synchronous = NORMAL")


def sync_stock_list(
    conn: sqlite3.Connection,
    bs: Any,
    *,
    preserve_existing: bool = True,
) -> list[str]:
    rs = bs.query_stock_basic()
    bs_a_codes: set[str] = set()
    name_map: dict[str, str] = {}
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        code = row[0]
        name = row[1]
        if _is_a_share_bs_code(code):
            market, number = code.split(".")
            ts_code = f"{number}.{market.upper()}"
            bs_a_codes.add(ts_code)
            name_map[ts_code] = name

    cur = conn.cursor()
    existing = {row[0] for row in cur.execute("SELECT ts_code FROM stocks")}
    for ts_code in sorted(bs_a_codes - existing):
        cur.execute(
            "INSERT OR IGNORE INTO stocks(ts_code, name) VALUES(?, ?)",
            (ts_code, name_map.get(ts_code, "")),
        )
    if not preserve_existing:
        for ts_code in sorted(existing - bs_a_codes):
            cur.execute("DELETE FROM stocks WHERE ts_code = ?", (ts_code,))
            cur.execute("DELETE FROM daily_qfq WHERE ts_code = ?", (ts_code,))
    for ts_code, name in sorted(name_map.items()):
        cur.execute(
            "UPDATE stocks SET name = ? WHERE ts_code = ?",
            (name, ts_code),
        )
    conn.commit()
    return [
        row[0] for row in cur.execute("SELECT ts_code FROM stocks ORDER BY ts_code")
    ]


def _sync_stock_list_compat(
    conn: sqlite3.Connection,
    bs: Any,
    *,
    preserve_existing: bool,
) -> list[str]:
    try:
        signature = inspect.signature(sync_stock_list)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and "preserve_existing" not in signature.parameters:
        return sync_stock_list(conn, bs)
    try:
        return sync_stock_list(conn, bs, preserve_existing=preserve_existing)
    except TypeError as exc:
        if "preserve_existing" not in str(exc):
            raise
        return sync_stock_list(conn, bs)


def _symbol_date_bounds(
    conn: sqlite3.Connection, ts_code: str
) -> tuple[date | None, date | None]:
    row = conn.execute(
        """
        SELECT MIN(CAST(trade_date AS TEXT)), MAX(CAST(trade_date AS TEXT))
        FROM daily_qfq
        WHERE ts_code = ? AND trade_date != 'SKIP'
        """,
        (ts_code,),
    ).fetchone()
    if not row:
        return None, None
    return _parse_trade_date(row[0]), _parse_trade_date(row[1])


def _resolve_fetch_start_day(
    *,
    first: date | None,
    latest: date | None,
    start_day: date | None,
    target_day: date,
    force_from_start: bool,
    fill_history_gaps: bool,
) -> date:
    if force_from_start and start_day is not None:
        return start_day
    if fill_history_gaps and start_day is not None:
        # Calendar files may not include every old exchange holiday. Treat a
        # first stored row within the opening week as covered, while still
        # repairing symbols that only have recent partial history.
        prefix_grace_day = start_day + timedelta(days=7)
        if first is None or first > prefix_grace_day:
            return start_day
    if latest is not None:
        return _next_calendar_day(latest)
    return start_day or target_day


def _latest_symbol_date(conn: sqlite3.Connection, ts_code: str) -> date | None:
    return _symbol_date_bounds(conn, ts_code)[1]


def _adjustflag_for_price_mode(price_mode: str) -> str:
    if price_mode == "raw":
        # BaoStock: 1=back-adjusted, 2=forward-adjusted, 3=unadjusted.
        return "3"
    if price_mode == "qfq":
        return "2"
    raise ValueError(f"unsupported price_mode: {price_mode}")


def _run_with_timeout(fetch: Any, timeout_seconds: float) -> Any:
    if timeout_seconds <= 0 or not hasattr(signal, "setitimer"):
        return fetch()

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"query timed out after {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return fetch()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _query_history_rows(
    *,
    bs: Any,
    ts_code: str,
    fetch_start_day: date,
    target_day: date,
    price_mode: str,
    timeout_seconds: float,
) -> tuple[str, list[list[str]]]:
    def _fetch() -> tuple[str, list[list[str]]]:
        rs = bs.query_history_k_data_plus(
            code=_bs_code(ts_code),
            fields="date,open,high,low,close,volume,amount",
            start_date=fetch_start_day.isoformat(),
            end_date=target_day.isoformat(),
            frequency="d",
            adjustflag=_adjustflag_for_price_mode(price_mode),
        )
        rows: list[list[str]] = []
        if rs.error_code != "0":
            return str(rs.error_code), rows
        while rs.next():
            rows.append(rs.get_row_data())
        return str(rs.error_code), rows

    return _run_with_timeout(_fetch, timeout_seconds)


def _insert_bar(conn: sqlite3.Connection, ts_code: str, row: list[str]) -> bool:
    if len(row) < 7 or not row[4]:
        return False
    trade_day = row[0].replace("-", "")
    open_price = float(row[1]) if row[1] else None
    high = float(row[2]) if row[2] else None
    low = float(row[3]) if row[3] else None
    close = float(row[4]) if row[4] else None
    volume = int(float(row[5])) if row[5] else None
    amount = float(row[6]) if row[6] else None
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_qfq(
            ts_code, trade_date, open, high, low, close_qfq, volume, amount, close
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts_code, trade_day, open_price, high, low, close, volume, amount, close),
    )
    return True


def _count_target_day_symbols(
    conn: sqlite3.Connection,
    target_day: date,
    symbols: list[str],
) -> int:
    if not symbols:
        return 0
    placeholders = ",".join("?" for _ in symbols)
    row = conn.execute(
        "SELECT COUNT(DISTINCT ts_code) FROM daily_qfq "
        f"WHERE trade_date = ? AND ts_code IN ({placeholders})",
        (_to_compact(target_day), *symbols),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _raw_max_trade_date(conn: sqlite3.Connection, symbols: list[str]) -> date | None:
    if not symbols:
        return None
    placeholders = ",".join("?" for _ in symbols)
    row = conn.execute(
        "SELECT MAX(CAST(trade_date AS TEXT)) FROM daily_qfq "
        f"WHERE trade_date != 'SKIP' AND ts_code IN ({placeholders})",
        tuple(symbols),
    ).fetchone()
    return _parse_trade_date(row[0]) if row and row[0] else None


def _target_coverage_error(
    *,
    target_day: date,
    target_day_symbol_count: int,
    total_symbols: int,
    raw_max_trade_date: date | None,
) -> str | None:
    if total_symbols <= 0:
        return None
    if raw_max_trade_date is None:
        return (
            "raw sqlite has no valid trade_date after update; "
            f"target={target_day.isoformat()} coverage=0/{total_symbols}"
        )
    if raw_max_trade_date < target_day:
        return (
            f"target={target_day.isoformat()} exceeds raw "
            "MAX(trade_date)="
            f"{raw_max_trade_date.isoformat()}; "
            f"coverage={target_day_symbol_count}/{total_symbols}"
        )
    if target_day_symbol_count == 0:
        return (
            f"target={target_day.isoformat()} has no rows after update; "
            f"raw MAX(trade_date)={raw_max_trade_date.isoformat()} "
            f"coverage=0/{total_symbols}"
        )
    return None


def _success_stamp_path(db_path: Path) -> Path:
    """「上次成功日更」印章路径（紧贴 DB，随 DB 被换掉而失配 ⇒ 正是信号）。"""
    return db_path.with_name(db_path.name + ".update_stamp.json")


def _write_success_stamp(db_path: Path, *, summary: UpdateSummary) -> bool:
    """#402：让 freshness 能区分「数据来自日更」与「数据来自推库」。

    只在**干净成功**（无失败符号、无覆盖缺口、未被预算中止）时写；否则旧印章
    原样保留 ⇒  freshness 看到的就是「最近一次真的成功的日更」。周六推库只
    rsync 主库文件、不搬印章 ⇒ 推库后 stamp_db_max 与库内 MAX(trade_date)
    失配，即可观测地点亮「数据是推库换来的，不是日更」。
    原子写（tmp + replace）：崩溃不能留下半个 json。
    """
    payload = {
        "finished_at": now_shanghai().isoformat(timespec="seconds"),
        "target_day": summary.target_day.isoformat(),
        "db_max_trade_date": (
            summary.raw_max_trade_date.isoformat()
            if summary.raw_max_trade_date
            else None
        ),
        "updated_rows": summary.updated_rows,
        "processed": f"{summary.processed_symbols}/{summary.total_symbols}",
    }
    try:
        atomic_write_text(
            _success_stamp_path(db_path),
            json.dumps(payload, ensure_ascii=False) + "\n",
        )
    except OSError:
        return False
    return True


def update_sqlite_daily(
    db_path: Path,
    *,
    target_day: date,
    sleep_seconds: float,
    limit: int,
    symbols: tuple[str, ...] = (),
    start_day: date | None = None,
    force_from_start: bool = False,
    fill_history_gaps: bool = False,
    price_mode: str = "raw",
    query_timeout_seconds: float = 15.0,
    offset: int = 0,
    max_runtime_seconds: float = 0.0,
    require_target_coverage: bool = True,
    rate_limit_cooldown_seconds: float = _RATE_LIMIT_COOLDOWN_BASE_SECONDS,
    max_consecutive_failures: int = 0,
    max_total_failures: int = _TOTAL_FAILURE_BUDGET_DEFAULT,
    max_no_progress_seconds: float = _NO_PROGRESS_BUDGET_SECONDS_DEFAULT,
) -> UpdateSummary:
    # 既有库的价基必须是 raw：验证链路（gate/walk-forward）以 ``adjust=""`` 读
    # ``open/high/low/close``，把这些列写成前复权价会**静默污染验证价基**（比 qfq 列
    # 本身缺失更隐蔽）。此处 fail-closed。
    # ⚠️ ``SqliteDbSource.price_mode()`` 只会返回 "raw" / "qfq" / "unknown"（实测确认），
    # 原先比对的 "invalid" 永不成立 ⇒ 守卫形同虚设，故改为 ``!= "raw"``。
    existing_price_mode = (
        SqliteDbSource(db_path=db_path, cache=None).price_mode()
        if db_path.exists()
        else "raw"
    )
    if existing_price_mode != "raw":
        raise RuntimeError(
            f"existing sqlite price basis is {existing_price_mode!r}, but the "
            "validation pipeline requires an unadjusted (raw) database; build a new "
            "raw database in bounded batches and switch only after coverage validation"
        )
    bs = _load_baostock()
    _login_baostock_session(bs)

    updated_rows = 0
    skipped = 0
    failed = 0
    already_current = 0
    empty_response = 0
    total_symbols = 0
    consecutive_failures = 0
    session_relogins = 0
    try:
        with sqlite3.connect(db_path) as conn:
            configure_sqlite_connection(conn)
            ensure_schema(conn)
            selected_symbols = _requested_ts_codes(symbols)
            if not selected_symbols:
                selected_symbols = _sync_stock_list_compat(
                    conn,
                    bs,
                    preserve_existing=True,
                )
            safe_offset = max(0, int(offset))
            if safe_offset:
                selected_symbols = selected_symbols[safe_offset:]
            if limit > 0:
                selected_symbols = selected_symbols[:limit]
            total_symbols = len(selected_symbols)
            deadline = (
                time.monotonic() + max_runtime_seconds
                if max_runtime_seconds > 0
                else None
            )
            processed_symbols = 0
            budget_exhausted = False
            budget_abort_reason: str | None = None
            last_progress_at = time.monotonic()
            for index, ts_code in enumerate(selected_symbols, start=1):
                if deadline is not None and time.monotonic() >= deadline:
                    budget_exhausted = True
                    print(
                        f"运行预算耗尽: processed={processed_symbols}/{len(selected_symbols)}",
                        flush=True,
                    )
                    break
                # #402 总预算（回退路径的灵魂修复）：累计失败数 / 最长无进展时长
                # 任一超限即 fail-closed 退出并释放 flock，让下一次日更接管——
                # 绝不再出现「上游持续故障 ⇒ 冷却重试死循环数天占锁」。
                if max_total_failures > 0 and failed >= max_total_failures:
                    budget_abort_reason = (
                        f"累计失败 {failed} 次达到总预算 {max_total_failures} 次"
                        "（疑似上游持续不可用；fail-closed 退出以释放 flock，"
                        "让下一次日更接管）"
                    )
                    budget_exhausted = True
                    print(f"[ABORT] {budget_abort_reason}", flush=True)
                    break
                if (
                    max_no_progress_seconds > 0
                    and time.monotonic() - last_progress_at >= max_no_progress_seconds
                ):
                    budget_abort_reason = (
                        f"{max_no_progress_seconds:.0f}s 无任何成功查询"
                        "（疑似卡死或上游不可达；fail-closed 退出以释放 flock，"
                        "让下一次日更接管）"
                    )
                    budget_exhausted = True
                    print(f"[ABORT] {budget_abort_reason}", flush=True)
                    break
                processed_symbols = index
                first, latest = _symbol_date_bounds(conn, ts_code)
                fetch_start_day = _resolve_fetch_start_day(
                    first=first,
                    latest=latest,
                    start_day=start_day,
                    target_day=target_day,
                    force_from_start=force_from_start,
                    fill_history_gaps=fill_history_gaps,
                )
                if fetch_start_day > target_day:
                    skipped += 1
                    already_current += 1
                    continue
                error_code, rows = _query_history_rows_with_retry(
                    bs=bs,
                    ts_code=ts_code,
                    fetch_start_day=fetch_start_day,
                    target_day=target_day,
                    price_mode=price_mode,
                    timeout_seconds=query_timeout_seconds,
                    deadline=deadline,
                )
                if error_code != "0":
                    failed += 1
                    consecutive_failures += 1
                    # #378：会话失效（含 baostock 传输错误码这条**真实主路径**，
                    # 以及连续失败兜底）⇒ 重建会话（logout+login），按上限封顶；
                    # 业务性失败（如限流码）也会因兜底条款试一次重登录，代价极低。
                    if _looks_like_session_failure(error_code, consecutive_failures):
                        if session_relogins < _SESSION_RELOGIN_LIMIT:
                            session_relogins += 1
                            print(
                                f"会话疑似失效（error_code={error_code}，连续失败"
                                f" {consecutive_failures} 次）→ 第 {session_relogins}/"
                                f"{_SESSION_RELOGIN_LIMIT} 次重新登录",
                                flush=True,
                            )
                            _logout_baostock_session(bs)
                            try:
                                _login_baostock_session(bs)
                                print("重新登录成功，继续本批", flush=True)
                            except Exception as exc:
                                print(f"重新登录失败: {exc}", flush=True)
                        elif session_relogins == _SESSION_RELOGIN_LIMIT:
                            session_relogins += 1  # 只提示一次，之后静默走冷却
                            print(
                                f"重新登录已达上限 {_SESSION_RELOGIN_LIMIT} 次，"
                                "转为纯冷却重试（上游可能持续不可用）",
                                flush=True,
                            )
                    if (
                        max_consecutive_failures > 0
                        and consecutive_failures >= max_consecutive_failures
                    ):
                        print(
                            f"[ABORT] 连续失败 {consecutive_failures} 次"
                            f"（疑似上游限流）；终止本批更新以避免死循环。",
                            flush=True,
                        )
                        budget_exhausted = True
                        break
                    cooldown = _cooldown_seconds(
                        consecutive_failures,
                        _RATE_LIMIT_COOLDOWN_THRESHOLD,
                        rate_limit_cooldown_seconds,
                        _RATE_LIMIT_COOLDOWN_MAX_SECONDS,
                    )
                    if cooldown > 0:
                        print(
                            f"连续失败 {consecutive_failures} 次，限流冷却 {cooldown:.1f}s",
                            flush=True,
                        )
                        time.sleep(cooldown)
                    continue
                consecutive_failures = 0
                # 成功查询（含 0 行停牌空响应）即视为有进展：它证明上游活着。
                last_progress_at = time.monotonic()
                inserted = 0
                for row in rows:
                    if _insert_bar(conn, ts_code, row):
                        inserted += 1
                if inserted:
                    updated_rows += inserted
                else:
                    skipped += 1
                    empty_response += 1
                if index % 200 == 0:
                    conn.commit()
                    print(
                        f"进度: {index}/{len(selected_symbols)} | 更新行:{updated_rows} 跳过:{skipped} 失败:{failed}",
                        flush=True,
                    )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            conn.commit()
            target_day_symbol_count = _count_target_day_symbols(
                conn, target_day, selected_symbols
            )
            raw_max_trade_date = _raw_max_trade_date(conn, selected_symbols)
            coverage_error = (
                _target_coverage_error(
                    target_day=target_day,
                    target_day_symbol_count=target_day_symbol_count,
                    total_symbols=total_symbols,
                    raw_max_trade_date=raw_max_trade_date,
                )
                if require_target_coverage
                else None
            )
            if coverage_error:
                print(f"[ERROR] {coverage_error}", flush=True)
    finally:
        _logout_baostock_session(bs)
    summary = UpdateSummary(
        updated_rows=updated_rows,
        skipped_symbols=skipped,
        failed_symbols=failed,
        target_day=target_day,
        price_mode=price_mode,
        target_day_symbol_count=target_day_symbol_count,
        total_symbols=total_symbols,
        raw_max_trade_date=raw_max_trade_date,
        coverage_error=coverage_error,
        processed_symbols=processed_symbols,
        budget_exhausted=budget_exhausted,
        budget_abort_reason=budget_abort_reason,
        already_current_symbols=already_current,
        empty_response_symbols=empty_response,
        session_relogins=session_relogins,
    )
    # #402：只有干净成功才写「上次成功日更」印章（freshness 靠它区分数据来源）。
    if (
        not summary.failed_symbols
        and not summary.coverage_error
        and not summary.budget_abort_reason
    ):
        _write_success_stamp(db_path, summary=summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="sqlite db path")
    parser.add_argument(
        "--target-date",
        default="",
        help="YYYY-MM-DD, default previous/current trading day",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.3,
        help="seconds to sleep between symbols (default 0.3; raise if baostock throttles)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="update at most N selected symbols"
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="skip this many selected symbols before --limit",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="comma-separated test/repair hook, accepts 600519 or 600519.SH",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="YYYY-MM-DD historical backfill start; default incremental only",
    )
    parser.add_argument(
        "--fill-history-gaps",
        action="store_true",
        help="with --start-date, repair symbols whose first stored row is later than the requested start",
    )
    parser.add_argument(
        "--force-from-start",
        action="store_true",
        help="refetch from --start-date even if newer rows already exist",
    )
    parser.add_argument(
        "--price-mode",
        choices=("qfq", "raw"),
        default="raw",
        help="baostock adjustment mode: raw is required for validation; qfq is legacy-only",
    )
    parser.add_argument(
        "--query-timeout-seconds",
        type=float,
        default=15.0,
        help="per-symbol upstream query timeout; 0 disables the timeout guard",
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=0.0,
        help="stop between symbols after this runtime; 0 disables the batch budget",
    )
    parser.add_argument(
        "--allow-partial-target-coverage",
        action="store_true",
        help="for a scheduled chunk, defer target-day coverage validation to its coordinator",
    )
    parser.add_argument(
        "--rate-limit-cooldown-seconds",
        type=float,
        default=5.0,
        help="pause this many seconds (exponential, capped at 300s) after N consecutive "
        "failures, assuming the upstream IP is throttled; 0 disables the cooldown",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=0,
        help="abort the batch after this many consecutive symbol failures "
        "(e.g. hard rate-limit); 0 disables the abort",
    )
    parser.add_argument(
        "--max-total-failures",
        type=int,
        default=_TOTAL_FAILURE_BUDGET_DEFAULT,
        help="abort the whole run after this many failed symbols in total "
        "(#402 fail-closed budget: release the lock for the next run "
        "instead of looping on a dead upstream); 0 disables",
    )
    parser.add_argument(
        "--max-no-progress-seconds",
        type=float,
        default=_NO_PROGRESS_BUDGET_SECONDS_DEFAULT,
        help="abort the whole run after this long without any successful query "
        "(#402 fail-closed budget for stuck/dead upstream); 0 disables",
    )
    args = parser.parse_args()

    if not args.db.exists():
        if args.price_mode != "raw":
            raise SystemExit(f"database does not exist: {args.db}")
        args.db.parent.mkdir(parents=True, exist_ok=True)
    target = _target_trade_day(args.target_date)
    start_day = date.fromisoformat(args.start_date) if args.start_date else None
    if args.force_from_start and start_day is None:
        raise SystemExit("--force-from-start requires --start-date")
    if args.fill_history_gaps and start_day is None:
        raise SystemExit("--fill-history-gaps requires --start-date")
    if args.offset < 0:
        raise SystemExit("--offset must be non-negative")
    if args.max_runtime_seconds < 0:
        raise SystemExit("--max-runtime-seconds must be non-negative")
    if args.max_total_failures < 0:
        raise SystemExit("--max-total-failures must be non-negative")
    if args.max_no_progress_seconds < 0:
        raise SystemExit("--max-no-progress-seconds must be non-negative")
    print(
        f"sqlite daily backfill target={target.isoformat()} "
        f"start={start_day.isoformat() if start_day else 'incremental'} "
        f"fill_history_gaps={args.fill_history_gaps} "
        f"price_mode={args.price_mode} db={args.db}"
    )
    summary = update_sqlite_daily(
        args.db,
        target_day=target,
        sleep_seconds=args.sleep_seconds,
        limit=args.limit,
        symbols=tuple(item.strip() for item in args.symbols.split(",") if item.strip()),
        start_day=start_day,
        force_from_start=args.force_from_start,
        fill_history_gaps=args.fill_history_gaps,
        price_mode=args.price_mode,
        query_timeout_seconds=args.query_timeout_seconds,
        offset=args.offset,
        max_runtime_seconds=args.max_runtime_seconds,
        require_target_coverage=not args.allow_partial_target_coverage,
        rate_limit_cooldown_seconds=args.rate_limit_cooldown_seconds,
        max_consecutive_failures=args.max_consecutive_failures,
        max_total_failures=args.max_total_failures,
        max_no_progress_seconds=args.max_no_progress_seconds,
    )
    print(
        "sqlite daily backfill done: "
        f"updated_rows={summary.updated_rows} "
        f"skipped_symbols={summary.skipped_symbols} "
        f"failed_symbols={summary.failed_symbols} "
        f"target={summary.target_day.isoformat()} "
        f"price_mode={summary.price_mode} "
        f"target_day_symbols={summary.target_day_symbol_count}/{summary.total_symbols} "
        f"raw_max={summary.raw_max_trade_date.isoformat() if summary.raw_max_trade_date else '-'} "
        f"processed={summary.processed_symbols}/{summary.total_symbols} "
        f"budget_exhausted={summary.budget_exhausted}"
    )
    if summary.budget_abort_reason:
        print(f"[ABORT] {summary.budget_abort_reason}", flush=True)
    if summary.coverage_error:
        print(f"sqlite daily backfill blocked: {summary.coverage_error}")
    return (
        1
        if summary.failed_symbols
        or summary.coverage_error
        or summary.budget_abort_reason
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
