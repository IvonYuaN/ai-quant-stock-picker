# Prometheus 监控集成使用指南

## 概述

AQSP API 已集成 Prometheus 指标监控，可以实时监控 API 性能、业务指标和系统健康状态。

## 快速开始

### 1. 安装依赖

```bash
# 安装 prometheus-client
pip install -e ".[api]"
```

### 2. 启动 API 服务

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8900
```

### 3. 访问指标端点

```bash
curl http://localhost:8900/metrics
```

## 指标说明

### API 请求指标

#### `aqsp_http_requests_total`
- **类型**: Counter（计数器）
- **说明**: HTTP 请求总数
- **标签**:
  - `method`: HTTP 方法（GET, POST, DELETE）
  - `endpoint`: API 端点路径
  - `status_code`: HTTP 状态码

#### `aqsp_http_request_duration_seconds`
- **类型**: Histogram（直方图）
- **说明**: HTTP 请求响应时间分布
- **标签**:
  - `method`: HTTP 方法
  - `endpoint`: API 端点路径
- **Buckets**: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0 秒

### 数据源健康状态指标

#### `aqsp_datasource_available`
- **类型**: Gauge（测量值）
- **说明**: 数据源可用性（1=可用, 0=不可用）
- **标签**:
  - `source`: 数据源名称

#### `aqsp_datasource_last_success_timestamp`
- **类型**: Gauge
- **说明**: 数据源最后成功获取数据的时间戳
- **标签**:
  - `source`: 数据源名称

### 业务指标

#### `aqsp_strategy_hit_rate_percent`
- **类型**: Gauge
- **说明**: 策略命中率百分比（0-100）
- **标签**:
  - `strategy`: 策略名称

#### `aqsp_candidate_stocks_count`
- **类型**: Gauge
- **说明**: 候选股数量
- **标签**:
  - `date`: 日期（ISO 格式）

#### `aqsp_portfolio_holdings_count`
- **类型**: Gauge
- **说明**: 持仓数量

#### `aqsp_portfolio_total_market_value_cny`
- **类型**: Gauge
- **说明**: 持仓总市值（人民币）

#### `aqsp_portfolio_unrealized_pnl_cny`
- **类型**: Gauge
- **说明**: 持仓浮动盈亏（人民币）

### 缓存指标

#### `aqsp_cache_hit_rate_percent`
- **类型**: Gauge
- **说明**: 缓存命中率百分比（0-100）
- **标签**:
  - `cache_name`: 缓存名称

#### `aqsp_cache_entries_count`
- **类型**: Gauge
- **说明**: 缓存条目数量
- **标签**:
  - `cache_name`: 缓存名称

## Prometheus 配置

### 配置文件位置

`deploy/prometheus/prometheus.yml`

### 启动 Prometheus

```bash
# 使用 Docker
docker run -d \
  --name prometheus \
  -p 9090:9090 \
  -v $(pwd)/deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus

# 或使用二进制
prometheus --config.file=deploy/prometheus/prometheus.yml
```

### 访问 Prometheus UI

打开浏览器访问: http://localhost:9090

### 示例查询

```promql
# API 请求速率（QPS）
rate(aqsp_http_requests_total[1m])

# P99 响应时间
histogram_quantile(0.99, rate(aqsp_http_request_duration_seconds_bucket[5m]))

# 错误率（5xx 状态码）
sum(rate(aqsp_http_requests_total{status_code=~"5.."}[5m])) / sum(rate(aqsp_http_requests_total[5m]))

# 数据源可用性
aqsp_datasource_available

# 策略命中率
aqsp_strategy_hit_rate_percent

# 候选股数量趋势
aqsp_candidate_stocks_count
```

## Grafana 配置

### 配置文件位置

`deploy/grafana/dashboard.json`

### 启动 Grafana

```bash
# 使用 Docker
docker run -d \
  --name grafana \
  -p 3000:3000 \
  grafana/grafana

