from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.indicators import atr, normalize_ohlcv, rsi


@dataclass(frozen=True)
class CandidateFactor:
    name: str
    formula: str
    category: str
    lookback_period: int
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactorEvaluation:
    factor_name: str
    ic_mean: float
    ic_std: float
    ic_ir: float
    turnover_rate: float
    monotonicity: float
    sample_size: int
    is_valid: bool


class AutoFactorMiner:
    def __init__(
        self,
        min_ic: float = 0.03,
        min_ir: float = 0.5,
        min_samples: int = 100,
    ) -> None:
        self.min_ic = min_ic
        self.min_ir = min_ir
        self.min_samples = min_samples

    def generate_candidate_factors(self) -> list[CandidateFactor]:
        factors: list[CandidateFactor] = []
        factors.extend(self._price_factors())
        factors.extend(self._volume_factors())
        factors.extend(self._volatility_factors())
        factors.extend(self._momentum_factors())
        factors.extend(self._mean_reversion_factors())
        return factors

    def _price_factors(self) -> list[CandidateFactor]:
        return [
            CandidateFactor(
                name="close_to_high_20",
                formula="(close - high_20) / high_20",
                category="price",
                lookback_period=20,
            ),
            CandidateFactor(
                name="close_to_low_20",
                formula="(close - low_20) / low_20",
                category="price",
                lookback_period=20,
            ),
            CandidateFactor(
                name="range_position_20",
                formula="(close - low_20) / (high_20 - low_20)",
                category="price",
                lookback_period=20,
            ),
            CandidateFactor(
                name="upper_shadow_ratio",
                formula="(high - max(open, close)) / close",
                category="price",
                lookback_period=1,
            ),
            CandidateFactor(
                name="lower_shadow_ratio",
                formula="(min(open, close) - low) / close",
                category="price",
                lookback_period=1,
            ),
        ]

    def _volume_factors(self) -> list[CandidateFactor]:
        return [
            CandidateFactor(
                name="volume_ratio_5",
                formula="volume / volume_ma5",
                category="volume",
                lookback_period=5,
            ),
            CandidateFactor(
                name="volume_ratio_10",
                formula="volume / volume_ma10",
                category="volume",
                lookback_period=10,
            ),
            CandidateFactor(
                name="volume_change_5d",
                formula="(volume - volume_5d_ago) / volume_5d_ago",
                category="volume",
                lookback_period=5,
            ),
            CandidateFactor(
                name="amount_ratio_5",
                formula="amount / amount_ma5",
                category="volume",
                lookback_period=5,
            ),
            CandidateFactor(
                name="volume_price_corr_10",
                formula="corr(close, volume, 10)",
                category="volume",
                lookback_period=10,
            ),
        ]

    def _volatility_factors(self) -> list[CandidateFactor]:
        return [
            CandidateFactor(
                name="volatility_20",
                formula="std(returns, 20)",
                category="volatility",
                lookback_period=20,
            ),
            CandidateFactor(
                name="atr_ratio_14",
                formula="atr14 / close",
                category="volatility",
                lookback_period=14,
            ),
            CandidateFactor(
                name="amplitude_5d",
                formula="(high_5 - low_5) / close",
                category="volatility",
                lookback_period=5,
            ),
            CandidateFactor(
                name="realized_vol_10",
                formula="std(returns, 10) * sqrt(252)",
                category="volatility",
                lookback_period=10,
            ),
            CandidateFactor(
                name="vol_of_vol_20",
                formula="std(std(returns, 5), 20)",
                category="volatility",
                lookback_period=20,
                params={"inner_window": 5},
            ),
        ]

    def _momentum_factors(self) -> list[CandidateFactor]:
        return [
            CandidateFactor(
                name="momentum_5",
                formula="(close - close_5d_ago) / close_5d_ago",
                category="momentum",
                lookback_period=5,
            ),
            CandidateFactor(
                name="momentum_10",
                formula="(close - close_10d_ago) / close_10d_ago",
                category="momentum",
                lookback_period=10,
            ),
            CandidateFactor(
                name="momentum_20",
                formula="(close - close_20d_ago) / close_20d_ago",
                category="momentum",
                lookback_period=20,
            ),
            CandidateFactor(
                name="rsi_14",
                formula="rsi(close, 14)",
                category="momentum",
                lookback_period=14,
            ),
            CandidateFactor(
                name="macd_hist",
                formula="(ema12 - ema26 - signal) * 2",
                category="momentum",
                lookback_period=26,
            ),
        ]

    def _mean_reversion_factors(self) -> list[CandidateFactor]:
        return [
            CandidateFactor(
                name="bias_5",
                formula="(close / ma5 - 1) * 100",
                category="mean_reversion",
                lookback_period=5,
            ),
            CandidateFactor(
                name="bias_10",
                formula="(close / ma10 - 1) * 100",
                category="mean_reversion",
                lookback_period=10,
            ),
            CandidateFactor(
                name="bias_20",
                formula="(close / ma20 - 1) * 100",
                category="mean_reversion",
                lookback_period=20,
            ),
            CandidateFactor(
                name="z_score_20",
                formula="(close - ma20) / std(close, 20)",
                category="mean_reversion",
                lookback_period=20,
            ),
            CandidateFactor(
                name="distance_to_ma60",
                formula="(close - ma60) / ma60",
                category="mean_reversion",
                lookback_period=60,
            ),
        ]

    def calculate_factor_value(
        self, df: pd.DataFrame, factor: CandidateFactor
    ) -> pd.Series:
        df = normalize_ohlcv(df).copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        amount = df["amount"]
        op = df["open"]

        if factor.name == "close_to_high_20":
            high_20 = high.rolling(20).max()
            return (close - high_20) / high_20

        if factor.name == "close_to_low_20":
            low_20 = low.rolling(20).min()
            return (close - low_20) / low_20

        if factor.name == "range_position_20":
            high_20 = high.rolling(20).max()
            low_20 = low.rolling(20).min()
            return (close - low_20) / (high_20 - low_20).replace(0, np.nan)

        if factor.name == "upper_shadow_ratio":
            return (high - np.maximum(op, close)) / close

        if factor.name == "lower_shadow_ratio":
            return (np.minimum(op, close) - low) / close

        if factor.name == "volume_ratio_5":
            vol_ma5 = volume.rolling(5).mean()
            return volume / vol_ma5.replace(0, np.nan)

        if factor.name == "volume_ratio_10":
            vol_ma10 = volume.rolling(10).mean()
            return volume / vol_ma10.replace(0, np.nan)

        if factor.name == "volume_change_5d":
            vol_5d_ago = volume.shift(5)
            return (volume - vol_5d_ago) / vol_5d_ago.replace(0, np.nan)

        if factor.name == "amount_ratio_5":
            amt_ma5 = amount.rolling(5).mean()
            return amount / amt_ma5.replace(0, np.nan)

        if factor.name == "volume_price_corr_10":
            return close.rolling(10).corr(volume)

        if factor.name == "volatility_20":
            returns = close.pct_change()
            return returns.rolling(20).std()

        if factor.name == "atr_ratio_14":
            atr14 = atr(high, low, close, 14)
            return atr14 / close

        if factor.name == "amplitude_5d":
            high_5 = high.rolling(5).max()
            low_5 = low.rolling(5).min()
            return (high_5 - low_5) / close

        if factor.name == "realized_vol_10":
            returns = close.pct_change()
            return returns.rolling(10).std() * np.sqrt(252)

        if factor.name == "vol_of_vol_20":
            inner_window = factor.params.get("inner_window", 5)
            returns = close.pct_change()
            rolling_vol = returns.rolling(inner_window).std()
            return rolling_vol.rolling(20).std()

        if factor.name == "momentum_5":
            return close / close.shift(5) - 1

        if factor.name == "momentum_10":
            return close / close.shift(10) - 1

        if factor.name == "momentum_20":
            return close / close.shift(20) - 1

        if factor.name == "rsi_14":
            return rsi(close, 14)

        if factor.name == "macd_hist":
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            return (dif - dea) * 2

        if factor.name == "bias_5":
            ma5 = close.rolling(5).mean()
            return (close / ma5 - 1) * 100

        if factor.name == "bias_10":
            ma10 = close.rolling(10).mean()
            return (close / ma10 - 1) * 100

        if factor.name == "bias_20":
            ma20 = close.rolling(20).mean()
            return (close / ma20 - 1) * 100

        if factor.name == "z_score_20":
            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            return (close - ma20) / std20.replace(0, np.nan)

        if factor.name == "distance_to_ma60":
            ma60 = close.rolling(60).mean()
            return (close - ma60) / ma60.replace(0, np.nan)

        raise ValueError(f"Unknown factor: {factor.name}")

    def evaluate_factor(
        self, factor_values: pd.Series, forward_returns: pd.Series
    ) -> FactorEvaluation:
        aligned = pd.concat([factor_values, forward_returns], axis=1).dropna()
        if len(aligned) < self.min_samples:
            return FactorEvaluation(
                factor_name="",
                ic_mean=0.0,
                ic_std=0.0,
                ic_ir=0.0,
                turnover_rate=0.0,
                monotonicity=0.0,
                sample_size=len(aligned),
                is_valid=False,
            )

        fv = aligned.iloc[:, 0]
        fr = aligned.iloc[:, 1]

        ic_series = fv.rolling(20).corr(fr)
        ic_series = ic_series.dropna()

        if len(ic_series) < 10:
            return FactorEvaluation(
                factor_name="",
                ic_mean=0.0,
                ic_std=0.0,
                ic_ir=0.0,
                turnover_rate=0.0,
                monotonicity=0.0,
                sample_size=len(aligned),
                is_valid=False,
            )

        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0

        ranks = fv.rank(pct=True)
        rank_changes = ranks.diff().abs()
        turnover_rate = float(rank_changes.mean())

        n_quantiles = 5
        quantile_labels = pd.qcut(fv, n_quantiles, labels=False, duplicates="drop")
        if quantile_labels is not None and not quantile_labels.empty:
            quantile_returns = fr.groupby(quantile_labels).mean()
            if len(quantile_returns) >= 2:
                monotonicity = float(
                    np.polyfit(
                        range(len(quantile_returns)), quantile_returns.values, 1
                    )[0]
                )
            else:
                monotonicity = 0.0
        else:
            monotonicity = 0.0

        is_valid = abs(ic_mean) >= self.min_ic and abs(ic_ir) >= self.min_ir

        return FactorEvaluation(
            factor_name="",
            ic_mean=round(ic_mean, 6),
            ic_std=round(ic_std, 6),
            ic_ir=round(ic_ir, 4),
            turnover_rate=round(turnover_rate, 4),
            monotonicity=round(monotonicity, 6),
            sample_size=len(aligned),
            is_valid=is_valid,
        )

    def mine_factors(
        self, data: dict[str, pd.DataFrame], forward_days: int = 5
    ) -> list[dict[str, Any]]:
        candidates = self.generate_candidate_factors()
        results: list[dict[str, Any]] = []

        for factor in candidates:
            all_factor_values: list[pd.Series] = []
            all_forward_returns: list[pd.Series] = []

            for symbol, df in data.items():
                if df is None or df.empty:
                    continue
                try:
                    factor_values = self.calculate_factor_value(df, factor)
                    close = df["close"]
                    forward_returns = close.shift(-forward_days) / close - 1

                    all_factor_values.append(factor_values)
                    all_forward_returns.append(forward_returns)
                except Exception:
                    continue

            if not all_factor_values:
                continue

            combined_factor = pd.concat(all_factor_values)
            combined_returns = pd.concat(all_forward_returns)

            evaluation = FactorEvaluation(
                factor_name=factor.name,
                ic_mean=0.0,
                ic_std=0.0,
                ic_ir=0.0,
                turnover_rate=0.0,
                monotonicity=0.0,
                sample_size=0,
                is_valid=False,
            )

            try:
                evaluation = self.evaluate_factor(combined_factor, combined_returns)
                evaluation = FactorEvaluation(
                    factor_name=factor.name,
                    ic_mean=evaluation.ic_mean,
                    ic_std=evaluation.ic_std,
                    ic_ir=evaluation.ic_ir,
                    turnover_rate=evaluation.turnover_rate,
                    monotonicity=evaluation.monotonicity,
                    sample_size=evaluation.sample_size,
                    is_valid=evaluation.is_valid,
                )
            except Exception:
                continue

            if evaluation.is_valid:
                results.append(
                    {
                        "name": factor.name,
                        "category": factor.category,
                        "formula": factor.formula,
                        "lookback_period": factor.lookback_period,
                        "params": factor.params,
                        "is_active": False,
                        "status": "research_candidate",
                        "evaluation": {
                            "ic_mean": evaluation.ic_mean,
                            "ic_std": evaluation.ic_std,
                            "ic_ir": evaluation.ic_ir,
                            "turnover_rate": evaluation.turnover_rate,
                            "monotonicity": evaluation.monotonicity,
                            "sample_size": evaluation.sample_size,
                        },
                    }
                )

        results.sort(key=lambda x: abs(x["evaluation"]["ic_ir"]), reverse=True)
        return results

    def save_discovered_factors(
        self, factors: list[dict[str, Any]], output_path: str
    ) -> None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        now = now_shanghai().isoformat(timespec="seconds")

        if output.exists():
            with open(output, encoding="utf-8") as f:
                library = json.load(f)
        else:
            library = {
                "version": "1.0",
                "last_updated": now,
                "factors": [],
            }

        existing_names = {f["name"] for f in library["factors"]}

        for factor in factors:
            if factor["name"] not in existing_names:
                library["factors"].append(
                    {
                        "name": factor["name"],
                        "category": factor["category"],
                        "description": f"Auto-discovered {factor['category']} factor",
                        "formula": factor["formula"],
                        "lookback_period": factor["lookback_period"],
                        "is_active": False,
                        "status": "research_candidate",
                        "performance": {
                            "ic_mean": factor["evaluation"]["ic_mean"],
                            "ic_ir": factor["evaluation"]["ic_ir"],
                            "win_rate": 0.5,
                        },
                        "discovered_at": now,
                    }
                )
                existing_names.add(factor["name"])

        library["last_updated"] = now

        with open(output, "w", encoding="utf-8") as f:
            json.dump(library, f, indent=2, ensure_ascii=False)


