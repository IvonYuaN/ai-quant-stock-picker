from __future__ import annotations

import pandas as pd

from aqsp.data.cn.margin_trading import (
    compute_margin_factor,
    fetch_margin_data,
    _load_cache,
    _save_cache,
)


def test_compute_margin_factor_returns_change_ratio(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)],
            "symbol": ["600000"] * 10,
            "margin_balance": [1_000_000.0 + i * 10_000 for i in range(10)],
            "margin_buy": [50_000.0] * 10,
            "short_sell": [1_000.0] * 10,
        }
    )
    _save_cache(df, cache_file)
    factor = compute_margin_factor("600000", window=5, cache_path=cache_file)
    assert isinstance(factor, float)
    assert factor > 0.0


def test_compute_margin_factor_returns_zero_when_empty(tmp_path) -> None:
    cache_file = tmp_path / "empty.csv"
    factor = compute_margin_factor("600000", window=5, cache_path=cache_file)
    assert factor == 0.0


def test_compute_margin_factor_returns_zero_when_insufficient_data(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "symbol": ["600000", "600000"],
            "margin_balance": [1_000_000.0, 1_010_000.0],
            "margin_buy": [50_000.0, 50_000.0],
            "short_sell": [1_000.0, 1_000.0],
        }
    )
    _save_cache(df, cache_file)
    factor = compute_margin_factor("600000", window=5, cache_path=cache_file)
    assert factor == 0.0


def test_compute_margin_factor_returns_zero_when_no_change(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)],
            "symbol": ["600000"] * 10,
            "margin_balance": [1_000_000.0] * 10,
            "margin_buy": [50_000.0] * 10,
            "short_sell": [1_000.0] * 10,
        }
    )
    _save_cache(df, cache_file)
    factor = compute_margin_factor("600000", window=5, cache_path=cache_file)
    assert factor == 0.0


def test_compute_margin_factor_returns_zero_when_initial_zero(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)],
            "symbol": ["600000"] * 10,
            "margin_balance": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "margin_buy": [50_000.0] * 10,
            "short_sell": [1_000.0] * 10,
        }
    )
    _save_cache(df, cache_file)
    factor = compute_margin_factor("600000", window=5, cache_path=cache_file)
    assert factor == 0.0


def test_csv_cache_roundtrip_margin(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "symbol": ["600000", "600000"],
            "margin_balance": [1_000_000.0, 1_010_000.0],
            "margin_buy": [50_000.0, 55_000.0],
            "short_sell": [1_000.0, 1_100.0],
        }
    )
    _save_cache(df, cache_file)
    assert cache_file.exists()
    loaded = _load_cache(cache_file)
    assert len(loaded) == 2
    assert loaded.iloc[0]["symbol"] == "600000"


def test_csv_cache_handles_missing_file_margin(tmp_path) -> None:
    cache_file = tmp_path / "nonexistent.csv"
    loaded = _load_cache(cache_file)
    assert loaded.empty


def test_csv_cache_deduplicates_by_date_and_symbol(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-01", "2026-01-02"],
            "symbol": ["600000", "600000", "600000"],
            "margin_balance": [1_000_000.0, 999_999.0, 1_010_000.0],
            "margin_buy": [50_000.0, 50_000.0, 55_000.0],
            "short_sell": [1_000.0, 1_000.0, 1_100.0],
        }
    )
    _save_cache(df, cache_file)
    loaded = _load_cache(cache_file)
    assert len(loaded) == 2
    jan1 = loaded[loaded["date"] == "2026-01-01"]
    assert float(jan1.iloc[0]["margin_balance"]) == 999_999.0


def test_fetch_margin_data_returns_cached_on_api_failure(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    result = fetch_margin_data("600000", days=60, cache_path=cache_file)
    assert isinstance(result, pd.DataFrame)


def test_fetch_margin_data_uses_cache(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    cached = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)],
            "symbol": ["600000"] * 10,
            "margin_balance": [1_000_000.0 + i * 10_000 for i in range(10)],
            "margin_buy": [50_000.0] * 10,
            "short_sell": [1_000.0] * 10,
        }
    )
    _save_cache(cached, cache_file)
    result = fetch_margin_data("600000", days=5, cache_path=cache_file)
    assert len(result) <= 5
    assert "margin_balance" in result.columns


def test_compute_margin_factor_handles_multiple_symbols(tmp_path) -> None:
    cache_file = tmp_path / "margin_history.csv"
    df = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)] * 2,
            "symbol": ["600000"] * 10 + ["000001"] * 10,
            "margin_balance": [1_000_000.0 + i * 10_000 for i in range(10)]
            + [2_000_000.0 + i * 5_000 for i in range(10)],
            "margin_buy": [50_000.0] * 20,
            "short_sell": [1_000.0] * 20,
        }
    )
    _save_cache(df, cache_file)
    factor_600000 = compute_margin_factor("600000", window=5, cache_path=cache_file)
    factor_000001 = compute_margin_factor("000001", window=5, cache_path=cache_file)
    assert isinstance(factor_600000, float)
    assert isinstance(factor_000001, float)
    assert factor_600000 != factor_000001
