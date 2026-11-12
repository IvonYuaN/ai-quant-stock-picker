#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三类新因子数据源的历史采集（资金流 / 事件 / 解禁）。

为什么要这个脚本
----------------
2026-11-12 盘点发现：仓库里的 `fetch_longhubang.py` / `fetch_event_data.py` /
`fetch_lockup.py` **都只取「当前快照」，没有日期参数** ⇒ 无法回补历史。
而 `pit_cache/announcements.csv` 只有 9 天、基本面（quality/value）缺列 ⇒
三类新数据源在**任何环境都没有可用历史**，这正是「因子池只剩 2 个」的供给侧原因。

本脚本按日期/报告期**循环回补**，落本地 CSV（**只写本地 pit_cache，不碰生产库**）。

三个源（均已实测可按日期取历史，2026-11-12）
------------------------------------------
1. `longhubang` 龙虎榜：`RPT_DAILYBILLBOARD_DETAILS`，按 `TRADE_DATE` 逐交易日，
   字段含 `BILLBOARD_DEAL_AMT` / `DEAL_AMOUNT_RATIO` / `FREE_MARKET_CAP` / `EXPLAIN`。
   ⇒ 可造「龙虎榜净买入占比」「上榜次数」等资金流因子。
2. `forecast` 业绩预告：`RPT_PUBLIC_OP_NEWPREDICT`，按 `REPORT_DATE` 逐报告期，
   **含 `NOTICE_DATE`（披露日）** ⇒ 天然可做 PIT（同 holder 的教训：必须用披露日）。
3. `lockup` 限售解禁：`RPT_LIFT_STAGE`，按 `FREE_DATE` 区间，
   字段含 `CURRENT_FREE_SHARES` / `LIFT_MARKET_CAP`。

用法
----
    python scripts/data/fetch_factor_sources_history.py --source all \
        --start 2023-11-01 --end 2026-11-12 --out-dir pit_cache

    # 单跑
    python scripts/data/fetch_factor_sources_history.py --source forecast --start 2023-09-01 --end 2026-11-12
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# 公开行情/公告数据；本机 MITM 代理根证书不在 certifi 链内
CTX = ssl._create_unverified_context()
PAGE_SIZE = 500
MAX_PAGES = 60


def _get(report: str, filt: str, page: int, page_size: int = PAGE_SIZE) -> tuple[list[dict], int]:
    """单页查询；返回 ``(rows, total)``。失败重试 2 次。"""
    q = urllib.parse.urlencode(
        {"reportName": report, "columns": "ALL", "pageNumber": page, "pageSize": page_size, "filter": filt}
    )
    url = f"{API}?{q}"
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX, timeout=25) as resp:
                payload = json.load(resp)
            if not payload.get("success"):
                return [], 0
            result = payload.get("result") or {}
            return list(result.get("data") or []), int(result.get("count") or 0)
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试
            last = exc
            time.sleep(1.5 * (attempt + 1))
    print(f"  ⚠️ 查询失败（{filt}）：{type(last).__name__}: {str(last)[:70]}")
    return [], 0


def _fetch_all(report: str, filt: str, limit: int = 0) -> list[dict]:
    rows: list[dict] = []
    first, total = _get(report, filt, 1)
    rows.extend(first)
    pages = min(MAX_PAGES, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    for p in range(2, pages + 1):
        batch, _ = _get(report, filt, p)
        if not batch:
            break
        rows.extend(batch)
    if limit and len(rows) > limit:
        rows = rows[:limit]
    return rows


def _weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:  # 周末跳过（节假日由接口返回 0 行自然跳过）
            out.append(cur)
        cur += dt.timedelta(days=1)
    return out


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        print(f"  ⚠️ 无数据，未写 {path.name}")
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})
    print(f"  ✅ {len(rows)} 行 → {path}")


def fetch_longhubang(start: dt.date, end: dt.date, out: Path) -> int:
    days = _weekdays(start, end)
    print(f"[龙虎榜] 逐交易日 {len(days)} 天（{days[0]} ~ {days[-1]}）")
    rows: list[dict] = []
    empty = 0
    for i, d in enumerate(days, 1):
        batch = _fetch_all("RPT_DAILYBILLBOARD_DETAILS", f"(TRADE_DATE='{d.isoformat()}')")
        if not batch:
            empty += 1
        rows.extend(batch)
        if i % 50 == 0:
            print(f"  ...{i}/{len(days)} 天，累计 {len(rows)} 行")
    print(f"  空日（节假日/无榜）{empty} 天")
    _write(out / "longhubang_history.csv", rows)
    return len(rows)


def fetch_forecast(start: dt.date, end: dt.date, out: Path) -> int:
    quarters: list[dt.date] = []
    y, cur = start.year, start
    while cur <= end:
        q_end_month = ((cur.month - 1) // 3 + 1) * 3
        last = {3: 31, 6: 30, 9: 30, 12: 31}[q_end_month]
        quarters.append(dt.date(cur.year, q_end_month, last))
        cur = dt.date(cur.year, q_end_month, last) + dt.timedelta(days=1)
    quarters = sorted(set(quarters))
    # 往回多取 4 个季度：QoQ/同比类因子需要前值
    extra = []
    c = quarters[0]
    for _ in range(4):
        m = c.month - 3
        y2 = c.year if m > 0 else c.year - 1
        m2 = m if m > 0 else m + 12
        c = dt.date(y2, m2, {3: 31, 6: 30, 9: 30, 12: 31}[m2])
        extra.append(c)
    quarters = sorted(set(quarters + extra))
    print(f"[业绩预告] 报告期 {len(quarters)} 个（{quarters[0]} ~ {quarters[-1]}）")
    rows: list[dict] = []
    for q in quarters:
        batch = _fetch_all("RPT_PUBLIC_OP_NEWPREDICT", f"(REPORT_DATE='{q.isoformat()}')")
        print(f"  {q}: {len(batch)} 行")
        rows.extend(batch)
    _write(out / "forecast_history.csv", rows)
    return len(rows)


def fetch_lockup(start: dt.date, end: dt.date, out: Path) -> int:
    filt = f"(FREE_DATE>='{start.isoformat()}')(FREE_DATE<='{end.isoformat()}')"
    print(f"[限售解禁] 区间 {start} ~ {end}")
    rows = _fetch_all("RPT_LIFT_STAGE", filt)
    _write(out / "lockup_history.csv", rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("longhubang", "forecast", "lockup", "all"), required=True)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out-dir", default="pit_cache")
    args = ap.parse_args(argv)

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    out = Path(args.out_dir)
    jobs = (
        ("longhubang", fetch_longhubang),
        ("forecast", fetch_forecast),
        ("lockup", fetch_lockup),
    )
    total = 0
    for name, fn in jobs:
        if args.source not in (name, "all"):
            continue
        try:
            total += fn(start, end, out)
        except Exception as exc:  # noqa: BLE001 - 单源失败不影响其他源
            print(f"❌ {name} 失败：{type(exc).__name__}: {str(exc)[:90]}")
    print(f"\n完成：累计 {total} 行")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
