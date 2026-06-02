from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class LearnerConfig:
    min_independent_signal_days: int = 14
    rolling_window_days: int = 90
    weight_floor: float = 0.65
    weight_ceiling: float = 1.45
    aggregation: Literal["per_signal_day", "per_pick"] = "per_signal_day"
    weight_change_cooldown_days: int = 30
    by_regime: bool = True


@dataclass(frozen=True)
class LearningResult:
    strategy_name: str
    regime: str | None
    period: str
    independent_signal_days: int
    total_picks: int
    win_count: int
    win_rate: float
    avg_return: float
    max_drawdown: float
    sharpe_ratio: float


@dataclass(frozen=True)
class StrategyPerformance:
    strategy_name: str
    weights: dict[str, float]
    recent_performance: LearningResult
    rolling_performance: list[LearningResult]
    regime_weights: dict[str, float] | None = None


def _explode_strategies(df: pd.DataFrame) -> pd.DataFrame:
    if "strategies" not in df.columns:
        return df
    records: list[dict] = []
    for _, row in df.iterrows():
        strategies = row.get("strategies") or []
        if isinstance(strategies, str):
            strategies = [strategies]
        for strategy in strategies:
            new_row = row.to_dict()
            new_row["strategy"] = strategy
            records.append(new_row)
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _prepare_returns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "excess_return_pct" in df.columns:
        raw = df["excess_return_pct"]
        fallback = df.get("return_pct", pd.Series(0, index=df.index))
        df["return_decimal"] = (
            pd.to_numeric(raw, errors="coerce").fillna(
                pd.to_numeric(fallback, errors="coerce").fillna(0)
            )
            / 100
        )
    else:
        df["return_decimal"] = (
            pd.to_numeric(
                df.get("return_pct", pd.Series(0, index=df.index)), errors="coerce"
            ).fillna(0)
            / 100
        )
    return df


def _aggregate_per_signal_day(df: pd.DataFrame) -> pd.DataFrame:
    has_regime = "regime_at_signal" in df.columns
    records: list[dict] = []
    for (sd, strat), group in df.groupby(["signal_date", "strategy"]):
        entry: dict = {
            "signal_date": sd,
            "strategy": strat,
            "return_decimal": float(group["return_decimal"].mean()),
            "total_picks": len(group),
        }
        if has_regime:
            entry["regime"] = group["regime_at_signal"].iloc[0]
        records.append(entry)

    if not records:
        return pd.DataFrame(
            columns=[
                "signal_date",
                "strategy",
                "return_decimal",
                "total_picks",
                "regime",
            ]
        )
    agg = pd.DataFrame(records)
    if "regime" not in agg.columns:
        agg["regime"] = None
    return agg


