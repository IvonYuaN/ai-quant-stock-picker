from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    from scipy import stats as _scipy_stats
except (
    ImportError
):  # scipy is optional; DSR will fall back to a normal CDF approximation
    _scipy_stats = None

from aqsp.strategies.composite import CompositeStrategy


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
class WalkForwardResult:
    periods: List[BacktestResult]
    overall: BacktestResult
    robustness_score: float
    parameter_std: float
    deflated_sharpe: float = 0.0
    pbo: float = 0.0
    regime_winrates: Dict[str, float] = None

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
        fee_bps: float = 8.0,
        slippage_bps: float = 5.0,
        top_n: int = 10,
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.10,
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

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> WalkForwardResult:
        all_dates = self._collect_all_dates(data)
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
        while i + step <= end_idx:
            train_end_idx = i - self.purge_days - 1
            if train_end_idx < start_idx:
                i += step
                continue

            train_start = all_dates[start_idx]
            train_end = all_dates[train_end_idx]
            test_start = all_dates[i]
            test_end = all_dates[min(i + step - 1, end_idx)]

            train_data = self._slice_data(data, train_start, train_end)
            test_data = self._slice_data(data, test_start, test_end)

            trades = self._run_single_period(train_data, test_data, train_end)
            all_trades.extend(trades)

            executable = [t for t in trades if t.executable]
            if executable:
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

        n_trials = len(periods)
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
        )

    def _collect_all_dates(self, data: Dict[str, pd.DataFrame]) -> list[str]:
        dates: set[str] = set()
        for df in data.values():
            if df is not None and not df.empty:
                dates.update(df["date"].astype(str).tolist())
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
            date_col = df["date"].astype(str)
            mask = (date_col >= start_date) & (date_col <= end_date)
            sliced = df.loc[mask]
            if not sliced.empty:
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
            hist = df[df["date"].astype(str) <= signal_date]
            if not hist.empty:
                signal_data[symbol] = hist

        if not signal_data:
            return trades

        # Regime filter: skip trading if market breadth is negative
        market_returns = []
        for symbol, df in signal_data.items():
            if len(df) >= 20:
                recent = df.sort_values("date").tail(20)
                prices = recent["close"].values
                ret = (prices[-1] - prices[0]) / prices[0]
                market_returns.append(ret)
        market_regime = "unknown"
        if market_returns:
            avg_market_return = sum(market_returns) / len(market_returns)
            if avg_market_return < -0.02:
                market_regime = "bear_filter"
                return trades
            elif avg_market_return < -0.005:
                market_regime = "mild_bear"
            elif avg_market_return < 0.005:
                market_regime = "sideways"
            else:
                market_regime = "bull_trend" 

        selected = self.strategy.select_stocks(signal_data, n=self.top_n)

        for symbol in selected:
            if symbol not in test_data:
                continue
            test_df = test_data[symbol].sort_values("date").reset_index(drop=True)
            if test_df.empty:
                continue

            train_sym = train_data.get(symbol)
            if train_sym is None or train_sym.empty:
                continue
            prev_rows = train_sym[train_sym["date"].astype(str) <= signal_date]
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

            stop_loss = entry_price * (1 - self.stop_loss_pct)
            take_profit = entry_price * (1 + self.take_profit_pct)

            horizon_df = test_df.iloc[: self.horizon_days]
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
    def _calculate_deflated_sharpe(sharpe: float, n_trials: int, n_obs: int) -> float:
        if n_trials <= 1 or n_obs <= 1:
            return sharpe

        e_skew = 0.0
        e_kurt = 3.0

        sr_std = np.sqrt(
            (1 - e_skew * sharpe + (e_kurt - 1) / 4 * sharpe**2) / (n_obs - 1)
        )

        if _scipy_stats is not None:
            psr = _scipy_stats.norm.cdf(sharpe / sr_std)
        else:
            # Abramowitz & Stegun 26.2.17: pure-numpy normal CDF approximation
            x = sharpe / sr_std
            t = 1.0 / (1.0 + 0.2316419 * abs(x))
            d = 0.39894228 * np.exp(-x * x / 2.0)
            poly = t * (
                0.319381530
                + t
                * (
                    -0.356563782
                    + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))
                )
            )
            cdf_abs = 1.0 - d * poly
            psr = float(cdf_abs if x >= 0 else 1.0 - cdf_abs)

        n_combinations = 2**n_trials - 1
        dsr = psr * (1 - np.log(n_combinations) / n_trials)
        return round(float(dsr), 4)

    @staticmethod
    def _calculate_pbo(periods: list[BacktestResult]) -> float:
        if len(periods) < 3:
            return 0.0

        returns = np.array([p.total_return for p in periods])
        n = len(returns)

        splits = max(2, n // 3)
        is_rank = np.argsort(np.argsort(returns))

        oos_returns = []
        for i in range(0, n, splits):
            test_idx = list(range(i, min(i + splits, n)))
            train_idx = [j for j in range(n) if j not in test_idx]

            if not train_idx or not test_idx:
                continue

            train_returns = returns[train_idx]

            best_train_idx = np.argmax(train_returns)
            best_train_rank = is_rank[best_train_idx]

            test_rank = np.mean(is_rank[test_idx])
            oos_returns.append(float(test_rank < best_train_rank))

        if not oos_returns:
            return 0.0

        return round(float(np.mean(oos_returns)), 4)

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
    if prev_close <= 0:
        return True, ""
    open_price = float(entry_bar.get("open", 0))
    if open_price <= 0:
        return False, "no_open_price"
    volume = entry_bar.get("volume")
    if volume is not None:
        try:
            if float(volume) <= 0:
                return False, "suspended_or_no_trade"
        except (TypeError, ValueError):
            pass
    high = float(entry_bar.get("high", open_price))
    low = float(entry_bar.get("low", open_price))
    if open_price >= prev_close * 1.099 and high <= open_price * 1.0001:
        return False, "limit_up_at_open"
    if open_price <= prev_close * 0.901 and low >= open_price * 0.9999:
        return False, "limit_down_at_open"
    return True, ""


def _resolve_exit(
    window: pd.DataFrame,
    stop_loss: float,
    take_profit: float,
    slippage_bps: float,
) -> tuple[pd.Series, float, str]:
    slippage = slippage_bps / 10000
    for _, bar in window.iterrows():
        low = float(bar.get("low", bar["close"]))
        high = float(bar.get("high", bar["close"]))
        if stop_loss > 0 and low <= stop_loss:
            return bar, stop_loss * (1 - slippage), "stop_loss"
        if take_profit > 0 and high >= take_profit:
            return bar, take_profit * (1 - slippage), "take_profit"
    last = window.iloc[-1]
    return last, float(last["close"]) * (1 - slippage), "hold_period_close"


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
    returns_std = float(np.std(arr))
    sharpe_ratio = (
        float(np.mean(arr) / returns_std * np.sqrt(252)) if returns_std > 0 else 0.0
    )
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
