"""`PriceVolumeLevelCorrelation` —— 价量**水平**滚动相关（修复 5 的新维度）。

## 为什么新增这个因子（2026-10-08 实测依据）

对现有 7 维做「IC 显著性 × 独立性」叠看，发现一个硬矛盾：

| 信号 | IC | t | 独立性 |
|---|---|---|---|
| momentum | −0.0328 | −2.59 | ρ(tr) +0.61 ⇒ 高度相关 |
| triple_rise | −0.0159 | −1.25 | ρ(mom) +0.61 |
| volume 整体 | −0.0324 | −3.07 | ρ(mom) +0.62 ⇒ 中等 |
| volume.surge | −0.0370 | −4.81 | ρ(mom) +0.50 |
| volume.breakout | −0.0264 | −2.24 | ρ(mom) +0.67 ⇒ 高度相关 |
| volume.correlation（**Δp vs Δv**） | −0.0037 | −0.52 | 独立但**无 alpha** |

⇒ **有 alpha 的那 4 个彼此高度相关（同一簇）；唯一独立的那个恰好没有信号。**
⇒ 在 7 维里调权重 = 在同一簇内部挪动 ⇒ **换不来多样性**。

**本因子 = `close.rolling(N).corr(volume)`（价量**水平**相关）**，实测（400 票 / 125 截面）：

```
vpc_level   IC = −0.0308   t = −3.62   ★ 显著
            ρ(·, momentum) = +0.285   ρ(·, surge) = +0.101   ★ 独立
```

⇒ **它是当前唯一「既有显著 alpha、又与现有信号正交」的因子** ⇒ 补进池子即可真正增加多样性。

## ⚠️ 与 `volume._volume_price_correlation` 的区别（**同名不同物，实测差 8 倍**）

| 实现 | 计算对象 | 实测 IC / t |
|---|---|---|
| `volume._volume_price_correlation` | `corr(Δprice, Δvolume)`（**变化量**） | −0.0037 / t=−0.52（无信号） |
| **本因子** | `corr(close, volume)`（**水平量**，滚动 10 日） | **−0.0308 / t=−3.62（显著）** |

⇒ 二者语义完全不同（短期量价配合 vs 中期价量关系），**不可互相替代**。

## 口径与红线

- 只用日线 `close`/`volume` ⇒ **不需要 PIT 财务** ⇒ 天然不在 F10 空转名单；
- **PIT 合规**：只用截面日及之前的数据（`df.loc[:date]`），无负向位移、无中心化 rolling、无全期归一化；
- 分数域 `[0,1]`（`corr ∈ [−1,1]` 线性映射到 `[0,1]`），**方向需按 IC 符号解读** ——
  近 3 年 A 股实测为**反向有效**（IC 为负）⇒ 用 `invert_signal` 显式纠正，**默认 False**。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import PriceVolumeCorrThresholds, load_thresholds


class PriceVolumeLevelCorrelation(BaseStrategy):
    """价量水平滚动相关（`close.rolling(N).corr(volume)`）。

    实测（2026-10-08，400 票 / 125 截面、生产口径）：IC = −0.0308、t = −3.62，
    且与 momentum/surge 的相关仅 +0.285 / +0.101 ⇒ **当前唯一「有显著 alpha 且正交」的因子**。
    """

    name: str = "price_volume_corr"

    def __init__(self, config: StrategyConfig, thresholds=None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="price_volume_corr",
            version=self.thresholds.version,
            hypothesis=(
                "价量水平滚动相关可捕捉中期价量配合关系；"
                "与 momentum/量能冲击等价格-成交量信号正交（实测 ρ≤0.285）"
            ),
        )

    @property
    def _cfg(self) -> PriceVolumeCorrThresholds:
        return self.thresholds.price_volume_corr

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
        need = cfg.window + 2
        if len(df) < need:
            return 0.5
        # 🔴 PIT：只用截面日及之前的数据（`df` 由调用方切到 `loc[:date]`）
        closes = df["close"].astype(float)
        volumes = df["volume"].astype(float)
        corr = closes.rolling(cfg.window).corr(volumes).iloc[-1]
        if not np.isfinite(corr):
            return 0.5
        # corr ∈ [-1,1] → [0,1]
        score = max(0.0, min(1.0, (float(corr) + 1.0) / 2.0))
        # ⚠️ 方向纠正必须用 `1 - score` 而非 `-score`：
        # 本类末行的 clamp 会把负值压成 0 ⇒ 维度退化成常量（无区分度）。
        if cfg.invert_signal:
            score = 1.0 - score
        return score