from __future__ import annotations

import importlib
import importlib.util
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd

from aqsp.backtest.walk_forward import (
    BacktestResult,
    TradeResult,
    WalkForwardResult,
    _check_executable,
    _compute_backtest_metrics,
)

if TYPE_CHECKING:
    from aqsp.backtest.walk_forward import WalkForwardTester

ENGINE_CHOICES = ("auto", "builtin", "akquant")


@dataclass(frozen=True)
class WalkForwardEngineConfig:
    train_days: int
    test_days: int
    purge_days: int
    horizon_days: int
    fee_bps: float = 8.0
    slippage_bps: float = 5.0
    top_n: int = 10
    use_tiered_stop: bool = False
    n_variants: int = 1


@dataclass(frozen=True)
class EngineResolution:
    requested: str
    resolved: str
    mode: str
    message: str


class WalkForwardEngine(Protocol):
    engine_id: str

    def run(
        self,
        strategy: object,
        data: dict[str, pd.DataFrame],
        *,
        start_date: str | None,
        end_date: str | None,
        config: WalkForwardEngineConfig,
    ) -> WalkForwardResult: ...


class BuiltinWalkForwardEngine:
    engine_id = "builtin"

    def run(
        self,
        strategy: object,
        data: dict[str, pd.DataFrame],
        *,
        start_date: str | None,
        end_date: str | None,
        config: WalkForwardEngineConfig,
    ) -> WalkForwardResult:
        tester = _build_tester(strategy, config)
        return tester.run(data, start_date=start_date, end_date=end_date)


class AkquantWalkForwardEngine:
    engine_id = "akquant"

    def __init__(self, compat_engine: WalkForwardEngine | None = None) -> None:
        self._compat_engine = compat_engine or BuiltinWalkForwardEngine()

    def run(
        self,
        strategy: object,
        data: dict[str, pd.DataFrame],
        *,
        start_date: str | None,
        end_date: str | None,
        config: WalkForwardEngineConfig,
    ) -> WalkForwardResult:
        if config.use_tiered_stop:
            raise RuntimeError(
                "AKQuant 原生研究引擎暂不支持 tiered stop；请先切回 builtin。"
            )
        if not _akquant_importable():
            return self._compat_engine.run(
                strategy,
                data,
                start_date=start_date,
                end_date=end_date,
                config=config,
            )

        akquant = _import_akquant_module()
        tester = _build_tester(strategy, config)

        all_dates = tester._collect_all_dates(data)
        if not all_dates:
            raise ValueError("No data available")

        start_idx = 0 if start_date is None else tester._find_date_idx(all_dates, start_date)
        end_idx = (
            len(all_dates) - 1
            if end_date is None
            else tester._find_date_idx(all_dates, end_date)
        )

        periods: list[BacktestResult] = []
        all_trades: list[TradeResult] = []
        step = config.test_days
        cursor = start_idx + config.train_days + config.purge_days

        while cursor + step <= end_idx:
            train_end_idx = cursor - config.purge_days - 1
            if train_end_idx < start_idx:
                cursor += step
                continue

            train_start = all_dates[start_idx]
            train_end = all_dates[train_end_idx]
            test_start = all_dates[cursor]
            test_end = all_dates[min(cursor + step - 1, end_idx)]

            train_data = tester._slice_data(data, train_start, train_end)
            test_data = tester._slice_data(data, test_start, test_end)
            trades = self._run_single_period(
                akquant=akquant,
                tester=tester,
                strategy=strategy,
                train_data=train_data,
                test_data=test_data,
                signal_date=train_end,
            )
            all_trades.extend(trades)

            executable = [trade for trade in trades if trade.executable]
            if executable:
                period_returns = [trade.return_pct for trade in executable]
                periods.append(
                    _compute_backtest_metrics(
                        period_returns,
                        f"{test_start} to {test_end}",
                        len(trades) - len(executable),
                    )
                )

            cursor += step

        return _assemble_walkforward_result(tester, periods, all_trades, config.n_variants)

    def _run_single_period(
        self,
        *,
        akquant: Any,
        tester: WalkForwardTester,
        strategy: object,
        train_data: dict[str, pd.DataFrame],
        test_data: dict[str, pd.DataFrame],
        signal_date: str,
    ) -> list[TradeResult]:
        trades: list[TradeResult] = []
        signal_data = {
            symbol: df[df["date"].astype(str) <= signal_date]
            for symbol, df in train_data.items()
            if df is not None and not df.empty
        }
        signal_data = {symbol: df for symbol, df in signal_data.items() if not df.empty}
        if not signal_data:
            return trades

        market_regime = _resolve_market_regime(signal_data)
        if market_regime == "bear_filter":
            return trades

        selected = list(strategy.select_stocks(signal_data, n=tester.top_n))
        executable_data: dict[str, pd.DataFrame] = {}

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

            executable_data[symbol] = _prepare_akquant_frame(test_df, symbol)

        if not executable_data:
            return trades

        on_bar = _make_akquant_on_bar(
            entry_plan={symbol: 1.0 for symbol in executable_data},
            frame_lengths={
                symbol: len(test_data[symbol].sort_values("date").reset_index(drop=True))
                for symbol in executable_data
            },
            horizon_days=tester.horizon_days,
            stop_loss_pct=tester.stop_loss_pct,
            take_profit_pct=tester.take_profit_pct,
        )

        result = akquant.run_backtest(
            data=executable_data,
            strategy=on_bar,
            initialize=getattr(on_bar, "_aqsp_initialize"),
            symbols=sorted(executable_data),
            initial_cash=100000.0,
            commission_rate=0.0,
            stamp_tax_rate=0.0,
            transfer_fee_rate=0.0,
            min_commission=0.0,
            slippage=0.0,
            lot_size=1,
            history_depth=1,
            show_progress=False,
            fill_policy={"price_basis": "open", "temporal": "same_cycle"},
            timezone="Asia/Shanghai",
        )

        trades.extend(
            _convert_akquant_trades(
                trades_df=result.trades_df,
                signal_date=signal_date,
                market_regime=market_regime,
                fee_bps=tester.fee_bps,
                slippage_bps=tester.slippage_bps,
            )
        )
        return trades


