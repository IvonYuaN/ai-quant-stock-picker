from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from aqsp.backtest.walk_forward import (
    BacktestResult,
    TradeResult,
    WalkForwardDiagnostics,
    WalkForwardResult,
    WalkForwardTester,
    _check_executable,
    _compute_backtest_metrics,
    _norm_cdf,
    _norm_ppf,
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

    def test_prev_close_zero_is_not_executable(self) -> None:
        bar = pd.Series(
            {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 100}
        )
        ok, reason = _check_executable(bar, prev_close=0)
        assert ok is False
        assert reason == "missing_prev_close"

    def test_gap_up_below_limit_is_executable(self) -> None:
        bar = pd.Series(
            {"open": 10.9, "high": 11.0, "low": 10.8, "close": 10.95, "volume": 1000}
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is True
        assert reason == ""

    def test_real_5pct_limit_up_at_open_not_executable(self) -> None:
        bar = pd.Series(
            {
                "open": 10.5,
                "high": 10.5,
                "low": 10.5,
                "close": 10.5,
                "volume": 1000,
                "limit_up": 10.5,
            }
        )
        ok, reason = _check_executable(bar, prev_close=10.0)
        assert ok is False
        assert reason == "limit_up_at_open"

    def test_real_20pct_non_limit_open_is_executable(self) -> None:
        bar = pd.Series(
            {
                "open": 11.0,
                "high": 11.0,
                "low": 11.0,
                "close": 11.0,
                "volume": 1000,
                "limit_up": 12.0,
            }
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

    def test_sharpe_ratio_zero_for_zero_variance_returns(self) -> None:
        returns = [1.0] * 20
        result = _compute_backtest_metrics(returns, "test")
        assert result.sharpe_ratio == 0.0

    def test_sharpe_ratio_zero_for_zero_variance_losses(self) -> None:
        returns = [-4.15] * 10
        result = _compute_backtest_metrics(returns, "test")
        assert result.sharpe_ratio == 0.0


class TestWalkForwardDataOps:
    def test_resolve_market_regime_uses_training_cutoff_context(
        self, monkeypatch
    ) -> None:
        import aqsp.backtest.walk_forward as walk_forward
        from aqsp.regime.runtime import RuntimeRegimeContext

        seen: dict[str, object] = {}

        def fake_detect(frames, *, benchmark_symbol, thresholds):
            seen["latest"] = max(
                str(value["date"].max()) for value in frames.values()
            )
            seen["benchmark"] = benchmark_symbol
            return RuntimeRegimeContext(
                regime="volatile_bull",
                hmm_regime="bull",
                confidence=0.8,
                annualized_volatility=0.35,
                detector="test",
            )

        monkeypatch.setattr(
            walk_forward, "detect_runtime_regime_context", fake_detect
        )
        tester = WalkForwardTester(strategy=object(), benchmark_symbol="000300")
        frame = pd.DataFrame(
            {
                "date": ["2026-06-25", "2026-06-26"],
                "close": [100.0, 101.0],
            }
        )

        regime, blocked = tester._resolve_market_regime({"000300": frame})

        assert regime == "volatile_bull"
        assert blocked is False
        assert seen == {"latest": "2026-06-26", "benchmark": "000300"}

    def test_walkforward_excludes_benchmark_from_strategy_selection(self, monkeypatch):
        import aqsp.backtest.walk_forward as walk_forward
        from aqsp.regime.runtime import RuntimeRegimeContext

        selected_inputs: list[tuple[str, ...]] = []

        class Strategy:
            def select_stocks(self, data, n=10):
                selected_inputs.append(tuple(sorted(data)))
                return ["600519"]

        monkeypatch.setattr(
            walk_forward,
            "detect_runtime_regime_context",
            lambda *_args, **_kwargs: RuntimeRegimeContext(
                regime="stable_bull",
                hmm_regime="bull",
                confidence=0.8,
                annualized_volatility=0.1,
                detector="test",
            ),
        )
        dates = pd.date_range("2026-01-01", periods=25, freq="B").strftime(
            "%Y-%m-%d"
        ).tolist()
        next_date = pd.date_range("2026-01-01", periods=26, freq="B")[-1]
        tester = WalkForwardTester(strategy=Strategy(), benchmark_symbol="000300")

        tester._run_single_period(
            {
                "000300": _make_ohlcv("000300", dates, [100.0] * 25, [101.0] * 25),
                "600519": _make_ohlcv("600519", dates, [10.0] * 25, [10.1] * 25),
            },
            {
                "600519": _make_ohlcv(
                    "600519",
                    [next_date.strftime("%Y-%m-%d")],
                    [10.1],
                    [10.2],
                )
            },
            dates[-1],
        )

        assert selected_inputs == [("600519",)]

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
        dates = pd.date_range("2026-01-01", periods=n_days, freq="B").strftime(
            "%Y-%m-%d"
        ).tolist()
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
        assert isinstance(result.diagnostics, WalkForwardDiagnostics)
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
        dates = pd.date_range("2026-01-01", periods=n, freq="B").strftime(
            "%Y-%m-%d"
        ).tolist()
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


class TestWalkForwardDiagnostics:
    def test_build_diagnostics_summarizes_failure_sources(self) -> None:
        trades = [
            TradeResult(
                "600000",
                "2026-01-10",
                "2026-01-11",
                "2026-01-13",
                10,
                9,
                -10,
                "stop_loss",
            ),
            TradeResult(
                "600000",
                "2026-01-20",
                "2026-01-21",
                "2026-01-23",
                10,
                9.5,
                -5,
                "hold_period_close",
            ),
            TradeResult(
                "000001",
                "2026-01-10",
                "2026-01-11",
                "2026-01-13",
                10,
                10.5,
                5,
                "hold_period_close",
            ),
            TradeResult(
                "300001",
                "2026-01-10",
                "2026-01-11",
                "2026-01-11",
                0,
                0,
                0,
                "limit_up_at_open",
                executable=False,
            ),
        ]

        diagnostics = WalkForwardTester._build_diagnostics(trades)

        assert diagnostics.total_trades == 4
        assert diagnostics.executable_trades == 3
        assert diagnostics.not_executable == 1
        assert diagnostics.exit_reason_counts == (
            ("hold_period_close", 2),
            ("stop_loss", 1),
        )
        assert diagnostics.not_executable_reason_counts == (("limit_up_at_open", 1),)
        assert diagnostics.worst_symbols[0] == ("600000", 2, -7.5, -15.0)


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


class TestNormalHelpers:
    """Sanity checks on the in-house Φ / Φ⁻¹ helpers used by DSR."""

    def test_norm_cdf_at_zero(self) -> None:
        assert _norm_cdf(0.0) == pytest.approx(0.5, abs=1e-3)

    def test_norm_cdf_at_1_96(self) -> None:
        assert _norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)

    def test_norm_cdf_symmetry(self) -> None:
        for x in (0.5, 1.0, 2.0):
            assert _norm_cdf(x) + _norm_cdf(-x) == pytest.approx(1.0, abs=1e-3)

    def test_norm_ppf_inverse(self) -> None:
        for p in (0.025, 0.25, 0.5, 0.84, 0.975):
            x = _norm_ppf(p)
            assert _norm_cdf(x) == pytest.approx(p, abs=1e-3)


class TestDeflatedSharpe:
    """Regression suite for the rewritten DSR (López de Prado standard form).

    The previous implementation
        ``dsr = psr * (1 - log(2^n - 1) / n)``
    bounded DSR at ≈ 0.3069 for any ``n_trials ≥ 10``, making the
    CONSTITUTION §1.3 #12 gate (``DSR > 1.0``) mathematically unreachable.
    The new form returns the deflated z-statistic (unbounded), so:
      • a strong strategy can clear 1.0;
      • a weak / over-mined strategy lands ≤ 0;
      • inflating ``n_trials`` strictly lowers DSR for the same Sharpe.
    """

    def test_high_sharpe_few_trials_clears_gate(self) -> None:
        """Real strategy: ann. SR=3, only 2 alternatives mined → DSR > 1.0."""
        dsr = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=3.0, n_trials=2, n_obs=252
        )
        assert dsr > 1.0, f"expected DSR > 1.0, got {dsr}"

    def test_borderline_sharpe_many_trials_fails_gate(self) -> None:
        """Modest SR=1.5 ann. with 50 trials → deflated below 0."""
        dsr = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=1.5, n_trials=50, n_obs=252
        )
        assert dsr < 1.0
        assert dsr < 0.5  # falls well below the 1.0 gate

    def test_low_sharpe_many_trials_strongly_negative(self) -> None:
        """SR=0.5 ann. + 200 trials must be deflated to a clearly negative z."""
        dsr = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=0.5, n_trials=200, n_obs=100
        )
        assert dsr < 0.0

    def test_more_trials_lowers_dsr_monotonically(self) -> None:
        """Holding SR / T fixed, increasing n_trials must not increase DSR."""
        sharpe, t = 2.0, 252
        d_few = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=sharpe, n_trials=5, n_obs=t
        )
        d_many = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=sharpe, n_trials=200, n_obs=t
        )
        assert d_few > d_many

    def test_higher_sharpe_raises_dsr_monotonically(self) -> None:
        """Holding n_trials / T fixed, raising SR must not lower DSR."""
        n, t = 10, 252
        d_low = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=1.0, n_trials=n, n_obs=t
        )
        d_high = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=3.0, n_trials=n, n_obs=t
        )
        assert d_high > d_low

    def test_gate_is_reachable_upper_bound(self) -> None:
        """Critical: under realistic inputs the formula MUST be able to
        return values ≥ 1.0. If this ever fails again, the §1.3 #12 gate is
        dead and we are back to the 0.3069 ceiling bug."""
        dsr = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=4.0, n_trials=3, n_obs=500
        )
        assert dsr >= 1.0

    def test_n_trials_one_returns_zero(self) -> None:
        assert (
            WalkForwardTester._calculate_deflated_sharpe(
                sharpe=2.0, n_trials=1, n_obs=252
            )
            == 0.0
        )

    def test_n_obs_one_returns_zero(self) -> None:
        assert (
            WalkForwardTester._calculate_deflated_sharpe(
                sharpe=2.0, n_trials=10, n_obs=1
            )
            == 0.0
        )

    def test_zero_sharpe_below_gate(self) -> None:
        """SR=0 must always fail the gate, regardless of trials/obs."""
        dsr = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=0.0, n_trials=10, n_obs=252
        )
        assert dsr < 1.0

    def test_per_period_sharpe_input(self) -> None:
        """sharpe_is_annualized=False: SR is already per-period.
        Equivalent to passing the same SR × √252 with the default flag."""
        d_per = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=0.2, n_trials=5, n_obs=252, sharpe_is_annualized=False
        )
        d_ann = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=0.2 * np.sqrt(252), n_trials=5, n_obs=252
        )
        assert d_per == pytest.approx(d_ann, abs=1e-3)

    def test_non_normal_kurtosis_lowers_dsr(self) -> None:
        """Fat tails (γ_4 > 3) inflate σ_SR, which must lower DSR."""
        n, t, sr = 10, 252, 2.0
        d_normal = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=sr, n_trials=n, n_obs=t, kurtosis=3.0
        )
        d_fat_tail = WalkForwardTester._calculate_deflated_sharpe(
            sharpe=sr, n_trials=n, n_obs=t, kurtosis=10.0
        )
        assert d_fat_tail < d_normal


