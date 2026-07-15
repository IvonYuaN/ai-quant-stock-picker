from __future__ import annotations

import bisect
import inspect
import os
from dataclasses import dataclass
from collections.abc import Callable, Mapping, Sequence
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    from scipy import stats as _scipy_stats
except (
    ImportError
):  # scipy is optional; DSR will fall back to a normal CDF approximation
    _scipy_stats = None

from aqsp.ledger.base import _check_executable as _ledger_check_executable
from aqsp.backtest.audit import validate_backtest_frame
from aqsp.regime.runtime import detect_runtime_regime_context
from aqsp.regime.strategy_mixer import canonicalize_regime
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import RiskThresholds


DEFAULT_STREAM_BATCH_SIZE = 200
MIN_STREAM_FRAME_ROWS = 100
FrameBatchLoader = Callable[[list[str], str, str], Mapping[str, pd.DataFrame]]


def _chunks(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


def _norm_cdf(x: float) -> float:
    """Standard normal CDF. Uses scipy when available, else A&S 26.2.17."""
    if _scipy_stats is not None:
        return float(_scipy_stats.norm.cdf(x))
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.39894228 * float(np.exp(-x * x / 2.0))
    poly = t * (
        0.319381530
        + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    cdf_abs = 1.0 - d * poly
    return float(cdf_abs if x >= 0 else 1.0 - cdf_abs)


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF. Uses scipy when available, else
    Beasley-Springer-Moro / Acklam-style rational approximation."""
    if _scipy_stats is not None:
        return float(_scipy_stats.norm.ppf(p))
    # Acklam (2003) approximation, abs error < 1.15e-9 for p in (0,1)
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = float(np.sqrt(-2.0 * np.log(p)))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = float(np.sqrt(-2.0 * np.log(1.0 - p)))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


@dataclass(frozen=True)
class TradeResult:
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str
    market_regime: str = "unknown"
    executable: bool = True


@dataclass(frozen=True)
class BacktestResult:
    period: str
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_factor: float
    trades: int
    not_executable: int


@dataclass(frozen=True)
class WalkForwardDiagnostics:
    total_trades: int
    executable_trades: int
    not_executable: int
    worst_symbols: tuple[tuple[str, int, float, float], ...]
    exit_reason_counts: tuple[tuple[str, int], ...]
    not_executable_reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class WalkForwardResult:
    periods: List[BacktestResult]
    overall: BacktestResult
    robustness_score: float
    parameter_std: float
    deflated_sharpe: float = 0.0
    pbo: float = 0.0
    regime_winrates: Dict[str, float] = None
    diagnostics: WalkForwardDiagnostics | None = None

    def __post_init__(self):
        if self.regime_winrates is None:
            object.__setattr__(self, "regime_winrates", {})


class WalkForwardTester:
    def __init__(
        self,
        strategy: CompositeStrategy,
        train_period_days: int = 120,
        test_period_days: int = 30,
        purge_days: int = 5,
        horizon_days: int = 3,
        fee_bps: float = 3.0,
        slippage_bps: float = 20.0,
        top_n: int = 10,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        atr_stop_multiplier: float | None = None,
        use_tiered_stop: bool = False,
        n_variants: int = 1,
        benchmark_symbol: str | None = None,
    ):
        self.strategy = strategy
        self.train_period_days = train_period_days
        self.test_period_days = test_period_days
        self.purge_days = purge_days
        self.horizon_days = horizon_days
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.top_n = top_n
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.atr_stop_multiplier = atr_stop_multiplier
        self.use_tiered_stop = use_tiered_stop
        self.n_variants = n_variants
        self.benchmark_symbol = benchmark_symbol

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> WalkForwardResult:
        normalized_data = self._prepare_data_views(data)
        all_dates = self._collect_all_dates(normalized_data)
        if not all_dates:
            raise ValueError("No data available")

        start_idx = (
            0 if start_date is None else self._find_date_idx(all_dates, start_date)
        )
        end_idx = (
            len(all_dates) - 1
            if end_date is None
            else self._find_date_idx(all_dates, end_date)
        )

        periods: list[BacktestResult] = []
        all_trades: list[TradeResult] = []

        step = self.test_period_days
        i = start_idx + self.train_period_days + self.purge_days
        total_periods = self._planned_period_count(start_idx, end_idx)
        progress_enabled = str(
            os.getenv("AQSP_WALKFORWARD_PROGRESS", "true")
        ).strip().lower() in {"1", "true", "yes", "on"}
        period_index = 0
        while i + step <= end_idx:
            train_end_idx = i - self.purge_days - 1
            if train_end_idx < start_idx:
                i += step
                continue
            period_index += 1

            train_start = all_dates[start_idx]
            train_end = all_dates[train_end_idx]
            test_start = all_dates[i]
            test_end = all_dates[min(i + step - 1, end_idx)]
            if progress_enabled:
                print(
                    f"walkforward period {period_index}/{total_periods}: "
                    f"train={train_start}..{train_end} test={test_start}..{test_end}"
                )

            train_data = self._slice_data(normalized_data, train_start, train_end)
            test_data = self._slice_data(normalized_data, test_start, test_end)

            trades = self._run_single_period(train_data, test_data, train_end)
            all_trades.extend(trades)

            executable = [t for t in trades if t.executable]
            returns = [t.return_pct for t in executable]
            period_result = _compute_backtest_metrics(
                returns,
                f"{test_start} to {test_end}",
                len(trades) - len(executable),
            )
            periods.append(period_result)

            i += step

        all_executable = [t for t in all_trades if t.executable]
        all_returns = [t.return_pct for t in all_executable]
        not_exec_count = sum(1 for t in all_trades if not t.executable)
        overall = _compute_backtest_metrics(all_returns, "Overall", not_exec_count)
        robustness = self._calculate_robustness(periods)

        n_trials = self.n_variants
        dsr = self._calculate_deflated_sharpe(
            overall.sharpe_ratio, n_trials, len(all_returns)
        )
        pbo = self._calculate_pbo(periods)

        regime_winrates_calc: dict[str, list[float]] = {}
        for trade in all_trades:
            if trade.executable:
                regime_winrates_calc.setdefault(trade.market_regime, []).append(
                    1.0 if trade.return_pct > 0 else 0.0
                )
        regime_winrate_dict: dict[str, float] = {}
        for regime, wins in sorted(regime_winrates_calc.items()):
            regime_winrate_dict[regime] = sum(wins) / len(wins)

        return WalkForwardResult(
            periods=periods,
            overall=overall,
            robustness_score=robustness,
            parameter_std=self._calculate_parameter_std(periods),
            deflated_sharpe=dsr,
            pbo=pbo,
            regime_winrates=regime_winrate_dict,
            diagnostics=self._build_diagnostics(all_trades),
        )

    def run_streaming(
        self,
        symbols: Sequence[str],
        load_batch: FrameBatchLoader,
        all_dates: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        *,
        fixed_frames: Mapping[str, pd.DataFrame] | None = None,
        batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
        min_frame_rows: int = MIN_STREAM_FRAME_ROWS,
    ) -> WalkForwardResult:
        """Run walk-forward without retaining the full market in memory.

        ``load_batch`` must return raw, point-in-time-safe frames for exactly the
        requested symbols. Each batch is scored independently and only its local
        top-N frames are retained; the final selection is then reranked across
        that bounded candidate set. This is equivalent to a global top-N for
        per-symbol scoring strategies while keeping the memory bound independent
        of the market size.
        """
        if not symbols:
            raise ValueError("No symbols available for streaming walk-forward")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if min_frame_rows <= 0:
            raise ValueError("min_frame_rows must be positive")

        normalized_dates = sorted({str(value)[:10] for value in all_dates if value})
        if not normalized_dates:
            raise ValueError("No dates available for streaming walk-forward")
        start_idx = (
            0
            if start_date is None
            else self._find_date_idx(normalized_dates, str(start_date)[:10])
        )
        end_idx = (
            len(normalized_dates) - 1
            if end_date is None
            else self._find_date_idx(normalized_dates, str(end_date)[:10])
        )
        if end_idx <= start_idx:
            raise ValueError("Streaming walk-forward date range is empty")

        fixed_data = self._prepare_data_views(dict(fixed_frames or {}))
        ordered_symbols = list(dict.fromkeys(str(symbol) for symbol in symbols))
        if self.benchmark_symbol:
            ordered_symbols = [
                symbol
                for symbol in ordered_symbols
                if symbol != self.benchmark_symbol
            ]
        if not ordered_symbols:
            raise ValueError("No non-benchmark symbols available for streaming run")

        periods: list[BacktestResult] = []
        all_trades: list[TradeResult] = []
        step = self.test_period_days
        cursor = start_idx + self.train_period_days + self.purge_days
        total_periods = self._planned_period_count(start_idx, end_idx)
        progress_enabled = str(
            os.getenv("AQSP_WALKFORWARD_PROGRESS", "true")
        ).strip().lower() in {"1", "true", "yes", "on"}
        period_index = 0

        while cursor + step <= end_idx:
            train_end_idx = cursor - self.purge_days - 1
            if train_end_idx < start_idx:
                cursor += step
                continue
            period_index += 1
            train_start = normalized_dates[start_idx]
            train_end = normalized_dates[train_end_idx]
            test_start = normalized_dates[cursor]
            test_end = normalized_dates[min(cursor + step - 1, end_idx)]
            if progress_enabled:
                print(
                    f"walkforward streaming period {period_index}/{total_periods}: "
                    f"train={train_start}..{train_end} test={test_start}..{test_end}"
                )

            fixed_signal = self._slice_data(fixed_data, train_start, train_end)
            if fixed_signal:
                market_regime, regime_is_bear_filter = self._resolve_market_regime(
                    fixed_signal,
                    as_of=train_end,
                )
            else:
                market_returns: list[float] = []
                for chunk in _chunks(ordered_symbols, batch_size):
                    batch = self._prepare_stream_batch(
                        load_batch(chunk, train_start, test_end),
                        min_frame_rows=min_frame_rows,
                    )
                    signal_batch = self._screening_data(
                        self._slice_data(batch, train_start, train_end)
                    )
                    market_returns.extend(
                        self._market_returns_from_frames(signal_batch)
                    )
                market_regime, regime_is_bear_filter = (
                    self._fallback_market_regime_from_returns(market_returns)
                )

            candidate_train: dict[str, pd.DataFrame] = {}
            candidate_test: dict[str, pd.DataFrame] = {}
            candidate_signal: dict[str, pd.DataFrame] = {}
            if not regime_is_bear_filter:
                for chunk in _chunks(ordered_symbols, batch_size):
                    batch = self._prepare_stream_batch(
                        load_batch(chunk, train_start, test_end),
                        min_frame_rows=min_frame_rows,
                    )
                    train_batch = self._slice_data(batch, train_start, train_end)
                    test_batch = self._slice_data(batch, test_start, test_end)
                    signal_batch = self._screening_data(train_batch)
                    if not signal_batch:
                        continue
                    local_selected = self._select_stocks(
                        signal_batch,
                        regime=market_regime,
                    )
                    for symbol in local_selected:
                        if symbol not in signal_batch or symbol not in test_batch:
                            continue
                        candidate_signal[symbol] = signal_batch[symbol]
                        candidate_train[symbol] = train_batch[symbol]
                        candidate_test[symbol] = test_batch[symbol]

                selected = self._select_stocks(
                    candidate_signal,
                    regime=market_regime,
                ) if candidate_signal else []
                trades = self._run_selected_trades(
                    candidate_train,
                    candidate_test,
                    signal_date=train_end,
                    selected=selected,
                    market_regime=market_regime,
                )
            else:
                trades = []

            all_trades.extend(trades)
            executable = [trade for trade in trades if trade.executable]
            periods.append(
                _compute_backtest_metrics(
                    [trade.return_pct for trade in executable],
                    f"{test_start} to {test_end}",
                    len(trades) - len(executable),
                )
            )
            cursor += step

        all_executable = [trade for trade in all_trades if trade.executable]
        all_returns = [trade.return_pct for trade in all_executable]
        overall = _compute_backtest_metrics(
            all_returns,
            "Overall",
            sum(1 for trade in all_trades if not trade.executable),
        )
        regime_winrates: dict[str, list[float]] = {}
        for trade in all_executable:
            regime_winrates.setdefault(trade.market_regime, []).append(
                1.0 if trade.return_pct > 0 else 0.0
            )
        return WalkForwardResult(
            periods=periods,
            overall=overall,
            robustness_score=self._calculate_robustness(periods),
            parameter_std=self._calculate_parameter_std(periods),
            deflated_sharpe=self._calculate_deflated_sharpe(
                overall.sharpe_ratio,
                self.n_variants,
                len(all_returns),
            ),
            pbo=self._calculate_pbo(periods),
            regime_winrates={
                regime: sum(values) / len(values)
                for regime, values in sorted(regime_winrates.items())
            },
            diagnostics=self._build_diagnostics(all_trades),
        )

    def _prepare_stream_batch(
        self,
        data: Mapping[str, pd.DataFrame],
        *,
        min_frame_rows: int,
    ) -> dict[str, pd.DataFrame]:
        normalized = self._prepare_data_views(dict(data))
        prepared: dict[str, pd.DataFrame] = {}
        for symbol, frame in normalized.items():
            if frame is None or frame.empty or len(frame) < min_frame_rows:
                continue
            prepared[str(symbol)] = frame
        return prepared

    @staticmethod
    def _market_returns_from_frames(
        data: Mapping[str, pd.DataFrame],
    ) -> list[float]:
        returns: list[float] = []
        for frame in data.values():
            if len(frame) < 20:
                continue
            recent = frame.tail(20)
            first = float(recent.iloc[0]["close"])
            last = float(recent.iloc[-1]["close"])
            if first > 0:
                returns.append((last - first) / first)
        return returns

    @staticmethod
    def _fallback_market_regime_from_returns(
        market_returns: Sequence[float],
    ) -> tuple[str, bool]:
        if not market_returns:
            return "unknown", False
        average = sum(market_returns) / len(market_returns)
        if average < -0.02:
            return "defensive_bear", True
        if average < -0.005:
            return "defensive_bear", False
        if average < 0.005:
            return "rotation_sideways", False
        return "aggressive_bull", False

    def _planned_period_count(self, start_idx: int, end_idx: int) -> int:
        step = self.test_period_days
        cursor = start_idx + self.train_period_days + self.purge_days
        count = 0
        while cursor + step <= end_idx:
            if cursor - self.purge_days - 1 >= start_idx:
                count += 1
            cursor += step
        return count

    def _prepare_data_views(
        self, data: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        prepared: dict[str, pd.DataFrame] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                continue
            audit = validate_backtest_frame(df, symbol=str(symbol))
            if not audit.ok:
                raise ValueError("; ".join(audit.blockers))
            ordered = df.sort_values("date").reset_index(drop=True).copy()
            ordered.attrs["_aqsp_date_keys"] = ordered["date"].astype(str).tolist()
            prepared[symbol] = ordered
        return prepared

    def _collect_all_dates(self, data: Dict[str, pd.DataFrame]) -> list[str]:
        dates: set[str] = set()
        for df in data.values():
            if df is not None and not df.empty:
                dates.update(self._date_keys(df))
        return sorted(dates)

    def _find_date_idx(self, dates: list[str], target: str) -> int:
        idx = bisect.bisect_left(dates, target)
        if idx >= len(dates):
            return len(dates) - 1
        return idx

    def _slice_data(
        self, data: Dict[str, pd.DataFrame], start_date: str, end_date: str
    ) -> Dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                continue
            date_keys = self._date_keys(df)
            left = bisect.bisect_left(date_keys, start_date)
            right = bisect.bisect_right(date_keys, end_date)
            if left < right:
                sliced = df.iloc[left:right]
                result[symbol] = sliced.copy()
        return result

    def _run_single_period(
        self,
        train_data: Dict[str, pd.DataFrame],
        test_data: Dict[str, pd.DataFrame],
        signal_date: str,
    ) -> list[TradeResult]:
        trades: list[TradeResult] = []

        signal_data: dict[str, pd.DataFrame] = {}
        for symbol, df in train_data.items():
            hist = self._slice_until(df, signal_date)
            if not hist.empty:
                signal_data[symbol] = hist

        if not signal_data:
            return trades

        market_regime, regime_is_bear_filter = self._resolve_market_regime(
            signal_data, as_of=signal_date
        )
        # Keep the historical safety filter, but label the trade with the same
        # canonical regime used by the live runtime whenever a benchmark exists.
        if regime_is_bear_filter:
            return trades

        selected = self._select_stocks(
            self._screening_data(signal_data),
            regime=market_regime,
        )

        return self._run_selected_trades(
            train_data,
            test_data,
            signal_date=signal_date,
            selected=selected,
            market_regime=market_regime,
        )

    def _run_selected_trades(
        self,
        train_data: Mapping[str, pd.DataFrame],
        test_data: Mapping[str, pd.DataFrame],
        *,
        signal_date: str,
        selected: Sequence[str],
        market_regime: str,
    ) -> list[TradeResult]:
        trades: list[TradeResult] = []
        for symbol in selected:
            if symbol not in test_data:
                continue
            test_df = test_data[symbol]
            if test_df.empty:
                continue

            train_sym = train_data.get(symbol)
            if train_sym is None or train_sym.empty:
                continue
            prev_rows = self._slice_until(train_sym, signal_date)
            if prev_rows.empty:
                continue
            prev_close = float(prev_rows.iloc[-1]["close"])

            entry_bar = test_df.iloc[0]
            entry_date = str(entry_bar["date"])

            executable, reason = _check_executable(entry_bar, prev_close)
            if not executable:
                trades.append(
                    TradeResult(
                        symbol=symbol,
                        signal_date=signal_date,
                        entry_date=entry_date,
                        exit_date=entry_date,
                        entry_price=0.0,
                        exit_price=0.0,
                        return_pct=0.0,
                        exit_reason=reason,
                        market_regime=market_regime,
                        executable=False,
                    )
                )
                continue

            entry_price = float(entry_bar["open"]) * (1 + self.slippage_bps / 10000)

            stop_pct, take_profit_pct = self._resolve_exit_parameters(
                prev_rows, entry_price
            )
            stop_loss = entry_price * (1 - stop_pct)
            take_profit = entry_price * (1 + take_profit_pct)

            horizon_df = test_df.iloc[: self.horizon_days]
            if self.use_tiered_stop:
                exit_bar, exit_price, exit_reason = _resolve_exit_tiered(
                    horizon_df, entry_price, stop_pct, self.slippage_bps
                )
            else:
                exit_bar, exit_price, exit_reason = _resolve_exit(
                    horizon_df, stop_loss, take_profit, self.slippage_bps
                )

            fee_pct = self.fee_bps / 100
            ret = (exit_price - entry_price) / entry_price * 100 - fee_pct

            trades.append(
                TradeResult(
                    symbol=symbol,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    exit_date=str(exit_bar["date"]),
                    entry_price=round(entry_price, 4),
                    exit_price=round(exit_price, 4),
                    return_pct=round(ret, 4),
                    exit_reason=exit_reason,
                    market_regime=market_regime,
                    executable=True,
                )
            )

        return trades

    def _resolve_market_regime(
        self,
        signal_data: Dict[str, pd.DataFrame],
        *,
        as_of: str | None = None,
    ) -> tuple[str, bool]:
        """Resolve regime from data available at the signal cutoff only."""
        benchmark = self._benchmark_symbol_for(signal_data)
        if benchmark:
            try:
                detect_kwargs = {
                    "benchmark_symbol": benchmark,
                    "thresholds": getattr(self.strategy, "thresholds", None),
                }
                if "as_of" in inspect.signature(
                    detect_runtime_regime_context
                ).parameters:
                    detect_kwargs["as_of"] = as_of
                context = detect_runtime_regime_context(signal_data, **detect_kwargs)
                regime = canonicalize_regime(str(context.regime or "unknown"))
                return regime, False
            except Exception:
                # A malformed historical benchmark falls back to breadth rather
                # than silently using data outside the training cutoff.
                pass
        return self._fallback_market_regime(signal_data)

    def _select_stocks(
        self,
        data: Dict[str, pd.DataFrame],
        *,
        regime: str,
    ) -> list[str]:
        """Pass the cutoff regime without breaking legacy strategy adapters."""
        selector = self.strategy.select_stocks
        try:
            parameters = inspect.signature(selector).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "regime" in parameters:
            return list(selector(data, n=self.top_n, regime=regime))
        return list(selector(data, n=self.top_n))

    def _risk_thresholds(self) -> RiskThresholds:
        thresholds = getattr(self.strategy, "thresholds", None)
        risk = getattr(thresholds, "risk", None)
        return risk if isinstance(risk, RiskThresholds) else RiskThresholds()

    def _resolve_exit_parameters(
        self,
        signal_frame: pd.DataFrame,
        entry_price: float,
    ) -> tuple[float, float]:
        risk = self._risk_thresholds()
        if self.stop_loss_pct is not None:
            stop_pct = float(self.stop_loss_pct)
        else:
            atr = _compute_atr(signal_frame, period=risk.dynamic_stop_atr_period)
            multiplier = (
                float(self.atr_stop_multiplier)
                if self.atr_stop_multiplier is not None
                else float(risk.dynamic_stop_atr_multiplier)
            )
            atr_pct = atr * multiplier / entry_price if entry_price > 0 else 0.0
            stop_pct = atr_pct if atr_pct > 0 else float(risk.dynamic_stop_fallback_pct)
            stop_pct = min(stop_pct, float(risk.single_stock_stop_pct))
        take_profit_pct = (
            float(self.take_profit_pct)
            if self.take_profit_pct is not None
            else float(risk.profit_take_threshold_pct)
        )
        return max(0.0, stop_pct), max(0.0, take_profit_pct)

    def _benchmark_symbol_for(
        self, signal_data: Dict[str, pd.DataFrame]
    ) -> str | None:
        benchmark = self.benchmark_symbol
        if benchmark is None and "000300" in signal_data:
            benchmark = "000300"
        return benchmark if benchmark and benchmark in signal_data else None

    def _screening_data(
        self, signal_data: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        benchmark = self._benchmark_symbol_for(signal_data)
        if benchmark is None:
            return signal_data
        return {
            symbol: frame
            for symbol, frame in signal_data.items()
            if symbol != benchmark
        }

    @staticmethod
    def _fallback_market_regime(
        signal_data: Dict[str, pd.DataFrame],
    ) -> tuple[str, bool]:
        return WalkForwardTester._fallback_market_regime_from_returns(
            WalkForwardTester._market_returns_from_frames(signal_data)
        )

    @staticmethod
    def _date_keys(df: pd.DataFrame) -> list[str]:
        cached = df.attrs.get("_aqsp_date_keys")
        if isinstance(cached, list):
            return cached
        values = df["date"].astype(str).tolist()
        df.attrs["_aqsp_date_keys"] = values
        return values

    def _slice_until(self, df: pd.DataFrame, end_date: str) -> pd.DataFrame:
        date_keys = self._date_keys(df)
        right = bisect.bisect_right(date_keys, end_date)
        if right <= 0:
            return df.iloc[0:0]
        return df.iloc[:right]

    @staticmethod
    def _calculate_robustness(periods: list[BacktestResult]) -> float:
        if len(periods) < 2:
            return 0.0
        sharpe_ratios = [p.sharpe_ratio for p in periods if p.sharpe_ratio != 0]
        if not sharpe_ratios:
            return 0.0
        mean_sharpe = float(np.mean(sharpe_ratios))
        std_sharpe = float(np.std(sharpe_ratios))
        consistency = 1.0 - (std_sharpe / (abs(mean_sharpe) + 1e-6))
        return max(0.0, min(1.0, consistency))

    @staticmethod
    def _calculate_parameter_std(periods: list[BacktestResult]) -> float:
        if len(periods) < 2:
            return 0.0
        returns = np.array([p.total_return for p in periods])
        return float(np.std(returns))

    @staticmethod
    def _build_diagnostics(trades: list[TradeResult]) -> WalkForwardDiagnostics:
        executable = [trade for trade in trades if trade.executable]
        not_executable = [trade for trade in trades if not trade.executable]

        symbol_returns: dict[str, list[float]] = {}
        exit_reason_counts: dict[str, int] = {}
        not_exec_reason_counts: dict[str, int] = {}

        for trade in executable:
            symbol_returns.setdefault(trade.symbol, []).append(trade.return_pct)
            exit_reason_counts[trade.exit_reason] = (
                exit_reason_counts.get(trade.exit_reason, 0) + 1
            )

        for trade in not_executable:
            not_exec_reason_counts[trade.exit_reason] = (
                not_exec_reason_counts.get(trade.exit_reason, 0) + 1
            )

        worst_symbols = tuple(
            sorted(
                (
                    (
                        symbol,
                        len(returns),
                        round(float(np.mean(returns)), 4),
                        round(float(sum(returns)), 4),
                    )
                    for symbol, returns in symbol_returns.items()
                ),
                key=lambda item: (item[3], item[2], item[0]),
            )[:10]
        )

        return WalkForwardDiagnostics(
            total_trades=len(trades),
            executable_trades=len(executable),
            not_executable=len(not_executable),
            worst_symbols=worst_symbols,
            exit_reason_counts=tuple(sorted(exit_reason_counts.items())),
            not_executable_reason_counts=tuple(sorted(not_exec_reason_counts.items())),
        )

    @staticmethod
    def _calculate_deflated_sharpe(
        sharpe: float,
        n_trials: int,
        n_obs: int,
        skew: float = 0.0,
        kurtosis: float = 3.0,
        sharpe_is_annualized: bool = True,
        periods_per_year: int = 252,
    ) -> float:
        """Deflated Sharpe Ratio (Bailey & López de Prado 2014, eq. 8).

        Returns the **deflated test statistic** (z-score), not the
        probability Φ(z). This matches CONSTITUTION §1.3 #12's gate
        semantics (`DSR > 1.0`), which is only meaningful for an unbounded
        z-statistic — Φ(z) is bounded in [0, 1].

        Replaces the previous implementation `psr * (1 - log(2^n - 1)/n)`
        which collapsed to ≈ 0.3069 for any `n_trials ≥ 10`, making the
        gate mathematically unreachable.

        Form (per-period units):

            σ_SR   = √( (1 − γ_3·SR + (γ_4 − 1)/4·SR²) / (T − 1) )
            SR*/σ  = (1 − γ_E)·Φ⁻¹(1 − 1/N) + γ_E·Φ⁻¹(1 − 1/(N·e))
            z      = SR/σ_SR  −  SR*/σ

        SR is converted to per-period units when `sharpe_is_annualized=True`.
        Higher z = the observed Sharpe sits further above the deflated
        benchmark; z > 1 corresponds roughly to a one-sided 84% confidence
        that the strategy is real after deflating for `n_trials` selections.
        """
        if n_trials <= 1 or n_obs <= 1:
            return 0.0

        sr = (
            float(sharpe) / float(np.sqrt(periods_per_year))
            if sharpe_is_annualized
            else float(sharpe)
        )
        n = max(int(n_trials), 2)
        t_obs = max(int(n_obs), 2)

        # σ_SR — standard error of the Sharpe ratio estimator (per-period units)
        sigma_sq = (1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr) / (t_obs - 1)
        if sigma_sq <= 0.0:
            sigma_sq = 1e-12
        sigma_sr = float(np.sqrt(sigma_sq))

        # Expected maximum of N iid SR estimates (in σ_SR units)
        EULER = 0.5772156649015329
        z1 = _norm_ppf(1.0 - 1.0 / n)
        z2 = _norm_ppf(1.0 - 1.0 / (n * float(np.e)))
        threshold_in_sigma = (1.0 - EULER) * z1 + EULER * z2

        z_stat = sr / sigma_sr - threshold_in_sigma
        return round(float(z_stat), 4)

    @staticmethod
    def _calculate_pbo(periods: list[BacktestResult]) -> float:
        pbo = WalkForwardTester.calculate_cscv_pbo_from_single(periods)
        return pbo

    @staticmethod
    def calculate_cscv_pbo(
        returns_matrix: np.ndarray, s: int = 10
    ) -> tuple[float, dict]:
        t, n = returns_matrix.shape
        if n < 2:
            raise ValueError("CSCV requires N >= 2 strategy configurations")
        if t < s:
            raise ValueError(f"returns_matrix rows ({t}) must be >= S ({s})")

        block_size = t // s
        if block_size < 2:
            raise ValueError(
                f"Block size ({block_size}) must be >= 2; reduce S or increase T"
            )

        trimmed = returns_matrix[: block_size * s]
        blocks = trimmed.reshape(s, block_size, n)

        from itertools import combinations

        combos = list(combinations(range(s), s // 2))
        n_combos = len(combos)

        lambdas = []
        for train_indices in combos:
            test_indices = [i for i in range(s) if i not in train_indices]

            train_matrix = np.concatenate([blocks[i] for i in train_indices], axis=0)
            test_matrix = np.concatenate([blocks[i] for i in test_indices], axis=0)

            train_sr = np.array(
                [_sample_sharpe_ratio(train_matrix[:, j], annualized=False) for j in range(n)]
            )
            test_sr = np.array(
                [_sample_sharpe_ratio(test_matrix[:, j], annualized=False) for j in range(n)]
            )

            n_star = int(np.argmax(train_sr))

            ranks = np.argsort(np.argsort(test_sr))
            omega = (ranks[n_star] + 1) / (n + 1)

            omega = max(1e-10, min(1 - 1e-10, omega))
            lam = np.log(omega / (1 - omega))
            lambdas.append(float(lam))

        lambdas_arr = np.array(lambdas)
        pbo = float(np.mean(lambdas_arr <= 0))

        details = {
            "n_combos": n_combos,
            "n_lambda_le_0": int(np.sum(lambdas_arr <= 0)),
            "lambda_median": float(np.median(lambdas_arr)),
            "lambda_mean": float(np.mean(lambdas_arr)),
            "s": s,
            "block_size": block_size,
            "t_trimmed": block_size * s,
            "n_variants": n,
        }
        return round(pbo, 4), details

    @staticmethod
    def calculate_cscv_pbo_from_single(
        periods: list[BacktestResult], s: int = 10
    ) -> float:
        returns = np.array([[p.total_return] for p in periods])
        try:
            pbo, _ = WalkForwardTester.calculate_cscv_pbo(returns, s=s)
            return pbo
        except ValueError:
            return 0.0

    def print_report(self, result: WalkForwardResult) -> None:
        print("=" * 60)
        print("Walk-Forward 回测报告")
        print("=" * 60)
        print(f"稳健性评分: {result.robustness_score:.2%}")
        print(f"参数标准差: {result.parameter_std:.4f}")
        print(f"Deflated Sharpe Ratio: {result.deflated_sharpe:.4f}")
        print(f"PBO (过拟合概率): {result.pbo:.2%}")
        print("-" * 60)
        print("整体表现:")
        print(f"  总收益: {result.overall.total_return:.2%}")
        print(f"  年化收益: {result.overall.annual_return:.2%}")
        print(f"  最大回撤: {result.overall.max_drawdown:.2%}")
        print(f"  Sharpe: {result.overall.sharpe_ratio:.2f}")
        print(f"  胜率: {result.overall.win_rate:.2%}")
        print(f"  盈利因子: {result.overall.profit_factor:.2f}")
        print(f"  交易次数: {result.overall.trades}")
        print(f"  不可成交: {result.overall.not_executable}")
        if result.regime_winrates:
            print("-" * 60)
            print("分 Regime 胜率:")
            for regime, wr in sorted(result.regime_winrates.items()):
                print(f"  {regime}: {wr:.2%}")
        print("-" * 60)
        print("分阶段表现:")
        for period in result.periods[:5]:
            print(
                f"  {period.period}: 收益 {period.total_return:.2%}, "
                f"Sharpe {period.sharpe_ratio:.2f}, "
                f"交易 {period.trades}, 不可成交 {period.not_executable}"
            )
        if len(result.periods) > 5:
            print(f"  ... 还有 {len(result.periods) - 5} 个阶段")
        print("=" * 60)


def _check_executable(entry_bar: pd.Series, prev_close: float) -> tuple[bool, str]:
    return _ledger_check_executable(entry_bar, prev_close, {})


def _resolve_exit(
    window: pd.DataFrame,
    stop_loss: float,
    take_profit: float,
    slippage_bps: float,
) -> tuple[pd.Series, float, str]:
    slippage = slippage_bps / 10000
    for bar in window.itertuples(index=False, name="PriceBar"):
        bar_close = float(getattr(bar, "close"))
        low = float(getattr(bar, "low", bar_close))
        high = float(getattr(bar, "high", bar_close))
        if stop_loss > 0 and low <= stop_loss:
            return pd.Series(bar._asdict()), stop_loss * (1 - slippage), "stop_loss"
        if take_profit > 0 and high >= take_profit:
            return pd.Series(bar._asdict()), take_profit * (1 - slippage), "take_profit"
    last = window.iloc[-1]
    return last, float(last["close"]) * (1 - slippage), "hold_period_close"


def _compute_atr(frame: pd.DataFrame, *, period: int) -> float:
    """Compute ATR using only bars available at the signal cutoff."""
    if frame.empty or not {"high", "low", "close"}.issubset(frame.columns):
        return 0.0
    ordered = frame.sort_values("date")
    high = pd.to_numeric(ordered["high"], errors="coerce")
    low = pd.to_numeric(ordered["low"], errors="coerce")
    close = pd.to_numeric(ordered["close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    true_range = true_range.dropna()
    if true_range.empty:
        return 0.0
    window = max(1, int(period))
    return float(true_range.tail(window).mean())


def _resolve_exit_tiered(
    window: pd.DataFrame,
    entry_price: float,
    hard_stop_pct: float,
    slippage_bps: float,
) -> tuple[pd.Series, float, str]:
    slippage = slippage_bps / 10000
    hard_stop = entry_price * (1 - hard_stop_pct)
    remaining_weight = 1.0
    weighted_exit = 0.0
    exit_bar = None

    for bar in window.itertuples(index=False, name="PriceBar"):
        bar_series = pd.Series(bar._asdict())
        low = float(getattr(bar, "low", getattr(bar, "close")))
        close = float(getattr(bar, "close"))
        drop_pct = (entry_price - low) / entry_price

        if drop_pct >= hard_stop_pct and remaining_weight > 0:
            exit_price = hard_stop * (1 - slippage)
            weighted_exit += remaining_weight * exit_price
            return bar_series, weighted_exit, "hard_stop"

        if drop_pct >= 0.02 and remaining_weight > 0.9:
            reduce = 0.20
            exit_price = entry_price * 0.98 * (1 - slippage)
            weighted_exit += reduce * exit_price
            remaining_weight -= reduce

        elif drop_pct >= 0.0 and remaining_weight > 0.9:
            reduce = 0.10
            exit_price = entry_price * (1 - drop_pct) * (1 - slippage)
            weighted_exit += reduce * exit_price
            remaining_weight -= reduce

        if remaining_weight <= 0:
            return bar_series, weighted_exit, "tiered_exit"

    if remaining_weight > 0:
        last = window.iloc[-1]
        close = float(last["close"]) * (1 - slippage)
        weighted_exit += remaining_weight * close
        return last, weighted_exit, "hold_period_close"

    return exit_bar, weighted_exit, "tiered_exit"


def _sample_sharpe_ratio(
    values: np.ndarray,
    *,
    annualized: bool,
    periods_per_year: int = 252,
    zero_std_epsilon: float = 1e-12,
) -> float:
    if values.size == 0:
        return 0.0
    mean_value = float(np.mean(values))
    std_value = float(np.std(values))
    if not np.isfinite(mean_value) or not np.isfinite(std_value):
        return 0.0
    if std_value <= zero_std_epsilon:
        return 0.0
    scale = float(np.sqrt(periods_per_year)) if annualized else 1.0
    return float(mean_value / std_value * scale)


def _compute_backtest_metrics(
    returns: list[float], period: str, not_executable: int = 0
) -> BacktestResult:
    if not returns:
        return BacktestResult(
            period=period,
            total_return=0.0,
            annual_return=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            trades=0,
            not_executable=not_executable,
        )
    arr = np.array(returns) / 100.0
    equity = np.cumprod(1 + arr)
    total_return = float(equity[-1] - 1)
    n = len(returns)
    annual_return = float((1 + total_return) ** (252 / max(n, 1)) - 1)
    running_max = np.maximum.accumulate(equity)
    drawdown = 1 - equity / running_max
    max_drawdown = float(drawdown.max())
    sharpe_ratio = _sample_sharpe_ratio(arr, annualized=True)
    wins = sum(1 for r in returns if r > 0)
    win_rate = wins / n
    pos_sum = float(np.sum(arr[arr > 0])) if any(r > 0 for r in returns) else 0.0
    neg_sum = float(np.sum(arr[arr < 0])) if any(r < 0 for r in returns) else 0.0
    profit_factor = pos_sum / abs(neg_sum) if neg_sum != 0 else 0.0
    return BacktestResult(
        period=period,
        total_return=round(total_return, 6),
        annual_return=round(annual_return, 6),
        max_drawdown=round(max_drawdown, 6),
        sharpe_ratio=round(sharpe_ratio, 4),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4),
        trades=n,
        not_executable=not_executable,
    )
