"""Prometheus 指标定义和收集器。

所有指标遵循 Prometheus 命名规范：
- 使用下划线分隔
- 后缀表示单位或类型（_total, _seconds, _bytes 等）
- 业务指标带 aqsp_ 前缀
"""

from prometheus_client import Counter, Gauge, Histogram

# ============================================================================
# API 请求指标
# ============================================================================

# API 请求总数（按端点、方法、状态码分组）
http_requests_total = Counter(
    "aqsp_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

# API 响应时间分布（按端点、方法分组）
http_request_duration_seconds = Histogram(
    "aqsp_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ============================================================================
# 数据源健康状态指标
# ============================================================================

# 数据源可用性（1=可用, 0=不可用）
datasource_available = Gauge(
    "aqsp_datasource_available",
    "Data source availability (1=up, 0=down)",
    ["source"],
)

# 数据源最后成功时间（Unix timestamp）
datasource_last_success_timestamp = Gauge(
    "aqsp_datasource_last_success_timestamp",
    "Last successful data fetch timestamp",
    ["source"],
)

# ============================================================================
# 业务指标
# ============================================================================

# 策略命中率（百分比 0-100）
strategy_hit_rate = Gauge(
    "aqsp_strategy_hit_rate_percent",
    "Strategy hit rate percentage",
    ["strategy"],
)

# 候选股数量
candidate_stocks_count = Gauge(
    "aqsp_candidate_stocks_count",
    "Number of candidate stocks",
    ["date"],
)

# 持仓数量
portfolio_holdings_count = Gauge(
    "aqsp_portfolio_holdings_count",
    "Number of holdings in portfolio",
)

# 持仓总市值
portfolio_total_market_value = Gauge(
    "aqsp_portfolio_total_market_value_cny",
    "Total market value of portfolio in CNY",
)

# 持仓浮动盈亏
portfolio_unrealized_pnl = Gauge(
    "aqsp_portfolio_unrealized_pnl_cny",
    "Unrealized profit/loss in CNY",
)

# ============================================================================
# 缓存指标
# ============================================================================

# 缓存命中率
cache_hit_rate = Gauge(
    "aqsp_cache_hit_rate_percent",
    "Cache hit rate percentage",
    ["cache_name"],
)

# 缓存条目数
cache_entries_count = Gauge(
    "aqsp_cache_entries_count",
    "Number of entries in cache",
    ["cache_name"],
)


def update_datasource_health(source: str, is_available: bool, timestamp: float | None = None):
    """更新数据源健康状态指标。

    Args:
        source: 数据源名称（如 "tencent_quote", "eastmoney_reports"）
        is_available: 是否可用
        timestamp: 最后成功时间戳（可选）
    """
    datasource_available.labels(source=source).set(1 if is_available else 0)
    if timestamp is not None:
        datasource_last_success_timestamp.labels(source=source).set(timestamp)


def update_strategy_metrics(strategy: str, hit_rate: float):
    """更新策略命中率指标。

    Args:
        strategy: 策略名称
        hit_rate: 命中率百分比（0-100）
    """
    strategy_hit_rate.labels(strategy=strategy).set(hit_rate)


def update_candidate_count(date: str, count: int):
    """更新候选股数量指标。

    Args:
        date: 日期（ISO 格式）
        count: 候选股数量
    """
    candidate_stocks_count.labels(date=date).set(count)


def update_portfolio_metrics(holdings_count: int, total_value: float, unrealized_pnl: float):
    """更新持仓相关指标。

    Args:
        holdings_count: 持仓数量
        total_value: 总市值
        unrealized_pnl: 浮动盈亏
    """
    portfolio_holdings_count.set(holdings_count)
    portfolio_total_market_value.set(total_value)
    portfolio_unrealized_pnl.set(unrealized_pnl)


def update_cache_metrics(cache_name: str, hit_rate: float, entries_count: int):
    """更新缓存指标。

    Args:
        cache_name: 缓存名称
        hit_rate: 命中率百分比（0-100）
        entries_count: 缓存条目数
    """
    cache_hit_rate.labels(cache_name=cache_name).set(hit_rate)
    cache_entries_count.labels(cache_name=cache_name).set(entries_count)
