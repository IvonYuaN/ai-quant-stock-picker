"""Tests for China macroeconomic data source."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from aqsp.data.cn.macro import (
    _save_cache,
    compute_macro_climate_score,
    get_macro_summary,
)


def _make_macro_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "indicator", "value", "prev_value"])


def _expansionary_df() -> pd.DataFrame:
    """PMI above 50, CPI in healthy range, M2 in healthy range."""
    return _make_macro_df(
        [
            {
                "date": "2026-06-30",
                "indicator": "pmi",
                "value": 51.5,
                "prev_value": 50.8,
            },
            {
                "date": "2026-06-30",
                "indicator": "cpi_yoy",
                "value": 1.2,
                "prev_value": 0.8,
            },
            {
                "date": "2026-06-30",
                "indicator": "m2_yoy",
                "value": 10.5,
                "prev_value": 10.2,
            },
            {
                "date": "2026-06-20",
                "indicator": "lpr_1y",
                "value": 3.0,
                "prev_value": 3.1,
            },
        ]
    )


def _contractionary_df() -> pd.DataFrame:
    """PMI below 50, CPI deflation, M2 low."""
    return _make_macro_df(
        [
            {
                "date": "2026-06-30",
                "indicator": "pmi",
                "value": 48.5,
                "prev_value": 49.2,
            },
            {
                "date": "2026-06-30",
                "indicator": "cpi_yoy",
                "value": -0.5,
                "prev_value": 0.1,
            },
            {
                "date": "2026-06-30",
                "indicator": "m2_yoy",
                "value": 6.5,
                "prev_value": 7.0,
            },
        ]
    )


def _neutral_df() -> pd.DataFrame:
    """Mixed signals: PMI expansion but CPI inflation, M2 high (no score change)."""
    return _make_macro_df(
        [
            {
                "date": "2026-06-30",
                "indicator": "pmi",
                "value": 51.0,
                "prev_value": 50.5,
            },
            {
                "date": "2026-06-30",
                "indicator": "cpi_yoy",
                "value": 4.5,
                "prev_value": 4.0,
            },
            {
                "date": "2026-06-30",
                "indicator": "m2_yoy",
                "value": 15.0,
                "prev_value": 14.5,
            },
        ]
    )


def test_get_macro_summary_returns_expansion_when_all_positive():
    df = _expansionary_df()
    summary = get_macro_summary(df)
    assert summary["climate"] == "expansion"
    assert summary["pmi"] == 51.5
    assert summary["cpi_yoy"] == 1.2
    assert summary["m2_yoy"] == 10.5
    assert summary["lpr_1y"] == 3.0
    assert "荣枯线上方" in summary["climate_detail"]
    assert "温和区间" in summary["climate_detail"]


def test_get_macro_summary_returns_contraction_when_all_negative():
    df = _contractionary_df()
    summary = get_macro_summary(df)
    assert summary["climate"] == "contraction"
    assert summary["pmi"] == 48.5
    assert "荣枯线下方" in summary["climate_detail"]
    assert "通缩压力" in summary["climate_detail"]
    assert "增速偏低" in summary["climate_detail"]


def test_get_macro_summary_returns_neutral_when_mixed():
    df = _neutral_df()
    summary = get_macro_summary(df)
    assert summary["climate"] == "neutral"
    assert "荣枯线上方" in summary["climate_detail"]
    assert "通胀压力" in summary["climate_detail"]


def test_get_macro_summary_returns_unknown_on_empty():
    summary = get_macro_summary(pd.DataFrame())
    assert summary["climate"] == "unknown"
    assert summary["pmi"] is None
    assert summary["cpi_yoy"] is None


def test_get_macro_summary_returns_unknown_on_none(tmp_path):
    with patch("aqsp.data.cn.macro.fetch_macro_data", return_value=pd.DataFrame()):
        summary = get_macro_summary(None, cache_path=tmp_path / "nonexistent.csv")
    assert summary["climate"] == "unknown"


def test_compute_macro_climate_score_positive_for_expansion():
    df = _expansionary_df()
    score = compute_macro_climate_score(df)
    assert score > 0.0
    assert score <= 1.0


def test_compute_macro_climate_score_negative_for_contraction():
    df = _contractionary_df()
    score = compute_macro_climate_score(df)
    assert score < 0.0
    assert score >= -1.0


def test_compute_macro_climate_score_zero_for_empty():
    score = compute_macro_climate_score(pd.DataFrame())
    assert score == 0.0


def test_compute_macro_climate_score_zero_for_unknown(tmp_path):
    with patch("aqsp.data.cn.macro.fetch_macro_data", return_value=pd.DataFrame()):
        score = compute_macro_climate_score(None, cache_path=tmp_path / "no.csv")
    assert score == 0.0


def test_compute_macro_climate_score_partial_data_still_works():
    """Only PMI available — score should still compute."""
    df = _make_macro_df(
        [{"date": "2026-06-30", "indicator": "pmi", "value": 52.0, "prev_value": 51.0}]
    )
    score = compute_macro_climate_score(df)
    assert score > 0.0


def test_save_and_load_cache_roundtrip(tmp_path):
    cache_path = tmp_path / "macro.csv"
    df = _make_macro_df(
        [
            {
                "date": "2026-06-30",
                "indicator": "pmi",
                "value": 51.5,
                "prev_value": 50.8,
            },
            {
                "date": "2026-06-30",
                "indicator": "cpi_yoy",
                "value": 1.2,
                "prev_value": 0.8,
            },
        ]
    )
    _save_cache(df, cache_path)

    from aqsp.data.cn.macro import _load_cache

    loaded = _load_cache(cache_path)
    assert len(loaded) == 2
    assert set(loaded["indicator"]) == {"pmi", "cpi_yoy"}


def test_save_cache_deduplicates_by_date_and_indicator(tmp_path):
    cache_path = tmp_path / "macro.csv"
    df1 = _make_macro_df(
        [{"date": "2026-06-30", "indicator": "pmi", "value": 51.0, "prev_value": 50.5}]
    )
    df2 = _make_macro_df(
        [{"date": "2026-06-30", "indicator": "pmi", "value": 51.8, "prev_value": 51.0}]
    )
    _save_cache(df1, cache_path)
    combined = pd.concat([df1, df2], ignore_index=True)
    _save_cache(combined, cache_path)

    from aqsp.data.cn.macro import _load_cache

    loaded = _load_cache(cache_path)
    assert len(loaded) == 1
    assert loaded["value"].iloc[0] == 51.8


def test_fetch_macro_data_uses_cache_when_all_fetches_fail(tmp_path):
    cache_path = tmp_path / "macro.csv"
    cached_df = _make_macro_df(
        [{"date": "2026-06-30", "indicator": "pmi", "value": 51.0, "prev_value": 50.5}]
    )
    _save_cache(cached_df, cache_path)

    with (
        patch("aqsp.data.cn.macro._try_fetch_cpi", return_value=[]),
        patch("aqsp.data.cn.macro._try_fetch_pmi", return_value=[]),
        patch("aqsp.data.cn.macro._try_fetch_m2", return_value=[]),
        patch("aqsp.data.cn.macro._try_fetch_lpr", return_value=[]),
    ):
        from aqsp.data.cn.macro import fetch_macro_data

        result = fetch_macro_data(cache_path=cache_path)

    assert len(result) == 1
    assert result.iloc[0]["indicator"] == "pmi"


def test_fetch_macro_data_merges_new_data_with_cache(tmp_path):
    cache_path = tmp_path / "macro.csv"
    cached_df = _make_macro_df(
        [{"date": "2026-05-31", "indicator": "pmi", "value": 50.5, "prev_value": 50.0}]
    )
    _save_cache(cached_df, cache_path)

    new_rows = [
        {"date": "2026-06-30", "indicator": "pmi", "value": 51.5, "prev_value": 50.5},
    ]
    with (
        patch("aqsp.data.cn.macro._try_fetch_cpi", return_value=[]),
        patch("aqsp.data.cn.macro._try_fetch_pmi", return_value=new_rows),
        patch("aqsp.data.cn.macro._try_fetch_m2", return_value=[]),
        patch("aqsp.data.cn.macro._try_fetch_lpr", return_value=[]),
    ):
        from aqsp.data.cn.macro import fetch_macro_data

        result = fetch_macro_data(cache_path=cache_path)

    assert len(result) == 2
    assert set(result["date"]) == {"2026-05-31", "2026-06-30"}
