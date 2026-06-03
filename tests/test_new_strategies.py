"""新策略 + 风控 + 自进化模块测试。

覆盖本轮新增的全部模块。VM 无 pytest，需在 trae 真环境跑：
    pytest tests/test_new_strategies.py -v

测试原则（宪法红线）：
- 不改测试让它绿，失败贴原文
- 验证新策略默认 enabled=False
- 验证策略接口契约（calculate_score 返回 dict[str,float]）
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _make_df(prices: list[float], volumes: list[float] | None = None, name: str = "测试股") -> pd.DataFrame:
    """构造测试用 K 线 DataFrame。"""
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "symbol": ["600000"] * n,
        "name": [name] * n,
        "open": prices,
        "high": [p * 1.02 for p in prices],
        "low": [p * 0.98 for p in prices],
        "close": prices,
        "volume": volumes,
        "amount": [p * v for p, v in zip(prices, volumes)],
    })


# ============================================================
# 短线策略：默认 enabled=False（宪法红线）
# ============================================================

def test_all_short_term_strategies_default_disabled():
    """所有新短线策略默认 enabled=False（未经双门验证不上线）。"""
    from aqsp.strategies import (
        LimitUpLadderStrategy,
        IntradayTradeStrategy,
        SectorRotationStrategy,
        MABreakoutStrategy,
        EventDrivenStrategy,
    )
    for cls in [
        LimitUpLadderStrategy,
        IntradayTradeStrategy,
        SectorRotationStrategy,
        MABreakoutStrategy,
        EventDrivenStrategy,
    ]:
        strategy = cls()
        assert strategy.config.enabled is False, f"{cls.__name__} 默认应 enabled=False"


def test_all_strategies_have_hypothesis():
    """所有策略必须有非空 hypothesis（宪法 #8）。"""
    from aqsp.strategies import (
        LimitUpLadderStrategy,
        IntradayTradeStrategy,
        SectorRotationStrategy,
        MABreakoutStrategy,
        EventDrivenStrategy,
    )
    for cls in [
        LimitUpLadderStrategy,
        IntradayTradeStrategy,
        SectorRotationStrategy,
        MABreakoutStrategy,
        EventDrivenStrategy,
    ]:
        strategy = cls()
        assert strategy.hypothesis, f"{cls.__name__} hypothesis 不能为空"
        assert len(strategy.hypothesis) > 10


# ============================================================
# 涨停板梯度策略
# ============================================================

def test_limit_up_ladder_score_range():
    """评分必须在 [0, 1]。"""
    from aqsp.strategies import LimitUpLadderStrategy
    s = LimitUpLadderStrategy()
    # 构造连续涨停（每日+10%）
    prices = [10.0]
    for _ in range(5):
        prices.append(round(prices[-1] * 1.10, 2))
    df = _make_df(prices)
    scores = s.calculate_score({"600000": df})
    assert "600000" in scores
    assert 0.0 <= scores["600000"] <= 1.0


def test_limit_up_ladder_detects_consecutive():
    """连板检测：连续涨停应得高分。"""
    from aqsp.strategies import LimitUpLadderStrategy
    s = LimitUpLadderStrategy()
    # 3连板
    prices = [10.0] * 15  # 前期平稳
    for _ in range(3):
        prices.append(round(prices[-1] * 1.10, 2))
    df = _make_df(prices)
    scores = s.calculate_score({"600000": df})
    # 有连板信号，分数应 > 0
    assert scores["600000"] > 0.0


def test_limit_up_ladder_empty_data():
    """空数据不崩。"""
    from aqsp.strategies import LimitUpLadderStrategy
    s = LimitUpLadderStrategy()
    scores = s.calculate_score({"600000": pd.DataFrame()})
    assert scores["600000"] == 0.0


# ============================================================
# 脏数据健壮性（停牌/数据缺失防御 - 2026-06-03 复核新增）
# ============================================================

def test_strategies_reject_zero_prices():
    """全0价格（脏数据）应判0分，不误评分。"""
    from aqsp.strategies import (
        LimitUpLadderStrategy, IntradayTradeStrategy,
        MABreakoutStrategy, EventDrivenStrategy,
    )
    zero_df = _make_df([0.0] * 70)
    for cls in [LimitUpLadderStrategy, IntradayTradeStrategy, MABreakoutStrategy, EventDrivenStrategy]:
        s = cls()
        scores = s.calculate_score({"600000": zero_df})
        assert scores["600000"] == 0.0, f"{cls.__name__} 全0价格应判0"


