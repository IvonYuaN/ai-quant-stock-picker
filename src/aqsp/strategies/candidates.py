"""candidates.py — 候选因子（来自外部方案的吸收，仅作诊断/候选，不进入 gate/composite）

背景：AQSP 现有因子（momentum / triple_rise / composite / volume）在 2023–2026 窗口 IC 显著为负
（反向失效，见 outputs/factor_ic_五因子诊断结论_2026-09-10.md）。诊断结论指明的方向是
**重审因子集**——引入与失效族不同源、不同 regime 暴露的新候选因子，再用同一套 IC 框架逐一验 IC。

本模块的两个候选因子吸收自开源 A 股选股系统 Sequoia-X（sngyai/Sequoia-X，7034★），
但做了关键改造以符合 AQSP 纪律：
  - Sequoia-X 用后复权(hfq)存储；本书写沿用 AQSP 诊断脚本既有的 qfq 价格源（与既有 5 因子同口径，
    保证 IC 可比），不引入未来复权信息。
  - Sequoia-X 因子是布尔"是否入选"；本书写改为**连续打分**（0~1，越高=信号越强），
    以便做横截面 Spearman IC。
  - 全部点-in-time：只用截面日及之前的数据，禁止 .shift(-N) / 中心化 rolling，
    通过 look-ahead 静态守卫（tests/test_runtime_redline_guard.py）。
  - 这里只产出打分，不进 composite、不进回测、不产出交易信号。

两个候选：
  - RpsCandidate：横截面相对强度（RPS）百分位——全市场 trailing 收益排名。与 AQSP 既有时间序列
    momentum 是**不同构造**，可能在不同 regime 下表现不同，正好检验"相对强度"方向。
  - HighTightFlagCandidate：强势后的极窄整理+缩量（Minervini 式波动收敛），是 continuation 类形态。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class RpsCandidate(BaseStrategy):
    """横截面相对强度（RPS）候选因子。

    构造：在给定截面日，对每只票计算 trailing `rps_period` 日收益率（仅用截面日及之前数据），
    再在全市场横截面上做百分位排名 → RPS∈[0,1]，越高=相对强度越强。
    这是 O'Neil 式 RPS 的连续化版本；与 AQSP 既有的时间序列 momentum 构造不同，
    用于检验"相对强度"方向在该窗口是否具正向 IC。
    """

    name: str = "rps_candidate"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        self.rps_period: int = int((config.params or {}).get("rps_period", 120))
        super().__init__(
            config,
            id="rps_candidate",
            version=self.thresholds.version,
            hypothesis="横截面相对强度（RPS）越高，未来收益越好（强者恒强/相对强度效应）",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        # 1) 逐票计算 trailing 收益（点-in-time，仅用截面日及之前）
        rets: Dict[str, float] = {}
        for sym, df in data.items():
            if df is None or df.empty:
                continue
            s = self._trailing_return(df)
            if s == s:  # 非 NaN 才保留
                rets[sym] = float(s)
        if not rets:
            return {sym: float("nan") for sym in data}
        # 2) 横截面百分位排名（RPS）
        series = pd.Series(rets)
        rps = series.rank(pct=True)  # 0~1
        return {sym: float(rps.get(sym, float("nan"))) for sym in data}

    def _trailing_return(self, df: pd.DataFrame) -> float:
        p = df["close"].astype(float).values
        k = self.rps_period
        if len(p) < k + 1 or p[-1 - k] == 0:
            return float("nan")
        return p[-1] / p[-1 - k] - 1.0

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        # 单票无法做横截面排名，退化为"归一化 trailing 收益"（tanh 压到 0~1）
        r = self._trailing_return(df)
        if r != r:
            return 0.0
        return _clamp(0.5 + float(np.tanh(r)), 0.0, 1.0)


class HighTightFlagCandidate(BaseStrategy):
    """高而窄的旗形（波动收敛）候选因子，连续化打分。

    构造（全部点-in-time，用截面日及之前窗口）：
      - 强动量：近 40 日 最高/最低 > mom_min（区间涨幅超阈值）
      - 极窄整理：近 10 日 最高/最低 < tight_max（振幅收敛）
      - 高位抗跌：近 10 日最低 >= 40 日最高 * level_floor
      - 缩量：当日成交量 < 近 20 日均量 * vol_shrink
    综合为 0~1 连续分，越高=越符合"强势后极窄整理"的 continuation 形态。
    """

    name: str = "high_tight_flag_candidate"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        p = config.params or {}
        self.strong_win: int = int(p.get("strong_win", 40))
        self.tight_win: int = int(p.get("tight_win", 10))
        self.mom_min: float = float(p.get("mom_min", 1.6))
        self.tight_max: float = float(p.get("tight_max", 1.15))
        self.level_floor: float = float(p.get("level_floor", 0.8))
        self.vol_shrink: float = float(p.get("vol_shrink", 0.6))
        super().__init__(
            config,
            id="high_tight_flag_candidate",
            version=self.thresholds.version,
            hypothesis="强势后的极窄缩量整理（波动收敛）后倾向延续/突破，未来收益为正",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for sym, df in data.items():
            out[sym] = self._calculate_single_score(df) if df is not None else float("nan")
        return out

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        if df is None or len(df) < self.strong_win:
            return 0.0
        df = df.sort_values("date")
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        vol = df["volume"].astype(float).values

        tail40_h = high[-self.strong_win :]
        tail40_l = low[-self.strong_win :]
        high40 = float(np.max(tail40_h))
        low40 = float(np.min(tail40_l))
        if low40 <= 0:
            return 0.0
        momentum_ratio = high40 / low40

        tail10_h = high[-self.tight_win :]
        tail10_l = low[-self.tight_win :]
        high10 = float(np.max(tail10_h))
        low10 = float(np.min(tail10_l))
        if low10 <= 0:
            return 0.0
        tightness = high10 / low10

        # 强动量分：1.0~mom_min 映射到 0~1，超过 mom_min 取 1
        mom_score = _clamp((momentum_ratio - 1.0) / (self.mom_min - 1.0)) if momentum_ratio > 1.0 else 0.0
        # 极窄整理分：tight_max~1.0 映射到 0~1（越窄越高）
        tight_score = _clamp((self.tight_max - tightness) / (self.tight_max - 1.0)) if tightness < self.tight_max else 0.0
        # 高位抗跌：近 10 日最低守住 40 日最高的 level_floor
        level_score = 1.0 if (low10 >= high40 * self.level_floor) else 0.0
        # 缩量：当日量 < 近 20 日均量 * vol_shrink
        vol_ma20 = float(np.mean(vol[-21:-1])) if len(vol) >= 21 else 0.0
        vol_score = 1.0 if (vol_ma20 > 0 and vol[-1] < vol_ma20 * self.vol_shrink) else 0.0

        # 形态主导（动量 + 收敛），量与位作为确认
        score = mom_score * 0.4 + tight_score * 0.4 + level_score * 0.1 + vol_score * 0.1
        return _clamp(score)
