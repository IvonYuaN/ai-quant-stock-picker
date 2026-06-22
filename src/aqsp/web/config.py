"""仪表盘配置文件"""

from aqsp.strategies.thresholds import Thresholds
from aqsp.strategies.thresholds import load_thresholds

# Streamlit配置
DASHBOARD_CONFIG = {
    "port": 8501,
    "host": "localhost",
    "theme": "light",
    "refresh_interval": 60,  # 秒
}

# 数据源配置
DATA_CONFIG = {
    "ledger_path": "data/predictions.jsonl",
    "paper_ledger_path": "data/paper_trades.jsonl",
    "logs_path": "logs/trades",
    "cache_ttl": 60,  # 秒
}

# 仪表盘显示配置
DISPLAY_CONFIG = {
    # 核心指标
    "show_metrics": True,
    "metric_columns": 4,
    # 图表
    "show_charts": True,
    "chart_height": 350,
    # 表格
    "rows_per_page": 20,
    "show_search": True,
    # 告警
    "show_alerts": True,
    "alert_severity": ["critical", "warning"],
}

_STRATEGY_DISPLAY_NAMES = {
    "momentum": "动量趋势",
    "quality": "质量稳健",
    "value": "价值低估",
    "volume": "量能突破",
    "mean_reversion": "均值回归",
    "triple_rise": "三连阳",
}
_STRATEGY_DISPLAY_DESCRIPTIONS = {
    "momentum": "顺势筛选相对强势候选",
    "quality": "偏防守的基本面质量因子",
    "value": "低估值与安全边际因子",
    "volume": "量价配合的短线触发因子",
    "mean_reversion": "震荡/超跌后的回归机会",
    "triple_rise": "连续阳线后的短线强势形态",
}


def get_risk_config(*, thresholds: Thresholds | None = None) -> dict[str, object]:
    current = thresholds or load_thresholds()
    return {
        "single_stock_stop": -float(current.risk.single_stock_stop_pct),
        "portfolio_stop": -float(current.risk.portfolio_stop_pct),
        "warning_threshold": -float(current.risk.warning_threshold_pct),
        "enable_trailing": bool(current.risk.enable_trailing_stop),
        "trailing_stop_pct": float(current.risk.trailing_stop_pct),
    }


def get_strategy_display_config(
    *, thresholds: Thresholds | None = None, regime: str = "stable_bull"
) -> dict[str, dict[str, object]]:
    current = thresholds or load_thresholds()
    weights = current.regime.strategy_weights.get(regime)
    if weights is None:
        weights = current.regime.strategy_weights.get("stable_bull")
    raw_weights = weights.__dict__ if weights is not None else {}
    total = sum(float(value) for value in raw_weights.values() if float(value) > 0)
    if total <= 0:
        total = 1.0
    return {
        strategy_id: {
            "name": _STRATEGY_DISPLAY_NAMES.get(strategy_id, strategy_id),
            "weight": round(float(weight) / total, 4),
            "description": _STRATEGY_DISPLAY_DESCRIPTIONS.get(strategy_id, ""),
        }
        for strategy_id, weight in raw_weights.items()
        if float(weight) > 0
    }


# 兼容旧导入；新代码用函数，避免长生命周期进程读到陈旧阈值。
RISK_CONFIG = get_risk_config()
STRATEGIES = get_strategy_display_config()
