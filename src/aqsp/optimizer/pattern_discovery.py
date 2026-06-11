from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class DiscoveredPattern:
    pattern_id: str
    pattern_type: str
    description: str
    conditions: dict[str, Any]
    historical_win_rate: float
    historical_avg_return: float
    sample_size: int
    confidence: float
    first_seen: str
    last_seen: str


class PatternDiscoveryEngine:
    def __init__(
        self,
        min_sample_size: int = 20,
        min_win_rate: float = 0.55,
    ) -> None:
        self.min_sample_size = min_sample_size
        self.min_win_rate = min_win_rate
        self._detectors = [
            self._detect_momentum_burst,
            self._detect_volume_divergence,
            self._detect_mean_reversion,
            self._detect_breakout,
            self._detect_seasonal,
        ]

    def discover(
        self,
        ledger_df: pd.DataFrame,
        frames: dict[str, pd.DataFrame],
    ) -> list[DiscoveredPattern]:
        patterns: list[DiscoveredPattern] = []
        for detector in self._detectors:
            found = detector(ledger_df, frames)
            patterns.extend(found)
        return patterns

    def _filter_pattern(
        self,
        occurrences: list[dict[str, Any]],
        pattern_type: str,
        description: str,
        conditions: dict[str, Any],
    ) -> DiscoveredPattern | None:
        if len(occurrences) < self.min_sample_size:
            return None
        returns = [o["return_pct"] for o in occurrences]
        win_count = sum(1 for r in returns if r > 0)
        win_rate = win_count / len(returns)
        if win_rate < self.min_win_rate:
            return None
        avg_return = float(np.mean(returns))
        dates = [o["date"] for o in occurrences]
        confidence = min(1.0, win_rate * (1.0 + np.log1p(len(occurrences)) / 10.0))
        return DiscoveredPattern(
            pattern_id=f"pat_{uuid.uuid4().hex[:8]}",
            pattern_type=pattern_type,
            description=description,
            conditions=conditions,
            historical_win_rate=round(win_rate, 4),
            historical_avg_return=round(avg_return, 4),
            sample_size=len(occurrences),
            confidence=round(confidence, 4),
            first_seen=min(dates),
            last_seen=max(dates),
        )

    def _detect_momentum_burst(
        self,
        ledger_df: pd.DataFrame,
        frames: dict[str, pd.DataFrame],
    ) -> list[DiscoveredPattern]:
        occurrences: list[dict[str, Any]] = []
        for symbol, df in frames.items():
            if df is None or len(df) < 10:
                continue
            df_sorted = df.sort_values("date").reset_index(drop=True)
            closes = df_sorted["close"].values
            volumes = df_sorted["volume"].values if "volume" in df_sorted else None
            for i in range(3, len(df_sorted) - 5):
                ret_3d = (closes[i] - closes[i - 3]) / closes[i - 3]
                if ret_3d <= 0.05:
                    continue
                if volumes is not None:
                    vol_ma = float(np.mean(volumes[max(0, i - 20) : i]))
                    if vol_ma > 0 and volumes[i] < vol_ma * 1.2:
                        continue
                future_ret = (closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[
                    i
                ]
                occurrences.append(
                    {
                        "symbol": symbol,
                        "date": str(df_sorted.iloc[i]["date"]),
                        "return_pct": round(float(future_ret * 100), 4),
                    }
                )
        result = self._filter_pattern(
            occurrences,
            "momentum_burst",
            "3日涨幅>5%且放量后的延续性形态",
            {"lookback_days": 3, "min_return_pct": 5.0, "volume_multiplier": 1.2},
        )
        return [result] if result else []

    def _detect_volume_divergence(
        self,
        ledger_df: pd.DataFrame,
        frames: dict[str, pd.DataFrame],
    ) -> list[DiscoveredPattern]:
        occurrences: list[dict[str, Any]] = []
        for symbol, df in frames.items():
            if df is None or len(df) < 20:
                continue
            df_sorted = df.sort_values("date").reset_index(drop=True)
            closes = df_sorted["close"].values
            volumes = df_sorted["volume"].values if "volume" in df_sorted else None
            if volumes is None:
                continue
            for i in range(10, len(df_sorted) - 5):
                price_up = closes[i] > closes[i - 10]
                vol_trend = np.polyfit(range(10), volumes[i - 10 : i], 1)
                vol_declining = vol_trend[0] < 0
                if not (price_up and vol_declining):
                    continue
                future_ret = (closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[
                    i
                ]
                occurrences.append(
                    {
                        "symbol": symbol,
                        "date": str(df_sorted.iloc[i]["date"]),
                        "return_pct": round(float(future_ret * 100), 4),
                    }
                )
        result = self._filter_pattern(
            occurrences,
            "volume_divergence",
            "价格上涨但成交量萎缩的背离形态（潜在反转信号）",
            {"lookback_days": 10, "trend_check": "price_up_vol_down"},
        )
        return [result] if result else []

    def _detect_mean_reversion(
        self,
        ledger_df: pd.DataFrame,
        frames: dict[str, pd.DataFrame],
    ) -> list[DiscoveredPattern]:
        occurrences: list[dict[str, Any]] = []
        for symbol, df in frames.items():
            if df is None or len(df) < 20:
                continue
            df_sorted = df.sort_values("date").reset_index(drop=True)
            closes = df_sorted["close"].values
            for i in range(5, len(df_sorted) - 5):
                drop_5d = (closes[i] - closes[i - 5]) / closes[i - 5]
                if drop_5d >= -0.10:
                    continue
                ma20 = float(np.mean(closes[max(0, i - 20) : i]))
                if ma20 <= 0:
                    continue
                if closes[i] < ma20 * 0.95:
                    bounce = (closes[i] - closes[i - 1]) / closes[i - 1]
                    if bounce <= 0:
                        continue
                future_ret = (closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[
                    i
                ]
                occurrences.append(
                    {
                        "symbol": symbol,
                        "date": str(df_sorted.iloc[i]["date"]),
                        "return_pct": round(float(future_ret * 100), 4),
                    }
                )
        result = self._filter_pattern(
            occurrences,
            "mean_reversion",
            "5日跌幅>10%后从支撑位反弹的均值回归形态",
            {"lookback_days": 5, "min_drop_pct": 10.0, "support_factor": 0.95},
        )
        return [result] if result else []

    def _detect_breakout(
        self,
        ledger_df: pd.DataFrame,
        frames: dict[str, pd.DataFrame],
    ) -> list[DiscoveredPattern]:
        occurrences: list[dict[str, Any]] = []
        for symbol, df in frames.items():
            if df is None or len(df) < 60:
                continue
            df_sorted = df.sort_values("date").reset_index(drop=True)
            closes = df_sorted["close"].values
            highs = df_sorted["high"].values if "high" in df_sorted else closes
            volumes = df_sorted["volume"].values if "volume" in df_sorted else None
            for i in range(60, len(df_sorted) - 5):
                high_60 = float(np.max(highs[i - 60 : i]))
                if closes[i] <= high_60:
                    continue
                if volumes is not None:
                    vol_ma = float(np.mean(volumes[max(0, i - 20) : i]))
                    if vol_ma > 0 and volumes[i] < vol_ma * 1.3:
                        continue
                future_ret = (closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[
                    i
                ]
                occurrences.append(
                    {
                        "symbol": symbol,
                        "date": str(df_sorted.iloc[i]["date"]),
                        "return_pct": round(float(future_ret * 100), 4),
                    }
                )
        result = self._filter_pattern(
            occurrences,
            "breakout",
            "突破60日新高且成交量放大的突破形态",
            {"lookback_days": 60, "volume_multiplier": 1.3},
        )
        return [result] if result else []

    def _detect_seasonal(
        self,
        ledger_df: pd.DataFrame,
        frames: dict[str, pd.DataFrame],
    ) -> list[DiscoveredPattern]:
        monthly_returns: dict[str, dict[int, list[float]]] = {}
        for symbol, df in frames.items():
            if df is None or len(df) < 60:
                continue
            df_sorted = df.sort_values("date").reset_index(drop=True)
            closes = df_sorted["close"].values
            dates = df_sorted["date"].astype(str).values
            for i in range(1, len(df_sorted)):
                month = int(dates[i][5:7])
                ret = (closes[i] - closes[i - 1]) / closes[i - 1]
                monthly_returns.setdefault(symbol, {}).setdefault(month, []).append(ret)

        occurrences: list[dict[str, Any]] = []
        for symbol, month_data in monthly_returns.items():
            for month, rets in month_data.items():
                if len(rets) < 20:
                    continue
                avg_ret = float(np.mean(rets))
                win_rate = sum(1 for r in rets if r > 0) / len(rets)
                if win_rate >= self.min_win_rate and avg_ret > 0:
                    occurrences.append(
                        {
                            "symbol": symbol,
                            "date": f"month_{month:02d}",
                            "return_pct": round(avg_ret * 100, 4),
                            "month": month,
                            "win_rate": win_rate,
                        }
                    )

        if not occurrences:
            return []

        month_agg: dict[int, list[float]] = {}
        for o in occurrences:
            month_agg.setdefault(o["month"], []).append(o["return_pct"])

        results: list[DiscoveredPattern] = []
        now = now_shanghai().strftime("%Y-%m-%d")
        for month, rets in sorted(month_agg.items()):
            if len(rets) < 3:
                continue
            avg = float(np.mean(rets))
            wr = sum(1 for r in rets if r > 0) / len(rets)
            if wr >= self.min_win_rate:
                conf = min(1.0, wr * (1.0 + np.log1p(len(rets)) / 10.0))
                results.append(
                    DiscoveredPattern(
                        pattern_id=f"pat_{uuid.uuid4().hex[:8]}",
                        pattern_type="seasonal",
                        description=f"月份{month:02d}季节性效应：多只标的在该月份持续表现优异",
                        conditions={
                            "month": month,
                            "min_observed_win_rate": round(wr, 4),
                        },
                        historical_win_rate=round(wr, 4),
                        historical_avg_return=round(avg, 4),
                        sample_size=len(rets),
                        confidence=round(conf, 4),
                        first_seen=now,
                        last_seen=now,
                    )
                )
        return results


def format_discovered_patterns(patterns: list[DiscoveredPattern]) -> str:
    if not patterns:
        return "未发现满足条件的研究形态。"
    lines = [
        "# 研究形态发现报告",
        "",
        "仅供研究复核：以下结果使用历史后验窗口统计，不会自动写入主链、阈值或纸面复核名单。",
        "",
        f"共发现 {len(patterns)} 个形态",
        "",
    ]
    type_labels = {
        "momentum_burst": "动量爆发",
        "volume_divergence": "量价背离",
        "mean_reversion": "均值回归",
        "breakout": "突破",
        "seasonal": "季节性",
    }
    for p in patterns:
        label = type_labels.get(p.pattern_type, p.pattern_type)
        lines.extend(
            [
                f"## {label}: {p.description}",
                "",
                f"- 模式ID: `{p.pattern_id}`",
                f"- 类型: `{p.pattern_type}`",
                f"- 历史胜率: {p.historical_win_rate:.2%}",
                f"- 历史平均收益: {p.historical_avg_return:.2f}%",
                f"- 样本量: {p.sample_size}",
                f"- 置信度: {p.confidence:.2%}",
                "- 输出状态: research_candidate / proposal_only",
                f"- 首次出现: {p.first_seen}",
                f"- 最后出现: {p.last_seen}",
                f"- 条件: {p.conditions}",
                "",
            ]
        )
    return "\n".join(lines)