# 默认登录: admin/admin
```

### 导入仪表盘

1. 登录 Grafana (http://localhost:3000)
2. 添加 Prometheus 数据源:
   - Configuration → Data Sources → Add data source
   - 选择 Prometheus
   - URL: http://prometheus:9090 (Docker) 或 http://localhost:9090
   - Save & Test

3. 导入仪表盘:
   - Create → Import
   - 上传 `deploy/grafana/dashboard.json`
   - 选择 Prometheus 数据源
   - Import

### 仪表盘面板说明

- **API 请求总数（QPS）**: 实时请求速率
- **API 响应时间（P99）**: P50/P95/P99 响应时间
- **HTTP 状态码分布**: 状态码饼图
- **数据源健康状态**: 各数据源可用性
- **候选股数量**: 候选股数量趋势
- **策略命中率**: 各策略命中率仪表盘
- **持仓统计**: 持仓数量、市值、盈亏
- **热门端点 Top 10**: 最热门的 API 端点

## 业务指标更新

### 在代码中更新指标

```python
from metrics import (
    update_datasource_health,
    update_strategy_metrics,
    update_candidate_count,
    update_portfolio_metrics,
    update_cache_metrics,
)

# 更新数据源健康状态
import time
update_datasource_health("tencent_quote", True, time.time())

# 更新策略命中率
update_strategy_metrics("momentum", 68.5)

# 更新候选股数量
update_candidate_count("2026-09-28", 42)

# 更新持仓指标
update_portfolio_metrics(
    holdings_count=10,
    total_value=150000.0,
    unrealized_pnl=5000.0
)

# 更新缓存指标
update_cache_metrics("quote_cache", 85.5, 1234)
```

## 测试

运行指标集成测试:

```bash
pytest backend/tests/test_metrics.py -v
```

## 告警配置（可选）

### 创建告警规则

在 `deploy/prometheus/alerts/` 目录下创建告警规则文件:

```yaml
# deploy/prometheus/alerts/aqsp_alerts.yml
groups:
  - name: aqsp_api
    interval: 30s
    rules:
      # API 错误率过高
      - alert: HighErrorRate
        expr: |
          sum(rate(aqsp_http_requests_total{status_code=~"5.."}[5m])) 
          / sum(rate(aqsp_http_requests_total[5m])) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "API 错误率过高"
          description: "最近 5 分钟 API 错误率超过 5%"

      # API 响应时间过长
      - alert: SlowResponse
        expr: |
          histogram_quantile(0.99, 
            rate(aqsp_http_request_duration_seconds_bucket[5m])
          ) > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "API 响应时间过长"
          description: "P99 响应时间超过 2 秒"

      # 数据源不可用
      - alert: DatasourceDown
        expr: aqsp_datasource_available == 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "数据源不可用"
          description: "数据源 {{ $labels.source }} 已不可用超过 5 分钟"

      # 策略命中率过低
      - alert: LowHitRate
        expr: aqsp_strategy_hit_rate_percent < 40
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "策略命中率过低"
          description: "策略 {{ $labels.strategy }} 命中率低于 40%"
```

### 配置 Alertmanager（可选）

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'default'

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'http://your-webhook-endpoint'
```

## 生产部署建议

1. **安全性**:
   - `/metrics` 端点应仅在内网访问
   - 使用防火墙限制 Prometheus 访问
   - 考虑添加基础认证

2. **性能**:
   - 指标抓取间隔建议 10-15 秒
   - 避免高基数标签（已通过路径参数简化处理）
   - 定期清理过期指标

3. **存储**:
   - Prometheus 默认保留 15 天数据
   - 考虑配置远程存储（如 Thanos, Cortex）
   - 监控磁盘使用情况

4. **高可用**:
   - 部署多个 Prometheus 实例
   - 使用 Prometheus Federation
   - 配置告警去重

## 故障排查

### 指标端点无响应

```bash
# 检查服务是否运行
curl http://localhost:8900/api/health

# 检查指标端点
curl http://localhost:8900/metrics
```

### Prometheus 无法抓取指标

```bash
# 检查 Prometheus targets 状态
# 访问 http://localhost:9090/targets

# 检查网络连通性
curl http://localhost:8900/metrics
```

### 指标数据异常

```bash
# 重启 API 服务会清空内存中的指标
# 考虑使用 Prometheus 的持久化存储
```

## 参考资料

- [Prometheus 官方文档](https://prometheus.io/docs/)
- [Grafana 官方文档](https://grafana.com/docs/)
- [prometheus-client Python 文档](https://prometheus.github.io/client_python/)
- [Prometheus 最佳实践](https://prometheus.io/docs/practices/naming/)