def resolve_walkforward_engine(requested: str) -> tuple[WalkForwardEngine, EngineResolution]:
    normalized = (requested or "auto").strip().lower() or "auto"
    if normalized not in ENGINE_CHOICES:
        raise ValueError(
            f"unknown research engine: {requested}; expected one of {ENGINE_CHOICES}"
        )

    if normalized == "builtin":
        return BuiltinWalkForwardEngine(), EngineResolution(
            requested="builtin",
            resolved="builtin",
            mode="native",
            message="使用内置 Python walk-forward 引擎。",
        )

    if normalized == "akquant":
        if _akquant_importable():
            return AkquantWalkForwardEngine(), EngineResolution(
                requested="akquant",
                resolved="akquant",
                mode="native",
                message="AKQuant 已安装；窗口编排仍由 AQSP 控制，单窗口执行由 AKQuant 原生承载。",
            )
        if _allow_akquant_compat():
            return AkquantWalkForwardEngine(), EngineResolution(
                requested="akquant",
                resolved="builtin",
                mode="compat",
                message="AKQuant 未安装；先走 compat 模式，执行逻辑回退到内置引擎。",
            )
        raise RuntimeError(
            "AQSP_RESEARCH_ENGINE=akquant 但未安装 akquant，且 AQSP_AKQUANT_ALLOW_COMPAT=false。"
        )

    if _prefer_akquant_auto() and (_akquant_importable() or _allow_akquant_compat()):
        engine, resolution = resolve_walkforward_engine("akquant")
        return engine, EngineResolution(
            requested="auto",
            resolved=resolution.resolved,
            mode=resolution.mode,
            message=f"auto 选择 AKQuant 路线：{resolution.message}",
        )
    return BuiltinWalkForwardEngine(), EngineResolution(
        requested="auto",
        resolved="builtin",
        mode="native",
        message="auto 默认选择内置 Python walk-forward 引擎。",
    )


def _build_tester(strategy: object, config: WalkForwardEngineConfig) -> WalkForwardTester:
    from aqsp.backtest.walk_forward import WalkForwardTester

    return WalkForwardTester(
        strategy=strategy,
        train_period_days=config.train_days,
        test_period_days=config.test_days,
        purge_days=config.purge_days,
        horizon_days=config.horizon_days,
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
        top_n=config.top_n,
        use_tiered_stop=config.use_tiered_stop,
        n_variants=config.n_variants,
    )


def _assemble_walkforward_result(
    tester: WalkForwardTester,
    periods: list[BacktestResult],
    all_trades: list[TradeResult],
    n_trials: int,
) -> WalkForwardResult:
    executable = [trade for trade in all_trades if trade.executable]
    all_returns = [trade.return_pct for trade in executable]
    not_executable = sum(1 for trade in all_trades if not trade.executable)
    overall = _compute_backtest_metrics(all_returns, "Overall", not_executable)
    regime_map: dict[str, list[float]] = {}
    for trade in executable:
        regime_map.setdefault(trade.market_regime, []).append(
            1.0 if trade.return_pct > 0 else 0.0
        )
    regime_winrates = {
        regime: sum(values) / len(values) for regime, values in sorted(regime_map.items())
    }
    return WalkForwardResult(
        periods=periods,
        overall=overall,
        robustness_score=tester._calculate_robustness(periods),
        parameter_std=tester._calculate_parameter_std(periods),
        deflated_sharpe=tester._calculate_deflated_sharpe(
            overall.sharpe_ratio, n_trials, len(all_returns)
        ),
        pbo=tester._calculate_pbo(periods),
        regime_winrates=regime_winrates,
    )


def _resolve_market_regime(signal_data: dict[str, pd.DataFrame]) -> str:
    market_returns: list[float] = []
    for df in signal_data.values():
        if len(df) < 20:
            continue
        recent = df.sort_values("date").tail(20)
        prices = recent["close"].values
        market_returns.append((float(prices[-1]) - float(prices[0])) / float(prices[0]))

    if not market_returns:
        return "unknown"

    avg_market_return = sum(market_returns) / len(market_returns)
    if avg_market_return < -0.02:
        return "bear_filter"
    if avg_market_return < -0.005:
        return "mild_bear"
    if avg_market_return < 0.005:
        return "sideways"
    return "bull_trend"


