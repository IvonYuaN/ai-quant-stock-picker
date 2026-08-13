"""Tests for market sentiment data source."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from aqsp.data.cn.sentiment import (
    _save_cache,
    compute_sentiment_factor,
    fetch_sentiment_data,
    get_sentiment_summary,
)


def _make_sentiment_df(
    dates: list[str],
    limit_ups: list[int],
    broken: list[int] | None = None,
    consecutive: list[int] | None = None,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "limit_up_count": limit_ups,
            "broken_count": broken or [0] * len(dates),
            "max_consecutive": consecutive or [1] * len(dates),
            "total_amount": [1e9] * len(dates),
        }
    )


def test_compute_sentiment_factor_returns_positive_z_when_recent_high(tmp_path):
    df = _make_sentiment_df(
        [f"2026-01-{d:02d}" for d in range(1, 11)],
        [30, 28, 32, 25, 27, 30, 29, 31, 28, 80],
    )
    z = compute_sentiment_factor(df)
    assert z > 1.0


def test_compute_sentiment_factor_returns_negative_z_when_recent_low(tmp_path):
    df = _make_sentiment_df(
        [f"2026-01-{d:02d}" for d in range(1, 11)],
        [80, 78, 82, 75, 77, 80, 79, 81, 78, 10],
    )
    z = compute_sentiment_factor(df)
    assert z < -1.0


def test_compute_sentiment_factor_returns_zero_when_insufficient_data(tmp_path):
    df = _make_sentiment_df(["2026-01-01"], [30])
    z = compute_sentiment_factor(df)
    assert z == 0.0


def test_compute_sentiment_factor_returns_zero_when_std_is_zero(tmp_path):
    df = _make_sentiment_df(
        [f"2026-01-{d:02d}" for d in range(1, 11)],
        [30] * 10,
    )
    z = compute_sentiment_factor(df)
    assert z == 0.0


def test_compute_sentiment_factor_returns_zero_on_empty_df(tmp_path):
    z = compute_sentiment_factor(pd.DataFrame())
    assert z == 0.0


def test_compute_sentiment_factor_returns_zero_on_none(tmp_path):
    cache_path = tmp_path / "nonexistent.csv"
    with patch("aqsp.data.cn.sentiment._try_fetch_zt_pool", return_value=None):
        z = compute_sentiment_factor(None, cache_path=cache_path)
    assert z == 0.0


def test_get_sentiment_summary_returns_hot_when_z_above_threshold(tmp_path):
    df = _make_sentiment_df(
        [f"2026-01-{d:02d}" for d in range(1, 11)],
        [30, 28, 32, 25, 27, 30, 29, 31, 28, 100],
    )
    summary = get_sentiment_summary(df)
    assert summary["temperature"] == "hot"
    assert summary["sentiment_z"] > 1.0
    assert summary["limit_up_count"] == 100
    assert summary["max_consecutive"] == 1


def test_get_sentiment_summary_returns_cold_when_z_below_threshold(tmp_path):
    df = _make_sentiment_df(
        [f"2026-01-{d:02d}" for d in range(1, 11)],
        [100, 98, 102, 95, 97, 100, 99, 101, 98, 5],
    )
    summary = get_sentiment_summary(df)
    assert summary["temperature"] == "cold"
    assert summary["sentiment_z"] < -1.0


def test_get_sentiment_summary_returns_neutral_when_z_in_middle(tmp_path):
    df = _make_sentiment_df(
        [f"2026-01-{d:02d}" for d in range(1, 11)],
        [30, 28, 32, 25, 27, 30, 29, 31, 28, 30],
    )
    summary = get_sentiment_summary(df)
    assert summary["temperature"] == "neutral"


def test_get_sentiment_summary_returns_unknown_on_empty():
    summary = get_sentiment_summary(pd.DataFrame())
    assert summary["temperature"] == "unknown"
    assert summary["sentiment_z"] == 0.0
    assert summary["limit_up_count"] == 0


def test_fetch_sentiment_data_uses_cache_when_fetch_fails(tmp_path):
    cache_path = tmp_path / "sentiment.csv"
    cached_df = _make_sentiment_df(
        ["2026-01-01", "2026-01-02"],
        [30, 40],
    )
    _save_cache(cached_df, cache_path)

    with patch("aqsp.data.cn.sentiment._try_fetch_zt_pool", return_value=None):
        result = fetch_sentiment_data(cache_path=cache_path)

    assert len(result) == 2
    assert set(result["date"]) == {"2026-01-01", "2026-01-02"}


def test_fetch_sentiment_data_merges_new_data_with_cache(tmp_path):
    cache_path = tmp_path / "sentiment.csv"
    cached_df = _make_sentiment_df(
        ["2026-01-01", "2026-01-02"],
        [30, 40],
    )
    _save_cache(cached_df, cache_path)

    mock_stats = {
        "limit_up_count": 50,
        "max_consecutive": 3,
        "total_amount": 2e9,
        "broken_count": 5,
    }

    with patch("aqsp.data.cn.sentiment._try_fetch_zt_pool", return_value=mock_stats):
        result = fetch_sentiment_data(cache_path=cache_path)

    assert len(result) >= 2
    assert "limit_up_count" in result.columns
    assert result["limit_up_count"].iloc[-1] == 50


def test_fetch_sentiment_data_returns_empty_when_no_cache_and_fetch_fails(tmp_path):
    cache_path = tmp_path / "nonexistent.csv"
    with patch("aqsp.data.cn.sentiment._try_fetch_zt_pool", return_value=None):
        result = fetch_sentiment_data(cache_path=cache_path)
    assert result.empty


def test_save_and_load_cache_roundtrip(tmp_path):
    cache_path = tmp_path / "sentiment.csv"
    df = _make_sentiment_df(
        ["2026-01-01", "2026-01-02", "2026-01-03"],
        [30, 40, 50],
        broken=[2, 3, 1],
        consecutive=[1, 2, 3],
    )
    _save_cache(df, cache_path)

    from aqsp.data.cn.sentiment import _load_cache

    loaded = _load_cache(cache_path)
    assert len(loaded) == 3
    assert loaded["limit_up_count"].tolist() == [30, 40, 50]
    assert loaded["max_consecutive"].tolist() == [1, 2, 3]


def test_save_cache_deduplicates_by_date(tmp_path):
    cache_path = tmp_path / "sentiment.csv"
    df1 = _make_sentiment_df(["2026-01-01"], [30])
    df2 = _make_sentiment_df(["2026-01-01"], [50])
    _save_cache(df1, cache_path)
    combined = pd.concat([df1, df2], ignore_index=True)
    _save_cache(combined, cache_path)

    from aqsp.data.cn.sentiment import _load_cache

    loaded = _load_cache(cache_path)
    assert len(loaded) == 1
    assert loaded["limit_up_count"].iloc[0] == 50
