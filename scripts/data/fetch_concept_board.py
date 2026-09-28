#!/usr/bin/env python3
"""预加载并缓存东财概念板块到本地 CSV。生产机联网执行。"""

from __future__ import annotations

import sys

from aqsp.core.errors import DataError
from aqsp.data.concept_board import ConceptBoardSource


def main() -> int:
    try:
        src = ConceptBoardSource()
        items = src.load(force=True)
        print(f"[concept_board] 已缓存 {len(items)} 条 -> {src._default_cache_path()}")
        return 0
    except DataError as exc:
        print(f"[concept_board] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
