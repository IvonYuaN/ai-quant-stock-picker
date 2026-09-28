# 数据源健康监控系统

## 概述

数据源健康监控系统提供主动健康检查、自动故障切换和告警通知功能，确保量化选股系统的数据源可靠性。

## 功能特性

1. **主动健康检查**
   - 定期探测所有配置的数据源
   - 记录响应时间和成功率
   - 跟踪连续失败次数

2. **自动故障切换**
   - 当主数据源失败时自动切换到备用源
   - 基于健康历史优化数据源选择顺序
   - 记录所有故障切换事件

3. **告警通知**
   - 连续失败达到阈值时自动发送告警
   - 复用现有的通知系统（飞书/邮件等）
   - 包含故障详情和恢复建议

4. **健康状态持久化**
   - 所有健康数据保存到 `data/source_health.json`
   - 支持跨进程/重启的状态共享
   - 并发安全的文件更新

## 快速开始

### 1. 启用监控

设置环境变量：

```bash
export AQSP_ENABLE_SOURCE_MONITOR=true
```

### 2. 运行监控服务

**单次检查**（用于测试）：

```bash
python scripts/monitor_data_sources.py
```

**守护进程模式**（持续监控）：

```bash
export AQSP_MONITOR_DAEMON=true
python scripts/monitor_data_sources.py
```

### 3. 配置选项

通过环境变量配置：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `AQSP_ENABLE_SOURCE_MONITOR` | `false` | 是否启用监控 |
| `AQSP_SOURCE_HEALTH` | `data/source_health.json` | 健康状态文件路径 |
| `AQSP_MONITOR_CHECK_INTERVAL_SECONDS` | `300` | 检查间隔（秒） |
| `AQSP_MONITOR_FAILURE_THRESHOLD` | `3` | 失败阈值（次） |
| `AQSP_MONITOR_CHECK_TIMEOUT_SECONDS` | `10.0` | 单次检查超时（秒） |
| `AQSP_MONITOR_DAEMON` | `false` | 守护进程模式 |

示例：

```bash
# 每10分钟检查一次，失败5次才告警
export AQSP_MONITOR_CHECK_INTERVAL_SECONDS=600
export AQSP_MONITOR_FAILURE_THRESHOLD=5
```

## 架构设计

### 核心组件

#### 1. DataSourceMonitor

主监控类，负责健康检查和状态管理：

```python
from aqsp.data.source_health import DataSourceMonitor

monitor = DataSourceMonitor(
    health_path="data/source_health.json",
    check_timeout_seconds=10.0,
    failure_threshold=3,
)

# 执行健康检查
result = monitor.check_source_health(
    source_id="eastmoney",
    check_func=lambda: check_eastmoney_connection(),
)

# 记录结果
monitor.record_check_result(result)

# 获取状态
status = monitor.get_source_status("eastmoney")
print(f"健康: {status.is_healthy}, 成功率: {status.success_rate:.1%}")
```

#### 2. 监控服务 (scripts/monitor_data_sources.py)

独立进程，定期检查所有数据源：

- 遍历所有 runtime_ready 的数据源
- 执行轻量级健康检查（获取股票池或少量测试数据）
- 记录响应时间和结果
- 生成健康报告
- 发送告警（如需要）

#### 3. MultiSource 集成

`MultiSource` 在故障切换时自动记录健康事件：

```python
# 在 multi_source.py 中自动集成
from aqsp.data.multi_source_health import track_multi_source_fetch

# 数据获取成功时
track_multi_source_fetch(
    requested_source="eastmoney",
    actual_source="sina",  # 实际使用了备用源
    start_time=fetch_start_time,
    success=True,
)

# 数据获取失败时
track_multi_source_fetch(
    requested_source="eastmoney",
    actual_source=None,
    start_time=fetch_start_time,
    success=False,
    error=DataError("所有数据源失败"),
)
```

### 健康状态文件格式

`data/source_health.json` 结构：

```json
{
  "updated_at": "2026-09-28T14:30:00",
  "consecutive_failures": 0,
  "last_success": "2026-09-28T14:30:00",
  "last_failure": "2026-09-28T14:00:00",
  "last_requested_source": "eastmoney",
  "last_actual_source": "eastmoney",
  "last_error": "",
  "fallback_used": false,
  "sources": {
    "eastmoney": {
      "successes": 100,
      "failures": 5,
      "consecutive_failures": 0,
      "last_success": "2026-09-28T14:30:00",
      "last_failure": "2026-09-28T14:00:00",
      "last_error": "",
      "response_times": [120.5, 135.2, 110.8],
      "avg_response_time_ms": 122.17
    },
    "sina": {
      "successes": 50,
      "failures": 2,
      "consecutive_failures": 0,
      "last_success": "2026-09-28T14:25:00",
      "avg_response_time_ms": 95.5
    }
  }
}
```

## 使用场景

### 场景1: 生产环境持续监控

```bash
# 在服务器上运行守护进程
export AQSP_ENABLE_SOURCE_MONITOR=true
export AQSP_MONITOR_DAEMON=true
export AQSP_MONITOR_CHECK_INTERVAL_SECONDS=300  # 每5分钟

nohup python scripts/monitor_data_sources.py > logs/monitor.log 2>&1 &
```

### 场景2: 开盘前检查

```bash
# 每日开盘前运行一次检查
python scripts/monitor_data_sources.py --verbose

# 查看健康报告
cat data/source_health_report.json
```

### 场景3: 调试数据源问题

```bash
# 详细日志模式
python scripts/monitor_data_sources.py --verbose

# 使用自定义阈值
python scripts/monitor_data_sources.py --threshold 1 --verbose
```

