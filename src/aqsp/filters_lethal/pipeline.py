from __future__ import annotations

import pandas as pd

from aqsp.filters_lethal.announcement_keyword import AnnouncementKeywordFilter
from aqsp.filters_lethal.base import LethalFilter
from aqsp.filters_lethal.holder_count import HolderCountFilter
from aqsp.filters_lethal.lockup_release import LockupReleaseFilter


class LethalFilterPipeline:
    def __init__(self, filters: list[LethalFilter] | None = None):
        self.filters = filters or [
            LockupReleaseFilter(),
            HolderCountFilter(),
            AnnouncementKeywordFilter(),
        ]

    def run(
        self, symbol: str, df: pd.DataFrame, **kwargs: object
    ) -> tuple[bool, list[str]]:
        rejected_by: list[str] = []
        for flt in self.filters:
            try:
                result = flt.check(symbol, df, **kwargs)
            except Exception as exc:  # noqa: BLE001
                # 单个排雷器异常不应让整个排雷链崩溃（崩溃会中断选股全流程）。
                # 记录告警并跳过该过滤器；不阻断（避免一个 bug 错杀全市场），
                # 但通过日志暴露问题供排查。
                import logging

                logging.getLogger("aqsp.filters_lethal").warning(
                    "排雷器 %s 检查 %s 时异常，已跳过该过滤器: %s",
                    getattr(flt, "name", flt.__class__.__name__),
                    symbol,
                    exc,
                )
                continue
            if not result.passed:
                rejected_by.append(result.filter_name)
        return len(rejected_by) == 0, rejected_by
