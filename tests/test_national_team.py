"""Tests for national team holdings tracking."""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.national_team import (
    NATIONAL_TEAM_KEYWORDS,
    NationalTeamTracker,
    symbol_to_ts_code,
    ts_code_to_symbol,
)


class TestSymbolConversion:
    """Test symbol/ts_code conversion functions."""

    def test_symbol_to_ts_code_shanghai(self) -> None:
        assert symbol_to_ts_code("600000") == "600000.SH"
        assert symbol_to_ts_code("601398") == "601398.SH"

    def test_symbol_to_ts_code_beijing(self) -> None:
        assert symbol_to_ts_code("830899") == "830899.BJ"
        assert symbol_to_ts_code("431039") == "431039.BJ"

    def test_symbol_to_ts_code_shenzhen(self) -> None:
        assert symbol_to_ts_code("000001") == "000001.SZ"
        assert symbol_to_ts_code("300123") == "300123.SZ"

    def test_ts_code_to_symbol(self) -> None:
        assert ts_code_to_symbol("600000.SH") == "600000"
        assert ts_code_to_symbol("000001.SZ") == "000001"
        assert ts_code_to_symbol("600000") == "600000"


class TestNationalTeamTrackerNormalization:
    """Test data normalization in NationalTeamTracker."""

    def test_normalize_top_holders_basic(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        df = pd.DataFrame(
            [
                {
                    "holder_name": "中央汇金投资有限责任公司",
                    "hold_ratio": 5.2,
                    "hold_amount": 100000000,
                    "ann_date": "20240101",
                    "rank": 1,
                }
            ]
        )

        result = tracker._normalize_top_holders(df, "600000")

        assert result.shape[0] == 1
        assert result.loc[0, "symbol"] == "600000"
        assert result.loc[0, "holder_name"] == "中央汇金投资有限责任公司"
        assert result.loc[0, "holding_ratio"] == 5.2
        assert result.loc[0, "holding_shares"] == 100000000
        assert result.loc[0, "announcement_date"] == "2024-01-01"
        assert result.loc[0, "rank"] == 1

    def test_normalize_top_holders_missing_columns(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        # Minimal dataframe
        df = pd.DataFrame(
            [
                {
                    "holder_name": "Some Holder",
                }
            ]
        )

        result = tracker._normalize_top_holders(df, "600000")

        assert result.shape[0] == 1
        assert result.loc[0, "symbol"] == "600000"
        assert result.loc[0, "holding_ratio"] == 0.0
        assert result.loc[0, "holding_shares"] == 0.0
        assert "rank" in result.columns

    def test_filter_national_team_single_keyword(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        df = pd.DataFrame(
            [
                {"holder_name": "中央汇金投资有限责任公司", "symbol": "600000"},
                {"holder_name": "普通私募基金", "symbol": "600000"},
                {"holder_name": "某某公司", "symbol": "600000"},
            ]
        )

        result = tracker._filter_national_team(df)

        assert result.shape[0] == 1
        assert result.loc[0, "holder_name"] == "中央汇金投资有限责任公司"

    def test_filter_national_team_multiple_keywords(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        df = pd.DataFrame(
            [
                {"holder_name": "中央汇金投资有限责任公司", "symbol": "600000"},
                {"holder_name": "全国社保基金理事会", "symbol": "600000"},
                {"holder_name": "基本养老保险基金", "symbol": "600000"},
                {"holder_name": "普通基金", "symbol": "600000"},
            ]
        )

        result = tracker._filter_national_team(df)

        assert result.shape[0] == 3

    def test_filter_national_team_case_insensitive(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        df = pd.DataFrame(
            [
                {"holder_name": "中央汇金", "symbol": "600000"},
                {"holder_name": "中央汇金（大写）", "symbol": "600000"},
            ]
        )

        result = tracker._filter_national_team(df)

        assert result.shape[0] == 2

    def test_filter_national_team_empty_dataframe(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        df = pd.DataFrame(columns=["holder_name", "symbol"])

        result = tracker._filter_national_team(df)

        assert result.empty


class TestNationalTeamTrackerWithMocks:
    """Test NationalTeamTracker behavior with mocked API."""

    def test_fetch_top_holders_success(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        class DummyPro:
            def top10_holders(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "holder_name": "中央汇金投资有限责任公司",
                            "hold_ratio": 5.2,
                            "hold_amount": 100000000,
                            "ann_date": "20240101",
                        },
                        {
                            "holder_name": "某某基金",
                            "hold_ratio": 3.1,
                            "hold_amount": 60000000,
                            "ann_date": "20240101",
                        },
                    ]
                )

        tracker._pro = DummyPro()

        result = tracker.fetch_top_holders("600000")

        # Only national team should be returned
        assert result.shape[0] == 1
        assert result.loc[0, "holder_name"] == "中央汇金投资有限责任公司"

    def test_fetch_top_holders_empty_result(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        class DummyPro:
            def top10_holders(self, **kwargs):
                return pd.DataFrame()

        tracker._pro = DummyPro()

        result = tracker.fetch_top_holders("600000")

        assert result.empty

    def test_fetch_top_holders_api_error(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        class DummyPro:
            def top10_holders(self, **kwargs):
                raise Exception("API限流")

        tracker._pro = DummyPro()

        with pytest.raises(DataError):
            tracker.fetch_top_holders("600000")

    def test_has_national_team_holding_true(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        class DummyPro:
            def top10_holders(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "holder_name": "中央汇金投资有限责任公司",
                            "hold_ratio": 5.2,
                            "hold_amount": 100000000,
                            "ann_date": "20240101",
                        }
                    ]
                )

        tracker._pro = DummyPro()

        result = tracker.has_national_team_holding("600000")

        assert result is True

    def test_has_national_team_holding_false(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        class DummyPro:
            def top10_holders(self, **kwargs):
                return pd.DataFrame(
                    [
                        {
                            "holder_name": "某某基金公司",
                            "hold_ratio": 3.1,
                            "hold_amount": 60000000,
                            "ann_date": "20240101",
                        }
                    ]
                )

        tracker._pro = DummyPro()

        result = tracker.has_national_team_holding("600000")

        assert result is False

    def test_has_national_team_holding_error_handling(self) -> None:
        tracker = object.__new__(NationalTeamTracker)

        class DummyPro:
            def top10_holders(self, **kwargs):
                raise Exception("Network error")

        tracker._pro = DummyPro()

        result = tracker.has_national_team_holding("600000")

        # Should not raise, should return False
        assert result is False


class TestKeywordsList:
    """Test that keywords list is properly defined."""

    def test_keywords_list_contains_expected_terms(self) -> None:
        assert "中央汇金" in NATIONAL_TEAM_KEYWORDS
        assert "证金公司" in NATIONAL_TEAM_KEYWORDS
        assert "全国社保基金" in NATIONAL_TEAM_KEYWORDS
        assert "基本养老保险基金" in NATIONAL_TEAM_KEYWORDS

    def test_keywords_list_not_empty(self) -> None:
        assert len(NATIONAL_TEAM_KEYWORDS) > 0
