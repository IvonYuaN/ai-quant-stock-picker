#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基本面 / 估值历史采集（补 quality / value 两个"全常量"因子的数据基础）。

为什么要它
----------
2026-11-12 结论：`quality` / `value` 在筛选里**恒为常量**，根因是**缺基本面列**。
而它们是唯一还**没被证伪**的因子方向（全部 OHLCV 因子与三类新源在作业面均无效）。
本脚本补上数据，让这两个因子可以被真正测量。

两个源（均已实测，带 PIT 性质）
------------------------------
1. `report` 业绩报表 `RPT_LICO_FN_CPD`，按 `REPORTDATE` 逐报告期：
   `BASIC_EPS` / `TOTAL_OPERATE_INCOME` / `PARENT_NETPROFIT` / `WEIGHTAVG_ROE`
   / **`NOTICE_DATE`（披露日）** —— 与 holder 同样的纪律：**必须用披露日**做可用时点，
   用报告期 = 前视 1~4 个月。
2. `valuation` 估值 `RPT_VALUEANALYSIS_DET`，按 `TRADE_DATE`：
   `PE_TTM` / `PB_MRQ`。按日取值本身即 PIT（当日已知）。

用法
----
    # 业绩报表：2022Q4 ~ 2026Q3（多取 4 期供同比）
    python scripts/data/fetch_fundamentals_history.py --source report \
        --start 2022-09-01 --end 2026-12-31 --out-dir pit_cache

    # 估值：按本地库的交易日每 N 个取一天（默认 10）
    python scripts/data/fetch_fundamentals_history.py --source valuation \
        --start 2023-11-01 --end 2026-11-12 --every 10 \
        --db "A股量化分析数据/astocks_raw.db" --out-dir pit_cache

⚠️ 只写本地 `pit_cache/`，不碰生产库、不碰生产机。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
CTX = ssl._create_unverified_context()
PAGE_SIZE = 500
MAX_PAGES = 80


def _get(report: str, filt: str, page: int) -> tuple[list[dict], int]:
    q = urllib.parse.urlencode(
        {"reportName": report, "columns": "ALL", "pageNumber": page,
         "pageSize": PAGE_SIZE, "filter": filt}
    )
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"{API}?{q}", headers=UA)
            with urllib.request.urlopen(req, context=CTX, timeout=25) as resp:
                payload = json.load(resp)
            if not payload.get("success"):
                return [], 0
            res = payload.get("result") or {}
            return list(res.get("data") or []), int(res.get("count") or 0)
        except Exception:  # noqa: BLE001 - 网络重试
            time.sleep(1.2 * (attempt + 1))
    return [], 0


def _fetch(report: str, filt: str) -> list[dict]:
    rows, total = _get(report, filt, 1)
    pages = min(MAX_PAGES, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    for p in range(2, pages + 1):
        batch, _ = _get(report, filt, p)
        if not batch:
            break
        rows.extend(batch)
    return rows


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        print(f"  ⚠️ 无数据：{path.name}")
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


def _quarters(start: dt.date, end: dt.date, extra: int = 4) -> list[dt.date]:
    out: list[dt.date] = []
    cur = start
    while cur <= end:
        qm = ((cur.month - 1) // 3 + 1) * 3
        last = {3: 31, 6: 30, 9: 30, 12: 31}[qm]
        d = dt.date(cur.year, qm, last)
        out.append(d)
        cur = d + dt.timedelta(days=1)
    out = sorted(set(out))
    # 往回多取 extra 期（同比/环比需要前值）
    for _ in range(extra):
        d = out[0]
        qm = d.month - 3
        y, m = (d.year, qm) if qm > 0 else (d.year - 1, qm + 12)
        out.insert(0, dt.date(y, m, {3: 31, 6: 30, 9: 30, 12: 31}[m]))
    return out


def fetch_report(start: dt.date, end: dt.date, out: Path) -> int:
    qs = _quarters(start, end)
    print(f"[业绩报表] 报告期 {len(qs)} 个：{qs[0]} ~ {qs[-1]}")
    rows: list[dict] = []
    for q in qs:
        batch = _fetch("RPT_LICO_FN_CPD", f"(REPORTDATE='{q.isoformat()}')")
        print(f"  {q}: {len(batch)} 行")
        rows.extend(batch)
    _write(out / "fundamentals_report_history.csv", rows)
    return len(rows)


def fetch_valuation(
    start: dt.date, end: dt.date, every: int, db: str, out: Path
) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    days = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT trade_date FROM daily_qfq WHERE trade_date BETWEEN ? AND ?"
            " ORDER BY trade_date",
            (start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
        )
    ]
    con.close()
    picked = days[:: max(1, every)]
    print(f"[估值] 交易日 {len(days)} 个 → 每 {every} 取 1 ⇒ {len(picked)} 个日期")

    def _one(d: str) -> list[dict]:
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return _fetch("RPT_VALUEANALYSIS_DET", f"(TRADE_DATE='{iso}')")

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, batch in enumerate(ex.map(_one, picked), 1):
            rows.extend(batch)
            if i % 20 == 0:
                print(f"  ...{i}/{len(picked)} 日，累计 {len(rows)} 行")
    _write(out / "valuation_history.csv", rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("report", "valuation", "all"), required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--every", type=int, default=10, help="估值取样的交易日间隔")
    ap.add_argument("--db", default="A股量化分析数据/astocks_raw.db")
    ap.add_argument("--out-dir", default="pit_cache")
    args = ap.parse_args(argv)

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    out = Path(args.out_dir)
    total = 0
    if args.source in ("report", "all"):
        total += fetch_report(start, end, out)
    if args.source in ("valuation", "all"):
        total += fetch_valuation(start, end, args.every, args.db, out)
    print(f"\n完成：累计 {total} 行")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
