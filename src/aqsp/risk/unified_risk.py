"""三层风控架构 - 个股/组合/系统三个层面的风险管理。

风控哲学：
- 个股层面：管单笔损失（已有部分实现）
- 组合层面：管单日/单周累计损失，控制相关性
- 系统层面：管系统性风险（大盘崩盘、板块共振、流动性危机）

任何一层触发风控 → 强制平仓或停止开仓
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict

from aqsp.core.time import today_shanghai


# ============================================================
# Layer 1: 个股风控
# ============================================================

@dataclass(frozen=True)
class StockRiskConfig:
    """个股风控配置。"""

    max_position_pct: float = 0.30  # 单股最大仓位
    hard_stop_loss: float = 0.05  # 硬止损：5%
    soft_stop_loss: float = 0.03  # 软止损：3%（提示）
    trailing_stop_activation: float = 0.05  # 浮盈5%后启动移动止损
    trailing_stop_distance: float = 0.03  # 移动止损距离
    max_holding_days: int = 10  # 最大持仓天数
    profit_take_threshold: float = 0.15  # 止盈线15%


@dataclass(frozen=True)
class StockRiskCheck:
    """个股风控检查结果。"""

    symbol: str
    action: str  # "hold" / "reduce" / "exit" / "stop_buy"
    reason: str
    current_pnl_pct: float
    holding_days: int
    suggested_position: float
    urgency: str  # "low" / "medium" / "high" / "critical"


class StockRiskManager:
    """个股层面风控。

    职责：
    - 单股止损执行
    - 移动止损跟踪
    - 持仓天数限制
    - 仓位大小检查
    """

    def __init__(self, config: StockRiskConfig | None = None):
        self.config = config or StockRiskConfig()

    def check_position(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        max_price_since_entry: float,
        entry_date: date,
        position_pct: float,
        today: date | None = None,
    ) -> StockRiskCheck:
        """检查单股仓位风险。"""
        today = today or today_shanghai()
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
        holding_days = (today - entry_date).days

        # 1. 硬止损（最高优先级）
        if pnl_pct <= -self.config.hard_stop_loss:
            return StockRiskCheck(
                symbol=symbol,
                action="exit",
                reason=f"硬止损触发：亏损{pnl_pct:.1%} ≤ -{self.config.hard_stop_loss:.0%}",
                current_pnl_pct=pnl_pct,
                holding_days=holding_days,
                suggested_position=0.0,
                urgency="critical",
            )

        # 2. 移动止损
        if max_price_since_entry > entry_price * (1 + self.config.trailing_stop_activation):
            stop_price = max_price_since_entry * (1 - self.config.trailing_stop_distance)
            if current_price <= stop_price:
                return StockRiskCheck(
                    symbol=symbol,
                    action="exit",
                    reason=f"移动止损触发：从最高{max_price_since_entry:.2f}回落超过{self.config.trailing_stop_distance:.0%}",
                    current_pnl_pct=pnl_pct,
                    holding_days=holding_days,
                    suggested_position=0.0,
                    urgency="high",
                )

        # 3. 止盈
        if pnl_pct >= self.config.profit_take_threshold:
            return StockRiskCheck(
                symbol=symbol,
                action="reduce",
                reason=f"达到止盈线：盈利{pnl_pct:.1%} ≥ {self.config.profit_take_threshold:.0%}，建议减仓50%",
                current_pnl_pct=pnl_pct,
                holding_days=holding_days,
                suggested_position=position_pct * 0.5,
                urgency="medium",
            )

        # 4. 持仓天数超限
        if holding_days >= self.config.max_holding_days:
            return StockRiskCheck(
                symbol=symbol,
                action="exit",
                reason=f"持仓超期：{holding_days}天 ≥ {self.config.max_holding_days}天",
                current_pnl_pct=pnl_pct,
                holding_days=holding_days,
                suggested_position=0.0,
                urgency="medium",
            )

        # 5. 仓位过大
        if position_pct > self.config.max_position_pct:
            return StockRiskCheck(
                symbol=symbol,
                action="reduce",
                reason=f"仓位超限：{position_pct:.1%} > {self.config.max_position_pct:.0%}",
                current_pnl_pct=pnl_pct,
                holding_days=holding_days,
                suggested_position=self.config.max_position_pct,
                urgency="medium",
            )

        # 6. 软止损提示
        if pnl_pct <= -self.config.soft_stop_loss:
            return StockRiskCheck(
                symbol=symbol,
                action="hold",
                reason=f"软止损警告：亏损{pnl_pct:.1%}，关注是否需要止损",
                current_pnl_pct=pnl_pct,
                holding_days=holding_days,
                suggested_position=position_pct,
                urgency="low",
            )

        # 正常持有
        return StockRiskCheck(
            symbol=symbol,
            action="hold",
            reason="风险可控",
            current_pnl_pct=pnl_pct,
            holding_days=holding_days,
            suggested_position=position_pct,
            urgency="low",
        )


# ============================================================
# Layer 2: 组合风控
# ============================================================

@dataclass(frozen=True)
class PortfolioRiskConfig:
    """组合风控配置。"""

    max_daily_loss_pct: float = 0.02  # 单日最大亏损 2%
    max_weekly_loss_pct: float = 0.05  # 周最大亏损 5%
    max_drawdown_pct: float = 0.10  # 最大回撤 10%
    max_positions: int = 8  # 最大持仓数
    max_single_position_pct: float = 0.30  # 单股仓位上限
    max_sector_concentration: float = 0.40  # 单板块仓位上限
    max_correlation: float = 0.70  # 持仓相关性上限
    min_cash_reserve: float = 0.10  # 最低现金保留


@dataclass
class PortfolioState:
    """组合当前状态。"""

    total_value: float
    cash: float
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    peak_value: float = 0.0
    drawdown: float = 0.0


@dataclass(frozen=True)
class PortfolioRiskCheck:
    """组合风控检查结果。"""

    overall_action: str  # "normal" / "reduce" / "stop_buy" / "emergency_exit"
    blocking_reasons: list[str]
    warnings: list[str]
    suggested_actions: list[str]
    can_open_new_position: bool
    max_new_position_pct: float


class PortfolioRiskManager:
    """组合层面风控。

    职责：
    - 日/周累计亏损监控
    - 最大回撤监控
    - 持仓数量和集中度
    - 板块/相关性控制
    """

    def __init__(self, config: PortfolioRiskConfig | None = None):
        self.config = config or PortfolioRiskConfig()

    def check_portfolio(
        self,
        state: PortfolioState,
        sector_map: Dict[str, str] | None = None,
    ) -> PortfolioRiskCheck:
        """检查组合风险。"""
        blocking: list[str] = []
        warnings: list[str] = []
        suggested: list[str] = []
        can_open = True
        max_new = self.config.max_single_position_pct

        # 1. 日亏损检查
        daily_loss_pct = abs(state.daily_pnl) / state.total_value if state.total_value > 0 else 0
        if state.daily_pnl < 0 and daily_loss_pct >= self.config.max_daily_loss_pct:
            blocking.append(
                f"⛔ 单日亏损{daily_loss_pct:.1%} ≥ {self.config.max_daily_loss_pct:.0%}"
            )
            suggested.append("停止当日新开仓")
            can_open = False

        # 2. 周亏损检查
        weekly_loss_pct = abs(state.weekly_pnl) / state.total_value if state.total_value > 0 else 0
        if state.weekly_pnl < 0 and weekly_loss_pct >= self.config.max_weekly_loss_pct:
            blocking.append(
                f"⛔ 单周亏损{weekly_loss_pct:.1%} ≥ {self.config.max_weekly_loss_pct:.0%}"
            )
            suggested.append("本周内只减仓不加仓")
            can_open = False

        # 3. 最大回撤检查
        if state.drawdown >= self.config.max_drawdown_pct:
            blocking.append(
                f"⛔ 回撤{state.drawdown:.1%} ≥ {self.config.max_drawdown_pct:.0%}"
            )
            suggested.append("强制减仓至 50% 以下")

        # 4. 持仓数检查
        position_count = len(state.positions)
        if position_count >= self.config.max_positions:
            warnings.append(
                f"持仓数{position_count}达上限{self.config.max_positions}，需先卖出再买入"
            )
            can_open = False

        # 5. 现金储备
        cash_ratio = state.cash / state.total_value if state.total_value > 0 else 1.0
        if cash_ratio < self.config.min_cash_reserve:
            warnings.append(
                f"现金比例{cash_ratio:.0%} < {self.config.min_cash_reserve:.0%}，建议保留弹药"
            )
            max_new *= 0.5  # 限制新仓位大小

        # 6. 板块集中度
        if sector_map:
            sector_pcts: Dict[str, float] = {}
            for symbol, pos in state.positions.items():
                sector = sector_map.get(symbol, "unknown")
                pct = pos.get("position_pct", 0)
                sector_pcts[sector] = sector_pcts.get(sector, 0) + pct

            for sector, pct in sector_pcts.items():
                if pct > self.config.max_sector_concentration:
                    warnings.append(
                        f"板块[{sector}]仓位{pct:.0%} > {self.config.max_sector_concentration:.0%}，板块共振风险"
                    )
                    suggested.append(f"减仓板块[{sector}]")

        # 7. 单股仓位检查
        for symbol, pos in state.positions.items():
            pct = pos.get("position_pct", 0)
            if pct > self.config.max_single_position_pct:
                warnings.append(
                    f"单股[{symbol}]仓位{pct:.0%} > {self.config.max_single_position_pct:.0%}"
                )

        # 综合决策
        if blocking:
            overall_action = "emergency_exit" if state.drawdown >= self.config.max_drawdown_pct else "stop_buy"
        elif warnings:
            overall_action = "reduce" if cash_ratio < self.config.min_cash_reserve else "normal"
        else:
            overall_action = "normal"

        return PortfolioRiskCheck(
            overall_action=overall_action,
            blocking_reasons=blocking,
            warnings=warnings,
            suggested_actions=suggested,
            can_open_new_position=can_open,
            max_new_position_pct=max_new,
        )


# ============================================================
# Layer 3: 系统风控
# ============================================================

@dataclass(frozen=True)
class SystemRiskConfig:
    """系统风控配置。"""

    market_crash_threshold: float = -0.05  # 大盘单日暴跌阈值
    market_correction_threshold: float = -0.10  # 大盘累计调整阈值
    panic_index_threshold: float = 0.40  # 恐慌指数阈值（波动率）
    liquidity_min: float = 50_000_000  # 个股最低成交额（5000万）
    sector_panic_threshold: int = 5  # 单板块跌停>=5只触发警报
    halt_trigger_count: int = 3  # 连续3日触发系统风控 → 暂停所有策略
    auto_resume_days: int = 1  # 1天后自动恢复


@dataclass(frozen=True)
class MarketSnapshot:
    """市场快照。"""

    date: date
    hs300_change: float  # 沪深300日涨幅
    hs300_change_5d: float  # 沪深300 5日涨幅
    market_volatility: float  # 市场波动率
    limit_down_count: int  # 跌停股数量
    limit_up_count: int  # 涨停股数量
    avg_volume_ratio: float  # 整体量比
    north_flow: float = 0.0  # 北向资金净流入


@dataclass(frozen=True)
class SystemRiskCheck:
    """系统风控检查结果。"""

    risk_level: str  # "normal" / "elevated" / "high" / "critical"
    triggered_rules: list[str]
    market_regime: str  # "healthy" / "volatile" / "crash" / "panic"
    recommended_actions: list[str]
    halt_all_strategies: bool
    duration_days: int  # 风控持续天数


class SystemRiskManager:
    """系统层面风控。

    职责：
    - 大盘暴跌检测
    - 系统性风险预警
    - 板块共振预警
    - 流动性危机检测
    - 暂停/恢复所有策略
    """

    STATE_FILE = "data/system_risk_state.json"

    def __init__(self, config: SystemRiskConfig | None = None):
        self.config = config or SystemRiskConfig()
        self.state_path = Path(self.STATE_FILE)

    def check_market(self, snapshot: MarketSnapshot) -> SystemRiskCheck:
        """检查市场系统性风险。"""
        triggered: list[str] = []
        actions: list[str] = []
        risk_level = "normal"
        market_regime = "healthy"
        halt = False

        # 1. 大盘暴跌
        if snapshot.hs300_change <= self.config.market_crash_threshold:
            triggered.append(
                f"🔴 大盘暴跌：沪深300单日{snapshot.hs300_change:+.1%}"
            )
            actions.append("立即停止所有进攻策略")
            actions.append("仅保留防御性持仓")
            risk_level = "critical"
            market_regime = "crash"
            halt = True

        # 2. 大盘累计调整
        elif snapshot.hs300_change_5d <= self.config.market_correction_threshold:
            triggered.append(
                f"🟠 大盘调整：沪深300 5日{snapshot.hs300_change_5d:+.1%}"
            )
            actions.append("减仓至 50% 以下")
            actions.append("提高现金比例")
            risk_level = "high"
            market_regime = "volatile"

        # 3. 板块恐慌（跌停集中）
        if snapshot.limit_down_count >= self.config.sector_panic_threshold:
            triggered.append(
                f"🟠 跌停集中：今日{snapshot.limit_down_count}只跌停"
            )
            actions.append("警惕系统性风险，缩减新开仓")
            if risk_level == "normal":
                risk_level = "elevated"

        # 4. 波动率过高
        if snapshot.market_volatility >= self.config.panic_index_threshold:
            triggered.append(
                f"🟡 波动率过高：{snapshot.market_volatility:.1%}"
            )
            actions.append("缩小仓位，等待波动率回落")
            if risk_level == "normal":
                risk_level = "elevated"

        # 5. 量能萎缩（流动性问题）
        if snapshot.avg_volume_ratio < 0.7:
            triggered.append(
                f"🟡 量能萎缩：整体量比{snapshot.avg_volume_ratio:.1f}"
            )
            actions.append("市场冷清，减少新开仓")

        # 6. 北向资金大幅流出
        if snapshot.north_flow < -5_000_000_000:  # -50亿
            triggered.append(
                f"🟠 北向大幅流出：{snapshot.north_flow/100000000:.1f}亿"
            )
            actions.append("外资撤离，警惕白马股下跌")
            if risk_level == "normal":
                risk_level = "elevated"

        # 检查连续触发
        state = self._load_state()
        if triggered:
            state["consecutive_triggers"] = state.get("consecutive_triggers", 0) + 1
            state["last_trigger_date"] = snapshot.date.isoformat()
        else:
            state["consecutive_triggers"] = 0

        # 连续 N 日触发 → 强制停所有策略
        if state.get("consecutive_triggers", 0) >= self.config.halt_trigger_count:
            halt = True
            triggered.append(
                f"⛔ 连续{self.config.halt_trigger_count}日触发系统风控，所有策略暂停"
            )
            actions.append(f"等待 {self.config.auto_resume_days} 天后自动恢复")

        self._save_state(state)

        return SystemRiskCheck(
            risk_level=risk_level,
            triggered_rules=triggered,
            market_regime=market_regime,
            recommended_actions=actions,
            halt_all_strategies=halt,
            duration_days=state.get("consecutive_triggers", 0),
        )

    def is_halt_active(self) -> bool:
        """检查是否处于暂停状态。"""
        state = self._load_state()
        last_trigger = state.get("last_trigger_date")
        triggers = state.get("consecutive_triggers", 0)

        if not last_trigger or triggers < self.config.halt_trigger_count:
            return False

        last_date = date.fromisoformat(last_trigger)
        today = today_shanghai()
        days_since = (today - last_date).days

        if days_since >= self.config.auto_resume_days:
            # 自动恢复
            self._reset_state()
            return False

        return True

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _reset_state(self) -> None:
        self._save_state({"consecutive_triggers": 0})


# ============================================================
# 统一风控调度器
# ============================================================

@dataclass(frozen=True)
class UnifiedRiskReport:
    """统一风控报告。"""

    timestamp: datetime
    stock_checks: list[StockRiskCheck]
    portfolio_check: PortfolioRiskCheck
    system_check: SystemRiskCheck
    final_action: str  # 综合建议
    blocking_messages: list[str]


class UnifiedRiskManager:
    """三层风控统一调度。

    优先级：System > Portfolio > Stock
    任意一层触发紧急 → 强制平仓
    """

    def __init__(
        self,
        stock_config: StockRiskConfig | None = None,
        portfolio_config: PortfolioRiskConfig | None = None,
        system_config: SystemRiskConfig | None = None,
    ):
        self.stock_mgr = StockRiskManager(stock_config)
        self.portfolio_mgr = PortfolioRiskManager(portfolio_config)
        self.system_mgr = SystemRiskManager(system_config)

    def check_all(
        self,
        market_snapshot: MarketSnapshot,
        portfolio_state: PortfolioState,
        positions_detail: list[Dict[str, Any]],
        sector_map: Dict[str, str] | None = None,
    ) -> UnifiedRiskReport:
        """综合风控检查。

        positions_detail: [
            {symbol, entry_price, current_price, max_price, entry_date, position_pct}, ...
        ]
        """
        # 1. 系统风控（最高优先级）
        system_check = self.system_mgr.check_market(market_snapshot)

        # 2. 组合风控
        portfolio_check = self.portfolio_mgr.check_portfolio(
            portfolio_state, sector_map
        )

        # 3. 个股风控
        stock_checks = []
        for pos in positions_detail:
            check = self.stock_mgr.check_position(
                symbol=pos["symbol"],
                entry_price=pos["entry_price"],
                current_price=pos["current_price"],
                max_price_since_entry=pos["max_price"],
                entry_date=pos["entry_date"],
                position_pct=pos["position_pct"],
            )
            stock_checks.append(check)

        # 综合决策
        blocking: list[str] = []

        if system_check.halt_all_strategies:
            final_action = "halt_all"
            blocking.extend(system_check.triggered_rules)
        elif portfolio_check.overall_action == "emergency_exit":
            final_action = "emergency_exit_all"
            blocking.extend(portfolio_check.blocking_reasons)
        elif portfolio_check.overall_action == "stop_buy":
            final_action = "stop_buy_new"
            blocking.extend(portfolio_check.blocking_reasons)
        elif any(c.urgency == "critical" for c in stock_checks):
            critical_stocks = [c.symbol for c in stock_checks if c.urgency == "critical"]
            final_action = f"exit_stocks:{','.join(critical_stocks)}"
        else:
            final_action = "normal"

        return UnifiedRiskReport(
            timestamp=datetime.now(),
            stock_checks=stock_checks,
            portfolio_check=portfolio_check,
            system_check=system_check,
            final_action=final_action,
            blocking_messages=blocking,
        )

    def format_report(self, report: UnifiedRiskReport) -> str:
        """格式化报告为可读文本。"""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("🛡️ 三层风控综合报告")
        lines.append("=" * 60)

        # 系统风控
        lines.append(f"\n## Layer 3: 系统风控 - {report.system_check.risk_level.upper()}")
        if report.system_check.triggered_rules:
            for rule in report.system_check.triggered_rules:
                lines.append(f"  {rule}")
        else:
            lines.append("  ✅ 市场状态正常")

        # 组合风控
        lines.append(f"\n## Layer 2: 组合风控 - {report.portfolio_check.overall_action}")
        if report.portfolio_check.blocking_reasons:
            for r in report.portfolio_check.blocking_reasons:
                lines.append(f"  {r}")
        for w in report.portfolio_check.warnings[:3]:
            lines.append(f"  ⚠️ {w}")
        if not report.portfolio_check.blocking_reasons and not report.portfolio_check.warnings:
            lines.append("  ✅ 组合健康")

        # 个股风控
        critical = [c for c in report.stock_checks if c.urgency in ("high", "critical")]
        lines.append(f"\n## Layer 1: 个股风控 - {len(critical)}/{len(report.stock_checks)} 需关注")
        for check in critical[:5]:
            lines.append(f"  {check.symbol}: {check.action.upper()} - {check.reason}")

        # 综合决策
        lines.append("\n" + "=" * 60)
        lines.append(f"🎯 综合决策: {report.final_action}")
        if report.blocking_messages:
            lines.append("\n阻止行为：")
            for msg in report.blocking_messages:
                lines.append(f"  • {msg}")
        lines.append("=" * 60)

        return "\n".join(lines)