def test_strategies_reject_nan_prices():
    """含NaN价格（停牌/缺失）应判0分。"""
    from aqsp.strategies import LimitUpLadderStrategy, MABreakoutStrategy, EventDrivenStrategy
    for cls in [LimitUpLadderStrategy, MABreakoutStrategy, EventDrivenStrategy]:
        df = _make_df([10.0] * 70)
        df.loc[5, "close"] = np.nan
        s = cls()
        scores = s.calculate_score({"600000": df})
        assert scores["600000"] == 0.0, f"{cls.__name__} 含NaN应判0"


def test_sector_rotation_rejects_dirty_data():
    """板块轮动对脏数据判0。"""
    from aqsp.strategies import SectorRotationStrategy
    s = SectorRotationStrategy()
    df = _make_df([0.0] * 20)
    df["sector"] = "新能源"
    scores = s.calculate_score({"600000": df})
    assert scores["600000"] == 0.0


# ============================================================
# 均线突破策略
# ============================================================

def test_ma_breakout_score_range():
    from aqsp.strategies import MABreakoutStrategy
    s = MABreakoutStrategy()
    # 70天上升趋势
    prices = [10.0 + i * 0.1 for i in range(75)]
    df = _make_df(prices)
    scores = s.calculate_score({"600000": df})
    assert 0.0 <= scores["600000"] <= 1.0


def test_ma_breakout_uptrend_scores_high():
    """稳定上升趋势 + 放量突破应得较高分。"""
    from aqsp.strategies import MABreakoutStrategy
    s = MABreakoutStrategy()
    # 上升趋势
    prices = [10.0 + i * 0.15 for i in range(74)]
    prices.append(prices[-1] * 1.05)  # 突破日大涨
    volumes = [1_000_000] * 74 + [2_000_000]  # 突破放量
    df = _make_df(prices, volumes)
    scores = s.calculate_score({"600000": df})
    # 趋势+突破+放量，应有显著分数
    assert scores["600000"] > 0.4


def test_ma_breakout_insufficient_data():
    from aqsp.strategies import MABreakoutStrategy
    s = MABreakoutStrategy()
    df = _make_df([10.0] * 5)  # 不足 MA60
    scores = s.calculate_score({"600000": df})
    assert scores["600000"] == 0.0


# ============================================================
# 日内交易策略
# ============================================================

def test_intraday_score_range():
    from aqsp.strategies import IntradayTradeStrategy
    s = IntradayTradeStrategy()
    prices = [10.0 + np.sin(i) for i in range(30)]
    df = _make_df(prices)
    scores = s.calculate_score({"600000": df})
    assert 0.0 <= scores["600000"] <= 1.0


# ============================================================
# 板块轮动策略
# ============================================================

def test_sector_rotation_score_range():
    from aqsp.strategies import SectorRotationStrategy
    s = SectorRotationStrategy()
    prices = [10.0 + i * 0.1 for i in range(20)]
    df = _make_df(prices)
    df["sector"] = "新能源"
    scores = s.calculate_score({"600000": df})
    assert 0.0 <= scores["600000"] <= 1.0


def test_sector_rotation_analyze():
    """板块分析返回 SectorMetrics。"""
    from aqsp.strategies import SectorRotationStrategy
    s = SectorRotationStrategy()
    df1 = _make_df([10.0 + i * 0.2 for i in range(20)])
    df1["sector"] = "新能源"
    df2 = _make_df([20.0 + i * 0.3 for i in range(20)])
    df2["sector"] = "新能源"
    df2["symbol"] = "600001"
    metrics = s.analyze_sectors({"600000": df1, "600001": df2})
    assert "新能源" in metrics
    assert metrics["新能源"].heat_score >= 0


# ============================================================
# 事件驱动策略
# ============================================================

def test_event_driven_score_range():
    from aqsp.strategies import EventDrivenStrategy
    s = EventDrivenStrategy()
    prices = [10.0] * 30
    prices[-1] = 11.0  # 突然大涨
    volumes = [1_000_000] * 29 + [5_000_000]  # 放巨量
    df = _make_df(prices, volumes)
    scores = s.calculate_score({"600000": df})
    assert 0.0 <= scores["600000"] <= 1.0


# ============================================================
# 三层风控
# ============================================================

def test_stock_risk_hard_stop():
    """硬止损触发。"""
    from aqsp.risk.unified_risk import StockRiskManager
    from datetime import date
    mgr = StockRiskManager()
    check = mgr.check_position(
        symbol="600000",
        entry_price=10.0,
        current_price=9.4,  # -6%
        max_price_since_entry=10.0,
        entry_date=date(2024, 1, 1),
        position_pct=0.2,
        today=date(2024, 1, 3),
    )
    assert check.action == "exit"
    assert check.urgency == "critical"


