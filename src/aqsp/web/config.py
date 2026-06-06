"""仪表盘配置文件"""

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

# 风险阈值
RISK_CONFIG = {
    "single_stock_stop": -0.08,  # 单股-8%
    "portfolio_stop": -0.15,     # 组合-15%
    "warning_threshold": -0.05,  # 警告阈值-5%
    "enable_trailing": True,
    "trailing_stop_pct": 0.05,   # 5%移动止损
}

# 策略配置（用于显示）
STRATEGIES = {
    "mean_reversion": {
        "name": "均值回归",
        "weight": 0.30,
        "description": "捕捉超买超卖机会",
    },
    "sector_rotation": {
        "name": "板块轮动",
        "weight": 0.28,
        "description": "跟踪热点板块切换",
    },
    "trend_following": {
        "name": "趋势跟踪",
        "weight": 0.25,
        "description": "顺势而为，中期持有",
    },
    "event_driven": {
        "name": "事件驱动",
        "weight": 0.17,
        "description": "公告驱动，机会型交易",
    },
}
