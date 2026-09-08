"""CYQ 筹码分布 —— 获利比例 / 平均成本 / 成本区间 / 集中度 / 筹码峰。

移植自 `simonlin1212/a-stock-data` v3.8.0 §4.6（该版本经上游 Codex 独立审计 PASS）。
按 AQSP 契约改造：纯函数、type hints 齐全、数据异常统一抛 `DataError`、
返回 frozen dataclass。

算法要点（与上游一致，含三处防坑修正）：
1. **本地推演**：历史筹码按换手率衰减，当日成交量按三角分布撒进 `[low, high]`。
   东财没有公开 CYQ 接口，业界通行做法是本地推演，零新增数据源。
2. **首日全流通盘播种**：初始筹码 = 首日全部流通盘的三角分布，不能从全零起步。
   否则会把"窗口之前的存量持仓"一笔勾销——两个 1% 换手日（价 10 和 100）
   会被算成约 50/50，真实应约 99% 仍在 10 附近。
3. **换手衰减递推**：`chips = chips*(1-t) + w*t`，t = 换手率×decay，兜到 [0,1]。
4. **输入必须是前复权价**：用不复权价跨除权日会把成本算错（调用方负责复权，
   本模块不碰复权因子——复权交给 `aqsp.data.adjust`）。

读法与自检（见上游文档）：
- `profit_ratio` 必在 [0,1]；`avg_cost` 必落在网格最低~最高之间；
- `cost_90` 必包含 `cost_70`；90% 集中度必大于 70% 集中度。
- 这是推演不是实测持仓，看形态与相对变化，不要求绝对值对齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from aqsp.core.errors import DataError


@dataclass(frozen=True)
class ChipDistribution:
    """单标的在给定窗口末的筹码分布快照。"""

    symbol: str
    price: float
    profit_ratio: float  # 现价之下持仓占比 [0,1]
    avg_cost: float  # 加权平均持仓成本
    cost_90: tuple[float, float]  # 5%~95% 分位价格区间
    cost_70: tuple[float, float]  # 15%~85% 分位价格区间
    concentration_90: Optional[float]  # (高-低)/(高+低)
    concentration_70: Optional[float]
    peak_price: float  # 筹码最密集价位
    histogram: tuple[tuple[float, float], ...]  # (price, weight)，仅保留 >1e-6


def _triangular_weights(
    grid: np.ndarray, low: float, high: float, avg: float
) -> np.ndarray:
    """当日筹码在价格网格上的三角分布权重（峰值在均价，面积归一）。"""
    w = np.zeros_like(grid)
    if not np.isfinite([low, high, avg]).all() or high < low:
        return w
    if high - low < 1e-9:  # 一字板：全部堆在一个价位
        w[np.argmin(np.abs(grid - low))] = 1.0
        return w
    avg = min(max(avg, low), high)  # 均价必须落在当日区间内
    left = (grid >= low) & (grid <= avg)
    right = (grid > avg) & (grid <= high)
    if avg - low > 1e-9:
        w[left] = (grid[left] - low) / (avg - low)
    else:
        w[left] = 1.0
    if high - avg > 1e-9:
        w[right] = (high - grid[right]) / (high - avg)
    else:
        w[right] = 1.0
    total = w.sum()
    if total > 0:
        return w / total
    # 兜底：当日振幅窄于网格步长时，可能一个网格点都没落进 [low, high]，
    # 权重会全为 0。若就此跳过该日，连它的换手衰减也会一并丢失——
    # 低波动标的（银行股等）+ 长窗口下会累积成很大偏差。映射到最近网格点。
    w[np.argmin(np.abs(grid - avg))] = 1.0
    return w


def chip_distribution(
    df: pd.DataFrame,
    symbol: str = "",
    grid_size: int = 300,
    decay: float = 1.0,
) -> ChipDistribution:
    """计算筹码分布。

    Args:
        df: 必须含 `date, high, low, close, turn` 五列。
            `turn` 为百分数（0.31 表示 0.31%）；价格须为**前复权**价。
        symbol: 标的代码，仅用于结果标注。
        grid_size: 价格网格点数。
        decay: 换手衰减系数。1.0=按真实换手率换手；同花顺口径常用 1.5~2.0 加快历史筹码消散。

    Returns:
        ChipDistribution 快照。

    Raises:
        DataError: 缺列、有效行数为 0、或价格区间无效时。
    """
    need = {"date", "high", "low", "close", "turn"}
    missing = need - set(df.columns)
    if missing:
        raise DataError(
            f"chip_distribution 缺少列: {sorted(missing)}（date 用于强制时间升序）"
        )
    if not np.isfinite(decay) or decay <= 0:
        raise DataError(f"chip_distribution: decay 必须为正有限值, 收到 {decay!r}")

    d = df.dropna(subset=["high", "low", "close", "turn"]).copy()
    d = d[d["high"] > 0]
    if d.empty:
        raise DataError(
            "chip_distribution: 有效行数为 0（检查是否全是停牌日，或字段类型不对）"
        )
    # 强制按 date 升序：换手衰减是有方向的时序递推，倒序会让衰减反向推且
    # close.iloc[-1] 把最老收盘价当现价，结果完全错却不会报错。
    d = d.sort_values("date").reset_index(drop=True)

    lo, hi = float(d["low"].min()), float(d["high"].max())
    pad = (hi - lo) * 0.02 or max(lo * 0.02, 0.01)
    grid = np.linspace(lo - pad, hi + pad, grid_size)

    chips: Optional[np.ndarray] = None
    for row in d.itertuples(index=False):
        t = float(row.turn) / 100.0 * decay
        t = min(max(t, 0.0), 1.0)  # 换手率兜到 [0,1]，防异常值把筹码一次清零
        avg = (float(row.high) + float(row.low) + float(row.close)) / 3.0
        w = _triangular_weights(grid, float(row.low), float(row.high), avg)
        if w.sum() <= 0:
            continue
        if chips is None:
            chips = w.copy()  # 首日分布 = 期初全部流通筹码（防前窗清零）
            continue
        chips = chips * (1.0 - t) + w * t
    if chips is None:
        raise DataError("chip_distribution: 所有交易日的价格区间都无效，无法构建分布")

    total = chips.sum()
    if total <= 0:
        raise DataError("chip_distribution: 筹码总量为 0，无法计算指标")
    chips = chips / total

    price = float(d["close"].iloc[-1])
    cum = np.cumsum(chips)

    def price_at(q: float) -> float:
        return float(np.interp(q, cum, grid))

    p05, p15, p85, p95 = (price_at(q) for q in (0.05, 0.15, 0.85, 0.95))
    peak_i = int(np.argmax(chips))
    return ChipDistribution(
        symbol=symbol,
        price=price,
        profit_ratio=float(chips[grid <= price].sum()),
        avg_cost=float((grid * chips).sum()),
        cost_90=(p05, p95),
        cost_70=(p15, p85),
        concentration_90=float((p95 - p05) / (p95 + p05)) if p95 + p05 else None,
        concentration_70=float((p85 - p15) / (p85 + p15)) if p85 + p15 else None,
        peak_price=float(grid[peak_i]),
        histogram=tuple(
            (float(pp), float(cc)) for pp, cc in zip(grid, chips) if cc > 1e-6
        ),
    )


def cyq_screen_signal(
    dist: ChipDistribution,
    *,
    profit_ratio_min: float = 0.0,
    concentration_max: float = 1.0,
) -> float:
    """把筹码分布转成 0..1 的「可选」筛选用信号（越高越优）。

    ⚠️ 仅作**扩展点**：须经 walk-forward 验证 + ``thresholds.yaml`` 的 ``cyq.enabled``
    开启后才接入主筛选（架构 §3.5 / §5：魔法阈值须有出处、新加权机制须验证）。
    默认不调用——本函数不参与任何打分，纯推导，不改写策略逻辑。

    启发式：盈利比例高 + 90% 成本区间相对现价越窄（长尾小）-> 信号越强。
    """
    if dist.price <= 0:
        return 0.0
    signal = max(0.0, min(1.0, float(dist.profit_ratio)))
    lo90, hi90 = dist.cost_90
    if hi90 > lo90:
        width = (hi90 - lo90) / dist.price  # 相对现价成本跨度
        signal *= max(0.0, 1.0 - min(1.0, width))
    signal = max(0.0, min(1.0, signal))
    if signal < float(profit_ratio_min):
        return 0.0
    if dist.concentration_90 is not None and dist.concentration_90 > float(
        concentration_max
    ):
        return 0.0
    return signal
