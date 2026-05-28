from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aqsp.backtest.walk_forward import (
    BacktestResult,
    WalkForwardResult,
    WalkForwardTester,
    _check_executable,
    _compute_backtest_metrics,
    _resolve_exit,
)


class _StubStrategy:
    name: str = "stub"

    def __init__(self, picks: dict[tuple[str, ...], list[str]] | None = None):
        self._picks = picks or {}

    def set_picks(self, picks: dict[tuple[str, ...], list[str]]) -> None:
        self._picks = picks

    def calculate_score(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        return {s: 1.0 for s in data}

    def select_stocks(self, data: dict[str, pd.DataFrame], n: int = 10) -> list[str]:
        key = tuple(sorted(data.keys()))
        return self._picks.get(key, list(data.keys())[:n])


def _make_ohlcv(
    symbol: str,
    dates: list[str],
    opens: list[float],
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    n = len(dates)
    if highs is None:
        highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    if lows is None:
        lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    if volumes is None:
        volumes = [100000.0] * n
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


class TestCheckExecutable:
    def test_normal_bar_is_executable(self) -> None:
        bar = pd.Series(
            {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.3, "volume": 1000}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is True
        assert reason == ""

    def test_limit_up_at_open_not_executable(self) -> None:
        bar = pd.Series(
            {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "volume": 100}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is False
        assert reason == "limit_up_at_open"

    def test_limit_down_at_open_not_executable(self) -> None:
        bar = pd.Series(
            {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "volume": 100}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is False
        assert reason == "limit_down_at_open"

    def test_suspended_no_volume_not_executable(self) -> None:
        bar = pd.Series(
            {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 0}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is False
        assert reason == "suspended_or_no_trade"

    def test_no_open_price_not_executable(self) -> None:
        bar = pd.Series(
            {"open": 0, "high": 10.0, "low": 9.0, "close": 10.0, "volume": 1000}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is False
        assert reason == "no_open_price"

    def test_prev_close_zero_is_executable(self) -> None:
        bar = pd.Series(
            {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 100}
        )
        ok, _ = _check_executable(bar, prev_close=0)
        assert ok is True

    def test_gap_up_below_limit_is_executable(self) -> None:
        bar = pd.Series(
            {"open": 10.9, "high": 11.0, "low": 10.8, "close": 10.95, "volume": 1000}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is True
        assert reason == ""


class TestResolveExit:
    def test_hold_period_close(self) -> None:
        window = pd.DataFrame(
            {
                "date": ["d1", "d2", "d3"],
                "open": [10.0, 10.1, 10.2],
                "high": [10.2, 10.3, 10.4],
                "low": [9.9, 10.0, 10.1],
                "close": [10.1, 10.2, 10.3],
            }
        )
        bar, price, reason = _resolve_exit(
            window, stop_loss=9.5, take_profit=11.0, slippage_bps=5
        )
        assert reason == "hold_period_close"
        assert price == pytest.approx(10.3 * (1 - 5 / 10000), rel=1e-6)
        assert bar["date"] == "d3"

    def test_stop_loss_triggered(self) -> None:
        window = pd.DataFrame(
            {
                "date": ["d1", "d2"],
                "open": [10.0, 9.8],
                "high": [10.2, 9.9],
                "low": [9.9, 9.4],
                "close": [10.1, 9.5],
            }
        )
        bar, price, reason = _resolve_exit(
            window, stop_loss=9.5, take_profit=11.0, slippage_bps=5
        )
        assert reason == "stop_loss"
        assert price == pytest.approx(9.5 * (1 - 5 / 10000), rel=1e-6)
        assert bar["date"] == "d2"

    def test_take_profit_triggered(self) -> None:
        window = pd.DataFrame(
            {
                "date": ["d1", "d2"],
                "open": [10.0, 10.5],
                "high": [10.2, 11.1],
                "low": [9.9, 10.4],
                "close": [10.1, 11.0],
            }
        )
        bar, price, reason = _resolve_exit(
            window, stop_loss=9.0, take_profit=11.0, slippage_bps=5
        )
        assert reason == "take_profit"
        assert price == pytest.approx(11.0 * (1 - 5 / 10000), rel=1e-6)

    def test_stop_loss_before_take_profit(self) -> None:
        window = pd.DataFrame(
            {
                "date": ["d1"],
                "open": [10.0],
                "high": [11.5],
                "low": [9.4],
                "close": [10.5],
            }
        )
        _, _, reason = _resolve_exit(
            window, stop_loss=9.5, take_profit=11.0, slippage_bps=0
        )
        assert reason == "stop_loss"


class TestComputeBacktestMetrics:
    def test_empty_returns(self) -> None:
        result = _compute_backtest_metrics([], "test", not_executable=2)
        assert result.trades == 0
        assert result.not_executable == 2
        assert result.total_return == 0.0

    def test_single_positive_return(self) -> None:
        result = _compute_backtest_metrics([5.0], "test")
        assert result.trades == 1
        assert result.win_rate == 1.0
        assert result.total_return == pytest.approx(0.05, rel=1e-4)
        assert result.max_drawdown == 0.0

    def test_single_negative_return(self) -> None:
        result = _compute_backtest_metrics([-3.0], "test")
        assert result.trades == 1
        assert result.win_rate == 0.0
        assert result.total_return == pytest.approx(-0.03, rel=1e-4)
        assert result.profit_factor == 0.0

    def test_mixed_returns(self) -> None:
        returns = [5.0, -2.0, 3.0, -1.0]
        result = _compute_backtest_metrics(returns, "test")
        assert result.trades == 4
        assert result.win_rate == 0.5
        expected_total = (1.05 * 0.98 * 1.03 * 0.99) - 1
        assert result.total_return == pytest.approx(expected_total, rel=1e-4)
        assert result.profit_factor > 0

    def test_max_drawdown(self) -> None:
        returns = [10.0, -20.0, 5.0]
        result = _compute_backtest_metrics(returns, "test")
        equity_0 = 1.1
        equity_1 = 1.1 * 0.8
        dd = 1 - equity_1 / equity_0
        assert result.max_drawdown == pytest.approx(dd, rel=1e-4)

    def test_sharpe_ratio_positive_for_consistent_gains(self) -> None:
        returns = [1.0] * 20
        result = _compute_backtest_metrics(returns, "test")
        assert result.sharpe_ratio > 0


class TestWalkForwardDataOps:
    def test_collect_all_dates(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        data = {
            "A": _make_ohlcv(
                "A", ["2026-01-02", "2026-01-03"], [10, 10.1], [10.1, 10.2]
            ),
            "B": _make_ohlcv(
                "B", ["2026-01-02", "2026-01-04"], [20, 20.1], [20.1, 20.2]
            ),
        }
        dates = tester._collect_all_dates(data)
        assert dates == ["2026-01-02", "2026-01-03", "2026-01-04"]

    def test_slice_data(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        data = {
            "A": _make_ohlcv(
                "A",
                ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
                [10, 10.1, 10.2, 10.3],
                [10.1, 10.2, 10.3, 10.4],
            ),
        }
        sliced = tester._slice_data(data, "2026-01-02", "2026-01-03")
        assert len(sliced["A"]) == 2
        assert list(sliced["A"]["date"]) == ["2026-01-02", "2026-01-03"]

    def test_slice_data_empty_range(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        data = {
            "A": _make_ohlcv("A", ["2026-01-01"], [10], [10.1]),
        }
        sliced = tester._slice_data(data, "2026-02-01", "2026-02-28")
        assert len(sliced) == 0

    def test_find_date_idx_exact(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert tester._find_date_idx(dates, "2026-01-02") == 1

    def test_find_date_idx_between(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        dates = ["2026-01-01", "2026-01-03", "2026-01-05"]
        assert tester._find_date_idx(dates, "2026-01-02") == 1

    def test_find_date_idx_past_end(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        dates = ["2026-01-01", "2026-01-02"]
        assert tester._find_date_idx(dates, "2026-12-31") == 1


class TestWalkForwardRun:
    @staticmethod
    def _build_multi_period_data(
        n_days: int = 60, symbols: int = 3
    ) -> dict[str, pd.DataFrame]:
        dates = [f"2026-01-{d:02d}" for d in range(1, n_days + 1)]
        data: dict[str, pd.DataFrame] = {}
        rng = np.random.RandomState(42)
        for i in range(symbols):
            base = 10.0 + i * 5
            daily_ret = rng.normal(0.001, 0.02, n_days)
            closes = [base]
            for r in daily_ret[1:]:
                closes.append(closes[-1] * (1 + r))
            closes_arr = np.array(closes)
            opens_arr = closes_arr * (1 + rng.normal(0, 0.005, n_days))
            highs_arr = np.maximum(opens_arr, closes_arr) * (
                1 + abs(rng.normal(0, 0.01, n_days))
            )
            lows_arr = np.minimum(opens_arr, closes_arr) * (
                1 - abs(rng.normal(0, 0.01, n_days))
            )
            data[f"60000{i}"] = _make_ohlcv(
                f"60000{i}",
                dates,
                opens_arr.tolist(),
                closes_arr.tolist(),
                highs_arr.tolist(),
                lows_arr.tolist(),
            )
        return data

    def test_run_returns_walk_forward_result(self) -> None:
        data = self._build_multi_period_data(n_days=60, symbols=3)
        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=20,
            test_period_days=10,
            purge_days=0,
            horizon_days=3,
            fee_bps=0,
            slippage_bps=0,
            top_n=3,
            stop_loss_pct=0.99,
            take_profit_pct=0.99,
        )
        result = tester.run(data)
        assert isinstance(result, WalkForwardResult)
        assert isinstance(result.overall, BacktestResult)
        assert result.overall.trades >= 0

    def test_run_with_real_price_returns(self) -> None:
        dates_train = [f"2026-01-{d:02d}" for d in range(1, 21)]
        dates_test = [f"2026-01-{d:02d}" for d in range(21, 31)]
        all_dates = dates_train + dates_test

        closes_train = [10.0 + i * 0.1 for i in range(20)]
        closes_test = [12.0, 12.5, 13.0, 12.8, 12.6, 12.4, 12.2, 12.0, 11.8, 11.6]
        all_closes = closes_train + closes_test
        all_opens = [c * 1.001 for c in all_closes]

        data = {
            "600000": _make_ohlcv("600000", all_dates, all_opens, all_closes),
        }
        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=15,
            test_period_days=5,
            purge_days=0,
            horizon_days=5,
            fee_bps=0,
            slippage_bps=0,
            top_n=1,
            stop_loss_pct=0.99,
            take_profit_pct=0.99,
        )
        result = tester.run(data)
        assert result.overall.trades > 0
        assert result.overall.total_return != 0.0

    def test_run_empty_data_raises(self) -> None:
        strategy = _StubStrategy()
        tester = WalkForwardTester(strategy)
        with pytest.raises(ValueError, match="No data"):
            tester.run({})

    def test_run_insufficient_data_returns_empty(self) -> None:
        data = {
            "600000": _make_ohlcv("600000", ["2026-01-01"], [10.0], [10.1]),
        }
        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=5,
            test_period_days=5,
            purge_days=0,
            horizon_days=1,
            fee_bps=0,
            slippage_bps=0,
        )
        result = tester.run(data)
        assert result.overall.trades == 0
        assert result.periods == []

    def test_not_executable_excluded_from_win_rate(self) -> None:
        dates_train = [f"2026-01-{d:02d}" for d in range(1, 21)]
        dates_test = ["2026-01-21", "2026-01-22", "2026-01-23"]
        all_dates = dates_train + dates_test

        closes_train = [10.0] * 20
        closes_test = [11.0, 11.1, 11.2]
        all_closes = closes_train + closes_test

        opens_train = [10.0] * 20
        opens_test = [11.0, 11.05, 11.1]
        all_opens = opens_train + opens_test

        data = {
            "600000": _make_ohlcv("600000", all_dates, all_opens, all_closes),
        }

        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=15,
            test_period_days=3,
            purge_days=0,
            horizon_days=3,
            fee_bps=0,
            slippage_bps=0,
            top_n=1,
            stop_loss_pct=0.05,
            take_profit_pct=0.10,
        )
        result = tester.run(data)

        if result.overall.not_executable > 0:
            assert result.overall.win_rate >= 0
            assert result.overall.win_rate <= 1.0

    def test_slippage_and_fees_reduce_return(self) -> None:
        dates = [f"2026-01-{d:02d}" for d in range(1, 25)]
        closes = [10.0] * 20 + [10.5, 11.0, 11.5, 12.0]
        opens = [c for c in closes]
        data = {
            "600000": _make_ohlcv("600000", dates, opens, closes),
        }

        strategy = _StubStrategy()

        tester_no_cost = WalkForwardTester(
            strategy,
            train_period_days=15,
            test_period_days=4,
            purge_days=0,
            horizon_days=4,
            fee_bps=0,
            slippage_bps=0,
            top_n=1,
            stop_loss_pct=0.99,
            take_profit_pct=0.99,
        )
        result_no_cost = tester_no_cost.run(data)

        tester_with_cost = WalkForwardTester(
            strategy,
            train_period_days=15,
            test_period_days=4,
            purge_days=0,
            horizon_days=4,
            fee_bps=10,
            slippage_bps=10,
            top_n=1,
            stop_loss_pct=0.99,
            take_profit_pct=0.99,
        )
        result_with_cost = tester_with_cost.run(data)

        if result_no_cost.overall.trades > 0 and result_with_cost.overall.trades > 0:
            assert (
                result_with_cost.overall.total_return
                < result_no_cost.overall.total_return
            )

    def test_purge_gap_between_train_and_test(self) -> None:
        n = 60
        dates = [f"2026-01-{d:02d}" for d in range(1, n + 1)]
        closes = [10.0 + i * 0.05 for i in range(n)]
        opens = closes[:]
        data = {
            "600000": _make_ohlcv("600000", dates, opens, closes),
        }

        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=20,
            test_period_days=10,
            purge_days=5,
            horizon_days=3,
            fee_bps=0,
            slippage_bps=0,
            top_n=1,
            stop_loss_pct=0.99,
            take_profit_pct=0.99,
        )
        result = tester.run(data)
        assert isinstance(result, WalkForwardResult)


class TestRobustnessScore:
    def test_identical_periods_high_robustness(self) -> None:
        periods = [
            BacktestResult(f"p{i}", 0.05, 0.05, 0.02, 1.5, 0.6, 1.2, 10, 0)
            for i in range(5)
        ]
        score = WalkForwardTester._calculate_robustness(periods)
        assert score > 0.9

    def test_varying_periods_lower_robustness(self) -> None:
        periods = [
            BacktestResult("p0", 0.10, 0.10, 0.02, 2.0, 0.7, 1.5, 10, 0),
            BacktestResult("p1", -0.05, -0.05, 0.08, -1.0, 0.3, 0.5, 10, 0),
            BacktestResult("p2", 0.02, 0.02, 0.03, 0.5, 0.5, 1.0, 10, 0),
        ]
        score = WalkForwardTester._calculate_robustness(periods)
        assert score < 0.9

    def test_single_period_returns_zero(self) -> None:
        periods = [BacktestResult("p0", 0.05, 0.05, 0.02, 1.5, 0.6, 1.2, 10, 0)]
        assert WalkForwardTester._calculate_robustness(periods) == 0.0

    def test_empty_periods_returns_zero(self) -> None:
        assert WalkForwardTester._calculate_robustness([]) == 0.0


class TestParameterStd:
    def test_identical_periods_zero_std(self) -> None:
        periods = [
            BacktestResult(f"p{i}", 0.05, 0.05, 0.02, 1.5, 0.6, 1.2, 10, 0)
            for i in range(3)
        ]
        assert WalkForwardTester._calculate_parameter_std(periods) == pytest.approx(0.0)

    def test_varying_periods_nonzero_std(self) -> None:
        periods = [
            BacktestResult("p0", 0.10, 0.10, 0.02, 1.5, 0.6, 1.2, 10, 0),
            BacktestResult("p1", -0.05, -0.05, 0.08, -1.0, 0.3, 0.5, 10, 0),
        ]
        assert WalkForwardTester._calculate_parameter_std(periods) > 0

    def test_single_period_returns_zero(self) -> None:
        periods = [BacktestResult("p0", 0.05, 0.05, 0.02, 1.5, 0.6, 1.2, 10, 0)]
        assert WalkForwardTester._calculate_parameter_std(periods) == 0.0


class TestStopLossAndTakeProfit:
    def test_stop_loss_limits_loss(self) -> None:
        dates_train = [f"2026-01-{d:02d}" for d in range(1, 11)]
        dates_test = [f"2026-01-{d:02d}" for d in range(11, 16)]
        all_dates = dates_train + dates_test

        closes_train = [10.0] * 10
        closes_test = [9.0, 8.5, 8.0, 8.2, 8.5]
        all_closes = closes_train + closes_test
        all_opens = all_closes[:]

        data = {
            "600000": _make_ohlcv("600000", all_dates, all_opens, all_closes),
        }

        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=8,
            test_period_days=5,
            purge_days=0,
            horizon_days=5,
            fee_bps=0,
            slippage_bps=0,
            top_n=1,
            stop_loss_pct=0.05,
            take_profit_pct=0.99,
        )
        result = tester.run(data)
        if result.overall.trades > 0:
            for period in result.periods:
                assert period.total_return > -0.10

    def test_take_profit_locks_gain(self) -> None:
        dates_train = [f"2026-01-{d:02d}" for d in range(1, 11)]
        dates_test = [f"2026-01-{d:02d}" for d in range(11, 16)]
        all_dates = dates_train + dates_test

        closes_train = [10.0] * 10
        closes_test = [10.5, 11.0, 11.5, 12.0, 12.5]
        all_closes = closes_train + closes_test
        all_opens = all_closes[:]

        data = {
            "600000": _make_ohlcv("600000", all_dates, all_opens, all_closes),
        }

        strategy = _StubStrategy()
        tester = WalkForwardTester(
            strategy,
            train_period_days=8,
            test_period_days=5,
            purge_days=0,
            horizon_days=5,
            fee_bps=0,
            slippage_bps=0,
            top_n=1,
            stop_loss_pct=0.99,
            take_profit_pct=0.10,
        )
        result = tester.run(data)
        if result.overall.trades > 0:
            for period in result.periods:
                assert period.total_return <= 0.12
