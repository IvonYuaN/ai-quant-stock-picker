from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.efinance_source import EfinanceSource
from aqsp.data.cache import DataCache


class _FakeStockApi:
    @staticmethod
    def get_quote_history(*_args, **_kwargs):
        return pd.DataFrame()


def test_efinance_daily_empty_result_raises_data_error(tmp_path) -> None:
    source = object.__new__(EfinanceSource)
    source._ef = SimpleNamespace(stock=_FakeStockApi())
    source.cache = DataCache(tmp_path / "cache.db")

    with pytest.raises(DataError, match="efinance 日线获取失败"):
        source.fetch_daily(["600519"], date(2026, 6, 1), date(2026, 6, 2))
