#!/usr/bin/env python3
"""预加载并缓存 PIT 数据源（申万行业 + 宏观社融/PMI + 日频估值）到本地 CSV。

生产机联网执行即可激活 ``industry_pit`` / ``macro_pit`` / ``valuation_history``
的管线接入：
- 申万行业表 -> ``<AQSP_RUNTIME_DATA_ROOT>/pit_cache/industry_pit.csv``
  （IndustryPitSource 默认缓存；#335 起缓存统一落 runtime data root，
  不再落不可变 release 目录内）
- 宏观序列   -> ``<AQSP_RUNTIME_DATA_ROOT>/pit_cache/macro_pit.csv``
  （MacroPitSource 默认缓存）
- 日频估值   -> ``<AQSP_RUNTIME_DATA_ROOT>/pit_cache/valuation_history.csv``
  （ValuationHistorySource 默认缓存；生产 ``value`` 子分当前 enabled:false，
   估值维度整格空着，本缓存是「行业内 PB」进生产的硬前提）

沙箱不可联网，本脚本会抛 DataError 并退出非 0，属预期——管线靠优雅回退运行。
宏观抓取还需在生产机为 ``macro_pit.PBC_SOCIAL_FINANCING_URL`` /
``NBS_PMI_URL`` 配置真实动态地址（见模块内说明）后才能落盘。

估值区间用 ``--valuation-start/--valuation-end/--valuation-stride-days`` 控制：
默认只补最近 30 天（够生产日常滚动，30 天 ≈ 180 次请求 ≈ 5 分钟）；
**三年历史回填须显式指定区间**（约 380 万行、数小时，应在 runner 上跑，
别在生产机抢资源）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai
from aqsp.data.industry_pit import IndustryPitSource
from aqsp.data.macro_pit import MacroPitSource
from aqsp.data.valuation_history import ValuationHistorySource

_DEFAULT_VALUATION_DAYS = 30


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预加载 PIT 数据源缓存")
    parser.add_argument(
        "--valuation-start",
        default="",
        help="估值采集起始日 YYYY-MM-DD（默认：end 往前 30 天）",
    )
    parser.add_argument(
        "--valuation-end",
        default=today_shanghai().isoformat(),
        help="估值采集截止日 YYYY-MM-DD（默认：今天）",
    )
    parser.add_argument(
        "--valuation-stride-days",
        type=int,
        default=1,
        help="估值采集步长（自然日）：1=每日、14=双周（回填可省 14 倍请求）",
    )
    parser.add_argument(
        "--skip-valuation",
        action="store_true",
        help="跳过估值采集（只要行业/宏观）",
    )
    return parser.parse_args(argv)


def _valuation_dates(start: str, end: str, stride: int) -> list[str]:
    """把 [start, end] 按 stride 切成候选日期列表（非交易日由接口自然过滤）。"""
    end_day = date.fromisoformat(end)
    start_day = date.fromisoformat(start)
    if start_day > end_day:
        raise DataError(f"估值区间非法：{start} > {end}")
    step = max(1, stride)
    out: list[str] = []
    cursor = start_day
    while cursor <= end_day:
        out.append(cursor.isoformat())
        cursor += timedelta(days=step)
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ok = True

    try:
        src = IndustryPitSource()
        df = src.load(force=True)
        print(f"[industry_pit] 已缓存 {len(df)} 行 -> {src._default_cache_path()}")
    except DataError as exc:
        print(f"[industry_pit] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        ok = False

    try:
        ms = MacroPitSource()
        ms.load(force=True)
        print(f"[macro_pit] 已缓存 -> {ms._default_cache_path()}")
    except DataError as exc:
        print(f"[macro_pit] 跳过（需配置 PBC/NBS URL）: {exc}", file=sys.stderr)
        ok = False

    if args.skip_valuation:
        print("[valuation_history] 按 --skip-valuation 跳过")
    else:
        end = args.valuation_end
        start = args.valuation_start or (
            date.fromisoformat(end) - timedelta(days=_DEFAULT_VALUATION_DAYS)
        ).isoformat()
        try:
            vs = ValuationHistorySource()
            dates = _valuation_dates(start, end, args.valuation_stride_days)
            added = vs.sync_dates(dates)
            print(
                f"[valuation_history] 新增 {added} 行 "
                f"（{len(dates)} 个候选日 {start}~{end}）-> {vs._default_cache_path()}"
            )
        except (DataError, ValueError) as exc:
            print(f"[valuation_history] 跳过（网络/日期非法）: {exc}", file=sys.stderr)
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
