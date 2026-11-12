#!/usr/bin/env python3
"""腾讯行情源回补日线（baostock 不可用时的替代通道）。

口径已验证与库内一致（000001.SZ 20261105 逐字段比对）：
  腾讯 day = [date, open, close, high, low, volume(手)]
  库 daily_qfq = (open, high, low, close, close_qfq, volume(股), amount)
  => volume(股) = 腾讯 volume(手) * 100（科创板除外，见 volume_scale）
  => close 与 close_qfq 写同值（price_mode=raw）

amount 腾讯不提供，写 NULL；覆盖率门禁只依赖 ts_code 计数，不受影响。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,{start},{end},320,"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# 本机存在 MITM 代理，其根证书不在 certifi 信任链内；行情为公开数据，不做证书校验
SSL_CTX = ssl._create_unverified_context()


def to_tx(ts_code: str) -> str:
    code, _, mkt = ts_code.partition(".")
    return ("sh" if mkt.upper() == "SH" else "sz") + code


# 科创板（688/689）腾讯返回的 volume 已是「股」，其余板块是「手」。
# 实测 2026-11-06：sh688001=8_630_559、sh688469=111_188_422（股）；
# sh600000=642_707、sz000001=1_640_276（手）。对科创板再乘 100 会把
# volume 放大两个数量级，污染成交额、换手率与任何绝对量阈值。
_STAR_BOARD_PREFIXES = ("688", "689")


def volume_scale(ts_code: str) -> int:
    """腾讯 fqkline 的 volume 换算到「股」所需的倍数。

    入参必须是本脚本通篇使用的 ``ts_code``（``688001.SH``）形态，
    而不是 ``to_tx`` 产出的 ``sh688001`` —— 两种形态混用正是这个
    缺陷最初没被发现的原因。
    """
    code = ts_code.partition(".")[0]
    if code.startswith(_STAR_BOARD_PREFIXES):
        return 1
    return 100


def fetch_range(sym: str, start: str, end: str) -> list[list[str]]:
    """按 ~300 自然日窗口**分段**拉取（腾讯单次上限 320 根 K 线）。

    不分段时一次请求只会拿到尾部 320 根：本机回补 2023-12-29 → 2026-11-12
    （约 690 个交易日）会**静默丢掉前 ~370 天**，看起来"补完了"其实缺一年多。
    """
    from datetime import date, timedelta

    def _d(x: str) -> date:
        y, m, d = x.split("-")
        return date(int(y), int(m), int(d))

    out: list[list[str]] = []
    seen: set[str] = set()
    cursor = _d(start)
    hard_end = _d(end)
    while cursor <= hard_end:
        window_end = min(cursor + timedelta(days=300), hard_end)
        for bar in fetch(sym, cursor.isoformat(), window_end.isoformat()):
            key = bar[0] if bar else ""
            if key and key not in seen:
                seen.add(key)
                out.append(bar)
        cursor = window_end + timedelta(days=1)
    out.sort(key=lambda b: b[0])
    return out


def fetch(sym: str, start: str, end: str) -> list[list[str]]:
    url = API.format(sym=sym, start=start, end=end)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if payload.get("msg"):
        # 腾讯对非法参数以 code=0 + msg=param error + 空 data 静默返回，必须显式暴露
        raise ValueError(f"api msg={payload['msg']}")
    node = (payload.get("data") or {}).get(sym) or {}
    return node.get("day") or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--target", default="2026-11-06", help="YYYY-MM-DD 拉取终点")
    ap.add_argument("--start", default="2026-08-01", help="库内缺口回补起点")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--batch", type=int, default=300)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = WAL")

    last_seen = dict(
        conn.execute("SELECT ts_code, MAX(trade_date) FROM daily_qfq GROUP BY ts_code").fetchall()
    )
    symbols = sorted(last_seen)
    pending = [s for s in symbols if (last_seen[s] or "") < args.target.replace("-", "")]
    if args.limit:
        pending = pending[: args.limit]
    print(f"[plan] total={len(symbols)} pending={len(pending)} target={args.target}", flush=True)

    start_dt = args.start
    end_dt = args.target
    written = 0
    failed = 0
    done = 0
    t0 = time.time()

    for i in range(0, len(pending), args.batch):
        chunk = pending[i : i + args.batch]
        rows: list[tuple] = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {s: pool.submit(fetch_range, to_tx(s), start_dt, end_dt) for s in chunk}
            for sym, fut in futures.items():
                try:
                    bars = fut.result()
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
                    failed += 1
                    print(f"[warn] {sym} fetch failed: {exc}", flush=True)
                    continue
                if not bars:
                    failed += 1
                    continue
                scale = volume_scale(sym)
                for bar in bars:
                    if len(bar) < 6:
                        continue
                    d, o, c, h, low, vol = bar[0], bar[1], bar[2], bar[3], bar[4], bar[5]
                    try:
                        rows.append(
                            (
                                sym,
                                d.replace("-", ""),
                                float(o) if o else None,
                                float(h) if h else None,
                                float(low) if low else None,
                                float(c) if c else None,
                                int(float(vol) * scale) if vol else None,
                                float(c) if c else None,
                            )
                        )
                    except (TypeError, ValueError):
                        continue
        if rows:
            # 必须用 INSERT OR IGNORE：REPLACE 会整行覆盖，把腾讯不提供的 amount 清成 NULL，
            # 等于毁掉 baostock 时代已有的成交额。这里只补缺失行，已有行原样保留。
            conn.executemany(
                "INSERT OR IGNORE INTO daily_qfq"
                "(ts_code, trade_date, open, high, low, close_qfq, volume, amount, close)"
                " VALUES(?,?,?,?,?,?,?,NULL,?)",
                rows,
            )
            conn.commit()
            written += len(rows)
        done += len(chunk)
        rate = done / max(1e-9, time.time() - t0)
        print(
            f"[progress] {done}/{len(pending)} rows={written} failed={failed} {rate:.1f} sym/s",
            flush=True,
        )
        time.sleep(0.15)

    print(f"[done] written={written} failed={failed} elapsed={time.time()-t0:.0f}s", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
