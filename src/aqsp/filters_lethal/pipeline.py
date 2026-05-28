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
            result = flt.check(symbol, df, **kwargs)
            if not result.passed:
                rejected_by.append(result.filter_name)
        return len(rejected_by) == 0, rejected_by
