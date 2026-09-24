from __future__ import annotations

from aqsp.data.eastmoney_source import _eastmoney_market_prefix
from aqsp.data.sina_source import _sina_market_prefix


def test_sina_index_market_prefix_routes_shenzhen_indices_to_sz() -> None:
    assert _sina_market_prefix("000300", is_index=True) == "sh"
    assert _sina_market_prefix("399001", is_index=True) == "sz"
    assert _sina_market_prefix("600000", is_index=False) == "sh"
    assert _sina_market_prefix("000001", is_index=False) == "sz"


def test_eastmoney_index_market_prefix_routes_shenzhen_indices_to_zero() -> None:
    assert _eastmoney_market_prefix("000300", is_index=True) == "1"
    assert _eastmoney_market_prefix("399001", is_index=True) == "0"
    assert _eastmoney_market_prefix("600000", is_index=False) == "1"
    assert _eastmoney_market_prefix("000001", is_index=False) == "0"
