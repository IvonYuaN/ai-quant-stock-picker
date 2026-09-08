#!/usr/bin/env python3
"""预加载并缓存 PIT 数据源（申万行业 + 宏观社融/PMI）到本地 CSV。

生产机联网执行一次即可激活 ``industry_pit`` / ``macro_pit`` 的管线接入：
- 申万行业表 -> ``src/aqsp/data/industry_pit.csv``（IndustryPitSource 默认缓存）
- 宏观序列   -> ``src/aqsp/data/macro_pit.csv``（MacroPitSource 默认缓存）

沙箱不可联网，本脚本会抛 DataError 并退出非 0，属预期——管线靠优雅回退运行。
宏观抓取还需在生产机为 ``macro_pit.PBC_SOCIAL_FINANCING_URL`` /
``NBS_PMI_URL`` 配置真实动态地址（见模块内说明）后才能落盘。
"""

from __future__ import annotations

import sys

from aqsp.core.errors import DataError
from aqsp.data.industry_pit import IndustryPitSource
from aqsp.data.macro_pit import MacroPitSource


def main() -> int:
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

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