def test_walkforward_keeps_zero_trade_periods_for_cscv_stability(monkeypatch) -> None:
    strategy = _StubStrategy()
    tester = WalkForwardTester(
        strategy=strategy,
        train_period_days=60,
        test_period_days=20,
        purge_days=3,
    )
    data = {
        "600519": pd.DataFrame(
            [
                {
                    "date": "2024-01-01",
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                }
            ]
        )
    }

    monkeypatch.setattr(
        tester,
        "_collect_all_dates",
        lambda _data: [f"2024-01-{day:02d}" for day in range(1, 121)],
    )
    monkeypatch.setattr(tester, "_slice_data", lambda _data, _start, _end: data)
    monkeypatch.setattr(tester, "_run_single_period", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        tester,
        "_calculate_robustness",
        lambda periods: float(len(periods)),
    )
    monkeypatch.setattr(
        tester,
        "_calculate_parameter_std",
        lambda periods: float(len(periods)),
    )
    monkeypatch.setattr(
        tester,
        "_calculate_deflated_sharpe",
        lambda sharpe, n_trials, n_obs, **_kwargs: float(n_obs),
    )
    monkeypatch.setattr(
        tester,
        "_calculate_pbo",
        lambda periods: float(len(periods)),
    )
    monkeypatch.setattr(
        tester,
        "_build_diagnostics",
        lambda trades: WalkForwardDiagnostics(
            total_trades=len(trades),
            executable_trades=0,
            not_executable=0,
            worst_symbols=(),
            exit_reason_counts=(),
            not_executable_reason_counts=(),
        ),
    )

    result = tester.run(data, start_date="2024-01-01", end_date="2024-04-30")

    assert len(result.periods) > 0
    assert all(period.trades == 0 for period in result.periods)