def test_stock_risk_normal_hold():
    """正常持有。"""
    from aqsp.risk.unified_risk import StockRiskManager
    from datetime import date
    mgr = StockRiskManager()
    check = mgr.check_position(
        symbol="600000",
        entry_price=10.0,
        current_price=10.2,  # +2%
        max_price_since_entry=10.2,
        entry_date=date(2024, 1, 1),
        position_pct=0.2,
        today=date(2024, 1, 2),
    )
    assert check.action == "hold"


def test_portfolio_risk_daily_loss_blocks():
    """单日亏损超限阻止开仓。"""
    from aqsp.risk.unified_risk import PortfolioRiskManager, PortfolioState
    mgr = PortfolioRiskManager()
    state = PortfolioState(
        total_value=100000,
        cash=50000,
        daily_pnl=-3000,  # -3% > 2% 上限
    )
    check = mgr.check_portfolio(state)
    assert check.can_open_new_position is False
    assert any("单日亏损" in r for r in check.blocking_reasons)


def test_system_risk_market_crash():
    """大盘暴跌触发系统风控。"""
    from aqsp.risk.unified_risk import SystemRiskManager, MarketSnapshot
    from datetime import date
    mgr = SystemRiskManager()
    snapshot = MarketSnapshot(
        date=date(2024, 1, 1),
        hs300_change=-0.06,  # -6% 暴跌
        hs300_change_5d=-0.08,
        market_volatility=0.35,
        limit_down_count=20,
        limit_up_count=5,
        avg_volume_ratio=1.2,
    )
    check = mgr.check_market(snapshot)
    assert check.risk_level == "critical"
    assert check.halt_all_strategies is True


# ============================================================
# 自进化
# ============================================================

def test_factor_adaptor_dead_factor():
    """IC 转负的因子应停用。"""
    from aqsp.strategies.adaptive_evolution import FactorWeightAdaptor, FactorPerformance
    from datetime import date
    adaptor = FactorWeightAdaptor()
    perf = {
        "momentum": FactorPerformance(
            factor_name="momentum",
            ic_30d=-0.05,  # 负 IC
            ic_decay=-0.1,
            win_rate_30d=0.4,
            sharpe_30d=0.2,
            sample_count=30,
            last_updated=date(2024, 1, 1),
        )
    }
    adjustments = adaptor.evaluate_factors(perf, {"momentum": 0.4})
    assert len(adjustments) == 1
    assert adjustments[0].suggested_weight == 0.0  # 停用


def test_strategy_mix_regime_selection():
    """市场制度匹配策略组合。"""
    from aqsp.strategies.adaptive_evolution import StrategyMixAdaptor
    adaptor = StrategyMixAdaptor()
    bull_mix = adaptor.select_mix("stable_bull")
    assert "limit_up_ladder" in bull_mix.enabled_strategies
    bear_mix = adaptor.select_mix("stable_bear")
    assert "quality" in bear_mix.enabled_strategies


def test_emergency_defensive_on_halt():
    """系统暂停时切换到紧急防守。"""
    from aqsp.strategies.adaptive_evolution import AdaptiveEvolutionCoordinator
    coord = AdaptiveEvolutionCoordinator()
    mix = coord.daily_adapt("stable_bull", is_system_halt=True)
    assert mix.name == "紧急防守"


# ============================================================
# 资金流分析
# ============================================================

def test_fund_flow_main_force_accumulating():
    """主力吸筹模式识别。"""
    from aqsp.strategies.fund_flow_analyzer import FundFlowAnalyzer, FundFlowSnapshot
    from datetime import date
    analyzer = FundFlowAnalyzer()
    # 价格震荡 + 主力持续流入
    snapshots = [
        FundFlowSnapshot(
            symbol="600000",
            date=date(2024, 1, i + 1),
            main_inflow=5000,  # 持续流入
            super_large_inflow=3000,
            large_inflow=2000,
            medium_outflow=-1000,
            small_outflow=-2000,
            main_inflow_ratio=0.3,
            north_holding_change=0,
        )
        for i in range(5)
    ]
    price_df = _make_df([10.0, 10.1, 9.9, 10.0, 10.05])  # 震荡
    signal = analyzer.analyze_flow_pattern(snapshots, price_df)
    assert signal is not None
    assert signal.signal_type == "main_force_accumulating"


# ============================================================
# briefing schema/renderer
# ============================================================

def test_briefing_schema_imports():
    """schema 模块可导入（验证全部数据类都存在）。"""
    from aqsp.briefing.schema import (
        BriefingData,
        Pick,
        RegimeInfo,
        SourceStatus,
        ThemeHeat,
    )
    # 引用全部，既验证可导入又避免 F401
    assert all(
        cls is not None
        for cls in (BriefingData, Pick, RegimeInfo, SourceStatus, ThemeHeat)
    )


def test_markdown_renderer_imports():
    """renderer 模块可导入。"""
    from aqsp.briefing.renderer import MarkdownRenderer
    assert MarkdownRenderer is not None
