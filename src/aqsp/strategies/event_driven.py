"""事件驱动策略 - A股特色的事件反应交易。

策略哲学：
- A股是情绪市，事件催化往往带来短期暴利机会
- 重组复牌、高送转、业绩预增、政策利好是四大经典事件
- 事件后的「价格行为模式」可作为事件代理信号（无需实时事件数据）

适用场景：1-5 日（事件冲击波）
胜率目标：55%+（事件确认后）

⚠️ 数据依赖说明：
理想的事件驱动需要实时事件数据源（公告/财报/政策）。
当前实现用「技术形态」作为事件代理信号：
- 长期停牌后复牌 → 用「数据中断 + 复牌放量」识别
- 业绩预增 → 用「突然放量大涨脱离均线」近似

📅 事件日历标注（2026-09-22 接入）：
`generate_signals` 可接受一个**已预取**的
:class:`aqsp.features.event_calendar.EventCalendar`，用**真实已知事件**给信号
加证据标注：
- 前瞻预警：近期有**限售解禁** → 写进 `risks`；
- 回溯佐证：近期**登上龙虎榜** → 写进 `reasons`，并**消解**对应的
  `needs_external_data` 条目。

🔴 接线纪律（不可违反）：
1. 日历**只做标注，不改分**。`calculate_score` 是纯计算函数，**永不**调用日历，
   也**永不**触网；日历只影响 `generate_signals` 的证据文本。
2. 日历必须由调用方**显式注入**并**预先取好数**；`event_calendar=None`（默认）
   时行为与接入前**逐字一致**。打分链路内禁止 `autoload=True`。
3. 持仓决策权仍在本策略既有规则，日历只是证据，不构成 LLM 决策依据。

仍未接入（见文件末 TODO）：停复牌 / 业绩预告 / 分红送转 的真实取数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from aqsp.features.event_calendar import EventCalendar
from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds

# 解禁严重度中文标签（仅用于证据文案；分档判定在
# `aqsp.features.event_calendar.unlock_severity`）
_SEVERITY_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "negligible": "可忽略",
}


@dataclass(frozen=True)
class EventSignal:
    """事件驱动信号。"""

    symbol: str
    name: str
    event_type: (
        str  # "resume_trading" / "earnings_surge" / "sudden_breakout" / "policy_boost"
    )
    score: float
    current_price: float
    entry_price: float
    stop_loss: float
    take_profit: float
    position_pct: float
    holding_period: str
    confidence: float
    reasons: list[str]
    risks: list[str]
    needs_external_data: list[str]  # 需要外部数据验证的维度


class EventDrivenStrategy(BaseStrategy):
    """事件驱动策略。

    四种事件模式（基于技术形态代理）：

    1. **复牌接力** (resume_trading)
       - 检测：数据时间出现明显断层（停牌）后恢复
       - 复牌后放量涨停/大涨 → 接力机会
       - 风险：复牌补跌也可能

    2. **业绩兑现** (earnings_surge)
       - 检测：突然放量大涨，脱离震荡区间
       - 近似业绩预增/超预期行情
       - 需外部数据：实际财报数据

    3. **突发放量异动** (sudden_breakout)
       - 检测：长期横盘后突然放量突破
       - 可能有未公开消息/事件驱动
       - 资金提前埋伏特征

    4. **政策板块联动** (policy_boost)
       - 检测：整板块同步异动（需板块数据）
       - 政策利好的板块性拉升
       - 需外部数据：板块归属 + 政策事件
    """

    name: str = "event_driven"

    # 策略自带参数
    SUSPEND_GAP_DAYS = 5  # 数据断层视为停牌的天数阈值
    SURGE_VOLUME_RATIO = 2.5  # 异动放量倍数
    SURGE_PRICE_PCT = 0.07  # 异动涨幅阈值
    CONSOLIDATION_DAYS = 20  # 横盘判定天数
    CONSOLIDATION_RANGE = 0.12  # 横盘振幅阈值
    MIN_SCORE = 0.55

    # `needs_external_data` 文案常量：事件日历**消解**这些条目时要做精确匹配，
    # 因此统一在此声明，禁止在其他地方重复字面量。
    NEEDS_SUSPEND_REASON = "停牌原因（重组/违规）需公告数据确认"
    NEEDS_EARNINGS_DATA = "实际财报数据需 tushare/akshare 接入"
    NEEDS_LHB_CONFIRM = "龙虎榜/公告确认是否真有事件"

    # 事件日历证据前缀：便于前端 / 日志一眼区分「技术形态推断」与「已知事件」
    CALENDAR_TAG = "📅 事件日历"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
        event_calendar: Optional[EventCalendar] = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        # 日历可为 None（默认）：此时本策略行为与接入事件日历前逐字一致。
        # 传入时必须已被调用方**预取完毕**（autoload=False），打分链路内禁止触网。
        self.event_calendar = event_calendar
        # 默认 enabled=False：宪法红线，未经 walk-forward 双门验证不上线
        config = config or StrategyConfig(name="event_driven", enabled=False)
        super().__init__(
            config,
            id="event_driven",
            version=self.thresholds.version,
            hypothesis="A股事件催化（复牌/业绩/政策）带来短期价格冲击，事件后价格行为模式可捕捉超额收益",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_single_score(df)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        df = df.sort_values("date").tail(40)
        if len(df) < 10:
            return 0.0

        # 数据有效性校验：脏数据（NaN / 全0 / 负价）直接判 0
        close = df["close"]
        if close.isna().any() or (close <= 0).any():
            return 0.0

        # 取四种事件模式的最高分
        resume_score = self._score_resume_trading(df)
        surge_score = self._score_earnings_surge(df)
        breakout_score = self._score_sudden_breakout(df)

        return max(resume_score, surge_score, breakout_score)

    def _score_resume_trading(self, df: pd.DataFrame) -> float:
        """复牌接力评分：检测数据断层后的放量。"""
        if "date" not in df.columns or len(df) < 5:
            return 0.0

        # 检测日期断层
        try:
            dates = pd.to_datetime(df["date"])
        except Exception:
            return 0.0

        date_diffs = dates.diff().dt.days.dropna()
        if date_diffs.empty:
            return 0.0

        # 最近是否有明显断层（停牌）
        max_gap = float(date_diffs.iloc[-min(10, len(date_diffs)) :].max())
        if max_gap < self.SUSPEND_GAP_DAYS:
            return 0.0  # 没有停牌迹象

        # 复牌后的表现（最后一日）
        last_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else last_close
        change = (last_close - prev_close) / prev_close if prev_close > 0 else 0

        # 复牌放量
        if len(df) >= 6:
            last_vol = float(df["volume"].iloc[-1])
            avg_vol = float(df["volume"].iloc[-6:-1].mean())
            vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0
        else:
            vol_ratio = 0

        score = 0.0
        if change > 0.05 and vol_ratio > 1.5:
            score = 0.9  # 复牌放量大涨，强接力信号
        elif change > 0.02:
            score = 0.6
        elif change > 0:
            score = 0.4

        return score

    def _score_earnings_surge(self, df: pd.DataFrame) -> float:
        """业绩兑现评分：突然放量大涨脱离均线。"""
        if len(df) < 21:
            return 0.0

        last_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        change = (last_close - prev_close) / prev_close if prev_close > 0 else 0

        if change < self.SURGE_PRICE_PCT:
            return 0.0

        # 放量
        last_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-21:-1].mean())
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0

        if vol_ratio < self.SURGE_VOLUME_RATIO:
            return 0.0

        # 脱离均线（突破式上涨）
        ma20 = float(df["close"].rolling(20).mean().iloc[-1])
        deviation = (last_close - ma20) / ma20 if ma20 > 0 else 0

        score = 0.0
        if deviation > 0.08 and vol_ratio > 3.0:
            score = 0.85  # 放巨量脱离均线，业绩兑现特征
        elif deviation > 0.05:
            score = 0.65
        else:
            score = 0.5

        return score

    def _score_sudden_breakout(self, df: pd.DataFrame) -> float:
        """突发异动评分：长期横盘后突然放量突破。"""
        if len(df) < self.CONSOLIDATION_DAYS + 2:
            return 0.0

        # 判断前期是否横盘
        consolidation = df["close"].iloc[-self.CONSOLIDATION_DAYS - 1 : -1]
        c_high = float(consolidation.max())
        c_low = float(consolidation.min())
        if c_low <= 0:
            return 0.0
        c_range = (c_high - c_low) / c_low

        if c_range > self.CONSOLIDATION_RANGE:
            return 0.0  # 前期不是横盘

        # 当日突破横盘上沿 + 放量
        last_close = float(df["close"].iloc[-1])
        last_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-self.CONSOLIDATION_DAYS - 1 : -1].mean())
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0

        if last_close > c_high and vol_ratio > 2.0:
            return 0.8  # 横盘突破+放量，疑似事件驱动
        if last_close > c_high:
            return 0.5
        return 0.0

    def generate_signals(
        self,
        data: Dict[str, pd.DataFrame],
        as_of: Optional[str] = None,
    ) -> List[EventSignal]:
        """生成事件驱动信号。

        Args:
            data: symbol → OHLCV DataFrame（需含 ``date`` / ``close`` / ``volume``）。
            as_of: 事件日历查询基准日（ISO ``YYYY-MM-DD``）。``None``（默认）时
                从该 symbol 的**最后一根 K 线日期**推导 —— 回看历史数据时日历查询
                自动锚定在同一时点，**不会引入未来信息**。

        Returns:
            按 ``score`` 降序的信号列表。``event_calendar`` 为 ``None``（默认）时，
            返回内容与接入事件日历前逐字一致。
        """
        signals: List[EventSignal] = []

        for symbol, df in data.items():
            if df is None or df.empty:
                continue

            df_sorted = df.sort_values("date").tail(40)
            if len(df_sorted) < 10:
                continue

            # 评估各事件模式
            resume = self._score_resume_trading(df_sorted)
            surge = self._score_earnings_surge(df_sorted)
            breakout = self._score_sudden_breakout(df_sorted)

            event_scores = [
                ("resume_trading", resume, "1-3_days"),
                ("earnings_surge", surge, "2-5_days"),
                ("sudden_breakout", breakout, "1-5_days"),
            ]
            event_scores.sort(key=lambda x: x[1], reverse=True)
            event_type, best_score, holding = event_scores[0]

            if best_score < self.MIN_SCORE:
                continue

            current_price = float(df_sorted["close"].iloc[-1])
            entry, stop, target = self._calc_targets(event_type, current_price)
            position = self._suggest_position(event_type, best_score)

            reasons, risks, needs_data = self._collect_info(df_sorted, event_type)

            # 已知事件标注（best-effort）：只补证据文本，不改 best_score / 仓位 / 目标价
            ref_date = as_of or self._as_of_from_df(df_sorted)
            self._annotate_with_calendar(
                symbol, ref_date, event_type, reasons, risks, needs_data
            )

            signals.append(
                EventSignal(
                    symbol=symbol,
                    name=str(df_sorted["name"].iloc[-1])
                    if "name" in df_sorted.columns
                    else symbol,
                    event_type=event_type,
                    score=round(best_score * 100, 1),
                    current_price=round(current_price, 2),
                    entry_price=round(entry, 2),
                    stop_loss=round(stop, 2),
                    take_profit=round(target, 2),
                    position_pct=round(position, 2),
                    holding_period=holding,
                    confidence=round(best_score, 2),
                    reasons=reasons,
                    risks=risks,
                    needs_external_data=needs_data,
                )
            )

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def _calc_targets(
        self, event_type: str, current: float
    ) -> tuple[float, float, float]:
        if event_type == "resume_trading":
            # 复牌：波动大，宽止损
            return current, current * 0.93, current * 1.15
        if event_type == "earnings_surge":
            # 业绩：追高风险，紧止损
            return current, current * 0.95, current * 1.10
        # sudden_breakout
        return current, current * 0.95, current * 1.12

    def _suggest_position(self, event_type: str, score: float) -> float:
        # 事件驱动风险高，仓位保守
        if event_type == "resume_trading":
            return 0.10  # 复牌不确定性大
        if score > 0.7:
            return 0.15
        return 0.08

    def _collect_info(
        self, df: pd.DataFrame, event_type: str
    ) -> tuple[list[str], list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        needs_data: list[str] = []

        if event_type == "resume_trading":
            reasons.append("检测到停牌后复牌，放量异动")
            risks.append("复牌方向不确定，可能补跌")
            needs_data.append(self.NEEDS_SUSPEND_REASON)

        elif event_type == "earnings_surge":
            last_change = (
                float(df["close"].iloc[-1]) - float(df["close"].iloc[-2])
            ) / float(df["close"].iloc[-2])
            reasons.append(f"放巨量大涨{last_change:.1%}，脱离均线")
            reasons.append("疑似业绩超预期/重大利好")
            risks.append("可能是消息兑现，追高风险")
            needs_data.append(self.NEEDS_EARNINGS_DATA)

        elif event_type == "sudden_breakout":
            reasons.append("长期横盘后放量突破")
            reasons.append("资金提前埋伏，疑似事件驱动")
            risks.append("无消息确认，可能是诱多")
            needs_data.append(self.NEEDS_LHB_CONFIRM)

        return reasons, risks, needs_data

    # ---------------------------------------------------------------
    # 事件日历标注（只补证据，不改分）
    # ---------------------------------------------------------------

    @staticmethod
    def _as_of_from_df(df: pd.DataFrame) -> Optional[str]:
        """从 K 线最后一行 ``date`` 推导事件日历基准日（ISO 字符串）。

        日历查询必须锚定在**信号当天**：若用 `date.today()`，回看历史数据时就会
        把「当时还没发生」的解禁 / 龙虎榜当成已知信息（look-ahead）。
        无法解析时返回 ``None``，调用方据此整体跳过标注（fail-soft）。
        """
        if "date" not in df.columns or df.empty:
            return None
        try:
            ts = pd.to_datetime(df["date"].iloc[-1])
        except (TypeError, ValueError):
            return None
        if pd.isna(ts):
            return None
        return ts.strftime("%Y-%m-%d")

    def _annotate_with_calendar(
        self,
        symbol: str,
        as_of: Optional[str],
        event_type: str,
        reasons: List[str],
        risks: List[str],
        needs_data: List[str],
    ) -> None:
        """用事件日历给单条信号补证据，**就地修改**三个列表。

        - 近期解禁 → `risks`（前瞻预警）
        - 近期上榜龙虎榜 → `reasons`（回溯佐证），并消解 `NEEDS_LHB_CONFIRM`
        - 有龙虎榜数据面但该股未上榜 → `risks`，同样消解 `NEEDS_LHB_CONFIRM`

        日历是 **best-effort 增强**：任何异常都静默跳过，绝不因为「附加证据」
        失败而让整条选股链路失败。这与 `features/pit_enrichment.py` 的
        fail-soft 约定一致。
        """
        calendar = self.event_calendar
        if calendar is None or not as_of:
            return
        try:
            if calendar.is_empty():
                return
            self._annotate_unlocks(calendar, symbol, as_of, risks)
            self._annotate_longhubang(
                calendar, symbol, as_of, event_type, reasons, risks, needs_data
            )
        except Exception:  # noqa: BLE001 - best-effort enrichment, never break screening
            return

    def _annotate_unlocks(
        self,
        calendar: EventCalendar,
        symbol: str,
        as_of: str,
        risks: List[str],
    ) -> None:
        """前瞻预警：`as_of` 起 horizon 窗口内的限售解禁。

        只有当日历**确实装载了解禁数据面**时才做「无解禁」之外的判断 ——
        没数据 ≠ 没事件，绝不能在缺数据时下结论。
        """
        if not calendar.has_unlock_data():
            return
        for event in calendar.upcoming_unlocks(symbol, as_of):
            label = _SEVERITY_LABELS.get(event.severity, event.severity)
            risks.append(f"{self.CALENDAR_TAG}·解禁预警({label})：{event.detail}")

    def _annotate_longhubang(
        self,
        calendar: EventCalendar,
        symbol: str,
        as_of: str,
        event_type: str,
        reasons: List[str],
        risks: List[str],
        needs_data: List[str],
    ) -> None:
        """回溯佐证：`as_of` 前 lookback 窗口内的龙虎榜记录。

        上榜 → 把真实记录写进 `reasons`；未上榜 → **仅当**该信号的立论本身
        就是「资金提前埋伏」（`sudden_breakout`）时才写进 `risks`。
        两种情况都代表「龙虎榜已核实」，因此都能消解 `NEEDS_LHB_CONFIRM`。
        """
        if not calendar.has_longhubang_data():
            return

        records = calendar.recent_longhubang(symbol, as_of)
        if records:
            for record in records[:2]:
                reasons.append(f"{self.CALENDAR_TAG}：{record.detail}")
            self._resolve_needs(needs_data, self.NEEDS_LHB_CONFIRM)
            return

        if event_type == "sudden_breakout":
            lookback = calendar.longhubang_lookback_days
            risks.append(
                f"{self.CALENDAR_TAG}：近 {lookback} 个自然日未上龙虎榜，"
                "「资金提前埋伏」的推断未获确认"
            )
            self._resolve_needs(needs_data, self.NEEDS_LHB_CONFIRM)

    @staticmethod
    def _resolve_needs(needs_data: List[str], text: str) -> None:
        """从 `needs_external_data` 中移除已被日历回答的条目（就地修改）。"""
        if text in needs_data:
            needs_data.remove(text)


def format_event_signals(signals: List[EventSignal], top_n: int = 5) -> str:
    """格式化事件驱动信号。"""
    if not signals:
        return "📰 事件驱动策略：今日无事件异动信号"

    type_labels = {
        "resume_trading": "🔄 复牌接力",
        "earnings_surge": "📊 业绩兑现",
        "sudden_breakout": "⚡ 突发异动",
        "policy_boost": "🏛️ 政策联动",
    }

    lines: list[str] = []
    lines.append("事件驱动观察")
    lines.append("=" * 50)
    lines.append(
        f"发现 {len(signals)} 只待复核候选，展示前 {min(top_n, len(signals))} 只:"
    )
    lines.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        label = type_labels.get(signal.event_type, signal.event_type)
        lines.append(f"【{i}】{signal.symbol} {signal.name} - {label}")
        lines.append(f"   得分: {signal.score:.1f} | 置信度: {signal.confidence:.0%}")
        lines.append(
            f"   现价: {signal.current_price:.2f} | 周期: {signal.holding_period}"
        )
        lines.append(
            f"   止损: {signal.stop_loss:.2f} | 目标: {signal.take_profit:.2f} | 参考仓位: {signal.position_pct:.0%}"
        )
        lines.append("   理由:")
        for r in signal.reasons:
            lines.append(f"     • {r}")
        if signal.risks:
            lines.append("   ⚠️ 风险:")
            for r in signal.risks:
                lines.append(f"     • {r}")
        if signal.needs_external_data:
            lines.append("   📡 需外部数据确认:")
            for d in signal.needs_external_data:
                lines.append(f"     • {d}")
        lines.append("")

    lines.append("⚡ 操作纪律:")
    lines.append("  1. 事件驱动风险高，仓位务必保守")
    lines.append("  2. 复牌股不确定性大，轻仓试探")
    lines.append("  3. 务必结合公告/财报确认事件真实性")

    return "\n".join(lines)


# ============================================================
# 真实事件数据接入 —— 进度（2026-09-22 复核）
# ============================================================
# ✅ 已完成：事件日历标注层
#    `aqsp.features.event_calendar.EventCalendar` + `generate_signals(as_of=...)`
#    - 前瞻预警：限售解禁（数据面 = `aqsp.data.lockup`，已在 main）
#    - 回溯佐证：龙虎榜（数据面 = `aqsp.data.longhubang`，已在 main），
#      并消解 `needs_external_data` 中「龙虎榜/公告确认是否真有事件」
#    - 点内安全：as_of 默认取 K 线最后一行日期，不引入未来信息
#
# ⏳ 取数模块待落地（不影响上面标注层：日历对缺数据面自动降级）：
# 1. 停复牌   → akshare `ak.stock_tfp_em()`   等价东财 datacenter 源
# 2. 业绩预告 → akshare `ak.stock_yjyg_em()`  等价东财 datacenter 源
# 3. 分红送转 → akshare `ak.stock_fhps_em()`  等价东财 datacenter 源
#    （三者按 `aqsp/data/lockup.py` 的模板实现后，喂给 `EventCalendar` 即可）
# 4. 政策事件 → 需新闻/公告 NLP（可选 LLM，走 `llm_call_or_fallback` wrapper）
#
# ⛔ 明确不做（做了会违反宪法红线，不是"待办"）：
# - **`calculate_score` 增加「事件数据加权」**：事件权重属于策略参数，
#   引入必须走 walk-forward 双门验证 + `thresholds.yaml` 注入，
#   不能凭直觉写常量。当前事件只作**证据标注**，不进打分向量。
# - **用真实事件类型整体替代技术代理**：技术代理是「价格行为」先验，
#   与「已知事件」互补而非互斥；替代会改变策略基线。改基线须先过双门。
# - 任何把日历结果写进 `ledger` 权重 / 交给 LLM 决策的用法。