def test_walkforward_planned_period_count_matches_window_math() -> None:
    tester = WalkForwardTester(
        strategy=_StubStrategy(),
        train_period_days=120,
        test_period_days=30,
        purge_days=5,
    )

    assert tester._planned_period_count(0, 707) == 19


def test_walkforward_streaming_matches_full_run_when_data_is_loaded_in_batches() -> None:
    dates = [
        (date(2024, 1, 1) + timedelta(days=day)).isoformat()
        for day in range(40)
    ]
    frames = {
        symbol: _make_ohlcv(
            symbol,
            dates,
            [10.0 + offset + day * 0.01 for day in range(40)],
            [10.1 + offset + day * 0.01 for day in range(40)],
        )
        for offset, symbol in enumerate(("000001", "000002", "000003", "000004"))
    }

    class _PriceRankStrategy:
        name = "price_rank"

        def select_stocks(
            self,
            data: dict[str, pd.DataFrame],
            n: int = 2,
            regime: str = "unknown",
        ) -> list[str]:
            del regime
            return sorted(
                data,
                key=lambda symbol: float(data[symbol].iloc[-1]["close"]),
                reverse=True,
            )[:n]

    full_tester = WalkForwardTester(
        _PriceRankStrategy(),
        train_period_days=10,
        test_period_days=5,
        purge_days=2,
        horizon_days=2,
        top_n=2,
        n_variants=3,
    )
    streaming_tester = WalkForwardTester(
        _PriceRankStrategy(),
        train_period_days=10,
        test_period_days=5,
        purge_days=2,
        horizon_days=2,
        top_n=2,
        n_variants=3,
    )
    full = full_tester.run(frames, start_date=dates[0], end_date=dates[-1])

    calls: list[int] = []

    def load_batch(
        symbols: list[str], start: str, end: str
    ) -> dict[str, pd.DataFrame]:
        calls.append(len(symbols))
        return {
            symbol: frames[symbol].loc[
                (frames[symbol]["date"] >= start) & (frames[symbol]["date"] <= end)
            ].copy()
            for symbol in symbols
        }

    streamed = streaming_tester.run_streaming(
        list(frames),
        load_batch,
        dates,
        start_date=dates[0],
        end_date=dates[-1],
        batch_size=2,
        min_frame_rows=1,
    )

    assert calls
    assert max(calls) <= 2
    assert streamed.periods == full.periods
    assert streamed.overall == full.overall
    assert streamed.deflated_sharpe == full.deflated_sharpe
    assert streamed.pbo == full.pbo
    assert streamed.diagnostics == full.diagnostics


