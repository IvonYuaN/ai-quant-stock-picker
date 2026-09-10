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
  - 全部点-in-time：只用截面日及之前的数据，禁止负向 shift / 中心化 rolling，
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
from aqsp.strategies.family_v2 import HighTightFlagStrategy as HighTightFlagCandidate
from aqsp.strategies.thresholds import Thresholds, load_thresholds

__all__ = ["RpsCandidate", "HighTightFlagCandidate"]


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


# 高窄旗形的实现已迁至 family_v2（单一事实来源：HighTightFlagStrategy）。
# 此处通过顶部 import 的别名 HighTightFlagCandidate 保留兼容（诊断脚本仍按旧名引用）。
