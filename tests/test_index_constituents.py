from __future__ import annotations

from datetime import date

import pandas as pd

from aqsp.data.index_constituents import load_optional_index_constituents
from aqsp.data.tushare_pit import TusharePitClient


def test_load_optional_index_constituents_when_client_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    assert load_optional_index_constituents("000300.SH", date(2026, 6, 1)) == []


def test_load_optional_index_constituents_uses_latest_trade_date() -> None:
    class DummyClient(TusharePitClient):
        def __init__(self) -> None:
            pass

        def fetch_index_weights(self, index_code, start, end):
            return pd.DataFrame(
                [
                    {
                        "index_code": index_code,
                        "trade_date": "2026-05-30",
                        "symbol": "000001",
                        "weight": 2.0,
                    },
                    {
                        "index_code": index_code,
                        "trade_date": "2026-06-01",
                        "symbol": "300750",
                        "weight": 3.18,
                    },
                    {
                        "index_code": index_code,
                        "trade_date": "2026-06-01",
                        "symbol": "600519",
                        "weight": 4.52,
                    },
                ]
            )

    assert load_optional_index_constituents(
        "000300.SH",
        date(2026, 6, 1),
        client=DummyClient(),
    ) == ["600519", "300750"]
