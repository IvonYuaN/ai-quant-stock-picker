#!/usr/bin/env python3
"""预加载并缓存东财限售解禁到本地 CSV。生产机联网执行。

打印「行数 / 覆盖区间 / truncated」—— 预加载必须自证拿到了什么、覆盖到哪。
"""

from __future__ import annotations

import sys

from aqsp.core.errors import DataError
from aqsp.data.lockup import DEFAULT_HORIZON_DAYS, LockupSource


def _span(dates: list[str]) -> str:
    days = sorted(d[:10] for d in dates if d)
    return f"{days[0]} ~ {days[-1]}" if days else "-"


def main() -> int:
    try:
        src = LockupSource()
        items = src.load(force=True)
        print(
            f"[lockup] 已缓存 {len(items)} 条 | 覆盖 {_span([i.plan_date for i in items])} "
            f"| 窗口 {DEFAULT_HORIZON_DAYS}d | truncated={src.truncated} "
            f"-> {src._default_cache_path()}"
        )
        return 0
    except DataError as exc:
        print(f"[lockup] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
