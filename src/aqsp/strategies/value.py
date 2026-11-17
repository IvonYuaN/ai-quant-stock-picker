from __future__ import annotations

from typing import Dict, Optional
import pandas as pd

from aqsp.data.valuation_pit import ValuationPitSource
from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds

# PIT 估值源单例（cache-only，绝不联网；缺失 ⇒ fail-soft 回退）
_VALUATION_SOURCE = ValuationPitSource()


class ValueStrategy(BaseStrategy):
    name: str = "value"

    def __init__(self, config: StrategyConfig, thresholds: Thresholds = None):
        self.thresholds = thresholds or load_thresholds()
        super().__init__(
            config,
            id="value",
            version=self.thresholds.version,
            hypothesis="低估值股票在A股长期有超额收益，PE/PB/股息率是有效筛选因子（基本面数据接入后启用）",
            regime_required=("stable_bull", "stable_sideways", "stable_bear"),
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """估值维度打分。

        🔴 **截面分位**（2026-07 改造）：原实现是绝对水平 ``1 - pe/max_pe``，
        而 ``max_pe``（默认 30、门槛 15）远大于多数个股 PE ⇒ 几乎所有票得分
        挤在 1 附近、**零区分度**（这正是"7 维里有 3 维是常量"的根因之一）。
        现改为**横截面分位**：越便宜分位越高 ⇒ 恢复区分度。

        PIT 纪律：估值取 ``as_of <= 该票决策日`` 的最近一期快照（见
        :class:`aqsp.data.valuation_pit.ValuationPitSource`），**绝不取未来期**。
        数据缺失 ⇒ 该票取中性 0.5（fail-soft，行为与"无数据"时一致）。
        """
        # ① 逐票取决策日 + PIT 估值（asof = 该票 df 末行日期）
        raw: Dict[str, Dict[str, Optional[float]]] = {}
        for symbol, df in (data or {}).items():
            if df is None or df.empty:
                continue
            raw[symbol] = self._pit_valuation(symbol, df)

        # ② 截面分位（大 = 便宜 ⇒ 分位高）
        #
        # 🔴 命门 B（2026-11-17）：gate 走 streaming，value 是**截面分位**策略，
        # 但 streaming 在每个 500 只 batch 内现算分位、且 batch 板块高度同质
        # （covered 票按 ts_code 顺序切）⇒ "便宜/贵"分位实际是在 000 段或 300 段内
        # 排的、**不是全市场 ~5200 只** ⇒ 结构性失真。现改为优先用 PIT 源预计算
        # 的**全市场截面分位**（与 batch 切法无关）；缺失才回退本批次现算
        # （fail-soft，保持"无数据 ⇒ 中性"的原行为）。
        pe_rank, pb_rank, _ = self._value_ranks(raw)

        w = self.thresholds.value.weights
        # ③ 加权合成：dividend 本期无 PIT 数据 ⇒ 取中性 0.5 并把权重按可用分量归一
        scores: Dict[str, float] = {}
        for symbol in (data or {}):
            if symbol not in raw:
                scores[symbol] = 0.5  # 无估值数据 ⇒ 中性（fail-soft）
                continue
            pe_score = pe_rank.get(symbol, 0.5)
            pb_score = pb_rank.get(symbol, 0.5)
            # dividend 分量恒中性 ⇒ 其权重在归一后抵消（等价于按 pe/pb 权重比例分配）
            avail = w.pe + w.pb
            if avail <= 0:
                scores[symbol] = 0.5
                continue
            final = (pe_score * w.pe + pb_score * w.pb + 0.5 * w.dividend) / (
                w.pe + w.pb + w.dividend
            )
            scores[symbol] = max(0.0, min(1.0, final))
        return scores

    # ------------------------------------------------------------------ PIT 取数
    def _pit_valuation(
        self, symbol: str, df: pd.DataFrame
    ) -> Dict[str, Optional[float]]:
        """按该票决策日（df 末行日期）取 PIT 估值快照 + 全市场截面分位。

        返回键：
        - ``pe``/``pb``：原值（PE_TTM / PB_MRQ），用于回退的 batch 内现算；
        - ``pe_pct``/``pb_pct``：PIT 源预计算的**全市场** EP/BP 便宜度分位
          （与 streaming batch 切法无关，命门 B）。缺失 ⇒ None。
        """
        out: Dict[str, Optional[float]] = {
            "pe": None,
            "pb": None,
            "pe_pct": None,
            "pb_pct": None,
        }
        # 决策日 = df 末行日期（walk-forward 逐日切片后即信号日）
        try:
            as_of = df.sort_values("date").iloc[-1]["date"]
        except Exception:
            return out
        as_of_str = str(as_of)[:10].replace("-", "")
        if len(as_of_str) != 8:
            return out
        code = symbol.split(".")[0]
        point = _VALUATION_SOURCE.lookup(code, as_of_str)
        if point is None:
            return out
        out["pe"] = point.pe_ttm
        out["pb"] = point.pb_mrq
        # 全市场分位（PIT 源按当期快照日预计算，与 batch 组成无关）
        out["pe_pct"] = _VALUATION_SOURCE.ep_pct(code, as_of_str, autoload=False)
        out["pb_pct"] = _VALUATION_SOURCE.bp_pct(code, as_of_str, autoload=False)
        return out

    # ------------------------------------------------------------------ 截面分位
    def _value_ranks(
        self, raw: Dict[str, Dict[str, Optional[float]]]
    ) -> tuple[Dict[str, float], Dict[str, float], Dict[str, bool]]:
        """估值维度分位（命门 B：优先全市场分位，缺失才回退 batch 内现算）。

        返回 ``(pe_rank, pb_rank, whole_market_used)``，两个维度**独立**判断：
        - 该维度**任一**票带全市场分位（``*_pct``）⇒ 全市场路径：``*_pct``
          本就是 PIT 源按当期快照日预计算好的 0~1 便宜度（越高越便宜，与
          streaming batch 切法无关），直接透传；缺失票中性 0.5；
        - 该维度全市场分位整体缺失（PIT 源无估值数据）⇒ 回退本批次内用
          ``pe``/``pb`` 原值现算（保持"无数据 ⇒ 中性"的旧行为，fail-soft）。
        """
        result: Dict[str, Dict[str, float]] = {"pe": {}, "pb": {}}
        used: Dict[str, bool] = {}
        for dim in ("pe", "pb"):
            pct_key, orig_key = f"{dim}_pct", dim
            # 全市场分位是否可用（任一票带值即启用全市场口径，缺失票中性 0.5）
            whole_market_avail = any(
                raw[s].get(pct_key) is not None for s in raw
            )
            if whole_market_avail:
                result[dim] = {
                    s: (float(raw[s][pct_key]) if raw[s].get(pct_key) is not None else 0.5)
                    for s in raw
                }
                used[dim] = True
            else:
                # 回退：本批次内用估值原值现算截面分位（旧行为，fail-soft）
                result[dim] = self._cross_section_rank(
                    {s: raw[s].get(orig_key) for s in raw}, higher_is_cheaper=True
                )
                used[dim] = False
        return result["pe"], result["pb"], used

    @staticmethod
    def _cross_section_rank(
        values: Dict[str, Optional[float]], higher_is_cheaper: bool = True
    ) -> Dict[str, float]:
        """横截面分位（0~1）：**便宜 ⇒ 分位高**。

        入参是估值原值（PE / PB，越大越贵）；``higher_is_cheaper=True`` 时
        内部先取倒数再排名，使"更便宜 ⇒ 更高分"。缺失值 ⇒ 中性 0.5；
        单一有效样本 ⇒ 0.5（无区分度，不制造假信号）。
        """
        valid = {
            s: v
            for s, v in values.items()
            if v is not None and v == v and float(v) > 0
        }
        out: Dict[str, float] = {s: 0.5 for s in values}
        if len(valid) < 2:
            return out
        series = pd.Series(valid, dtype=float)
        if higher_is_cheaper:
            series = 1.0 / series          # 便宜（PE 小）⇒ 值大 ⇒ 分位高
        ranks = series.rank(pct=True)
        for symbol, value in ranks.items():
            out[symbol] = float(value)
        return out

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        """⚠️ 旧单票绝对水平路径（**已弃用**，保留仅为向后兼容）。

        绝对水平 ``1 - pe/max_pe`` 在 max_pe=15~30 下几乎所有票得分挤在 1 附近
        ⇒ 零区分度。新的 :meth:`calculate_score` 走 **PIT 估值 + 截面分位**。
        """
        df = df.sort_values("date").tail(5)

        if len(df) < 1:
            return 0.0

        pe_score = self._calculate_pe_score(df)
        pb_score = self._calculate_pb_score(df)
        dividend_score = self._calculate_dividend_score(df)

        w = self.thresholds.value.weights
        final_score = pe_score * w.pe + pb_score * w.pb + dividend_score * w.dividend
        return max(0.0, min(1.0, final_score))

    def _calculate_pe_score(self, df: pd.DataFrame) -> float:
        max_pe = self.thresholds.value.max_pe

        if "pe" not in df.columns:
            return 0.5

        pe_values = df["pe"].dropna()
        if pe_values.empty:
            return 0.5

        latest_pe = pe_values.iloc[-1]

        if latest_pe <= 0:
            return 0.3

        score = max(1 - latest_pe / max_pe, 0.0) if max_pe > 0 else 0.5
        return score

    def _calculate_pb_score(self, df: pd.DataFrame) -> float:
        max_pb = self.thresholds.value.max_pb

        if "pb" not in df.columns:
            return 0.5

        pb_values = df["pb"].dropna()
        if pb_values.empty:
            return 0.5

        latest_pb = pb_values.iloc[-1]

        if latest_pb <= 0:
            return 0.3

        score = max(1 - latest_pb / max_pb, 0.0) if max_pb > 0 else 0.5
        return score

    def _calculate_dividend_score(self, df: pd.DataFrame) -> float:
        min_yield = self.thresholds.value.min_dividend_yield

        if "dividend_yield" not in df.columns:
            return 0.5

        yield_values = df["dividend_yield"].dropna()
        if yield_values.empty:
            return 0.5

        avg_yield = yield_values.mean()
        return min(avg_yield / min_yield, 1.0) if min_yield > 0 else 0.5
