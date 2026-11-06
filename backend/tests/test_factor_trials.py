from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

import factor_trials
from aqsp.core.time import latest_completed_trading_day, now_shanghai


class RawDailySource:
    name = "sqlite_db"

    def __init__(self, frames, *, price_mode="raw"):
        self.frames = frames
        self._price_mode = price_mode
        self.workload = None

    def set_workload(self, workload):
        self.workload = workload

    def price_mode(self):
        return self._price_mode

    def get_available_symbols(self):
        return list(self.frames)

    def fetch_daily(self, symbols, start, end, adjust=""):
        assert adjust == ""
        return {
            symbol: self.frames[symbol].loc[
                (pd.to_datetime(self.frames[symbol]["date"]).dt.date >= start)
                & (pd.to_datetime(self.frames[symbol]["date"]).dt.date <= end)
            ].copy()
            for symbol in symbols
        }


@pytest.fixture
def local_market(monkeypatch):
    dates = pd.bdate_range(end="2026-10-30", periods=100)
    frames = {}
    symbols = [f"{index:06d}" for index in range(1, 31)]
    for index, symbol in enumerate(symbols, start=1):
        trend = 0.0005 * index
        close = [50 + index + day * trend + (day % 7) * 0.01 * index for day in range(len(dates))]
        frames[symbol] = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "symbol": symbol,
                "open": close,
                "high": [value * 1.01 for value in close],
                "low": [value * 0.99 for value in close],
                "close": close,
                "volume": [1000 + index * day for day in range(len(dates))],
                "amount": [100000 + index * day * 10 for day in range(len(dates))],
                "suspended": False,
            }
        )
    source = RawDailySource(frames)
    monkeypatch.setattr(factor_trials.source_factory, "build_data_source", lambda *_args, **_kwargs: source)
    return source, symbols, dates


def test_factor_trial_fingerprints_actual_raw_sample_when_input_changes(local_market):
    source, symbols, dates = local_market
    signal_start = dates[-45].date().isoformat()
    signal_end = dates[-2].date().isoformat()

    first = factor_trials.run_factor_trial(
        expression="rank(close / ts_mean(close, 5))",
        symbols=symbols,
        signal_start=signal_start,
        signal_end=signal_end,
        holding_days=3,
        quantile=5,
    )
    repeat = factor_trials.run_factor_trial(
        expression="rank(close / ts_mean(close, 5))",
        symbols=symbols,
        signal_start=signal_start,
        signal_end=signal_end,
        holding_days=3,
        quantile=5,
    )
    shorter_lookback = factor_trials.run_factor_trial(
        expression="close",
        symbols=symbols,
        signal_start=signal_start,
        signal_end=signal_end,
        holding_days=3,
        quantile=5,
    )

    assert first["data_sample_sha256"] == repeat["data_sample_sha256"]
    assert first["data_sample_sha256"] == shorter_lookback["data_sample_sha256"]
    assert len(first["data_sample_sha256"]) == 64
    assert first["data_rows"] > 0

    frame = source.frames[symbols[0]]
    frame.loc[frame["date"] == signal_start, "close"] += 0.01
    revised = factor_trials.run_factor_trial(
        expression="rank(close / ts_mean(close, 5))",
        symbols=symbols,
        signal_start=signal_start,
        signal_end=signal_end,
        holding_days=3,
        quantile=5,
    )
    assert revised["data_sample_sha256"] != first["data_sample_sha256"]


def test_factor_trial_uses_raw_local_data_and_keeps_result_preview_only(local_market):
    _source, symbols, dates = local_market
    signal_start = dates[-45].date().isoformat()
    signal_end = dates[-2].date().isoformat()

    result = factor_trials.run_factor_trial(
        expression="rank(close / ts_mean(close, 5))",
        symbols=symbols,
        signal_start=signal_start,
        signal_end=signal_end,
        holding_days=3,
        quantile=5,
    )

    assert result["source"] == "sqlite_db"
    assert result["price_mode"] == "raw"
    assert result["pit_scope"] == "raw_ohlcv_only"
    assert result["evidence_status"] == "preview_only"
    assert result["promotion_authorized"] is False
    assert result["effective_cross_section_dates"] >= factor_trials.LIMITS.min_oos_dates
    assert result["daily_ic"]


def test_factor_trial_rejects_non_raw_source(local_market, monkeypatch):
    _source, symbols, dates = local_market
    adjusted = RawDailySource(_source.frames, price_mode="qfq")
    monkeypatch.setattr(factor_trials.source_factory, "build_data_source", lambda *_args, **_kwargs: adjusted)

    with pytest.raises(factor_trials.DataError, match="raw 不复权"):
        factor_trials.run_factor_trial(
            expression="close",
            symbols=symbols,
            signal_start=dates[-45].date().isoformat(),
            signal_end=dates[-2].date().isoformat(),
            holding_days=3,
        )


def test_factor_trial_rejects_unavailable_fields_and_future_range(local_market):
    _source, symbols, dates = local_market
    with pytest.raises(ValueError, match="暂不支持字段"):
        factor_trials.run_factor_trial(
            expression="roe",
            symbols=symbols,
            signal_start=dates[-45].date().isoformat(),
            signal_end=dates[-2].date().isoformat(),
            holding_days=3,
        )


def test_factor_trial_blocks_incomplete_requested_symbol_sample(local_market):
    source, symbols, dates = local_market
    original_fetch = source.fetch_daily
    omitted = symbols[-1]

    def incomplete_fetch(requested, start, end, adjust=""):
        result = original_fetch(requested, start, end, adjust)
        result.pop(omitted, None)
        return result

    source.fetch_daily = incomplete_fetch
    result = factor_trials.run_factor_trial(
        expression="rank(close)",
        symbols=symbols,
        signal_start=dates[-45].date().isoformat(),
        signal_end=dates[-2].date().isoformat(),
        holding_days=3,
    )

    assert result["evidence_status"] == "blocked"
    assert result["used_symbols"] == len(symbols) - 1
    assert result["missing_data_symbols"] == [omitted]
    future_day = latest_completed_trading_day(now_shanghai()) + timedelta(days=1)
    with pytest.raises(ValueError, match="最近已收盘交易日"):
        factor_trials.run_factor_trial(
            expression="close",
            symbols=symbols,
            signal_start=dates[-2].date().isoformat(),
            signal_end=future_day.isoformat(),
            holding_days=3,
        )
