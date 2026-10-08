"""`VolumeSurge` —— 量能冲击独立维度（从 `volume` 因子里拆出的最强子分）。

## 为什么拆出来（2026-10-08 实测依据）

把 `volume` 因子内部的三个子分**逐一单独实测**（400 票 / 125 截面、生产代码、逐截面 IC）：

| 子分 | 内部权重 | IC | t | 判定 |
|---|---|---|---|---|
| **surge**（量能冲击） | 0.40 | **−0.0370** | **−4.81** | ★ **最强** |
| breakout（价突破） | 0.35 | −0.0264 | −2.24 | 显著但含价成分 |
| correlation（量价相关） | 0.25 | −0.0037 | −0.52 | ✗ 无 alpha |

⇒ **`volume` 的有效性几乎全部来自 `surge`**；`correlation` 子分不贡献任何收益。

**独立性**（同批实测，两两截面相关）：
```
ρ(surge, momentum) = +0.500   ρ(surge, triple_rise) = +0.580   ⇒ 中等相关（同一「近期表现」簇）
ρ(surge, breakout) = +0.552   ρ(surge, correlation) = −0.237
```
⇒ `surge` 属「量」的信息源，与「价」源的 momentum/triple_rise/breakout 只是中等相关
（对比：`ρ(mom, tr)=0.609`、`ρ(mom, breakout)=0.666` 更高）。

## 与 `volume` 因子的关系
- **直接复用** `VolumeBreakoutStrategy._volume_surge`（同一算法，无代码重复）；
- `volume` 因子 = surge/breakout/correlation 三者加权（0.40/0.35/0.25），
  其中 **breakout 与 correlation 稀释了 surge** ⇒ 本维度提供「只取 surge」的选项。

## 口径与红线
- 只用日线 `volume` ⇒ **不需要 PIT 财务** ⇒ 不在 F10 空转名单；
- 🔴 **PIT 合规**：只用截面日及之前的数据（`df` 由调用方切到 `loc[:date]`），
  无负向位移、无中心化 rolling、无全期归一化（AGENTS.md §5 红线）；
- 分数域 `[0,1]`（`_volume_surge` 原生即在 [0,1]）；
- 实测 IC 为**负** ⇒ 近 3 年 A 股反向有效 ⇒ `invert_signal` 显式纠正，**默认 False**。
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, VolumeSurgeThresholds, load_thresholds
from aqsp.strategies.volume import VolumeBreakoutStrategy


class VolumeSurge(BaseStrategy):
    """量能冲击（`volume` 因子的 surge 子分单独维度）。

    实测（2026-10-08，400 票 / 125 截面、生产口径）：IC = **−0.0370**、t = **−4.81**
    —— `volume` 三个子分里最强的一个。
    """

    name: str = "volume_surge"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="volume_surge",
            version=self.thresholds.version,
            hypothesis=(
                "量能冲击（当前量 / MA20 量）是独立于价格的量能信息源；"
                "volume 因子中 breakout/correlation 稀释了它，故单独成维"
            ),
        )
        # 复用 volume 的 surge 算法（不复制实现）
        self._impl = VolumeBreakoutStrategy(config, self.thresholds)

    @property
    def _cfg(self) -> VolumeSurgeThresholds:
        return self.thresholds.volume_surge

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        cfg = self._cfg
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or df.empty or len(df) < cfg.volume_ma_period + 2:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._score(df, cfg)
        return scores

    def _score(self, df: pd.DataFrame, cfg: VolumeSurgeThresholds) -> float:
        # 🔴 PIT：df 已由调用方切到 `loc[:date]`，此处只做窗口裁剪
        w = df.sort_values("date").tail(cfg.lookback_days)
        score = self._impl._volume_surge(
            w["volume"].values, cfg.volume_ma_period, cfg.surge_multiplier
        )
        # ⚠️ 方向纠正必须 `1 - score` 而非 `-score`：
        # 本类末行 clamp 会把负值压成 0 ⇒ 维度退化成常量（无区分度）。
        if cfg.invert_signal:
            score = 1.0 - score
        return max(0.0, min(1.0, float(score)))