class FactorLibrary:
    def __init__(self, library_path: str = "config/factor_library.json") -> None:
        self.library_path = Path(library_path)
        self.factors: list[dict[str, Any]] = []

    def load(self) -> None:
        if not self.library_path.exists():
            self.factors = []
            return

        with open(self.library_path, encoding="utf-8") as f:
            data = json.load(f)

        self.factors = data.get("factors", [])

    def save(self) -> None:
        self.library_path.parent.mkdir(parents=True, exist_ok=True)

        now = now_shanghai().isoformat(timespec="seconds")

        data = {
            "version": "1.0",
            "last_updated": now,
            "factors": self.factors,
        }

        with open(self.library_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_factor(self, factor: dict[str, Any]) -> bool:
        existing_names = {f["name"] for f in self.factors}
        if factor["name"] in existing_names:
            return False

        factor = {**factor, "is_active": bool(factor.get("is_active", False))}
        factor.setdefault("status", "research_candidate")
        self.factors.append(factor)
        return True

    def remove_factor(self, factor_name: str) -> bool:
        original_length = len(self.factors)
        self.factors = [f for f in self.factors if f["name"] != factor_name]
        return len(self.factors) < original_length

    def get_active_factors(self) -> list[dict[str, Any]]:
        return [f for f in self.factors if f.get("is_active") is True]

    def update_factor_performance(
        self, factor_name: str, performance: dict[str, float]
    ) -> None:
        for factor in self.factors:
            if factor["name"] == factor_name:
                factor["performance"] = performance
                return
        raise ValueError(f"Factor not found: {factor_name}")
