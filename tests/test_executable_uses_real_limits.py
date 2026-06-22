from __future__ import annotations

import pandas as pd

from aqsp.ledger.base import _check_executable


class TestCheckExecutableUsesRealLimits:
    def test_normal_stock_passes(self):
        entry_bar = pd.Series(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1_000_000,
                "suspended": False,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is True
        assert reason == ""

    def test_suspended_stock_blocked(self):
        entry_bar = pd.Series(
            {
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 0,
                "suspended": True,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "suspended_or_no_trade"

    def test_limit_up_10pct_blocked(self):
        entry_bar = pd.Series(
            {
                "open": 110.0,
                "high": 110.0,
                "low": 110.0,
                "close": 110.0,
                "volume": 1000,
                "suspended": False,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_up_at_open"

    def test_limit_up_20pct_for_chinext(self):
        entry_bar = pd.Series(
            {
                "open": 120.0,
                "high": 120.0,
                "low": 120.0,
                "close": 120.0,
                "volume": 1000,
                "suspended": False,
                "limit_up": 120.0,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_up_at_open"

    def test_limit_up_20pct_for_star_market(self):
        entry_bar = pd.Series(
            {
                "open": 120.0,
                "high": 120.0,
                "low": 120.0,
                "close": 120.0,
                "volume": 1000,
                "suspended": False,
                "limit_up": 120.0,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_up_at_open"

    def test_limit_down_10pct_blocked(self):
        entry_bar = pd.Series(
            {
                "open": 90.0,
                "high": 90.0,
                "low": 90.0,
                "close": 90.0,
                "volume": 1000,
                "suspended": False,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_down_at_open"

    def test_limit_down_20pct_for_chinext(self):
        entry_bar = pd.Series(
            {
                "open": 80.0,
                "high": 80.0,
                "low": 80.0,
                "close": 80.0,
                "volume": 1000,
                "suspended": False,
                "limit_down": 80.0,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_down_at_open"

    def test_uses_real_limit_up_from_bar(self):
        entry_bar = pd.Series(
            {
                "open": 115.0,
                "high": 115.0,
                "low": 115.0,
                "close": 115.0,
                "volume": 1000,
                "suspended": False,
                "limit_up": 115.0,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_up_at_open"

    def test_uses_real_limit_down_from_bar(self):
        entry_bar = pd.Series(
            {
                "open": 85.0,
                "high": 85.0,
                "low": 85.0,
                "close": 85.0,
                "volume": 1000,
                "suspended": False,
                "limit_down": 85.0,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "limit_down_at_open"

    def test_no_open_price_blocked(self):
        entry_bar = pd.Series(
            {
                "open": 0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
                "suspended": False,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is False
        assert reason == "no_open_price"

    def test_zero_prev_close_blocks_as_missing_prev_close(self):
        entry_bar = pd.Series(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000,
                "suspended": False,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=0.0, row={})
        assert executable is False
        assert reason == "missing_prev_close"

    def test_near_limit_up_with_tolerance(self):
        entry_bar = pd.Series(
            {
                "open": 109.5,
                "high": 109.5,
                "low": 109.5,
                "close": 109.5,
                "volume": 1000,
                "suspended": False,
            }
        )
        executable, reason = _check_executable(entry_bar, prev_close=100.0, row={})
        assert executable is True
