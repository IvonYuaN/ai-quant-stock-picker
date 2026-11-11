#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分板块数据覆盖审计（防「整块板块静默缺失」）。

为什么存在
----------
2026-11-11 实测发现仓库两处真实缺陷，且**能静默存在很久**：

1. **prod 库科创板整块停在 2023-12-29**（688×565 / 689×1 / 302×1），
   而主板已到 2026-11-11 ⇒ 生产每日选股三年看不到科创板。
2. **runner 库科创板 2024~2025 两年只剩 1 只票**（564 只全缺），
   2026 年虽有但 `volume`/`amount` 被 ×100（该板块腾讯源返回「股」而非「手」）。

为什么既有的覆盖率门禁抓不到：它按「活跃票」做**分母**，缺整块板块时
分母同步变小 ⇒ 仍然报 99.86% PASS（自证干净）。故必须补一层
**按板块 × 按年** 的绝对覆盖检查。

判定规则（fail-closed，只抓「塌陷」不抓「成长」）
------------------------------------------------
板块成分会随新股上市**逐年增长**，用「与峰值比」会大量误报（实测 001/003
在 2010-2019 年票数少是正常的）。真实缺陷形态只有两种，故只查这两条：

1. **停更**：该板块的 `MAX(trade_date)` 落后全市场 `MAX(trade_date)` 超过
   ``--stale-days``（默认 30 个自然日）⇒ 整块停更；
2. **塌陷**：某年票数 < 上一年的 50%（且上一年 >= 20 只）⇒ 整块缺某年；
3. **交易日短缺**：某年交易日 < 全市场该年交易日的 50%（且该年票数 >= 20）
   ⇒ 整段缺数据。

用法
----
    python scripts/data/audit_board_coverage.py --db <path> [--stale-days 30]
    python scripts/data/audit_board_coverage.py --db ... --json out.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

#: 只审计这些前缀（其余如指数 399、北交所 8xx 可自行扩展）
_BOARDS = ("688", "689", "302", "600", "601", "603", "605", "000", "001", "002", "003", "300", "301")


def audit(db: str, floor: float = 0.5, stale_days: int = 30) -> tuple[list[dict], list[str]]:
    """返回 ``(逐板块逐年明细, 告警列表)``。只读打开。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT substr(ts_code,1,3) b, substr(trade_date,1,4) y,"
            " COUNT(DISTINCT ts_code) s, COUNT(DISTINCT trade_date) d,"
            " MAX(trade_date) mx"
            " FROM daily_qfq GROUP BY b, y"
        ).fetchall()
        market_days = {
            y: d
            for y, d in con.execute(
                "SELECT substr(trade_date,1,4) y, COUNT(DISTINCT trade_date)"
                " FROM daily_qfq WHERE substr(ts_code,1,1) IN ('6','0','3')"
                " GROUP BY y"
            )
        }
        market_max = con.execute("SELECT MAX(trade_date) FROM daily_qfq").fetchone()[0]
    finally:
        con.close()

    detail: list[dict] = []
    by_board: dict[str, list[tuple[str, int, int, str]]] = defaultdict(list)
    for board, year, stocks, days, mx in rows:
        if board not in _BOARDS:
            continue
        by_board[board].append((year, stocks, days, mx))

    warnings: list[str] = []
    for board, items in sorted(by_board.items()):
        items.sort()
        peak_so_far = 0  # 该板块「历史最高票数」（只用更早年份）
        for idx, (year, stocks, days, mx) in enumerate(items):
            ref_days = market_days.get(year, 0) or 1
            detail.append(
                {
                    "board": board,
                    "year": year,
                    "stocks": stocks,
                    "days": days,
                    "market_days": ref_days,
                    "last_date": mx,
                }
            )
            # 2) 塌陷：与**历史峰值**比腰斩（不是只比上一年 —— 否则连续多年
            #    塌陷时第二年起就不再触发。实测 runner 库 688: 2023=565,
            #    2024=1（触发）, 2025=1（只比上一年 1 就不触发 ⇒ 漏报）。
            #    峰值机制 + 首年峰值=0 自然豁免开板当年。
            if peak_so_far >= 20 and stocks < peak_so_far * floor:
                warnings.append(
                    f"{board} {year}: 票数 {stocks} vs 历史峰值 {peak_so_far} "
                    f"（{stocks/peak_so_far:.0%} < {floor:.0%}）⇒ 疑整块缺该年"
                )
            # 3) 交易日短缺（首年豁免：板块开板当年天然不满一年，
            #    实测科创板 2019-07 开板 ⇒ 2019 只有 111 个交易日 vs 全市场 244）
            if idx > 0 and stocks >= 20 and days / ref_days < floor:
                warnings.append(
                    f"{board} {year}: 交易日 {days} vs 全市场 {ref_days} "
                    f"（{days/ref_days:.0%}）⇒ 疑整段缺数据"
                )
            peak_so_far = max(peak_so_far, stocks)
        # 1) 停更：板块最新日期落后全市场
        board_max = max(mx for _, _, _, mx in items)
        if market_max and board_max:
            lag = _days_between(board_max, market_max)
            if lag is not None and lag > stale_days:
                warnings.append(
                    f"{board}: 最新 {board_max} 落后全市场 {market_max} 达 {lag} 天"
                    f"（> {stale_days}）⇒ 疑整块停更"
                )
    return detail, warnings


def _days_between(early: str, late: str) -> int | None:
    """``YYYYMMDD`` 差值（天）；解析失败返回 None。"""
    try:
        from datetime import datetime

        a = datetime.strptime(str(early), "%Y%m%d")
        b = datetime.strptime(str(late), "%Y%m%d")
        return abs((b - a).days)
    except (ValueError, TypeError):
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--floor", type=float, default=0.5)
    p.add_argument("--stale-days", type=int, default=30)
    p.add_argument("--json", type=Path, default=None)
    args = p.parse_args(argv)

    if not Path(args.db).is_file():
        print(f"❌ 库不存在：{args.db}")
        return 2

    detail, warnings = audit(args.db, args.floor, args.stale_days)
    print(f"审计库：{args.db}（{len(detail)} 个 板块×年 组合）")
    for w in warnings:
        print(f"  ⚠️  {w}")
    if not warnings:
        print("  ✅ 所有板块年度覆盖均达标")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"detail": detail, "warnings": warnings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"明细已写出：{args.json}")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
