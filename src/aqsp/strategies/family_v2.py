"""family_v2.py — 选股内核 v2 因子族（反转 / 低波动 / 强势后收敛）。

背景（决定性结论见 outputs/regime_条件化IC_结论_2026-09-10.md）：
  gate 主变体的 momentum/composite 因子族在 2023–2026 窗口 **IC 显著为负（反向失效）**，
  且**分市场状态在所有状态下都为负**（bull −0.0541 / bear −0.1015）——说明问题不是"用错状态"，
  而是**因子族本身反向**。因此 v2 的方向是**更换因子族**，而不是在失效族内调参或做 regime 条件化。

本模块提供 3 个 v2 因子（连续打分、点-in-time、仅用截面日及之前数据）：
  - HighTightFlagStrategy：强势后极窄缩量收敛（波动收敛 → 延续）。全研究唯一稳健正向因子
    （gate 口径 IC +0.0366 t=+2.45；分状态全正）。
  - LowVolatilityStrategy：横截面低波动异象（低波动 → 风险调整后更优）。
  - PullbackContinuationStrategy：强势后"回调不破位"延续（深于旗形、但结构未破坏）。

纪律：
  - 只用 OHLCV 原始列（date/open/high/low/close/volume），不依赖上游预注入指标列，避免口径漂移。
  - 全部点-in-time；禁止负向 shift / 中心化 rolling（通过 look-ahead 静态守卫）。
  - 仅产出打分；进不进 composite 由调用方（CompositeStrategy）按权重决定，默认权重 0（不改变现状）。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _tail(df: pd.DataFrame, col: str) -> np.ndarray:
    return df.sort_values("date")[col].astype(float).to_numpy()


class HighTightFlagStrategy(BaseStrategy):
    """高而窄的旗形（波动收敛）——强势后极窄缩量整理的延续。

    构造（全部点-in-time，用截面日及之前窗口）：
      - 强动量：近 strong_win 日 最高/最低 > mom_min
      - 极窄整理：近 tight_win 日 最高/最低 < tight_max（振幅收敛）
      - 高位抗跌：近 tight_win 日最低 >= strong_win 日最高 * level_floor
      - 缩量：当日成交量 < 近 20 日均量 * vol_shrink
    """

    name: str = "high_tight_flag"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        p = config.params or {}
        self.strong_win = int(p.get("strong_win", 40))
        self.tight_win = int(p.get("tight_win", 10))
        self.mom_min = float(p.get("mom_min", 1.6))
        self.tight_max = float(p.get("tight_max", 1.15))
        self.level_floor = float(p.get("level_floor", 0.8))
        self.vol_shrink = float(p.get("vol_shrink", 0.6))
        super().__init__(
            config,
            id="high_tight_flag",
            version=self.thresholds.version,
            hypothesis="强势后的极窄缩量整理（波动收敛）后倾向延续/突破，未来收益为正。",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        return {
            sym: (self._calculate_single_score(df) if df is not None else float("nan"))
            for sym, df in data.items()
        }

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        if df is None or len(df) < self.strong_win:
            return 0.0
        df = df.sort_values("date")
        high = _tail(df, "high")
        low = _tail(df, "low")
        vol = _tail(df, "volume")

        high40 = float(np.max(high[-self.strong_win :]))
        low40 = float(np.min(low[-self.strong_win :]))
        if low40 <= 0:
            return 0.0
        momentum_ratio = high40 / low40

        high10 = float(np.max(high[-self.tight_win :]))
        low10 = float(np.min(low[-self.tight_win :]))
        if low10 <= 0:
            return 0.0
        tightness = high10 / low10

        mom_score = (
            _clamp((momentum_ratio - 1.0) / (self.mom_min - 1.0))
            if momentum_ratio > 1.0
            else 0.0
        )
        tight_score = (
            _clamp((self.tight_max - tightness) / (self.tight_max - 1.0))
            if tightness < self.tight_max
            else 0.0
        )
        level_score = 1.0 if (low10 >= high40 * self.level_floor) else 0.0
        vol_ma20 = float(np.mean(vol[-21:-1])) if len(vol) >= 21 else 0.0
        vol_score = (
            1.0 if (vol_ma20 > 0 and vol[-1] < vol_ma20 * self.vol_shrink) else 0.0
        )

        score = (
            mom_score * 0.4 + tight_score * 0.4 + level_score * 0.1 + vol_score * 0.1
        )
        return _clamp(score)


class LowVolatilityStrategy(BaseStrategy):
    """横截面低波动异象：已实现波动率越低，打分越高。

    构造（点-in-time）：逐票取近 vol_window 日收益率的标准差，再在全市场横截面上做百分位排名，
    低波动 → 高分（1 - pct_rank）。这是风险调整后收益方向的候选，与动量族构造不同源。
    """

    name: str = "low_volatility"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        p = config.params or {}
        self.vol_window = int(p.get("vol_window", 20))
        self.ref_vol = float(p.get("ref_vol", 0.05))
        super().__init__(
            config,
            id="low_volatility",
            version=self.thresholds.version,
            hypothesis="低波动率股票在该窗口的风险调整后收益更优（低波动异象）。",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        vols: Dict[str, float] = {}
        for sym, df in data.items():
            v = self._realized_vol(df)
            if v == v:  # 非 NaN
                vols[sym] = v
        if not vols:
            return {sym: float("nan") for sym in data}
        ranks = pd.Series(vols).rank(pct=True)  # 高波动 → rank 大
        out: Dict[str, float] = {}
        for sym in data:
            r = ranks.get(sym, float("nan"))
            out[sym] = float(1.0 - r) if r == r else float("nan")
        return out

    def _realized_vol(self, df: pd.DataFrame) -> float:
        if df is None or len(df) < self.vol_window + 1:
            return float("nan")
        p = _tail(df, "close")
        rets = p[1:] / p[:-1] - 1.0
        if len(rets) < self.vol_window:
            return float("nan")
        seg = rets[-self.vol_window :]
        return float(np.std(seg))

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        v = self._realized_vol(df)
        if v != v:
            return 0.0
        return _clamp(1.0 - v / self.ref_vol)


class PullbackContinuationStrategy(BaseStrategy):
    """强势后"回调不破位"延续（比旗形更深的回撤，但结构未破坏）。

    构造（点-in-time）：
      - 前期强势：近 strong_win 日 最高/最低 > mom_min
      - 适度回调：现价距近 tight_win 日高点回撤处于 [pullback_min, pullback_max] 甜区
      - 结构不破：近 tight_win 日最低 >= 近 tight_win 日高点 * structure_floor
      - 站上均线：现价 >= 近 ma_ref 日均值
      - 缩量：当日量 < 近期均量 * vol_shrink
    连续打分（分量分级），越高=越符合"强势后缩量回调、结构完好"的延续形态。
    """

    name: str = "pullback_continuation"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        p = config.params or {}
        self.strong_win = int(p.get("strong_win", 40))
        self.tight_win = int(p.get("tight_win", 10))
        self.mom_min = float(p.get("mom_min", 1.3))
        self.pullback_min = float(p.get("pullback_min", 0.03))
        self.pullback_max = float(p.get("pullback_max", 0.15))
        self.structure_floor = float(p.get("structure_floor", 0.85))
        self.ma_ref = int(p.get("ma_ref", 20))
        self.vol_shrink = float(p.get("vol_shrink", 0.8))
        super().__init__(
            config,
            id="pullback_continuation",
            version=self.thresholds.version,
            hypothesis="强势上涨后缩量浅回调且结构未破坏的股票，二次延续概率更高。",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        return {
            sym: (self._calculate_single_score(df) if df is not None else float("nan"))
            for sym, df in data.items()
        }

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        need = max(self.strong_win, self.ma_ref) + 1
        if df is None or len(df) < need:
            return 0.0
        df = df.sort_values("date")
        close = _tail(df, "close")
        high = _tail(df, "high")
        low = _tail(df, "low")
        vol = _tail(df, "volume")

        high_sw = float(np.max(high[-self.strong_win :]))
        low_sw = float(np.min(low[-self.strong_win :]))
        if low_sw <= 0:
            return 0.0
        momentum_ratio = high_sw / low_sw

        recent_high = float(np.max(high[-self.tight_win :]))
        recent_low = float(np.min(low[-self.tight_win :]))
        cur = float(close[-1])
        if recent_high <= 0 or cur <= 0:
            return 0.0

        pullback = (recent_high - cur) / recent_high

        # 强动量分（分级）
        mom_score = (
            _clamp((momentum_ratio - 1.0) / (self.mom_min - 1.0))
            if momentum_ratio > 1.0
            else 0.0
        )
        # 回调甜区分（三角/梯形分级）：<0 未回撤=0；[min,max]=1；外侧线性衰减
        if pullback <= 0.0:
            pb_score = 0.0
        elif pullback < self.pullback_min:
            pb_score = pullback / self.pullback_min
        elif pullback <= self.pullback_max:
            pb_score = 1.0
        else:
            pb_score = _clamp(1.0 - (pullback - self.pullback_max) / self.pullback_max)
        # 结构不破（分级：离 floor 越远越高）
        floor = recent_high * self.structure_floor
        structure_score = (
            _clamp((recent_low - floor) / (recent_high - floor))
            if recent_high > floor
            else 0.0
        )
        # 站上均线（分级）
        ma_ref_val = float(np.mean(close[-self.ma_ref :]))
        ma_score = _clamp((cur / ma_ref_val - 1.0) / 0.05) if ma_ref_val > 0 else 0.0
        # 缩量（分级）
        vol_ma = float(np.mean(vol[-21:-1])) if len(vol) >= 21 else 0.0
        vol_score = _clamp((vol_ma - vol[-1]) / (vol_ma * 0.5)) if vol_ma > 0 else 0.0

        score = (
            mom_score * 0.25
            + pb_score * 0.30
            + structure_score * 0.20
            + ma_score * 0.15
            + vol_score * 0.10
        )
        return _clamp(score)