def _prepare_akquant_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized = normalized.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    normalized["symbol"] = symbol

    last = normalized.iloc[-1].copy()
    next_date = pd.Timestamp(last["date"]) + pd.offsets.BDay(1)
    synthetic = {
        "date": next_date,
        "open": float(last["close"]),
        "high": float(last["close"]),
        "low": float(last["close"]),
        "close": float(last["close"]),
        "volume": float(last.get("volume", 0.0) or 0.0),
        "symbol": symbol,
    }
    for column in normalized.columns:
        if column not in synthetic:
            synthetic[column] = last[column]

    normalized = pd.concat([normalized, pd.DataFrame([synthetic])], ignore_index=True)
    return normalized


def _make_akquant_on_bar(
    *,
    entry_plan: dict[str, float],
    frame_lengths: dict[str, int],
    horizon_days: int,
    stop_loss_pct: float,
    take_profit_pct: float,
):
    exit_signal_index = max(horizon_days - 1, 0)
    terminal_index = {symbol: max(length, 0) for symbol, length in frame_lengths.items()}

    def on_bar(ctx: Any, bar: Any) -> None:
        symbol = str(bar.symbol)
        bar_index = ctx.bar_index.get(symbol, -1) + 1
        ctx.bar_index[symbol] = bar_index

        if symbol in ctx.closed_symbols:
            return

        if symbol not in ctx.entered_symbols:
            quantity = float(entry_plan.get(symbol, 0.0))
            if quantity > 0:
                ctx.entry_prices[symbol] = float(bar.open)
                ctx.buy(symbol=symbol, quantity=quantity, tag="wf_entry")
                ctx.entered_symbols.add(symbol)
            return

        position = float(ctx.get_position(symbol))
        if position > 0 and symbol in ctx.pending_exit_reason:
            reason = ctx.pending_exit_reason.pop(symbol)
            ctx.sell(symbol=symbol, quantity=position, tag=f"wf_{reason}")
            ctx.closed_symbols.add(symbol)
            return

        if position <= 0:
            return

        if bar_index >= terminal_index.get(symbol, 0):
            ctx.sell(symbol=symbol, quantity=position, tag="wf_hold_period_close")
            ctx.closed_symbols.add(symbol)
            return

        entry_price = float(ctx.entry_prices[symbol])
        reason = ""
        if float(bar.low) <= entry_price * (1 - stop_loss_pct):
            reason = "stop_loss"
        elif float(bar.high) >= entry_price * (1 + take_profit_pct):
            reason = "take_profit"
        elif bar_index >= exit_signal_index:
            reason = "hold_period_close"

        if reason:
            ctx.pending_exit_reason[symbol] = reason

    def initialize(ctx: Any) -> None:
        ctx.bar_index = defaultdict(int)
        ctx.entered_symbols = set()
        ctx.closed_symbols = set()
        ctx.entry_prices = {}
        ctx.pending_exit_reason = {}

    on_bar._aqsp_initialize = initialize  # type: ignore[attr-defined]
    return on_bar


def _convert_akquant_trades(
    *,
    trades_df: pd.DataFrame,
    signal_date: str,
    market_regime: str,
    fee_bps: float,
    slippage_bps: float,
) -> list[TradeResult]:
    if trades_df is None or trades_df.empty:
        return []

    ordered = trades_df.sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    trades: list[TradeResult] = []
    slippage = float(slippage_bps) / 10000.0
    fee_pct = float(fee_bps) / 100.0

    for _, row in ordered.iterrows():
        entry_raw = float(row["entry_price"])
        exit_raw = float(row["exit_price"])
        entry_price = entry_raw * (1 + slippage)
        exit_price = exit_raw * (1 - slippage)
        return_pct = ((exit_price - entry_price) / entry_price) * 100.0 - fee_pct
        exit_tag = str(row.get("exit_tag", "") or "")
        exit_reason = exit_tag.removeprefix("wf_") or "hold_period_close"

        entry_time = pd.Timestamp(row["entry_time"])
        exit_time = pd.Timestamp(row["exit_time"])
        trades.append(
            TradeResult(
                symbol=str(row["symbol"]),
                signal_date=signal_date,
                entry_date=entry_time.strftime("%Y-%m-%d"),
                exit_date=exit_time.strftime("%Y-%m-%d"),
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                return_pct=round(return_pct, 4),
                exit_reason=exit_reason,
                market_regime=market_regime,
                executable=True,
            )
        )
    return trades


def _allow_akquant_compat() -> bool:
    return os.getenv("AQSP_AKQUANT_ALLOW_COMPAT", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _prefer_akquant_auto() -> bool:
    return os.getenv("AQSP_PREFER_AKQUANT", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _akquant_importable() -> bool:
    return importlib.util.find_spec("akquant") is not None


def _import_akquant_module() -> Any:
    module = importlib.import_module("akquant")
    return module
