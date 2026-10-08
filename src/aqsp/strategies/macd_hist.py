"""`MACDHistStrategy` —— MACD 柱（动量轴的稳健子信号）。

## 为什么新增（2026-10-08 批量扫描实测，25 因子 × 4 板块、生产口径、逐截面 IC）

动量轴现只有 `momentum`（**1/4** 显著、IC 弱），而 `macd_hist` 是动量类里**唯一 3/4 一致显著**的：

| 板块 | macd_hist t（批量扫描） | 判定 |
|---|---|---|
| 深主板 | −5.55 | ★ |
| 沪主板 | −3.76 | ★ |
| 创业板 | −1.88 | 临界 |
| 科创板 | −2.28 | ★ |

⇒ **它是比 `momentum` 更强的动量信号**（与 momentum ρ=0.29~0.57，部分冗余但显著更强）。
IC 为负（MACD 柱越高 → 后续收益越低）⇒ 原生方向反。

## 权威公式（auto_factor_mining.calculate_factor_value:311-316，逐字一致）
```
ema12 = close.ewm(span=12, adjust=False).mean()
ema26 = close.ewm(span=26, adjust=False).mean()
dif = ema12 - ema26
dea = dif.ewm(span=9, adjust=False).mean()
raw = (dif - dea) * 2
```

## 口径与红线
- 只用日线 `close` ⇒ **不需要 PIT 财务**；
- 🔴 PIT 合规：只用截面日及之前（`df` 由调用方切到 `loc[:date]`），无负向位移；
- 分数域 `[0,1]`；IC 为负 ⇒ `invert_signal` 默认 False（启用时须显式设 True）。
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import (
    Thresholds,
    MacdHistThresholds,
    load_thresholds,
)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


class MACDHistStrategy(BaseStrategy):
    """MACD 柱（(dif − dea) × 2）。实测 3/4 板块显著、IC 为负。"""

    name: str = "macd_hist"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="macd_hist",
            version=self.thresholds.version,
            hypothesis=(
                "MACD 柱捕捉动量的加速/ deceleration；"
                "批量扫描显示它比 price-based momentum 更稳健（3/4 vs 1/4）"
            ),
        )

    @property
    def _cfg(self) -> MacdHistThresholds:
        return self.thresholds.macd_hist

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
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        raw = float(((dif - dea) * 2).iloc[-1])

        # 原生方向归一化（更高原生值 → 更高 native 分）；clip 到 [0,1]
        native = _clip01(raw / cfg.scale)
        # ⚠️ 方向纠正必须 `1 - score` 而非 `-score`：末行 clamp 会把负值压成 0
        # ⇒ 维度退化成常量（无区分度）。
        if cfg.invert_signal:
            native = 1.0 - native
        return _clip01(native)
