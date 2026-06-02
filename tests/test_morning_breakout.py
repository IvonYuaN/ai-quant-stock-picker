from __future__ import annotations

import numpy as np
import pandas as pd

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.morning_breakout import (
    BreakoutSignal,
    MorningBreakoutStrategy,
    format_morning_signals,
)


def _make_df(days=30, base_price=10.0, seed=42, final_change_pct=6.0):
    np.random.seed(seed)
    dates = pd.date_range("2025-01-01", periods=days, freq="B", tz="Asia/Shanghai")
    trend = np.linspace(base_price, base_price * 1.2, days)
    noise = np.random.randn(days) * 0.003
    prices = trend * (1 + noise)
    last_prev = prices[-2]
    prices[-1] = last_prev * (1 + final_change_pct / 100)

    base_vol = 1_000_000
    volumes = np.full(days, base_vol, dtype=float)
    volumes[-1] = base_vol * 3.0

    return pd.DataFrame(
        {
            "date": dates,
            "open": prices * (1 + np.random.randn(days) * 0.005),
            "high": prices * (1 + abs(np.random.randn(days) * 0.01)),
            "low": prices * (1 - abs(np.random.randn(days) * 0.01)),
            "close": prices,
            "volume": volumes,
            "name": ["测试股票"] * days,
        }
    )


class TestMorningBreakoutInit:
    def test_init_default(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)
        assert strategy.id == "morning_breakout"
        assert strategy.name == "morning_breakout"
        assert strategy.hypothesis != ""
        assert "stable_bull" in strategy.regime_required
        assert "volatile_bull" in strategy.regime_required

    def test_init_with_thresholds(self):
        from aqsp.strategies.thresholds import load_thresholds

        thresholds = load_thresholds()
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config, thresholds=thresholds)
        assert strategy.mb.min_change_pct == 5.0
        assert strategy.mb.min_score == 60.0


class TestAnalyzePreMarket:
    def test_returns_signals_when_data_valid(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        df = _make_df(days=30, final_change_pct=6.0)
        signals = strategy.analyze_pre_market({"600000": df})

        assert isinstance(signals, list)
        assert len(signals) > 0
        assert all(isinstance(s, BreakoutSignal) for s in signals)

    def test_signal_has_required_fields(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        df = _make_df(days=30, final_change_pct=6.0)
        signals = strategy.analyze_pre_market({"600000": df})

        assert len(signals) > 0
        signal = signals[0]
        assert signal.symbol == "600000"
        assert isinstance(signal.name, str)
        assert 0 <= signal.score <= 100
        assert 0 <= signal.confidence <= 1
        assert signal.current_price > 0
        assert signal.target_price > 0
        assert signal.stop_loss > 0
        assert isinstance(signal.signal_type, str)
        assert isinstance(signal.reasons, tuple)
        assert isinstance(signal.risks, tuple)
        assert isinstance(signal.entry_time, str)
        assert 0 < signal.position_pct <= 1

    def test_returns_empty_list_when_data_empty(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        signals = strategy.analyze_pre_market({"600000": pd.DataFrame()})
        assert signals == []

    def test_returns_empty_list_when_none_data(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        signals = strategy.analyze_pre_market({"600000": None})
        assert signals == []

    def test_filters_signals_below_min_score(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        df = _make_df(days=30, base_price=10.0, final_change_pct=5.1)
        np.random.seed(42)
        df["volume"] = 1_000_000.0

        signals = strategy.analyze_pre_market({"600000": df})

        for signal in signals:
            assert signal.score >= strategy.mb.min_score

    def test_filters_when_change_below_threshold(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        df = _make_df(days=30, final_change_pct=1.0)
        signals = strategy.analyze_pre_market({"600000": df})
        assert signals == []

    def test_multiple_symbols(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        df1 = _make_df(days=30, final_change_pct=6.0, seed=42)
        df2 = _make_df(days=30, base_price=20.0, final_change_pct=8.0, seed=43)
        signals = strategy.analyze_pre_market({"600000": df1, "600001": df2})

        assert isinstance(signals, list)
        symbols = {s.symbol for s in signals}
        assert symbols <= {"600000", "600001"}

    def test_signals_sorted_by_score_desc(self):
        config = StrategyConfig(name="morning_breakout")
        strategy = MorningBreakoutStrategy(config)

        df1 = _make_df(days=30, final_change_pct=6.0, seed=42)
        df2 = _make_df(days=30, base_price=20.0, final_change_pct=8.0, seed=43)
        signals = strategy.analyze_pre_market({"600000": df1, "600001": df2})

        if len(signals) >= 2:
            scores = [s.score for s in signals]
            assert scores == sorted(scores, reverse=True)


class TestFormatMorningSignals:
    def test_returns_string(self):
        result = format_morning_signals([])
        assert isinstance(result, str)

    def test_empty_signals_message(self):
        result = format_morning_signals([])
        assert "未发现符合条件的股票" in result

    def test_format_with_signals(self):
        signals = [
            BreakoutSignal(
                symbol="600000",
                name="测试股票",
                signal_type="首板打板",
                score=75.0,
                current_price=10.5,
                target_price=11.0,
                stop_loss=9.8,
                reasons=("涨幅>5%",),
                risks=(),
                confidence=0.7,
                entry_time="09:35 观察后入场",
                position_pct=0.2,
            ),
        ]
        result = format_morning_signals(signals)
        assert "早盘打板策略推荐" in result
        assert "600000" in result
        assert "测试股票" in result
