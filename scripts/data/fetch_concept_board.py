#!/usr/bin/env python3
"""预加载东财概念板块：当日截面 CSV + 追加题材历史库。生产机联网执行。

两个产物
--------
1. ``pit_cache/concept_board.csv`` —— **当日截面**，覆盖写。消费者：
   ``briefing/closing_review.py`` 的板块资金面段、``backend/info_stream.py``。
2. ``pit_cache/theme_history.csv`` —— **逐日累积的历史表**，同交易日幂等替换。
   消费者：``features/theme_index.py`` 聚合成日 / 周 / 月 K。

历史库是**增强项**：追加失败只告警（stderr），**不影响** 截面 CSV 产物，也不改变
本脚本退出码——否则一个历史库写失败会让 ``preload_event_data.sh`` 把整轮
concept_board 预载判为失败（历史不可回补，但截面断了会直接影响当日复盘）。

交易日口径
----------
历史表的 ``trade_date`` 用 :func:`aqsp.core.time.latest_completed_trading_day`，
不是 ``today()``：本脚本在 runner 上以 **UTC** 运行（中继路径），且周末 / 节假日
也可能被调度；直接用「今天」会把周六记成一个交易日，凭空在指数序列里插一根假 K。
"""

from __future__ import annotations

import sys

from aqsp.core.errors import DataError
from aqsp.data.concept_board import ConceptBoardSource


def _append_theme_history(items: list[object]) -> None:
    """把当日快照追加进题材历史库（best-effort：失败只告警）。"""
    try:
        from aqsp.core.time import latest_completed_trading_day
        from aqsp.data.theme_history import append_board_snapshot

        trade_date = latest_completed_trading_day()
        written = append_board_snapshot(items, trade_date=trade_date)  # type: ignore[arg-type]
        print(f"[concept_board] 题材历史 +{written} 行 -> {trade_date}")
    except Exception as exc:  # noqa: BLE001 - 历史库是增强项，绝不阻断截面产物
        print(f"[concept_board] 题材历史追加失败（不影响截面缓存）: {exc}", file=sys.stderr)


def main() -> int:
    try:
        src = ConceptBoardSource()
        items = src.load(force=True)
    except DataError as exc:
        print(f"[concept_board] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return 1
    print(f"[concept_board] 已缓存 {len(items)} 条 -> {src._default_cache_path()}")
    _append_theme_history(list(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
