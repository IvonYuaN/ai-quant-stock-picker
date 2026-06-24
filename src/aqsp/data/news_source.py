from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import pandas as pd

from aqsp.core.errors import DataError


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
        frames, errors = self._collect_frames(
            (
                ("stock_news_em", lambda: self._ak.stock_news_em(symbol=symbol)),
                (
                    "stock_individual_notice_report",
                    lambda: self._ak.stock_individual_notice_report(symbol=symbol),
                ),
                (
                    "stock_research_report_em",
                    lambda: self._ak.stock_research_report_em(symbol=symbol),
                ),
            )
        )
        if not frames:
            raise DataError(f"akshare 个股新闻获取失败: {symbol}; {'; '.join(errors)}")
        return frames

    def fetch_global_news(self) -> list[pd.DataFrame]:
        frames, errors = self._collect_frames(
            (
                ("stock_info_global_cls", self._ak.stock_info_global_cls),
                ("stock_info_global_em", self._ak.stock_info_global_em),
                ("stock_info_global_ths", self._ak.stock_info_global_ths),
                ("stock_info_global_futu", self._ak.stock_info_global_futu),
                ("stock_info_global_sina", self._ak.stock_info_global_sina),
                ("news_cctv", self._ak.news_cctv),
                ("news_economic_baidu", self._ak.news_economic_baidu),
                ("stock_notice_report", self._ak.stock_notice_report),
            )
        )
        if not frames:
            raise DataError(f"akshare 全市场新闻获取失败: {'; '.join(errors)}")
        return frames

    def _collect_frames(
        self,
        fetchers: tuple[tuple[str, Callable[[], object]], ...],
    ) -> tuple[list[pd.DataFrame], list[str]]:
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        for name, fetch in fetchers:
            try:
                frame = fetch()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                frames.append(frame)
            else:
                errors.append(f"{name}: empty")
        if frames and errors:
            warnings = tuple(errors[:5])
            for frame in frames:
                frame.attrs["aqsp_warnings"] = warnings
        return frames, errors


def build_default_news_source() -> NewsSource:
    return AkshareNewsSource()
