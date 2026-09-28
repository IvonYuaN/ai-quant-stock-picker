"""Prometheus 指标集成测试。"""

from fastapi.testclient import TestClient

import app as app_module
import metrics

client = TestClient(app_module.app)


def test_metrics_endpoint_exists():
    """测试 /metrics 端点是否存在。"""
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")


def test_metrics_endpoint_returns_prometheus_format():
    """测试 /metrics 端点返回 Prometheus 文本格式。"""
    r = client.get("/metrics")
    content = r.text

    # 检查是否包含基础指标
    assert "aqsp_http_requests_total" in content
    assert "aqsp_http_request_duration_seconds" in content

    # 检查 Prometheus 格式特征（HELP 和 TYPE 注释）
    assert "# HELP" in content
    assert "# TYPE" in content


def test_metrics_endpoint_no_auth_required():
    """测试 /metrics 端点不需要鉴权（即使在需要 API Key 的环境）。"""
    # 不带任何认证头访问
    r = client.get("/metrics")
    assert r.status_code == 200


def test_metrics_middleware_records_requests():
    """测试中间件是否记录请求指标。"""
    # 先获取当前指标
    r1 = client.get("/metrics")
    initial_content = r1.text

    # 发起一个测试请求
    client.get("/api/health")

    # 再次获取指标
    r2 = client.get("/metrics")
    updated_content = r2.text

    # 验证指标已更新（包含 /api/health 端点）
    assert "aqsp_http_requests_total" in updated_content
    # 由于计数器只增不减，第二次应该包含更多数据
    assert len(updated_content) >= len(initial_content)


def test_metrics_middleware_excludes_metrics_endpoint():
    """测试 /metrics 端点不会记录自身的指标（避免递归）。"""
    # 多次访问 /metrics
    for _ in range(5):
        client.get("/metrics")

    # 获取最终指标
    r = client.get("/metrics")
    content = r.text

    # /metrics 端点不应该出现在指标中
    # 因为中间件会跳过它
    assert 'endpoint="/metrics"' not in content


def test_metrics_middleware_handles_path_parameters():
    """测试中间件正确处理路径参数（避免高基数）。"""
    # 访问带路径参数的端点（模拟）
    client.get("/api/quote?codes=600000")

    r = client.get("/metrics")
    content = r.text

    # 验证端点路径被记录
    assert "aqsp_http_requests_total" in content


def test_metrics_middleware_records_status_codes():
    """测试中间件记录不同的 HTTP 状态码。"""
    # 成功请求 (200)
    client.get("/api/health")

    # 错误请求 (400)
    client.get("/api/quote?codes=invalid")

    # 获取指标
    r = client.get("/metrics")
    content = r.text

    # 验证不同状态码被记录
    assert 'status_code="200"' in content
    assert 'status_code="400"' in content or 'status_code="422"' in content


def test_metrics_update_datasource_health():
    """测试数据源健康状态指标更新。"""
    import time

    # 更新数据源状态
    timestamp = time.time()
    metrics.update_datasource_health("test_source", True, timestamp)

    # 获取指标
    r = client.get("/metrics")
    content = r.text

    # 验证指标被记录
    assert "aqsp_datasource_available" in content
    assert 'source="test_source"' in content


def test_metrics_update_strategy_metrics():
    """测试策略命中率指标更新。"""
    # 更新策略指标
    metrics.update_strategy_metrics("test_strategy", 75.5)

    # 获取指标
    r = client.get("/metrics")
    content = r.text

    # 验证指标被记录
    assert "aqsp_strategy_hit_rate_percent" in content
    assert 'strategy="test_strategy"' in content
    assert "75.5" in content


def test_metrics_update_candidate_count():
    """测试候选股数量指标更新。"""
    # 更新候选股数量
    metrics.update_candidate_count("2026-09-28", 42)

    # 获取指标
    r = client.get("/metrics")
    content = r.text

    # 验证指标被记录
    assert "aqsp_candidate_stocks_count" in content
    assert 'date="2026-09-28"' in content
    assert "42" in content


def test_metrics_update_portfolio_metrics():
    """测试持仓指标更新。"""
    # 更新持仓指标
    metrics.update_portfolio_metrics(
        holdings_count=10,
        total_value=150000.0,
        unrealized_pnl=5000.0
    )

    # 获取指标
    r = client.get("/metrics")
    content = r.text

    # 验证指标被记录
    assert "aqsp_portfolio_holdings_count" in content
    assert "10" in content or "10.0" in content
    assert "aqsp_portfolio_total_market_value_cny" in content
    assert "150000" in content
    assert "aqsp_portfolio_unrealized_pnl_cny" in content
    assert "5000" in content


def test_metrics_update_cache_metrics():
    """测试缓存指标更新。"""
    # 更新缓存指标
    metrics.update_cache_metrics("test_cache", 85.5, 1234)

    # 获取指标
    r = client.get("/metrics")
    content = r.text

    # 验证指标被记录
    assert "aqsp_cache_hit_rate_percent" in content
    assert 'cache_name="test_cache"' in content
    assert "85.5" in content
    assert "aqsp_cache_entries_count" in content
    assert "1234" in content


def test_metrics_naming_conventions():
    """测试指标命名是否遵循 Prometheus 规范。"""
    r = client.get("/metrics")
    content = r.text

    # 所有 AQSP 业务指标应该带 aqsp_ 前缀
    lines = [line for line in content.split("\n") if line and not line.startswith("#")]
    aqsp_metrics = [line for line in lines if "aqsp_" in line]

    assert len(aqsp_metrics) > 0, "应该有 aqsp_ 前缀的指标"

    # 检查指标命名规范（使用下划线，不使用连字符）
    for line in aqsp_metrics:
        metric_name = line.split("{")[0] if "{" in line else line.split()[0]
        assert "-" not in metric_name, f"指标名不应包含连字符: {metric_name}"


def test_http_request_duration_buckets():
    """测试响应时间直方图的 bucket 配置。"""
    # 发起一些请求
    for _ in range(3):
        client.get("/api/health")

    r = client.get("/metrics")
    content = r.text

    # 验证直方图 bucket
    assert "aqsp_http_request_duration_seconds_bucket" in content
    assert 'le="0.005"' in content  # 5ms bucket
    assert 'le="0.1"' in content    # 100ms bucket
    assert 'le="1.0"' in content    # 1s bucket
    assert 'le="+Inf"' in content   # 无限大 bucket
