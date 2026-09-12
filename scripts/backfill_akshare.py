#!/usr/bin/env python3
"""AQSP 备用日线补库（akshare **Sina 后端**，绕过 baostock 计数型限流）。

为何用 Sina 后端：
  - baostock 对本机 IP 计数型硬限流（~200 请求/窗口），本地补全不可靠；
  - akshare 默认 eastmoney 后端(`stock_zh_a_hist`)在沙箱内**间歇 ProxyError**，不可靠；
  - akshare **Sina 后端 `stock_zh_a_daily`** 走代理亦稳定，字段与库内 baostock 逐值一致。

特性：
  - 只补「缺口尾部」(MAX+1→target)；默认跳过长期退市票（MAX < --min-max-date，其数据本已完整）；
  - 多线程并行拉取（--workers，默认 6），DB 写入单线程串行（避免 sqlite 并发）；
  - `INSERT OR REPLACE` **严格镜像** `update_sqlite_daily.py::_insert_bar` 列集
    （open/high/low/close_qfq(=close)/volume/amount/close；qfq OHLC 不写，合库 99.94% NULL 约定）。
  - 北京所(BJ) Sina 不支持 → 跳过，留待 runner（baostock 源）。

⚠️ 并发警示（2026-09-12 实测）：
  - Sina 后端内部用 `libmini_racer`(V8) 解码，**线程不安全** → `--workers>1`
    可能触发 `[FATAL:address_pool_manager.cc(67)] Check failed: !pool->IsInitialized()`
    直接崩溃（0 写入）；建议 `--workers 1`。
  - 即便并行，~1000 请求后 Sina 亦会限流（empty 飙升）。
  - 结论：本机只适合**小批量**补库；bulk 补全应放 runner（baostock 源、无限流、无 mini_racer）。

用法：
  python scripts/backfill_akshare.py "<db>" --target 20260911 \
      [--symbols data/backfill_resume_20260912.txt] [--min-max-date 20250101] \
      [--workers 1] [--sleep-seconds 0.1]
不传 --symbols 则自动取 DB 内 MAX(trade_date)∈[min_max_date, target) 的票。
产出：直接写库（INSERT OR REPLACE）；本脚本不落日志文件，需要留痕请自行重定向 stdout。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak


def _clear_proxy_env() -> None:
    """清掉代理环境变量，让 akshare 直连。

    必须在**任何请求之前**执行；放在 `main()` 开头即可满足 —— 本脚本的全部网络调用都发生在
    `main()` 内，而 requests/urllib 是**按请求**读取代理 env 的（不在 import 期固化），
    因此不需要把它塞在 import 之前（那样会让整块 import 触发 Ruff E402，属于无谓的 lint 例外）。
    """
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)


def _sina_symbol(ts_code: str):
    parts = ts_code.split(".")
    if len(parts) != 2:
        return None
    code, ex = parts
    if ex == "SH":
        return "sh" + code
    if ex == "SZ":
        return "sz" + code
    return None  # BJ unsupported


def _fetch(symbol: str, start: str, end: str):
    """Sina 后端 raw 日线。3 次重试。返回 DataFrame 或 []。"""
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust="")
            if df is None or len(df) == 0:
                return []
            return df
        except Exception:  # noqa: BLE001
            if attempt < 2:
                time.sleep(1.5)
    return []


def _upsert(conn: sqlite3.Connection, ts_code: str, df) -> int:
    n = 0
    for _, r in df.iterrows():
        try:
            d = str(r["date"]).replace("-", "")
            open_ = float(r["open"])
            high = float(r["high"])
            low = float(r["low"])
            close = float(r["close"])
            vol = int(float(r["volume"]))
            amt = float(r["amount"])
        except (ValueError, TypeError, KeyError):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO daily_qfq("
            "ts_code,trade_date,open,high,low,close_qfq,volume,amount,close"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (ts_code, d, open_, high, low, close, vol, amt, close),
        )
        n += 1
    return n


def main() -> int:
    _clear_proxy_env()

    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--target", default="20260911")
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--min-max-date", default="20250101",
                    help="只补 MAX(trade_date)>=此值的票（跳过长期退市票）")
    # 默认 1：Sina 后端内部用 libmini_racer(V8) 解码、线程不安全，>1 有崩机风险（见模块 docstring）
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--sleep-seconds", type=float, default=0.1)
    args = ap.parse_args()

    db = Path(args.db)
    conn = sqlite3.connect(db, timeout=60)
    cur = conn.cursor()

    if args.symbols:
        raw = Path(args.symbols).read_text(encoding="utf-8")
        symbols = [s.strip() for s in raw.replace("\n", ",").split(",") if s.strip()]
    else:
        symbols = [r[0] for r in cur.execute(
            "SELECT ts_code FROM daily_qfq GROUP BY ts_code "
            "HAVING MAX(trade_date) < ? AND MAX(trade_date) >= ?",
            (args.target, args.min_max_date))]

    # 预构建任务（主线程读 DB，避免并发 sqlite）
    tasks = []
    bj = 0
    for ts_code in symbols:
        sym = _sina_symbol(ts_code)
        if sym is None:
            bj += 1
            continue
        row = cur.execute("SELECT MAX(trade_date) FROM daily_qfq WHERE ts_code=?", (ts_code,)).fetchone()
        maxd = row[0] if row and row[0] else None
        if maxd and maxd >= args.target:
            continue
        start = "20230101" if not maxd else f"{int(maxd)+1:08d}"
        tasks.append((ts_code, sym, start))

    print(f"akshare(sina) backfill target={args.target} candidates={len(symbols)} "
          f"tasks={len(tasks)} bj_skipped={bj} workers={args.workers} db={db}", flush=True)

    updated = 0
    failed = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut2code = {ex.submit(_fetch, sym, start, args.target): ts_code
                    for ts_code, sym, start in tasks}
        for fut in as_completed(fut2code):
            ts_code = fut2code[fut]
            done += 1
            try:
                df = fut.result()
            except Exception:  # noqa: BLE001
                df = []
            if df is None or len(df) == 0:
                failed += 1
            else:
                n = _upsert(conn, ts_code, df)
                conn.commit()
                updated += n
            if done % 100 == 0:
                print(f"进度: {done}/{len(tasks)} | 更新行:{updated} 空:{failed}", flush=True)
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    print(f"sqlite akshare(sina) backfill done: updated_rows={updated} empty={failed} "
          f"bj_skipped={bj} tasks={len(tasks)} target={args.target}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
