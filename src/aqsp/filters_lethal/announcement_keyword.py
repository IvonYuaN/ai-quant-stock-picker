from __future__ import annotations

from pathlib import Path

import pandas as pd

from aqsp.filters_lethal.base import FilterResult, LethalFilter

DEFAULT_BLACKLIST = [
    "立案调查",
    "违规",
    "退市风险",
    "行政处罚",
    "重大违法",
    "财务造假",
    "业绩暴雷",
]


class AnnouncementKeywordFilter(LethalFilter):
    name = "announcement_keyword"
    hypothesis = "公告中出现'立案调查''违规''退市风险'等关键词的股票，后续大跌概率显著高于市场均值"

    def __init__(
        self,
        data_path: str = "data/announcements.csv",
        keywords: list[str] | None = None,
    ):
        self.data_path = data_path
        self.keywords = keywords or list(DEFAULT_BLACKLIST)

    def _load_announcement_data(self) -> pd.DataFrame | None:
        path = Path(self.data_path)
        if not path.exists():
            return None
        return pd.read_csv(path, dtype={"symbol": str})

    def check(self, symbol: str, df: pd.DataFrame, **kwargs: object) -> FilterResult:
        announcement_text = kwargs.get("announcement_text", "")
        if not announcement_text:
            announcement_data = kwargs.get("announcement_data")
            if announcement_data is None:
                announcement_data = self._load_announcement_data()
            if announcement_data is None or announcement_data.empty:
                return FilterResult(
                    symbol=symbol,
                    passed=True,
                    reason="无公告数据，跳过",
                    filter_name=self.name,
                )
            symbol_rows = announcement_data[announcement_data["symbol"] == symbol]
            if symbol_rows.empty:
                return FilterResult(
                    symbol=symbol,
                    passed=True,
                    reason="无该股公告记录",
                    filter_name=self.name,
                )
            announcement_text = " ".join(symbol_rows["text"].astype(str).tolist())

        for keyword in self.keywords:
            if keyword in announcement_text:
                return FilterResult(
                    symbol=symbol,
                    passed=False,
                    reason=f"公告含负面关键词：{keyword}",
                    filter_name=self.name,
                )

        return FilterResult(
            symbol=symbol,
            passed=True,
            reason="公告无负面关键词",
            filter_name=self.name,
        )
