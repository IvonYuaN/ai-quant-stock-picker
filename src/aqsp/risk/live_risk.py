"""P0-2 LIVE 风控入口 —— 对实盘持仓跑风控，产「该卖提醒」（副驾驶的"该卖"部分）。

独立入口（mode=LIVE 语义）：只吃**实盘持仓 + 现行情**，产告警；**不进回测统计、
不污染回测时间轴**（PRD 7.3-1 关键约束）。复用的现成风控引擎（`StopLossManager` /
`compute_dynamic_stop`）在回测路径里被调用，本模块只是给它们喂实盘输入，零改动其逻辑。

复用的现成风控（不新建风控逻辑）：
- L1 硬止损：`StopLossManager.check_single_stock_stop`（成本 × (1−8%)，阈值来自生产
  `thresholds.risk.single_stock_stop_pct`）
- L5 动态止损：`compute_dynamic_stop`（ATR / 最近低点 / 支撑位 合成 recommended_stop）

T+1 语义（Q3=A 拍板）：
- 输入 `holding_days`（持仓交易日数，0 = 当日买入）。持仓模型 `{code,shares,cost}`
  无买入日期，故作为 LIVE 入口显式输入；缺省按 1（老仓，即时可卖）处理，未来
  `/api/portfolio` 补 `opened_at` 后据此计算真实持有天数。
- 当日买入（0 天）：**L1 硬止损今日不可执行**（T+1 卖不掉，给不可执行的"该卖"
  比不给更伤信任）⇒ 降为"明日生效"观察；L5 动态止损当日记为观察信号并标注
  "T+1 限制：当日买入，明日方可执行"。
- 老仓（≥1 天）：L1/L5 即时可卖。

产出 :class:`SellReminder` 带 :class:`basis_json`（可解释=可信任，PRD 7.3-4）：
**无依据不产提醒**——每条都写清"触发依据 + 实际值 + 证据"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from aqsp.risk.dynamic_stop import compute_dynamic_stop
from aqsp.risk.stop_loss import Position, StopLossConfig, StopLossManager


# T+1 限制文案（当日买入、今日不可卖出时挂到 L5 / L1 提醒上）
_T1_NOTE = "T+1 限制：当日买入，今日不可卖出"


@dataclass(frozen=True)
class SellReminder:
    """一条对实盘持仓的「该卖 / 观察」提醒（收件箱数据源）。

    不变量（PRD 7.3-4「无依据不产提醒」）：``basis`` 永远非空且 ``rule``/``actual``
    齐备——否则前端无法展开复核，等于黑盒，比不提醒更伤信任。
    """

    symbol: str
    level: str  # "L1" | "L5"
    current_price: float
    threshold_price: float
    pnl_pct: float  # 相对成本价的损益百分比（负数=亏）
    sellable_now: bool  # T+1 下今日能否执行（当日买入=False）
    action_hint: str  # "该卖" | "明日生效" | "观察"
    basis: Dict[str, str] = field(default_factory=dict)  # 触发依据（basis_json）
    t1_note: str = ""  # T+1 限制说明（当日买入时非空）

    def to_dict(self) -> Dict[str, object]:
        """供前端收件箱 / 外发 notifier 序列化的扁平视图。"""
        return {
            "symbol": self.symbol,
            "level": self.level,
            "current_price": self.current_price,
            "threshold_price": self.threshold_price,
            "pnl_pct": round(self.pnl_pct, 4),
            "sellable_now": self.sellable_now,
            "action_hint": self.action_hint,
            "t1_note": self.t1_note,
            "basis_json": self.basis,
        }


def _pnl_pct(cost: float, price: float) -> float:
    if cost <= 0:
        return 0.0
    return (price - cost) / cost


def _l1_reminder(
    *,
    symbol: str,
    cost: float,
    price: float,
    stop_price: float,
    stop_pct: float,
    sellable_now: bool,
) -> SellReminder:
    """L1 硬止损：现价 ≤ 成本×(1−stop_pct)。"""
    pnl = _pnl_pct(cost, price)
    basis = {
        "rule": f"成本价 {cost:.2f} × (1−{stop_pct:.0%}) = {stop_price:.2f}",
        "actual": f"现价 {price:.2f}",
        "evidence": "L1 硬止损（成本回撤单股止损阈值）",
    }
    if sellable_now:
        hint, t1 = "该卖", ""
    else:
        hint, t1 = "明日生效", _T1_NOTE
    return SellReminder(
        symbol=symbol,
        level="L1",
        current_price=price,
        threshold_price=stop_price,
        pnl_pct=pnl,
        sellable_now=sellable_now,
        action_hint=hint,
        basis=basis,
        t1_note=t1,
    )


def _l5_reminder(
    *,
    symbol: str,
    cost: float,
    price: float,
    stop_price: float,
    method: str,
    sellable_now: bool,
) -> SellReminder:
    """L5 动态止损：现价 ≤ compute_dynamic_stop 合成的 recommended_stop。"""
    pnl = _pnl_pct(cost, price)
    basis = {
        "rule": f"动态止损位 {stop_price:.2f}（{method} 法合成）",
        "actual": f"现价 {price:.2f}",
        "evidence": "L5 动态止损（ATR×2.0 / 最近低点回撤 / 支撑位取最高）",
    }
    if sellable_now:
        hint, t1 = "该卖", ""
    else:
        # Q3=A：当日买入 ⇒ L5 不当日催卖，降为观察 + T+1 标注
        hint, t1 = "观察", _T1_NOTE
    return SellReminder(
        symbol=symbol,
        level="L5",
        current_price=price,
        threshold_price=stop_price,
        pnl_pct=pnl,
        sellable_now=sellable_now,
        action_hint=hint,
        basis=basis,
        t1_note=t1,
    )


def run_live_risk_check(
    holdings: List[Dict[str, object]],
    current_prices: Dict[str, float],
    holding_days: Optional[Dict[str, int]] = None,
    price_history: Optional[Dict[str, pd.DataFrame]] = None,
    stop_config: Optional[StopLossConfig] = None,
) -> List[SellReminder]:
    """对实盘持仓跑 L1/L5 风控，产「该卖提醒」列表（独立 LIVE 入口）。

    Args:
        holdings: 实盘持仓，形态 ``[{"code","shares","cost"}]``（与
            ``backend/portfolio.py`` 的 ``add_holding`` 落盘结构一致）。
            ``cost`` 必须 > 0 才做 L1（融券/负成本场景无成本回撤基准，跳过）。
        current_prices: 现行情 ``{code: price}``。缺价的持仓直接跳过（无法判定）。
        holding_days: ``{code: 持有交易日数}``，缺省按 1（老仓）处理。0=当日买入
            ⇒ 触发 Q3=A 的 T+1 降格（L1 明日生效 / L5 观察）。
        price_history: ``{code: 日线 DataFrame}``，仅 L5 需要；缺则只出 L1。
        stop_config: L1 阈值配置；缺省 ``StopLossConfig.from_thresholds()``（生产值）。

    Returns:
        该卖/观察提醒列表（无依据不产；全绿持仓返回空列表）。
    """
    days = holding_days or {}
    history = price_history or {}
    slm = StopLossManager(stop_config)

    reminders: List[SellReminder] = []
    for h in holdings:
        code = str(h.get("code", ""))
        if not code:
            continue
        shares = float(h.get("shares", 0))
        cost = float(h.get("cost", 0))
        if code not in current_prices:
            continue  # 缺行情，无法判定（不产"无依据"提醒）
        price = float(current_prices[code])
        hdays = int(days.get(code, 1))
        sellable_now = hdays >= 1

        # L1 硬止损：需要正成本基准
        if cost > 0 and price > 0:
            position = Position(symbol=code, shares=int(shares), cost_basis=cost)
            l1 = slm.check_single_stock_stop(position, price)
            if l1.triggered:
                stop_price = round(cost * (1 - abs(slm.config.single_stock_stop)), 2)
                reminders.append(
                    _l1_reminder(
                        symbol=code,
                        cost=cost,
                        price=price,
                        stop_price=stop_price,
                        stop_pct=abs(slm.config.single_stock_stop),
                        sellable_now=sellable_now,
                    )
                )

        # L5 动态止损：需要日线历史
        df = history.get(code)
        if df is not None and not df.empty and cost > 0:
            dyn = compute_dynamic_stop(df, entry_price=cost, symbol=code)
            if dyn.recommended_stop > 0 and price <= dyn.recommended_stop:
                reminders.append(
                    _l5_reminder(
                        symbol=code,
                        cost=cost,
                        price=price,
                        stop_price=dyn.recommended_stop,
                        method=dyn.method,
                        sellable_now=sellable_now,
                    )
                )
    return reminders


__all__ = [
    "SellReminder",
    "run_live_risk_check",
]
