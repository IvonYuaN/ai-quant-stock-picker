from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import pandas as pd


class NewsSource(Protocol):
    name: str

    def fetch_symbol_news(self, symbol: str) -> list[pd.DataFrame]: ...

    def fetch_global_news(self) -> list[pd.DataFrame]: ...


class AkshareNewsSource:
    name = "akshare_news"

    def __init__(self) -> None:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("akshare not installed") from exc
        self._ak = ak

    def fetch_symbol_news(self, symbol: str) -> list[pd.DataFrame]:
        return [
            frame
            for frame in (
                self._try_fetch(lambda: self._ak.stock_news_em(symbol=symbol)),
                self._try_fetch(
                    lambda: self._ak.stock_individual_notice_report(symbol=symbol)
                ),
                self._try_fetch(lambda: self._ak.stock_research_report_em(symbol=symbol)),
            )
            if frame is not None
        ]

    def fetch_global_news(self) -> list[pd.DataFrame]:
        return [
            frame
            for frame in (
                self._try_fetch(self._ak.stock_info_global_cls),
                self._try_fetch(self._ak.stock_info_global_em),
                self._try_fetch(self._ak.stock_info_global_ths),
                self._try_fetch(self._ak.stock_info_global_futu),
                self._try_fetch(self._ak.stock_info_global_sina),
                self._try_fetch(self._ak.news_cctv),
                self._try_fetch(self._ak.news_economic_baidu),
                self._try_fetch(self._ak.stock_notice_report),
            )
            if frame is not None
        ]

    def _try_fetch(self, fetch: Callable[[], object]) -> pd.DataFrame | None:
        try:
            frame = fetch()
        except Exception:
            return None
        return frame if isinstance(frame, pd.DataFrame) else None
