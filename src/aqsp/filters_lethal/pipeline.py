from __future__ import annotations

import logging

import pandas as pd

from aqsp.filters_lethal.announcement_keyword import AnnouncementKeywordFilter
from aqsp.filters_lethal.base import LethalFilter
from aqsp.filters_lethal.holder_count import HolderCountFilter
from aqsp.filters_lethal.lockup_release import LockupReleaseFilter

logger = logging.getLogger("aqsp.filters_lethal")


class LethalFilterPipeline:
    def __init__(self, filters: list[LethalFilter] | None = None):
        self.filters = filters or [
            LockupReleaseFilter(),
            HolderCountFilter(),
            AnnouncementKeywordFilter(),
        ]

    def run(
        self,
        symbol: str,
        df: pd.DataFrame,
        missing_filters: list[str] | None = None,
        **kwargs: object,
    ) -> tuple[bool, list[str]]:
        """执行全链路排雷；可观测性见 ``run_with_observability``。

        ``missing_filters``（可选）：若传入列表，单遍收集「数据缺失静默放行」的
        排雷器名单到其中，避免双跑 check（CSV 读取/日期计算只做一次）。
        """
        rejected_by: list[str] = []
        for flt in self.filters:
            try:
                result = flt.check(symbol, df, **kwargs)
            except Exception as exc:  # noqa: BLE001
                # 单个排雷器异常不应让整个排雷链崩溃（崩溃会中断选股全流程）。
                # 记录告警并跳过该过滤器；不阻断（避免一个 bug 错杀全市场），
                # 但通过日志暴露问题供排查。
                logger.warning(
                    "排雷器 %s 检查 %s 时异常，已跳过该过滤器: %s",
                    getattr(flt, "name", flt.__class__.__name__),
                    symbol,
                    exc,
                )
                continue
            if not result.passed:
                rejected_by.append(result.filter_name)
            elif (
                missing_filters is not None
                and getattr(result, "data_missing", False)
            ):
                missing_filters.append(result.filter_name)
        return len(rejected_by) == 0, rejected_by

    def run_with_observability(
        self, symbol: str, df: pd.DataFrame, **kwargs: object
    ) -> tuple[bool, list[str], list[str]]:
        """带「数据缺失」观测的排雷入口（调用方用于聚合告警）。

        返回 (passed, rejected_by, missing_filters)：
        - missing_filters：该票实际处于「数据缺失静默放行」的排雷器名单。
          调用方（CLI）应跨票去重汇总后一次性告警，避免 N 只候选 × M 个排雷器
          的告警风暴。
        """
        missing_filters: list[str] = []
        passed, rejected_by = self.run(
            symbol,
            df,
            missing_filters=missing_filters,
            **kwargs,
        )
        return passed, rejected_by, missing_filters
