#!/usr/bin/env python3
"""预加载并缓存东财龙虎榜到本地 CSV。生产机联网执行。

打印「行数 / 覆盖区间 / truncated」—— 覆盖区间必须足够 lookback_days，
否则下游「近 N 日未上榜」的否定结论不成立。
"""

from __future__ import annotations

import sys

from aqsp.core.errors import DataError
from aqsp.data.longhubang import DEFAULT_LOOKBACK_DAYS, LongHubangSource


def _span(dates: list[str]) -> str:
    days = sorted(d[:10] for d in dates if d)
    return f"{days[0]} ~ {days[-1]}" if days else "-"


def main() -> int:
    try:
        src = LongHubangSource()
        items = src.load(force=True)
        print(
            f"[longhubang] 已缓存 {len(items)} 条 | "
            f"覆盖 {_span([i.trade_date for i in items])} "
            f"| lookback {DEFAULT_LOOKBACK_DAYS}d | truncated={src.truncated} "
            f"-> {src._default_cache_path()}"
        )
        if src.truncated:
            print(
                f"[longhubang] ⚠️ lookback {DEFAULT_LOOKBACK_DAYS}d 覆盖不完整，"
                "下游不得据此下「近 N 日未上榜」的否定结论",
                file=sys.stderr,
            )
        return 0
    except DataError as exc:
        print(f"[longhubang] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
