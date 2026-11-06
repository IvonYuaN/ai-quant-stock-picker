from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

import stock_levels


def _bars(count: int = 25) -> pd.DataFrame:
    end = date(2026, 10, 30)
    rows = []
    for offset in range(count - 1, -1, -1):
        day = end - timedelta(days=offset)
        close = 10.0 + (count - offset) / 10
        rows.append({
            "date": day.isoformat(),
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "adj_factor": 1.0,
            "volume": 1000,
        })
    frame = pd.DataFrame(rows)
    frame.attrs.update(
        price_mode="raw",
        source_name="tencent",
        fetched_at="2026-10-31T10:00:00+08:00",
        pit_as_of_factor=1.0,
        pit_factor_source="test-pit-source",
        pit_factor_fetched_at="2026-10-31T09:00:00+08:00",
        pit_factor_as_of="2026-10-30",
    )
    return frame


def test_stock_levels_calculate_raw_window_previous_session_and_pivot() -> None:
    result = stock_levels.calculate_stock_levels(
        _bars(), requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    assert result["as_of"] == "2026-10-30"
    assert result["price_basis"] == "pit_as_of"
    assert levels["rolling_range"]["status"] == "available"
    assert levels["rolling_range"]["levels"][0]["label"] == "区间压力"
    assert levels["previous_session"]["levels"][0]["date"] == "2026-10-29"
    assert levels["classic_pivot"]["levels"][0]["label"] == "P"
    assert levels["keltner_short"]["status"] == "available"
    assert levels["atr_channel"]["status"] == "available"
    assert levels["fibonacci"]["levels"][-1]["label"] == "Fib 78.6%"
    assert levels["round_numbers"]["status"] == "available"
    assert all(levels[kind]["status"] == "unavailable" for kind in stock_levels.UNSUPPORTED_LEVELS)


def test_stock_levels_apply_upstream_windows_and_leave_insufficient_keltner_windows_unavailable() -> None:
    result = stock_levels.calculate_stock_levels(
        _bars(65), requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    assert levels["keltner_short"]["status"] == "available"
    assert levels["keltner_mid"]["status"] == "available"
    assert levels["keltner_long"]["status"] == "unavailable"
    assert "MA60" in levels["keltner_mid"]["formula"]


def test_stock_levels_use_wilder_atr_after_a_true_range_spike() -> None:
    frame = _bars()
    frame.loc[frame.index[-1], "high"] += 0.5
    frame.loc[frame.index[-1], "low"] -= 0.5

    result = stock_levels.calculate_stock_levels(
        frame, requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    # The prior 24 true ranges are 1.0; Wilder smoothing updates the final 2.0 range by 1/14.
    atr = 1 + 1 / 14
    last_close = float(frame["close"].iloc[-1])
    mean20 = float(frame["close"].tail(20).mean())
    assert levels["atr_channel"]["levels"][0]["price"] == pytest.approx(last_close + 2 * atr, abs=0.01)
    assert levels["keltner_short"]["levels"][0]["price"] == pytest.approx(mean20 + 2 * atr, abs=0.01)
    assert "Wilder ATR14" in levels["atr_channel"]["formula"]


def test_stock_levels_return_source_formula_and_raw_basis_for_new_families() -> None:
    frame = _bars(125)
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column] * 10
    result = stock_levels.calculate_stock_levels(
        frame, requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    for level_type in stock_levels.SUPPORTED_LEVELS:
        assert levels[level_type]["status"] == "available"
        assert "tencent" in levels[level_type]["source"]
        assert "test-pit-source" in levels[level_type]["source"]
        assert levels[level_type]["price_basis"] == "pit_as_of"
        assert levels[level_type]["as_of"] == "2026-10-30"
        assert levels[level_type]["formula"]


def test_stock_levels_excludes_bars_after_requested_as_of() -> None:
    frame = _bars()
    frame.loc[len(frame)] = {
        "date": "2026-10-31",
        "open": 100,
        "high": 100,
        "low": 100,
        "close": 100,
        "volume": 1000,
    }
    frame.attrs.update(price_mode="raw", source_name="tencent", fetched_at="2026-10-31T10:00:00+08:00")

    result = stock_levels.calculate_stock_levels(
        frame, requested_as_of=date(2026, 10, 30)
    )

    assert result["as_of"] == "2026-10-30"
    assert result["available_rows"] == 25
    assert result["warning"].startswith("OHLC 使用截至")


def test_stock_levels_block_cross_day_levels_without_point_in_time_factors() -> None:
    frame = _bars().drop(columns=["adj_factor"])
    frame.attrs.pop("pit_as_of_factor")

    result = stock_levels.calculate_stock_levels(
        frame, requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    assert result["price_basis"] == "raw_partial"
    assert levels["rolling_range"]["status"] == "unavailable"
    assert levels["keltner_short"]["status"] == "unavailable"
    assert levels["atr_channel"]["status"] == "unavailable"
    assert levels["fibonacci"]["status"] == "unavailable"
    assert levels["previous_session"]["status"] == "available"
    assert levels["classic_pivot"]["status"] == "available"
    assert levels["round_numbers"]["status"] == "available"


def test_stock_levels_map_history_to_the_as_of_adjustment_factor() -> None:
    frame = _bars()
    frame.loc[:11, "adj_factor"] = 1.0
    frame.loc[12:, "adj_factor"] = 2.0
    frame.attrs["pit_as_of_factor"] = 2.0

    result = stock_levels.calculate_stock_levels(
        frame, requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    assert levels["rolling_range"]["levels"][1]["price"] == pytest.approx(4.8)
    assert levels["previous_session"]["levels"][0]["price"] == pytest.approx(
        frame.loc[len(frame) - 2, "high"]
    )


@pytest.mark.parametrize("price_mode", ["qfq", "hfq", "provider_default", "", None])
def test_stock_levels_reject_unverified_or_adjusted_price_basis(price_mode: str) -> None:
    frame = _bars()
    if price_mode is None:
        frame.attrs.pop("price_mode")
    else:
        frame.attrs["price_mode"] = price_mode

    result = stock_levels.calculate_stock_levels(
        frame, requested_as_of=date(2026, 10, 30)
    )

    assert result["price_basis"] == "unknown"
    assert all(level["status"] == "unavailable" for level in result["levels"])


def test_stock_levels_keeps_partial_availability_for_short_history() -> None:
    result = stock_levels.calculate_stock_levels(
        _bars(3), requested_as_of=date(2026, 10, 30)
    )
    levels = {item["type"]: item for item in result["levels"]}

    assert levels["rolling_range"]["status"] == "unavailable"
    assert levels["previous_session"]["status"] == "available"
    assert levels["classic_pivot"]["status"] == "available"
    assert levels["atr_channel"]["status"] == "unavailable"
    assert levels["keltner_long"]["status"] == "unavailable"


def test_stock_levels_rejects_invalid_window_configuration() -> None:
    with pytest.raises(ValueError, match="窗口"):
        stock_levels.calculate_stock_levels(
            _bars(), requested_as_of=date(2026, 10, 30), rolling_window=10
        )
