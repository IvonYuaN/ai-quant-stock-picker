from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from pathlib import Path

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class FactorPerformance:
    factor_name: str
    ic: float
    ic_ir: float
    turnover: float
    win_rate: float
    last_updated: str
    sample_count: int = 0
    is_effective: bool = True


@dataclass(frozen=True)
class FactorMonitorConfig:
    lookback_days: int = 60
    min_ic_threshold: float = 0.02
    min_ir_threshold: float = 0.5
    max_turnover: float = 0.5
    min_win_rate: float = 0.45
    disable_threshold: float = 0.02
    cooldown_days: int = 30
    min_sample_size: int = 30


class FactorMonitor:
    def __init__(self, config: Optional[FactorMonitorConfig] = None):
        self.config = config or FactorMonitorConfig()
        self._performance_history: Dict[str, List[FactorPerformance]] = {}

    def calculate_factor_ic(
        self, factor_values: pd.Series, returns: pd.Series
    ) -> float:
        if len(factor_values) < 10 or len(returns) < 10:
            return 0.0

        aligned = pd.concat([factor_values, returns], axis=1).dropna()
        if len(aligned) < 10:
            return 0.0

        ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        return float(ic) if not np.isnan(ic) else 0.0

    def calculate_factor_ir(self, ic_series: pd.Series) -> float:
        if len(ic_series) < 5:
            return 0.0

        ic_mean = ic_series.mean()
        ic_std = ic_series.std()

        if ic_std == 0 or np.isnan(ic_std):
            return 0.0

        return float(ic_mean / ic_std)

    def calculate_factor_turnover(
        self, factor_ranks: pd.DataFrame, window: int = 5
    ) -> float:
        if len(factor_ranks) < window + 1:
            return 0.0

        turnover_values = []
        for i in range(window, len(factor_ranks)):
            prev_rank = factor_ranks.iloc[i - window]
            curr_rank = factor_ranks.iloc[i]
            rank_change = (curr_rank - prev_rank).abs().mean()
            turnover_values.append(rank_change)

        return float(np.mean(turnover_values)) if turnover_values else 0.0

    def calculate_win_rate(
        self, factor_values: pd.Series, returns: pd.Series, threshold: float = 0.0
    ) -> float:
        if len(factor_values) < 10 or len(returns) < 10:
            return 0.0

        aligned = pd.concat([factor_values, returns], axis=1).dropna()
        if len(aligned) < 10:
            return 0.0

        positive_factor = aligned.iloc[:, 0] > threshold
        positive_return = aligned.iloc[:, 1] > 0

        wins = (positive_factor & positive_return).sum()
        total = positive_factor.sum()

        return float(wins / total) if total > 0 else 0.0

    def evaluate_factor_effectiveness(
        self,
        factor_name: str,
        factor_values: pd.Series,
        returns: pd.Series,
        factor_ranks: Optional[pd.DataFrame] = None,
    ) -> FactorPerformance:
        ic = self.calculate_factor_ic(factor_values, returns)

        ic_series = pd.Series([ic])
        ic_ir = self.calculate_factor_ir(ic_series)

        turnover = 0.0
        if factor_ranks is not None:
            turnover = self.calculate_factor_turnover(factor_ranks)

        win_rate = self.calculate_win_rate(factor_values, returns)

        is_effective = (
            abs(ic) >= self.config.min_ic_threshold
            and ic_ir >= self.config.min_ir_threshold
            and turnover <= self.config.max_turnover
            and win_rate >= self.config.min_win_rate
        )

        performance = FactorPerformance(
            factor_name=factor_name,
            ic=ic,
            ic_ir=ic_ir,
            turnover=turnover,
            win_rate=win_rate,
            last_updated=now_shanghai().isoformat(),
            sample_count=len(factor_values),
            is_effective=is_effective,
        )

        if factor_name not in self._performance_history:
            self._performance_history[factor_name] = []
        self._performance_history[factor_name].append(performance)

        return performance

    def should_disable_factor(
        self, performance: FactorPerformance, threshold: Optional[float] = None
    ) -> bool:
        threshold = threshold or self.config.disable_threshold

        if performance.sample_count < self.config.min_sample_size:
            return False

        if abs(performance.ic) < threshold:
            return True

        if performance.win_rate < self.config.min_win_rate:
            return True

        return False

    def get_factor_effectiveness(self) -> Dict[str, float]:
        effectiveness = {}

        for factor_name, history in self._performance_history.items():
            if not history:
                effectiveness[factor_name] = 0.0
                continue

            recent = history[-min(5, len(history)) :]
            avg_ic = np.mean([p.ic for p in recent])
            avg_win_rate = np.mean([p.win_rate for p in recent])

            effectiveness_score = abs(avg_ic) * 0.6 + avg_win_rate * 0.4
            effectiveness[factor_name] = float(effectiveness_score)

        return effectiveness

    def generate_factor_report(self, performances: List[FactorPerformance]) -> str:
        report_lines = [
            "# 因子有效性监控报告",
            f"生成时间: {now_shanghai().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 因子表现概览",
            "",
            "| 因子名称 | IC值 | IC_IR | 换手率 | 胜率 | 样本数 | 状态 |",
            "|---------|------|-------|--------|------|--------|------|",
        ]

        for perf in performances:
            status = "有效" if perf.is_effective else "失效"
            report_lines.append(
                f"| {perf.factor_name} | {perf.ic:.4f} | {perf.ic_ir:.2f} | "
                f"{perf.turnover:.2%} | {perf.win_rate:.2%} | {perf.sample_count} | {status} |"
            )

        report_lines.extend(
            [
                "",
                "## 建议操作",
                "",
            ]
        )

        disabled_factors = [p for p in performances if self.should_disable_factor(p)]
        if disabled_factors:
            report_lines.append("### 建议禁用因子:")
            for perf in disabled_factors:
                report_lines.append(
                    f"- {perf.factor_name}: IC={perf.ic:.4f}, 胜率={perf.win_rate:.2%}"
                )
        else:
            report_lines.append("所有因子表现正常，无需禁用。")

        return "\n".join(report_lines)

    def get_performance_history(self, factor_name: str) -> List[FactorPerformance]:
        return self._performance_history.get(factor_name, [])

    def clear_history(self, factor_name: Optional[str] = None) -> None:
        if factor_name:
            self._performance_history.pop(factor_name, None)
        else:
            self._performance_history.clear()


def load_factor_monitor_config(
    config_path: Optional[str] = None,
) -> FactorMonitorConfig:
    import yaml

    if config_path is None:
        config_path = str(
            Path(__file__).parent.parent.parent.parent / "config" / "factor_config.yaml"
        )

    path = Path(config_path)
    if not path.exists():
        return FactorMonitorConfig()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    monitor_config = data.get("factor_monitor", {})

    return FactorMonitorConfig(
        lookback_days=monitor_config.get("lookback_days", 60),
        min_ic_threshold=monitor_config.get("min_ic_threshold", 0.02),
        min_ir_threshold=monitor_config.get("min_ir_threshold", 0.5),
        max_turnover=monitor_config.get("max_turnover", 0.5),
        min_win_rate=monitor_config.get("min_win_rate", 0.45),
        disable_threshold=monitor_config.get("disable_threshold", 0.02),
        cooldown_days=monitor_config.get("cooldown_days", 30),
        min_sample_size=monitor_config.get("min_sample_size", 30),
    )
