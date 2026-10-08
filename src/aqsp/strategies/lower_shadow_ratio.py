"""`LowerShadowRatioStrategy` —— 下影线比（蜡烛形态轴的单因子信号）。

## 为什么新增（2026-10-08 批量扫描 + 权威公式重算）

批量扫描 `outputs/factor_scan.csv` 显示 `lower_shadow_ratio` 在深/沪/创 3 板块显著，
但 **扫描脚本 `factor_batch_scan.py:73` 用的公式与该因子在因子挖掘管道里的权威定义
`auto_factor_mining.calculate_factor_value:254-255` 不一致**：

- 扫描脚本（范围归一 + 10 日平滑）：`rolling10_mean((min(open,close)-low)/(high-low))` ⇒ 报告 IC **正** (+3.90)
- 权威管道（单日、除以收盘价）：`(min(open,close) - low) / close` ⇒ **真实 IC 为负**（见下）

⚠️ **两公式符号相反** ⇒ 若直接挪用扫描 CSV 的数字会彻底搞反方向（第 6 次静默失效陷阱）。
本策略严格对齐权威管道公式，并用生产代码逐板块重算 IC 确认：

| 板块 | 权威公式 IC | t | 判定 |
|---|---|---|---|
| 深主板 | −0.0336 | −6.98 | ★ |
| 沪主板 | −0.0212 | −4.16 | ★ |
| 创业板 | −0.0295 | −6.09 | ★ |
| 科创板 | −0.0156 | −3.60 | ★ |

⇒ **4/4 全显著**（比扫描 CSV 的 3/4 还强），IC 为负。
含义：当日下影线越长（盘中下探越深又拉回）→ 未来 5 日收益越低（短期反转/获利了结压力）。
⇒ 原生方向反，**启用时须 `invert_signal=True`**。

## 权威公式（auto_factor_mining.calculate_factor_value:254-255，逐字一致）
```
lower_shadow_ratio = (min(open, close) - low) / close
```

## 口径与红线
- 只用日线 OHLC ⇒ 不需要 PIT 财务；
- 🔴 PIT 合规：只用截面日及之前（`df` 由调用方切到 `loc[:date]`），无负向位移、无中心化 rolling；
- 分数域 `[0,1]`；IC 为负 ⇒ `invert_signal` 默认 False（启用时须显式设 True）。
- 单日因子（lookback_period=1）：取最新一根 K 线的下影线比。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import (
    Thresholds,
    LowerShadowRatioThresholds,
    load_thresholds,
)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


class LowerShadowRatioStrategy(BaseStrategy):
    """下影线比 `(min(open, close) - low) / close`。实测 4/4 板块显著、IC 为负。"""

    name: str = "lower_shadow_ratio"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="lower_shadow_ratio",
            version=self.thresholds.version,
            hypothesis=(
                "当日下影线越长 → 未来短期收益越低（短期反转/获利了结）；"
                "批量扫描 + 权威公式重算均显示 4/4 板块显著负 IC"
            ),
        )

    @property
    def _cfg(self) -> LowerShadowRatioThresholds:
        return self.thresholds.lower_shadow_ratio

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
        low = w["low"].astype(float)
        op = w["open"].astype(float)
        # 权威公式：最新一根 K 线的下影线比（单日）
        raw = float(((np.minimum(op, close) - low) / close.where(close > 0)).iloc[-1])

        # 原生方向归一化（更高原生值 → 更高 native 分）；clip 到 [0,1]
        native = _clip01(raw / cfg.scale)
        # ⚠️ 方向纠正必须 `1 - score` 而非 `-score`：末行 clamp 会把负值压成 0
        # ⇒ 维度退化成常量（无区分度）。
        if cfg.invert_signal:
            native = 1.0 - native
        return _clip01(native)
