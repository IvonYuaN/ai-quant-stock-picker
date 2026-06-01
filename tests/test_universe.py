from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aqsp.universe.pool import UniversePool, StockUniverse
from aqsp.universe.filters import (
    STFilter,
    SuspendedFilter,
    NewStockFilter,
    DelistedFilter,
    LiquidityFilter,
    PriceFilter,
    FilterPipeline,
)


def test_universe_pool_from_default():
    pool = UniversePool.from_default("sh300")
    assert pool.name == "sh300"
    assert pool.description == "沪深300"
    assert pool.index_codes == ["000300"]


def test_universe_pool_list_default():
    pools = UniversePool.list_default_pools()
    assert len(pools) > 0
    pool_names = [p[0] for p in pools]
    assert "sh300" in pool_names
    assert "zz500" in pool_names


def test_universe_pool_get_symbols_uses_optional_index_constituents(monkeypatch):
    pool = UniversePool.from_default("zz500")
    monkeypatch.setattr(
        "aqsp.universe.pool.load_optional_index_constituents",
        lambda index_code, as_of: ["000001", "600519"]
        if index_code == "000905.SH"
        else [],
    )

    symbols = pool.get_symbols(as_of=date(2026, 6, 1))
    assert symbols == ["000001", "600519"]


def test_universe_pool_get_symbols_raises_when_constituents_unavailable(monkeypatch):
    pool = UniversePool.from_default("cyb")
    monkeypatch.setattr(
        "aqsp.universe.pool.load_optional_index_constituents",
        lambda index_code, as_of: [],
    )

    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        pool.get_symbols(as_of=date(2026, 6, 1))


def test_stock_universe_basic():
    universe = StockUniverse(["600000", "600001"], ["浦发银行", "邯郸钢铁"])
    assert len(universe) == 2
    assert "600000" in universe
    assert "600002" not in universe
    assert universe.get_name("600000") == "浦发银行"


def test_stock_universe_filter():
    universe = StockUniverse(
        ["600000", "600001", "600002"], ["浦发银行", "邯郸钢铁", "齐鲁石化"]
    )
    filtered = universe.filter(["600000", "600002"])
    assert len(filtered) == 2
    assert "600000" in filtered
    assert "600002" in filtered


def test_stock_universe_union():
    u1 = StockUniverse(["600000", "600001"], ["浦发银行", "邯郸钢铁"])
    u2 = StockUniverse(["600001", "600002"], ["邯郸钢铁", "齐鲁石化"])
    union = u1.union(u2)
    assert len(union) == 3


def test_st_filter():
    st_filter = STFilter()
    names = {
        "600000": "浦发银行",
        "600001": "*ST钢构",
        "600002": "ST齐鲁",
        "600003": "退市海润",
    }
    universe = ["600000", "600001", "600002", "600003"]
    result = st_filter.filter(universe, names)
    assert len(result) == 1
    assert "600000" in result


def test_suspended_filter():
    filter = SuspendedFilter()
    data = {
        "600000": pd.DataFrame({"volume": [1000, 2000]}),
        "600001": pd.DataFrame({"volume": [0, 0]}),
        "600002": pd.DataFrame({"volume": [50, 60]}),
    }
    universe = ["600000", "600001", "600002"]
    result = filter.filter(universe, data)
    assert len(result) == 1
    assert "600000" in result


def test_new_stock_filter():
    filter = NewStockFilter(min_days_listed=90)
    listing_dates = {
        "600000": "2000-01-01",
        "600001": "2026-05-01",
        "600002": None,
    }
    universe = ["600000", "600001", "600002"]
    result = filter.filter(universe, listing_dates)
    assert len(result) == 1
    assert "600000" in result


def test_delisted_filter():
    filter = DelistedFilter()
    names = {
        "600000": "浦发银行",
        "600001": "退市钢铁",
        "600002": "退A公司",
    }
    universe = ["600000", "600001", "600002"]
    result = filter.filter(universe, names)
    assert len(result) == 1
    assert "600000" in result


def test_liquidity_filter():
    filter = LiquidityFilter(min_avg_volume=1000)
    data = {
        "600000": pd.DataFrame({"volume": [2000, 3000, 4000]}),
        "600001": pd.DataFrame({"volume": [100, 200, 300]}),
    }
    universe = ["600000", "600001"]
    result = filter.filter(universe, data)
    assert len(result) == 1
    assert "600000" in result


def test_price_filter():
    filter = PriceFilter(min_price=5.0, max_price=100.0)
    data = {
        "600000": pd.DataFrame({"close": [10.0]}),
        "600001": pd.DataFrame({"close": [3.0]}),
        "600002": pd.DataFrame({"close": [200.0]}),
    }
    universe = ["600000", "600001", "600002"]
    result = filter.filter(universe, data)
    assert len(result) == 1
    assert "600000" in result


def test_filter_pipeline():
    pipeline = FilterPipeline()
    pipeline.add_filter("st", STFilter())
    pipeline.add_filter("price", PriceFilter(min_price=5.0))

    names = {"600000": "浦发银行", "600001": "ST钢铁", "600002": "招商银行"}
    data = {
        "600000": pd.DataFrame({"close": [10.0]}),
        "600001": pd.DataFrame({"close": [6.0]}),
        "600002": pd.DataFrame({"close": [3.0]}),
    }

    universe = ["600000", "600001", "600002"]
    result = pipeline.apply(universe, names=names, data=data)
    assert len(result) == 1
    assert "600000" in result


def test_filter_pipeline_with_stats():
    pipeline = FilterPipeline()
    pipeline.add_filter("st", STFilter())

    names = {"600000": "浦发银行", "600001": "ST钢铁"}
    universe = ["600000", "600001"]

    stats = pipeline.apply_with_stats(universe, names=names)
    assert stats["initial_count"] == 2
    assert stats["final_count"] == 1
    assert stats["filter_stats"]["st"]["removed"] == 1
