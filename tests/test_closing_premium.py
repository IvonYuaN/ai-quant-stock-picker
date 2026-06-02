from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.closing_premium import (
    ClosingPremiumStrategy,
    PremiumSignal,
    format_closing_signals,
)
from aqsp.strategies.thresholds import load_thresholds


def _make_df(days=30, base_price=10.0, seed=42, final_change_pct=3.5):
    np.random.seed(seed)
    dates = pd.date_range("2025-01-01", periods=days, freq="B", tz="Asia/Shanghai")
    prices = np.full(days, base_price)
    prices[-2] = base_price
    prices[-1] = base_price * (1 + final_change_pct / 100)

    base_vol = 1_000_000
    volumes = np.full(days, base_vol, dtype=float)
    volumes[-5:] = base_vol * 1.5
    volumes[-2:] = base_vol * 4.0

    opens = prices * 0.98

    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": prices * (1 + abs(np.random.randn(days) * 0.01)),
            "low": prices * (1 - abs(np.random.randn(days) * 0.01)),
            "close": prices,
            "volume": volumes,
            "name": ["测试股票"] * days,
        }
    )


def _make_strategy(min_score=15.0):
    thresholds = load_thresholds()
    modified = replace(
        thresholds,
        closing_premium=replace(thresholds.closing_premium, min_score=min_score),
    )
    config = StrategyConfig(name="closing_premium")
    return ClosingPremiumStrategy(config, thresholds=modified)


class TestClosingPremiumInit:
    def test_init_default(self):
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config)
        assert strategy.id == "closing_premium"
        assert strategy.name == "closing_premium"
        assert strategy.hypothesis != ""
        assert "stable_bull" in strategy.regime_required
        assert "stable_sideways" in strategy.regime_required
        assert "volatile_bull" in strategy.regime_required

    def test_init_with_thresholds(self):
        thresholds = load_thresholds()
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config, thresholds=thresholds)
        assert strategy.cfg.min_change_pct == 2.0
        assert strategy.cfg.max_change_pct == 7.0
        assert strategy.cfg.min_score == 65.0


class TestAnalyzeClosing:
    def test_returns_signals_when_data_valid(self):
        strategy = _make_strategy()
        df = _make_df(days=30, final_change_pct=3.5)
        signals = strategy.analyze_closing({"600000": df})

        assert isinstance(signals, list)
        assert len(signals) > 0
        assert all(isinstance(s, PremiumSignal) for s in signals)

    def test_signal_has_required_fields(self):
        strategy = _make_strategy()
        df = _make_df(days=30, final_change_pct=3.5)
        signals = strategy.analyze_closing({"600000": df})

        assert len(signals) > 0
        signal = signals[0]
        assert signal.symbol == "600000"
        assert isinstance(signal.name, str)
        assert 0 <= signal.score <= 100
        assert 0 <= signal.confidence <= 1
        assert signal.current_price > 0
        assert signal.entry_price > 0
        assert signal.stop_loss > 0
        assert signal.take_profit_1 > 0
        assert signal.take_profit_2 > 0
        assert isinstance(signal.signal_type, str)
        assert isinstance(signal.reasons, tuple)
        assert isinstance(signal.risks, tuple)
        assert signal.holding_days > 0
        assert isinstance(signal.expected_return, float)

    def test_returns_empty_list_when_data_empty(self):
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config)

        signals = strategy.analyze_closing({"600000": pd.DataFrame()})
        assert signals == []

    def test_returns_empty_list_when_none_data(self):
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config)

        signals = strategy.analyze_closing({"600000": None})
        assert signals == []

    def test_filters_signals_below_min_score(self):
        strategy = _make_strategy(min_score=55.0)
        df = _make_df(days=30, final_change_pct=3.5)
        signals = strategy.analyze_closing({"600000": df})

        for signal in signals:
            assert signal.score >= strategy.cfg.min_score

    def test_filters_when_change_below_threshold(self):
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config)

        df = _make_df(days=30, final_change_pct=1.0)
        signals = strategy.analyze_closing({"600000": df})
        assert signals == []

    def test_filters_when_change_above_threshold(self):
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config)

        df = _make_df(days=30, final_change_pct=8.0)
        signals = strategy.analyze_closing({"600000": df})
        assert signals == []

    def test_multiple_symbols(self):
        strategy = _make_strategy()
        df1 = _make_df(days=30, final_change_pct=3.5, seed=42)
        df2 = _make_df(days=30, base_price=20.0, final_change_pct=4.0, seed=43)
        signals = strategy.analyze_closing({"600000": df1, "600001": df2})

        assert isinstance(signals, list)
        symbols = {s.symbol for s in signals}
        assert symbols <= {"600000", "600001"}

    def test_signals_sorted_by_score_desc(self):
        strategy = _make_strategy()
        df1 = _make_df(days=30, final_change_pct=3.5, seed=42)
        df2 = _make_df(days=30, base_price=20.0, final_change_pct=4.0, seed=43)
        signals = strategy.analyze_closing({"600000": df1, "600001": df2})

        if len(signals) >= 2:
            scores = [s.score for s in signals]
            assert scores == sorted(scores, reverse=True)

    def test_insufficient_data_returns_empty(self):
        config = StrategyConfig(name="closing_premium")
        strategy = ClosingPremiumStrategy(config)

        df = pd.DataFrame(
            {
                "date": ["2025-01-01"],
                "close": [10.0],
                "open": [9.8],
                "high": [10.2],
                "low": [9.7],
                "volume": [1_000_000],
            }
        )
        signals = strategy.analyze_closing({"600000": df})
        assert signals == []


class TestPremiumSignalDataclass:
    def test_frozen(self):
        signal = PremiumSignal(
            symbol="600000",
            name="测试",
            signal_type="尾盘拉升",
            score=75.0,
            current_price=10.5,
            entry_price=10.3,
            stop_loss=9.8,
            take_profit_1=11.0,
            take_profit_2=11.5,
            reasons=(),
            risks=(),
            confidence=0.7,
            holding_days=2,
            expected_return=6.8,
        )
        with pytest.raises(AttributeError):
            signal.symbol = "999999"


class TestFormatClosingSignals:
    def test_returns_string(self):
        result = format_closing_signals([])
        assert isinstance(result, str)

    def test_empty_signals_message(self):
        result = format_closing_signals([])
        assert "未发现符合条件的股票" in result

    def test_format_with_signals(self):
        signals = [
            PremiumSignal(
                symbol="600000",
                name="测试股票",
                signal_type="尾盘拉升",
                score=80.0,
                current_price=10.5,
                entry_price=10.3,
                stop_loss=9.8,
                take_profit_1=11.0,
                take_profit_2=11.5,
                reasons=("涨幅适中",),
                risks=(),
                confidence=0.8,
                holding_days=2,
                expected_return=6.8,
            ),
        ]
        result = format_closing_signals(signals)
        assert "尾盘溢价策略推荐" in result
        assert "600000" in result
        assert "测试股票" in result
        assert "操作建议" in result
