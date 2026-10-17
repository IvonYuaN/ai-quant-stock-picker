#!/usr/bin/env python3
"""开盘啦题材轮动 + 龙头梯队：runner 代抓脚本（#404）。生产机联网执行。

产物
----
- ``pit_cache/kaipanla_theme_rotation.csv`` —— 轮动矩阵落表（20 天窗口逐日累积，
  同交易日幂等替换）。
- ``pit_cache/kaipanla_theme_leaders.csv`` —— 龙一~龙五梯队落表。

为什么「日跑即回补」
------------------
轮动端点一次返回最近 20 个交易日；每天重跑时新窗口把最新日挤进表里、最早日
自然滚出窗口（但表内已存的行保留）。幂等语义保证同日的重复/修正写入不叠加。
历史库**从首次积累日开始，不可回补更早的日期**（开盘啦只给 20 天窗口）。

口径
----
- 热度是带符号的独立口径（#398 对账：非成交额/主力净流入/涨跌幅/振幅的简单函数；
  负值绿色渲染）⇒ **存原值、不做跨板块比较**。
- 频控：单次/日、盘后跑；东财侧无关（本脚本只抓开盘啦）。

退出码
------
0 = 成功（含 0 异常）；1 = 抓取/解析失败（DataError）；2 = 有 schema 漂移异常
（数据已落盘但需人工看一眼告警明细——不阻断当日数据，但值得记一笔）。
"""

from __future__ import annotations

import argparse
import sys

from aqsp.core.errors import DataError
from aqsp.data.kaipanla_theme import DEFAULT_DAYS, KaipanlaThemeSource


def main() -> int:
    parser = argparse.ArgumentParser(description="开盘啦题材轮动/龙头梯队抓取（runner 代抓）")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help="轮动/龙榜窗口交易日数（默认 20；开盘啦上限即 20）",
    )
    parser.add_argument(
        "--no-leaders",
        action="store_true",
        help="只抓轮动矩阵，不抓各板块龙榜（省一半请求数）",
    )
    parser.add_argument("--timeout", type=int, default=30, help="单请求超时秒数")
    args = parser.parse_args()

    source = KaipanlaThemeSource(days=args.days, timeout=args.timeout)
    try:
        written = source.load(with_leaders=not args.no_leaders)
    except DataError as exc:
        print(f"[kaipanla_theme] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return 1

    total_anomalies = sum(s.anomalies for s in source.stats.values())
    print(
        f"[kaipanla_theme] 已缓存 轮动 {written['rotation']} 行 / "
        f"龙榜 {written['leaders']} 行（days={args.days}）"
    )
    for key, stats in source.stats.items():
        if stats.anomalies:
            print(
                f"[kaipanla_theme] WARN {key}: {stats.anomalies} 条异常（已跳过，未写入）",
                file=sys.stderr,
            )
            for detail in stats.detail:
                print(f"    - {detail}", file=sys.stderr)
    if total_anomalies:
        # 数据已落盘但存在 schema 漂移迹象：给非零退出码供 cron 告警，
        # 但 preload 侧按 best-effort 处理不阻断其余源（见 preload_event_data.sh）。
        print(
            f"[kaipanla_theme] 完成但有 {total_anomalies} 条 schema 漂移异常（数据已落盘）",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
