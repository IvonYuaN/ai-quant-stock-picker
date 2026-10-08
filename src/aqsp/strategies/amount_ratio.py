"""`AmountRatioStrategy` —— 成交额比（资金/换手轴的单因子信号）。

## 为什么新增（2026-10-08 批量扫描实测，25 因子 × 4 板块）

扫描 `outputs/factor_scan.csv` 显示 `amount_ratio_5` 在深/沪/创 3 板块显著（科弱）：
| 板块 | amount_ratio_5 t | 判定 |
|---|---|---|
| 深主板 | −5.86 | ★ |
| 沪主板 | −5.87 | ★ |
| 创业板 | −8.28 | ★ |
| 科创板 | −1.42 | （弱）|

⇒ **3/4 显著**，IC 为负（成交额放得越大 → 未来 5 日收益越低，类「放量见顶/派发」的短期反转）。

## 权威公式一致性（无第 6 次静默失效风险）
- 扫描脚本 `factor_batch_scan.py:61`：`"amount_ratio_5": amt / amt.rolling(5).mean()`
- 权威管道 `auto_factor_mining.calculate_factor_value:269-271`：`amt_ma5 = amount.rolling(5).mean(); return amount / amt_ma5.replace(0, np.nan)`
- 两者**逐字一致** ⇒ 扫描 CSV 的 t 值可直接采信，符号不必重算反转。

## 权威公式（auto_factor_mining.calculate_factor_value:269-271，逐字一致）
```
amt_ma5 = amount.rolling(5).mean()
raw = amount / amt_ma5
```

## 口径与红线
- 只用日线 `amount`（成交额 = 价格 × 成交量）⇒ 不需 PIT 财务；
- 🔴 PIT 合规：只用截面日及之前（`df` 由调用方切到 `loc[:date]`），无负向位移；
- 分数域 `[0,1]`；IC 为负 ⇒ `invert_signal` 默认 False（启用时须显式设 True）。
- 取最新一根 K 线的成交额比（5 日均线为滚动基准）。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import (
    Thresholds,
    AmountRatioThresholds,
    load_thresholds,
)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


class AmountRatioStrategy(BaseStrategy):
    """成交额比 `amount / amount_ma5`。实测 3/4 板块显著、IC 为负。"""

    name: str = "amount_ratio"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="amount_ratio",
            version=self.thresholds.version,
            hypothesis=(
                "近期成交额相对 5 日均线放大 → 未来短期收益越低（放量见顶/派发的短期反转）；"
                "批量扫描显示深/沪/创 3 板块显著负 IC"
            ),
        )

    @property
    def _cfg(self) -> AmountRatioThresholds:
        return self.thresholds.amount_ratio

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

        amount = w["amount"].astype(float)
        amt_ma5 = amount.rolling(5).mean().replace(0, np.nan)
        raw = float((amount / amt_ma5).iloc[-1])

        # 原生方向归一化（更高原生值 → 更高 native 分）；clip 到 [0,1]
        native = _clip01(raw / cfg.scale)
        # ⚠️ 方向纠正必须 `1 - score` 而非 `-score`：末行 clamp 会把负值压成 0
        # ⇒ 维度退化成常量（无区分度）。
        if cfg.invert_signal:
            native = 1.0 - native
        return _clip01(native)
