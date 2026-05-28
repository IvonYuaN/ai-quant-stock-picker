from __future__ import annotations

import pandas as pd

from aqsp.data.cn.northbound import (
    compute_northbound_factor,
    fetch_northbound_flow,
    _load_cache,
    _save_cache,
)


def test_compute_northbound_factor_returns_z_score() -> None:
    df = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 21)],
            "net_flow": [100.0 + i * 10 for i in range(20)],
            "buy_amount": [1000.0] * 20,
            "sell_amount": [900.0] * 20,
        }
    )
    z = compute_northbound_factor(df, window=5)
    assert isinstance(z, float)
    assert z != 0.0


def test_compute_northbound_factor_returns_zero_when_empty() -> None:
    df = pd.DataFrame(columns=["date", "net_flow", "buy_amount", "sell_amount"])
    assert compute_northbound_factor(df) == 0.0


def test_compute_northbound_factor_returns_zero_when_none() -> None:
    assert compute_northbound_factor(None) == 0.0


def test_compute_northbound_factor_returns_zero_when_insufficient_data() -> None:
    df = pd.DataFrame({"net_flow": [1.0, 2.0]})
    assert compute_northbound_factor(df, window=5) == 0.0


def test_compute_northbound_factor_returns_zero_when_constant() -> None:
    df = pd.DataFrame({"net_flow": [100.0] * 20})
    assert compute_northbound_factor(df, window=5) == 0.0


def test_compute_northbound_factor_uses_custom_window() -> None:
    df = pd.DataFrame({"net_flow": [float(i) for i in range(30)]})
    z5 = compute_northbound_factor(df, window=5)
    z10 = compute_northbound_factor(df, window=10)
    assert isinstance(z5, float)
    assert isinstance(z10, float)


def test_csv_cache_roundtrip(tmp_path) -> None:
    cache_file = tmp_path / "northbound_history.csv"
    df = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "net_flow": [100.0, 200.0],
            "buy_amount": [500.0, 600.0],
            "sell_amount": [400.0, 400.0],
        }
    )
    _save_cache(df, cache_file)
    assert cache_file.exists()
    loaded = _load_cache(cache_file)
    assert len(loaded) == 2
    assert loaded.iloc[0]["net_flow"] == 100.0


def test_csv_cache_handles_missing_file(tmp_path) -> None:
    cache_file = tmp_path / "nonexistent.csv"
    loaded = _load_cache(cache_file)
    assert loaded.empty


def test_csv_cache_deduplicates_by_date(tmp_path) -> None:
    cache_file = tmp_path / "northbound_history.csv"
    df = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-01", "2026-01-02"],
            "net_flow": [100.0, 999.0, 200.0],
            "buy_amount": [500.0, 500.0, 600.0],
            "sell_amount": [400.0, 400.0, 400.0],
        }
    )
    _save_cache(df, cache_file)
    loaded = _load_cache(cache_file)
    assert len(loaded) == 2
    jan1 = loaded[loaded["date"] == "2026-01-01"]
    assert float(jan1.iloc[0]["net_flow"]) == 999.0


def test_fetch_northbound_flow_returns_empty_on_api_failure(tmp_path) -> None:
    cache_file = tmp_path / "empty.csv"
    result = fetch_northbound_flow(days=60, cache_path=cache_file)
    assert isinstance(result, pd.DataFrame)


def test_fetch_northbound_flow_uses_cache(tmp_path) -> None:
    cache_file = tmp_path / "northbound_history.csv"
    cached = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)],
            "net_flow": [float(i * 100) for i in range(1, 11)],
            "buy_amount": [1000.0] * 10,
            "sell_amount": [900.0] * 10,
        }
    )
    _save_cache(cached, cache_file)
    result = fetch_northbound_flow(days=5, cache_path=cache_file)
    assert len(result) <= 5
    assert "net_flow" in result.columns


def test_compute_northbound_factor_handles_nan_values() -> None:
    df = pd.DataFrame(
        {"net_flow": [1.0, float("nan"), 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]}
    )
    z = compute_northbound_factor(df, window=5)
    assert isinstance(z, float)
