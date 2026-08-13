"""Tests for midday NaN/inf null-value handling across the data pipeline.

Covers the P1 task:午盘空值处理 — ensures NaN prices during the midday gap
(11:30–13:00) do not contaminate PnL, scores, or dashboard rendering.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from aqsp.core.types import safe_float
from aqsp.data.realtime import RealtimeService
from aqsp.runtime_snapshot import _snapshot_candidate
from aqsp.web.data_provider import _runtime_float, _runtime_pct


# ---------------------------------------------------------------------------
# core/types.py: safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_safe_float_returns_value_for_valid_number(self) -> None:
        assert safe_float(42.5) == 42.5

    def test_safe_float_returns_zero_for_nan(self) -> None:
        assert safe_float(float("nan")) == 0.0

    def test_safe_float_returns_zero_for_inf(self) -> None:
        assert safe_float(float("inf")) == 0.0

    def test_safe_float_returns_zero_for_neg_inf(self) -> None:
        assert safe_float(float("-inf")) == 0.0

    def test_safe_float_returns_zero_for_none(self) -> None:
        assert safe_float(None) == 0.0

    def test_safe_float_returns_zero_for_empty_string(self) -> None:
        assert safe_float("") == 0.0

    def test_safe_float_returns_zero_for_invalid_string(self) -> None:
        assert safe_float("abc") == 0.0

    def test_safe_float_parses_numeric_string(self) -> None:
        assert safe_float("3.14") == 3.14

    def test_safe_float_respects_custom_default(self) -> None:
        assert safe_float(float("nan"), default=-1.0) == -1.0

    def test_safe_float_handles_nan_string(self) -> None:
        """``float('nan')`` is truthy so ``x or 0.0`` must NOT be used."""
        assert safe_float("nan") == 0.0


# ---------------------------------------------------------------------------
# data/realtime.py: NaN protection in quote extraction
#
# The freshness layer (validate_realtime_quotes) already rejects NaN/inf/<=0
# prices upstream in get_quotes().  The safe_float calls in get_price /
# get_bid_ask / get_volume_amount are **defense-in-depth**: they protect
# against any path that bypasses freshness validation.
# ---------------------------------------------------------------------------


def _nan_quote() -> dict:
    """Quote with NaN in all numeric fields (bypasses freshness in tests)."""
    return {
        "price": float("nan"),
        "bid1": float("nan"),
        "ask1": float("nan"),
        "volume": float("nan"),
        "amount": float("nan"),
    }


class TestRealtimeNanDefenseInDepth:
    """get_price / get_bid_ask / get_volume_amount use safe_float as
    defense-in-depth even when freshness validation is bypassed."""

    def test_get_price_returns_zero_for_nan_via_mocked_quotes(self) -> None:
        service = RealtimeService.__new__(RealtimeService)
        with patch.object(service, "get_quotes", return_value={"600000": _nan_quote()}):
            prices = service.get_price(["600000"])
        assert prices["600000"] == 0.0

    def test_get_bid_ask_returns_zeros_for_nan_via_mocked_quotes(self) -> None:
        service = RealtimeService.__new__(RealtimeService)
        with patch.object(service, "get_quotes", return_value={"600000": _nan_quote()}):
            bid, ask = service.get_bid_ask(["600000"])["600000"]
        assert bid == 0.0
        assert ask == 0.0

    def test_get_volume_amount_returns_zeros_for_nan_via_mocked_quotes(self) -> None:
        service = RealtimeService.__new__(RealtimeService)
        with patch.object(service, "get_quotes", return_value={"600000": _nan_quote()}):
            vol, amt = service.get_volume_amount(["600000"])["600000"]
        assert vol == 0.0
        assert amt == 0.0

    def test_calculate_intraday_return_returns_none_for_nan_price(self) -> None:
        service = RealtimeService.__new__(RealtimeService)
        with patch.object(service, "get_quotes", return_value={"600000": _nan_quote()}):
            result = service.calculate_intraday_return("600000", prev_close=10.0)
        assert result is None

    def test_calculate_intraday_return_returns_none_for_zero_price(self) -> None:
        quote = _nan_quote()
        quote["price"] = 0.0
        service = RealtimeService.__new__(RealtimeService)
        with patch.object(service, "get_quotes", return_value={"600000": quote}):
            result = service.calculate_intraday_return("600000", prev_close=10.0)
        assert result is None


# ---------------------------------------------------------------------------
# runtime_snapshot.py: NaN scores don't propagate
# ---------------------------------------------------------------------------


class TestRuntimeSnapshotNanProtection:
    def test_snapshot_candidate_converts_nan_score_to_zero(self) -> None:
        candidate = SimpleNamespace(
            symbol="600000",
            display_name="600000 测试",
            score=float("nan"),
            rank_label="观察",
            action_label="继续观察",
            status_label="",
            next_step="",
            blocker="",
            reasons=(),
            risks=(),
            news_catalyst_summary="",
            cross_market_summary="",
            decision_note="",
            candidate_fingerprint="",
        )
        result = _snapshot_candidate(candidate)
        assert result.score == 0.0
        assert not math.isnan(result.score)


# ---------------------------------------------------------------------------
# web/dashboard_beginner.py: _to_float / _to_optional_float
# ---------------------------------------------------------------------------


class TestDashboardToFloatNanProtection:
    def test_to_float_returns_zero_for_nan(self) -> None:
        from aqsp.web.dashboard_beginner import _to_float

        assert _to_float(float("nan")) == 0.0

    def test_to_float_returns_zero_for_inf(self) -> None:
        from aqsp.web.dashboard_beginner import _to_float

        assert _to_float(float("inf")) == 0.0

    def test_to_float_returns_zero_for_none(self) -> None:
        from aqsp.web.dashboard_beginner import _to_float

        assert _to_float(None) == 0.0

    def test_to_float_returns_value_for_valid_number(self) -> None:
        from aqsp.web.dashboard_beginner import _to_float

        assert _to_float(3.14) == 3.14

    def test_to_optional_float_returns_none_for_nan(self) -> None:
        from aqsp.web.dashboard_beginner import _to_optional_float

        assert _to_optional_float(float("nan")) is None

    def test_to_optional_float_returns_none_for_inf(self) -> None:
        from aqsp.web.dashboard_beginner import _to_optional_float

        assert _to_optional_float(float("inf")) is None

    def test_to_optional_float_returns_value_for_valid(self) -> None:
        from aqsp.web.dashboard_beginner import _to_optional_float

        assert _to_optional_float(3.14) == 3.14


class TestDashboardCompatToFloatNanProtection:
    def test_compat_to_float_returns_zero_for_nan(self) -> None:
        from aqsp.web.dashboard_beginner_compat import _to_float

        assert _to_float(float("nan")) == 0.0

    def test_compat_to_optional_float_returns_none_for_nan(self) -> None:
        from aqsp.web.dashboard_beginner_compat import _to_optional_float

        assert _to_optional_float(float("nan")) is None


# ---------------------------------------------------------------------------
# web/data_provider.py: _runtime_float / _runtime_pct
# ---------------------------------------------------------------------------


class TestRuntimeFloatNanProtection:
    def test_runtime_float_returns_none_for_nan(self) -> None:
        assert _runtime_float(float("nan")) is None

    def test_runtime_float_returns_none_for_inf(self) -> None:
        assert _runtime_float(float("inf")) is None

    def test_runtime_float_returns_value_for_valid(self) -> None:
        assert _runtime_float(3.14) == 3.14

    def test_runtime_float_returns_none_for_none(self) -> None:
        assert _runtime_float(None) is None

    def test_runtime_pct_returns_dash_for_nan(self) -> None:
        assert _runtime_pct(float("nan")) == "-"

    def test_runtime_pct_formats_valid_value(self) -> None:
        assert _runtime_pct(3.1415) == "3.14%"


# ---------------------------------------------------------------------------
# strategies/morning_breakout.py: _calc_change_pct NaN guard
# ---------------------------------------------------------------------------


class TestMorningBreakoutNanGuard:
    def test_calc_change_pct_returns_zero_for_nan_close(self) -> None:
        from aqsp.strategies.base import StrategyConfig
        from aqsp.strategies.morning_breakout import MorningBreakoutStrategy

        strategy = MorningBreakoutStrategy(StrategyConfig(name="morning_breakout"))
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=3, freq="B"),
                "open": [10.0, 10.5, float("nan")],
                "high": [10.5, 11.0, float("nan")],
                "low": [9.8, 10.3, float("nan")],
                "close": [10.0, 10.5, float("nan")],
                "volume": [1e6, 2e6, 1e6],
                "name": ["测试"] * 3,
            }
        )
        assert strategy._calc_change_pct(df) == 0.0

    def test_calc_change_pct_returns_zero_for_nan_prev_close(self) -> None:
        from aqsp.strategies.base import StrategyConfig
        from aqsp.strategies.morning_breakout import MorningBreakoutStrategy

        strategy = MorningBreakoutStrategy(StrategyConfig(name="morning_breakout"))
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=3, freq="B"),
                "open": [10.0, float("nan"), 11.0],
                "high": [10.5, float("nan"), 11.5],
                "low": [9.8, float("nan"), 10.8],
                "close": [10.0, float("nan"), 11.0],
                "volume": [1e6, 1e6, 1e6],
                "name": ["测试"] * 3,
            }
        )
        assert strategy._calc_change_pct(df) == 0.0

    def test_calc_change_pct_works_for_valid_data(self) -> None:
        from aqsp.strategies.base import StrategyConfig
        from aqsp.strategies.morning_breakout import MorningBreakoutStrategy

        strategy = MorningBreakoutStrategy(StrategyConfig(name="morning_breakout"))
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=3, freq="B"),
                "open": [10.0, 10.5, 11.0],
                "high": [10.5, 11.0, 11.5],
                "low": [9.8, 10.3, 10.8],
                "close": [10.0, 10.5, 11.0],
                "volume": [1e6, 2e6, 1e6],
                "name": ["测试"] * 3,
            }
        )
        result = strategy._calc_change_pct(df)
        assert result == pytest.approx((11.0 - 10.5) / 10.5 * 100)


# ---------------------------------------------------------------------------
# strategies/sector_rotation.py: _calc_change_pct NaN guard
# ---------------------------------------------------------------------------


class TestSectorRotationNanGuard:
    def test_calc_change_pct_returns_zero_for_nan_close(self) -> None:
        from aqsp.strategies.base import StrategyConfig
        from aqsp.strategies.sector_rotation import SectorRotationStrategy

        strategy = SectorRotationStrategy(
            StrategyConfig(name="sector_rotation", enabled=False)
        )
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=6, freq="B"),
                "open": [10.0, 10.5, 11.0, 10.8, 11.2, float("nan")],
                "high": [10.5, 11.0, 11.5, 11.0, 11.5, float("nan")],
                "low": [9.8, 10.3, 10.8, 10.5, 11.0, float("nan")],
                "close": [10.0, 10.5, 11.0, 10.8, 11.2, float("nan")],
                "volume": [1e6] * 6,
                "name": ["测试"] * 6,
            }
        )
        assert strategy._calc_change_pct(df, days=3) == 0.0

    def test_calc_change_pct_returns_zero_for_nan_past_close(self) -> None:
        from aqsp.strategies.base import StrategyConfig
        from aqsp.strategies.sector_rotation import SectorRotationStrategy

        strategy = SectorRotationStrategy(
            StrategyConfig(name="sector_rotation", enabled=False)
        )
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=6, freq="B"),
                "open": [10.0, float("nan"), 11.0, 10.8, 11.2, 11.5],
                "high": [10.5, float("nan"), 11.5, 11.0, 11.5, 12.0],
                "low": [9.8, float("nan"), 10.8, 10.5, 11.0, 11.3],
                "close": [10.0, float("nan"), 11.0, 10.8, 11.2, 11.5],
                "volume": [1e6] * 6,
                "name": ["测试"] * 6,
            }
        )
        assert strategy._calc_change_pct(df, days=4) == 0.0

    def test_calc_change_pct_works_for_valid_data(self) -> None:
        from aqsp.strategies.base import StrategyConfig
        from aqsp.strategies.sector_rotation import SectorRotationStrategy

        strategy = SectorRotationStrategy(
            StrategyConfig(name="sector_rotation", enabled=False)
        )
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=6, freq="B"),
                "open": [10.0, 10.5, 11.0, 10.8, 11.2, 11.5],
                "high": [10.5, 11.0, 11.5, 11.0, 11.5, 12.0],
                "low": [9.8, 10.3, 10.8, 10.5, 11.0, 11.3],
                "close": [10.0, 10.5, 11.0, 10.8, 11.2, 11.5],
                "volume": [1e6] * 6,
                "name": ["测试"] * 6,
            }
        )
        result = strategy._calc_change_pct(df, days=3)
        # current = iloc[-1] = 11.5, past = iloc[-4] = 11.0
        expected = (11.5 - 11.0) / 11.0 * 100
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# ledger/runtime.py: _safe_float delegation
# ---------------------------------------------------------------------------


class TestLedgerSafeFloatDelegation:
    def test_safe_float_returns_zero_for_nan(self) -> None:
        from aqsp.ledger.runtime import _safe_float

        assert _safe_float(float("nan")) == 0.0

    def test_safe_float_returns_zero_for_inf(self) -> None:
        from aqsp.ledger.runtime import _safe_float

        assert _safe_float(float("inf")) == 0.0

    def test_safe_float_returns_value_for_valid(self) -> None:
        from aqsp.ledger.runtime import _safe_float

        assert _safe_float(42.0) == 42.0

    def test_safe_float_returns_zero_for_none(self) -> None:
        from aqsp.ledger.runtime import _safe_float

        assert _safe_float(None) == 0.0