### 场景4: 集成到 cron 任务

```cron
# 每10分钟检查一次
*/10 * * * * cd /path/to/project && python scripts/monitor_data_sources.py >> logs/monitor.log 2>&1
```

## 告警示例

当数据源连续失败3次时，会发送类似如下的告警：

```markdown
# 数据源健康告警

## 概要

- 检测到 **2** 个数据源异常
- 失败阈值: 3 次

## 异常详情

### 🔴 严重 eastmoney

- **连续失败**: 6 次
- **成功率**: 45.5% (22 次检查)
- **最后成功**: 2026-09-28T10:30:00
- **最后失败**: 2026-09-28T14:35:00
- **平均响应**: 2500ms
- **错误信息**: `HTTPError: 429 Too Many Requests - 请求过于频繁`

### ⚠️ 警告 tencent

- **连续失败**: 3 次
- **成功率**: 87.5% (8 次检查)
- **最后成功**: 2026-09-28T14:00:00
- **最后失败**: 2026-09-28T14:30:00
- **错误信息**: `ConnectionError: 连接超时`

## 建议操作

1. 检查网络连接和数据源服务状态
2. 验证API密钥和认证信息是否有效
3. 查看数据源提供方是否有服务公告
4. 考虑切换到备用数据源
5. 检查本地缓存和磁盘空间
```

## API 参考

### 记录健康事件

```python
from aqsp.data.source_health import (
    record_source_success,
    record_source_failure,
)

# 记录成功
record_source_success(
    requested_source="eastmoney",
    actual_source="eastmoney",
    response_time_ms=123.45,
)

# 记录失败
record_source_failure(
    requested_source="eastmoney",
    error_message="网络超时",
)
```

### 查询健康状态

```python
from aqsp.data.source_health import (
    DataSourceMonitor,
    read_source_health,
)

monitor = DataSourceMonitor()

# 获取单个源状态
status = monitor.get_source_status("eastmoney")
print(f"连续失败: {status.consecutive_failures}")
print(f"成功率: {status.success_rate:.1%}")

# 获取所有不健康的源
unhealthy = monitor.get_unhealthy_sources()
for status in unhealthy:
    print(f"{status.source_id}: {status.consecutive_failures} 次失败")

# 直接读取原始数据
health = read_source_health()
print(health["sources"])
```

### 优化数据源顺序

```python
from aqsp.data.source_health import prioritize_source_ids

# 根据健康历史排序
sources = ["eastmoney", "sina", "tencent"]
prioritized = prioritize_source_ids(sources)
# 返回: ["sina", "tencent", "eastmoney"]  # 健康的排在前面
```

## 测试

运行测试套件：

```bash
# 运行所有监控相关测试
pytest tests/test_source_monitor.py -v

# 运行特定测试
pytest tests/test_source_monitor.py::test_health_check_success -v

# 测试覆盖率
pytest tests/test_source_monitor.py --cov=aqsp.data.source_health
```

## 注意事项

1. **性能影响**
   - 健康检查使用轻量级测试，对数据源影响最小
   - 默认5分钟检查一次，可根据需要调整
   - 健康记录异步进行，不阻塞主流程

2. **存储空间**
   - 响应时间历史最多保留100条记录
   - 健康文件通常小于100KB
   - 建议定期归档历史报告

3. **并发安全**
   - 使用文件锁保证并发写入安全
   - 支持多进程同时记录健康事件
   - 守护进程和主程序可同时运行

4. **告警频率**
   - 同一告警每天只发送一次
   - 使用状态文件避免重复通知
   - 恢复后会清除告警状态

## 故障排查

### 问题: 监控服务无法启动

```bash
# 检查环境变量
env | grep AQSP_MONITOR

# 检查文件权限
ls -la data/

# 查看详细日志
python scripts/monitor_data_sources.py --verbose
```

### 问题: 健康检查总是失败

```bash
# 测试单个数据源
python -c "
from aqsp.data import build_data_source
source = build_data_source('eastmoney')
print(source.get_available_symbols()[:5])
"

# 检查网络连接
curl -I https://push2.eastmoney.com
```

### 问题: 告警未发送

```bash
# 检查通知配置
python -c "
from aqsp.config import load_runtime_config
config = load_runtime_config()
print(f'notify_mode: {config.notify_mode}')
"

# 查看告警状态文件
cat data/monitor_notify_state.json
```

## 扩展开发

### 添加自定义健康检查

```python
def check_custom_source() -> bool:
    """自定义健康检查逻辑"""
    # 实现你的检查逻辑
    return True

monitor = DataSourceMonitor()
result = monitor.check_source_health("custom_source", check_custom_source)
monitor.record_check_result(result)
```

### 集成到现有流程

```python
# 在数据获取前检查健康状态
from aqsp.data.source_health import DataSourceMonitor

monitor = DataSourceMonitor()

if monitor.should_trigger_failover("eastmoney"):
    print("主数据源不健康，建议使用备用源")
    # 切换逻辑...
```

## 相关文档

- [数据源架构设计](docs/architecture.md#数据源)
- [通知系统](docs/notification.md)
- [错误处理](docs/error_handling.md)

## 变更日志

### v1.0.0 (2026-09-28)

- ✨ 新增 `DataSourceMonitor` 主动健康检查
- ✨ 新增 `scripts/monitor_data_sources.py` 独立监控服务
- ✨ 集成 `MultiSource` 自动故障记录
- ✨ 支持告警通知和健康报告生成
- 📝 完整的测试覆盖和文档
