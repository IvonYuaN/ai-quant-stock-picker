#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补全股东户数历史（研究用 CSV），覆盖 3 年 gate 窗口。

为什么需要
----------
2026-11-10 实测：现有 `pit_cache/holder_count.csv` 只有 **4 季**
（2025-09-30 ~ 2026-06-30），而 gate 窗口是 2023-11-10 ~ 2026-11-09
⇒ **63% 的窗口没有 holder 数据 ⇒ 分数全 0.5（中性）⇒ overlay 全程空转**。
在这种数据上跑 gate，ΔSharpe 无法解释（"没效果"与"没启用"不可区分）。

本脚本直接按季度向东方财富 DET 报表（`RPT_HOLDERNUM_DET`，含完整历史）
拉取指定季度全量，写成研究用 CSV（**不碰生产 pit_cache**，不写库）。

产出列与 `HolderNumSource._read_cache` 兼容：
    symbol, name, quarter, holder_count, notice_date
（notice_date 是**披露日**，PIT 判定必须用它，不是季末日。）

用法
----
    python scripts/research/fetch_holder_history.py \
        --out research/holder_history_15q.csv \
        --quarters 2022-12-31,2023-03-31,...

不给 `--quarters` 时按「覆盖 3y 窗口所需」自动生成：从 gate 起点前 2 季
到最新已披露季。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqsp.data.holder_num import HolderNumSource  # noqa: E402
from aqsp.core.time import today_shanghai  # noqa: E402


def default_quarters(lookback_years: int = 3) -> list[str]:
    """覆盖 ``lookback_years`` 所需季度（往前多留 2 季，保证起点就有 QoQ）。"""
    today = today_shanghai()
    # 最近一个已结束季度
    q_end_month = ((today.month - 1) // 3) * 3
    year, month = today.year, q_end_month
    if month == 0:
        year, month = year - 1, 12
    out: list[str] = []
    for _ in range(lookback_years * 4 + 2):
        last_day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
        out.append(f"{year:04d}-{month:02d}-{last_day:02d}")
        month -= 3
        if month <= 0:
            year, month = year - 1, month + 12
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quarters", default="",
                        help="逗号分隔的季末日期；不给则自动覆盖 3y")
    parser.add_argument("--lookback-years", type=int, default=3)
    args = parser.parse_args(argv)

    quarters = (
        [q.strip() for q in args.quarters.split(",") if q.strip()]
        if args.quarters
        else default_quarters(args.lookback_years)
    )
    print(f"[fetch] 目标季度 {len(quarters)} 个：{quarters[0]} .. {quarters[-1]}")

    src = HolderNumSource(cache_path=str(args.out))
    # 🔴 2026-11-10 事故修复：**不能**把输出 CSV 当 source 的 cache_path。
    # 因为 HolderNumSource.load() 在「读不到 cache」时会抓默认季度数并**回写
    # 该路径** ⇒ 我们辛苦抓的 14 季会被覆写成默认 4 季（实测：73644 行 →
    # 21422 行，且每票只剩 4 季，静默无报错）。
    # 改为：source 用**临时占位路径**（只用于不让它读写真实文件），
    # 直接调 _fetch 拿全量，再由本脚本独占写 --out。
    src = HolderNumSource(cache_path="/tmp/.holder_fetch_cache_placeholder.csv")
    # 直接用 _fetch（按季全量分页），绕开 load() 的合并上限与回写行为
    items = src._fetch(quarters)  # noqa: SLF001 - 研究脚本，取全量历史
    print(f"[fetch] 抓到 {len(items)} 行，truncated={src.truncated}")

    seen: dict[tuple[str, str], object] = {}
    for it in items:
        if not it.symbol or not it.quarter:
            continue
        seen[(it.symbol, it.quarter)] = it
    rows = sorted(seen.values(), key=lambda i: (i.symbol, i.quarter))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["symbol", "name", "quarter", "holder_count", "notice_date"]
        )
        writer.writeheader()
        for it in rows:
            writer.writerow({
                "symbol": it.symbol,
                "name": it.name,
                "quarter": it.quarter,
                "holder_count": it.holder_count,
                "notice_date": it.notice_date,
            })
    print(f"[fetch] 写出 {len(rows)} 行 → {args.out}")

    # 自检：季度覆盖 + 披露滞后 + 可算 QoQ 的票数
    import collections
    import datetime as dt
    import statistics

    per_q = collections.Counter(str(r.quarter)[:10] for r in rows)
    print("[fetch] 每季行数：", dict(sorted(per_q.items())))
    by_sym = collections.Counter(r.symbol for r in rows)
    print(f"[fetch] 唯一票 {len(by_sym)}；>=2 季（可算 QoQ）{sum(1 for c in by_sym.values() if c >= 2)}")
    lags = []
    for r in rows:
        try:
            q = dt.date.fromisoformat(str(r.quarter)[:10])
            n = dt.date.fromisoformat(str(r.notice_date)[:10])
            lags.append((n - q).days)
        except Exception:
            continue
    if lags:
        lags.sort()
        print(
            f"[fetch] 披露滞后：中位 {statistics.median(lags):.0f} 天 / "
            f"P90 {lags[int(len(lags)*0.9)]} / 最大 {lags[-1]} 天"
        )

    # 🔴 fail-closed 自检：每票季度数若明显少于目标季数，说明抓取/写盘出了问题
    # （2026-11-10 实测过：被 load() 回写成 4 季后每票只剩 4 季、却静默"成功"）。
    med = statistics.median(by_sym.values()) if by_sym else 0
    expected = max(2, len(per_q) - 2)
    if med < expected:
        print(
            f"[fetch] ❌ 自检失败：每票季度数中位 {med} < 期望 {expected} "
            f"⇒ 数据不完整，拒绝使用"
        )
        return 3
    print(f"[fetch] ✅ 自检通过：每票季度数中位 {med}（期望 ≥{expected}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
