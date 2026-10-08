"""`VolatilityPositionStrategy` —— 波动/位置类因子维度（3 个稳健子分打包）。

## 为什么新增（2026-10-08 批量扫描实测，25 因子 × 4 板块、生产口径、逐截面 IC）

在已实现的 momentum(1/4)、triple_rise(噪声)、price_volume_corr(4/4)、
volume_surge(3/4) 之外，「波动/位置」轴有 3 个独立且稳健的候选：

| 子分 | 权威公式（auto_factor_mining.py） | 深/沪/创/科 t | 判定 |
|---|---|---|---|
| **amplitude_5d** | (high_5 − low_5)/close | −8.13/−4.39/−9.18/−1.91 | ★3/4 |
| **vol_of_vol_20** | std(std(returns,5),20) | −5.34/−2.26/−5.35/−2.04 | ★3/4 |
| **distance_to_ma60** | (close − ma60)/ma60 | −5.49/−2.04/−4.62/−1.13 | 2/4（深创强） |

三者 IC **皆为负**（高波动 / 远离均线 → 后续收益低），属「波动/位置」轴，
与动量/量能簇只有中等相关 ⇒ 提供与现有维度**不同方向的信息源**。

★ 口径对齐：本类的子分计算与 `auto_factor_mining.calculate_factor_value` **逐字一致**
（`normalize_ohlcv` 仅做中文列名→英文改名，不改数值，故原始列直算等价）。
批量扫描的 t 值即本算法。

## 口径与红线
- 只用日线 OHLCV ⇒ **不需要 PIT 财务** ⇒ 不在 F10 空转名单；
- 🔴 **PIT 合规**：只用截面日及之前的数据（`df` 由调用方切到 `loc[:date]`），
  无负向位移、无中心化 rolling、无全期归一化（AGENTS.md §5 红线）；
- 分数域 `[0,1]`；三子分 IC 皆负 ⇒ 原生方向反 ⇒ `invert_signal` 默认 **False**
  （与 price_volume_corr / volume_surge 同约定：启用时须显式设 True，否则赌反方向）。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import (
    Thresholds,
    VolatilityPositionThresholds,
    load_thresholds,
)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


class VolatilityPositionStrategy(BaseStrategy):
    """波动/位置类维度：amplitude_5d + vol_of_vol_20 + distance_to_ma60 三子分打包。

    实测（2026-10-08，批量扫描 25 因子 × 4 板块、生产口径）：
      amplitude_5d    深−8.13/沪−4.39/创−9.18/科−1.91  ★3/4
      vol_of_vol_20   深−5.34/沪−2.26/创−5.35/科−2.04  ★3/4
      distance_to_ma60 深−5.49/沪−2.04/创−4.62/科−1.13  2/4（深创强）
    ⇒ 三者皆负 IC（高波动/远离均线→后续收益低），与动量/量能簇中等相关。
    """

    name: str = "volatility_position"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="volatility_position",
            version=self.thresholds.version,
            hypothesis=(
                "波动/位置类信息（振幅、波动率之波动率、偏离均线程度）"
                "与动量/量能簇中等相关，提供不同轴的多空信号"
            ),
        )

    @property
    def _cfg(self) -> VolatilityPositionThresholds:
        return self.thresholds.volatility_position

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.5
                continue
            scores[symbol] = self._calculate_single_score(df)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        cfg = self._cfg
        w = df.sort_values("date").tail(cfg.lookback_days)
        if len(w) < cfg.min_history:
            return 0.5

        close = w["close"].astype(float)
        high = w["high"].astype(float)
        low = w["low"].astype(float)

        # 子分 1：amplitude_5d = (high_5 − low_5) / close
        high_5 = high.rolling(cfg.amplitude_window).max()
        low_5 = low.rolling(cfg.amplitude_window).min()
        amp = ((high_5 - low_5) / close).iloc[-1]

        # 子分 2：vol_of_vol_20 = std(std(returns, inner), outer)
        returns = close.pct_change()
        rolling_vol = returns.rolling(cfg.vol_of_vol_inner).std()
        vov = rolling_vol.rolling(cfg.vol_of_vol_outer).std().iloc[-1]

        # 子分 3：distance_to_ma60 = (close − ma60) / ma60
        ma60 = close.rolling(cfg.ma_period).mean()
        dist = ((close - ma60) / ma60.replace(0.0, np.nan)).iloc[-1]

        # 原生方向归一化（更高原生值 → 更高 native 分）；clip 到 [0,1]
        amp_n = _clip01(amp / cfg.amp_scale)
        vov_n = _clip01(vov / cfg.vov_scale)
        dist_n = _clip01((dist + cfg.dist_center) / (2.0 * cfg.dist_center))

        native = (
            amp_n * cfg.w_amplitude
            + vov_n * cfg.w_vol_of_vol
            + dist_n * cfg.w_distance
        )
        # ⚠️ 方向纠正必须 `1 - score` 而非 `-score`：末行 clamp 会把负值压成 0
        # ⇒ 维度退化成常量（无区分度）。
        if cfg.invert_signal:
            native = 1.0 - native
        return _clip01(native)
