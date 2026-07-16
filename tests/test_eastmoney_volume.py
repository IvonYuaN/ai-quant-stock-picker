from __future__ import annotations

import pandas as pd

from aqsp.data.eastmoney_source import _normalize_stock_volume_to_shares


def test_eastmoney_stock_volume_normalizes_lots_to_shares() -> None:
    lots = pd.DataFrame({"close": [10.0], "volume": [1_000.0], "amount": [1_000_000.0]})
    shares = pd.DataFrame(
        {"close": [10.0], "volume": [100_000.0], "amount": [1_000_000.0]}
    )

    assert _normalize_stock_volume_to_shares(lots).iloc[0]["volume"] == 100_000.0
    assert _normalize_stock_volume_to_shares(shares).iloc[0]["volume"] == 100_000.0


def test_eastmoney_stock_volume_normalizes_mixed_cache_rows_independently() -> None:
    mixed = pd.DataFrame(
        {
            "close": [10.0] * 6,
            "volume": [100_000.0] * 5 + [1_000.0],
            "amount": [1_000_000.0] * 6,
        }
    )

    normalized = _normalize_stock_volume_to_shares(mixed)

    assert normalized["volume"].tolist() == [100_000.0] * 6