class PerformanceLearner:
    def __init__(
        self,
        config: LearnerConfig | None = None,
        weight_history_path: str | Path = "data/weight_history.jsonl",
    ):
        self.config = config or LearnerConfig()
        self.weight_history_path = Path(weight_history_path)
        self._last_weight_change: dict[str, float] = {}

    def learn_from_ledger(
        self, ledger_df: pd.DataFrame
    ) -> dict[str, StrategyPerformance]:
        if ledger_df.empty:
            return {}

        df = ledger_df[ledger_df["status"] != "not_executable"].copy()
        if df.empty:
            return {}

        df = _prepare_returns(df)
        df = _explode_strategies(df)
        if df.empty:
            return {}

        df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
        df = df.dropna(subset=["signal_date"])

        if self.config.aggregation == "per_signal_day":
            agg_df = _aggregate_per_signal_day(df)
        else:
            agg_df = df.copy()
            agg_df["total_picks"] = 1
            if "regime" not in agg_df.columns:
                agg_df["regime"] = agg_df.get("regime_at_signal")

        results: dict[str, StrategyPerformance] = {}

        for strategy in agg_df["strategy"].unique():
            strat_data = agg_df[agg_df["strategy"] == strategy].copy()

            regime_weights: dict[str, float] | None = None
            if self.config.by_regime and "regime" in strat_data.columns:
                regime_weights = {}
                for regime_val in strat_data["regime"].dropna().unique():
                    regime_data = strat_data[strat_data["regime"] == regime_val]
                    regime_result = self._compute_performance(
                        regime_data, strategy, regime=str(regime_val), period="all"
                    )
                    regime_weights[str(regime_val)] = self._calculate_weight(
                        regime_result
                    )

            recent = self._compute_performance(
                strat_data, strategy, regime=None, period="recent"
            )
            rolling = self._compute_rolling(strat_data, strategy)
            weight = self._calculate_weight(recent)

            old_weight = 1.0
            if not self._can_update_weight(strategy):
                weight = old_weight
            else:
                self._record_weight_change(
                    strategy, old_weight, weight, "learner_update"
                )

            results[strategy] = StrategyPerformance(
                strategy_name=strategy,
                weights={
                    "base": weight,
                    "confidence": min(
                        recent.independent_signal_days
                        / self.config.min_independent_signal_days,
                        1.0,
                    ),
                },
                recent_performance=recent,
                rolling_performance=rolling,
                regime_weights=regime_weights,
            )

        return results

    def compute_weights(self, ledger_df: pd.DataFrame) -> dict[str, float]:
        performances = self.learn_from_ledger(ledger_df)
        return {
            name: perf.weights.get("base", 1.0) for name, perf in performances.items()
        }

    def _compute_performance(
        self, df: pd.DataFrame, strategy: str, regime: str | None, period: str
    ) -> LearningResult:
        returns = df["return_decimal"]
        total_picks = (
            int(df["total_picks"].sum()) if "total_picks" in df.columns else len(df)
        )
        independent_days = len(df)
        win_count = int((returns > 0).sum())
        win_rate = win_count / len(returns) if len(returns) > 0 else 0.0
        avg_return = float(returns.mean()) if len(returns) > 0 else 0.0

        equity = (1 + returns).cumprod()
        drawdown = 1 - equity / equity.cummax()
        max_drawdown = float(drawdown.max()) if len(drawdown) > 0 else 0.0

        returns_std = float(returns.std()) if len(returns) > 0 else 1.0
        sharpe = avg_return / returns_std * np.sqrt(252) if returns_std > 0 else 0.0

        return LearningResult(
            strategy_name=strategy,
            regime=regime,
            period=period,
            independent_signal_days=independent_days,
            total_picks=total_picks,
            win_count=win_count,
            win_rate=round(win_rate, 4),
            avg_return=round(avg_return, 6),
            max_drawdown=round(max_drawdown, 4),
            sharpe_ratio=round(sharpe, 4),
        )

    def _compute_rolling(self, df: pd.DataFrame, strategy: str) -> list[LearningResult]:
        df = df.sort_values("signal_date")
        if len(df) < self.config.min_independent_signal_days:
            return []

        window_days = self.config.rolling_window_days
        step_days = 30
        results: list[LearningResult] = []

        min_date = df["signal_date"].min()
        max_date = df["signal_date"].max()
        current = min_date

        while current + timedelta(days=window_days) <= max_date:
            window_end = current + timedelta(days=window_days)
            window_df = df[
                (df["signal_date"] >= current) & (df["signal_date"] < window_end)
            ]

            if len(window_df) >= self.config.min_independent_signal_days:
                results.append(
                    self._compute_performance(
                        window_df,
                        strategy,
                        regime=None,
                        period=f"{current.date()}_{window_end.date()}",
                    )
                )

            current += timedelta(days=step_days)

        return results

    def _calculate_weight(self, result: LearningResult) -> float:
        if result.independent_signal_days < self.config.min_independent_signal_days:
            return 1.0

        weight = 1.0

        if result.win_rate < 0.4:
            weight *= 0.7
        elif result.win_rate > 0.6:
            weight *= 1.2

        if result.avg_return > 0:
            weight *= min(1.3, 1 + result.avg_return / 0.2)
        else:
            weight *= max(0.7, 1 + result.avg_return / 0.2)

        if result.sharpe_ratio > 1.0:
            weight *= 1.1
        elif result.sharpe_ratio < -0.5:
            weight *= 0.8

        return round(
            max(self.config.weight_floor, min(self.config.weight_ceiling, weight)),
            3,
        )

    def _can_update_weight(self, strategy: str) -> bool:
        last_ts = self._last_weight_change.get(strategy)
        if last_ts is None:
            return True
        return (
            now_shanghai().timestamp() - last_ts
        ) >= self.config.weight_change_cooldown_days * 86400

    def _record_weight_change(
        self, strategy: str, old_weight: float, new_weight: float, reason: str
    ) -> None:
        now = now_shanghai()
        self._last_weight_change[strategy] = now.timestamp()

        self.weight_history_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": now.isoformat(timespec="seconds"),
            "strategy": strategy,
            "old_weight": old_weight,
            "new_weight": new_weight,
            "reason": reason,
        }
        with open(self.weight_history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@dataclass(frozen=True)
class StrategyDecayAlert:
    strategy_name: str
    decay_days: int
    recent_win_rate: float
    recent_avg_return: float
    severity: str
    recommendation: str


class StrategyDecayDetector:
    def __init__(
        self,
        lookback_days: int = 7,
        min_win_rate: float = 0.4,
        min_avg_return: float = -0.02,
    ):
        self.lookback_days = lookback_days
        self.min_win_rate = min_win_rate
        self.min_avg_return = min_avg_return

    def detect(self, ledger_df: pd.DataFrame) -> list[StrategyDecayAlert]:
        if ledger_df.empty:
            return []

        df = ledger_df[ledger_df["status"] != "not_executable"].copy()
        if df.empty:
            return []

        df = _prepare_returns(df)
        df = _explode_strategies(df)
        if df.empty:
            return []

        df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
        df = df.dropna(subset=["signal_date"])

        cutoff = now_shanghai() - timedelta(days=self.lookback_days)
        recent = df[df["signal_date"] >= cutoff]
        if recent.empty:
            return []

        alerts: list[StrategyDecayAlert] = []
        for strategy in recent["strategy"].unique():
            strat_data = recent[recent["strategy"] == strategy]
            returns = strat_data["return_decimal"]
            win_rate = float((returns > 0).mean())
            avg_return = float(returns.mean())

            if win_rate < self.min_win_rate or avg_return < self.min_avg_return:
                decay_days = self._count_decay_days(df, strategy)
                if win_rate < 0.3 or avg_return < -0.05:
                    severity = "critical"
                    recommendation = f"建议将 {strategy} 权重降至最低"
                elif win_rate < self.min_win_rate:
                    severity = "warning"
                    recommendation = f"建议降低 {strategy} 权重"
                else:
                    severity = "info"
                    recommendation = f"关注 {strategy} 表现"

                alerts.append(
                    StrategyDecayAlert(
                        strategy_name=strategy,
                        decay_days=decay_days,
                        recent_win_rate=round(win_rate, 4),
                        recent_avg_return=round(avg_return, 6),
                        severity=severity,
                        recommendation=recommendation,
                    )
                )

        return alerts

    def _count_decay_days(self, df: pd.DataFrame, strategy: str) -> int:
        strat_data = df[df["strategy"] == strategy].sort_values(
            "signal_date", ascending=False
        )
        decay_days = 0
        for _, row in strat_data.iterrows():
            if row["return_decimal"] <= 0:
                decay_days += 1
            else:
                break
        return decay_days


def format_decay_alerts(alerts: list[StrategyDecayAlert]) -> str:
    if not alerts:
        return ""
    lines = ["## 策略衰减告警", ""]
    for alert in alerts:
        emoji = (
            "🔴"
            if alert.severity == "critical"
            else "🟡"
            if alert.severity == "warning"
            else "🔵"
        )
        lines.append(
            f"- {emoji} **{alert.strategy_name}**: "
            f"近{alert.lookback_days if hasattr(alert, 'lookback_days') else ''}天胜率 {alert.recent_win_rate:.1%}, "
            f"均收益 {alert.recent_avg_return:+.2%}, "
            f"连续{alert.decay_days}天亏损"
        )
        lines.append(f"  - {alert.recommendation}")
    return "\n".join(lines)