def test_walkforward_streaming_uses_fixed_benchmark_for_regime_detection(
    monkeypatch,
) -> None:
    dates = [
        (date(2024, 1, 1) + timedelta(days=day)).isoformat()
        for day in range(20)
    ]
    frames = {
        symbol: _make_ohlcv(
            symbol,
            dates,
            [10.0 + offset] * 20,
            [10.1 + offset] * 20,
        )
        for offset, symbol in enumerate(("000001", "000002"))
    }
    benchmark = _make_ohlcv(
        "000300",
        dates,
        [100.0] * 20,
        [100.1] * 20,
    )

    class _Strategy:
        def select_stocks(
            self,
            data: dict[str, pd.DataFrame],
            n: int = 1,
            regime: str = "unknown",
        ) -> list[str]:
            del regime
            return list(data)[:n]

    tester = WalkForwardTester(
        _Strategy(),
        train_period_days=5,
        test_period_days=3,
        purge_days=1,
        horizon_days=2,
        top_n=1,
        benchmark_symbol="000300",
    )
    seen: list[set[str]] = []

    def resolve_regime(data, *, as_of=None):
        del as_of
        seen.append(set(data))
        return "aggressive_bull", False

    monkeypatch.setattr(tester, "_resolve_market_regime", resolve_regime)

    tester.run_streaming(
        list(frames),
        lambda symbols, _start, _end: {symbol: frames[symbol] for symbol in symbols},
        dates,
        start_date=dates[0],
        end_date=dates[-1],
        fixed_frames={"000300": benchmark},
        batch_size=1,
        min_frame_rows=1,
    )

    assert seen
    assert all(symbols == {"000300"} for symbols in seen)
