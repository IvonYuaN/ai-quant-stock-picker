"""股东户数筹码集中度因子（2026-11-10 真突破：与 OHLCV 反转正交的正号新维度）。

## 为什么新增（探针实测，本地 daily_qfq + prod pit_cache/holder_count.csv）

现役因子池实际只有 momentum + price_volume_corr 两个且**都 invert_signal=true**
（1−score，反转），8 个候选 OHLCV 因子全部 IC 为负且高度共线
（vol20↔amp20=0.91、vr5_20↔turn20=0.73）⇒ 风格塌缩，是 gate DSR fail 的 suspect 根因
（memory：「因子池只剩 2 个，且都已反向 ⇒ 想改善必须补因子，调权重无用」）。

本因子用 `prod pit_cache/holder_count.csv`（股东户数季度报表）：
- 因子 = −Δ股东户数%（季度环比）：户数**减少** = 筹码**集中** = 看多。
- 探针（本地 daily_qfq，截面 Spearman，5 日）：**IC = +0.073**（正号！
  与现役全负 IC 的 OHLCV 反转因子相反），对 mom20/rev5/turn20 截面相关
  ≈0（0.007 / 0.072 / −0.004）⇒ **真正正交、可互补**。
- 量级（5 日 IC 0.073）与现役因子（0.04~0.07）同级，但符号相反 ⇒
  补一个正交正因子有望直接撑开因子池、改善 gate。

## 口径与红线

- 数据源：复用 `aqsp.data.holder_num.HolderNumSource`（读 pit_cache/holder_count.csv）。
  本地验证用环境变量 `AQSP_HOLDER_COUNT_CSV` 指定落地的 CSV 副本；生产用默认 runtime 路径。
- **PIT 合规**：每个 symbol 用其 OHLCV 帧的**最后一行日期**作为截面日 `as_of`，
  只取 `quarter_end <= as_of` 的最新一期 QoQ 变化 ⇒ 无前视（lookahead）。
- 分数域 `[0,1]`：线性映射 `score = clamp(0.5 - spread * pct, 0.001, 0.999)`，
  spread 默认 2.0（pct=−0.1 集中 → 0.7；pct=+0.1 扩散 → 0.3）。
- **方向纠正用 `1 - score` 而非 `-score`**（与全仓一致：末行 clamp 会把负值压成 0 ⇒ 退化常量）。
  但本因子原生即「集中=看涨」、IC 为正 ⇒ **invert_signal 默认 False（不反转）**。
  与 pvc/volume_surge 等 IC 为负的因子相反约定，务必保持 False。
- 缺失（无股东户数 / 不足两期）：返回 0.5（中性），不拖垮其它维度。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import HolderConcentrationThresholds, load_thresholds
from aqsp.data.holder_num import HolderNumSource


def _parse_date(v: object) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


class HolderConcentrationStrategy(BaseStrategy):
    """股东户数季度环比集中度（−Δ户数%）。

    实测 IC 为正 ⇒ 原生看涨 ⇒ 不反转。与现役 OHLCV 反转因子正交。
    """

    name: str = "holder_concentration"

    def __init__(self, config: StrategyConfig, thresholds=None):
        self.thresholds = thresholds or load_thresholds()
        cache_path = os.environ.get("AQSP_HOLDER_COUNT_CSV") or None
        self._source = HolderNumSource(cache_path=cache_path)
        self._series: Dict[str, List[tuple[date, float]]] = {}
        self._loaded = False
        super().__init__(
            config,
            id="holder_concentration",
            version=self.thresholds.version,
            hypothesis=(
                "股东户数季度环比减少（筹码集中）预示未来超额收益；"
                "该信号来自基本面/筹码结构，与 OHLCV 价格反转因子正交"
            ),
        )

    # ---- 数据加载（惰性、PIT） ----
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            items = self._source.load()
        except Exception:
            items = []
        by_sym: Dict[str, List[tuple[date, float]]] = {}
        for it in items:
            qe = _parse_date(it.quarter)
            try:
                hc = float(it.holder_count or 0)
            except (TypeError, ValueError):
                continue
            if qe is None or hc <= 0:
                continue
            by_sym.setdefault(it.symbol, []).append((qe, hc))
        for sym, lst in by_sym.items():
            lst.sort(key=lambda x: x[0])
            changes: List[tuple[date, float]] = []
            for i in range(1, len(lst)):
                prev, cur = lst[i - 1][1], lst[i][1]
                if prev > 0:
                    changes.append((lst[i][0], (cur - prev) / prev))
            if changes:
                self._series[sym] = changes
        self._loaded = True

    def _change_as_of(self, sym6: str, as_of: Optional[date]) -> Optional[float]:
        if as_of is None:
            return None
        series = self._series.get(sym6)
        if not series:
            return None
        chosen: Optional[float] = None
        for qe, pct in series:
            if qe <= as_of:
                chosen = pct
            else:
                break
        return chosen

    @staticmethod
    def _frame_as_of(df: pd.DataFrame) -> Optional[date]:
        if df is None or len(df) == 0:
            return None
        for col in ("trade_date", "date"):
            if col in df.columns:
                return _parse_date(df[col].iloc[-1])
        idx = df.index
        if isinstance(idx, pd.DatetimeIndex):
            return idx[-1].date()
        return _parse_date(idx[-1])

    # ---- 打分 ----
    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        self._ensure_loaded()
        cfg = self._cfg
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or len(df) == 0:
                scores[symbol] = 0.5
                continue
            pct = self._change_as_of(symbol[:6], self._frame_as_of(df))
            if pct is None:
                scores[symbol] = 0.5
                continue
            # 集中（pct<0）→ 高分；扩散（pct>0）→ 低分。
            score = 0.5 - cfg.spread * pct
            score = float(max(0.001, min(0.999, score)))
            if cfg.invert_signal:
                score = 1.0 - score
            scores[symbol] = score
        return scores

    @property
    def _cfg(self) -> HolderConcentrationThresholds:
        return self.thresholds.holder_concentration
