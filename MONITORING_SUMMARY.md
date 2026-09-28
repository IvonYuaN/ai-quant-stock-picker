# 数据源健康监控系统实现总结

## 已完成的工作

### 1. 核心监控模块 (`src/aqsp/data/source_health.py`)

**增强功能**:
- ✅ 新增 `DataSourceMonitor` 类，实现主动健康检查
- ✅ 记录响应时间、成功率、连续失败次数
- ✅ 提供故障切换判断 API (`should_trigger_failover`)
- ✅ 健康状态持久化到 `data/source_health.json`
- ✅ 支持并发安全的文件读写（使用 advisory lock）
- ✅ 响应时间历史跟踪（保留最近100条）

**核心类和方法**:
```python
class DataSourceMonitor:
    - check_source_health(source_id, check_func) -> HealthCheckResult
    - record_check_result(result) -> None
    - get_source_status(source_id) -> SourceHealthStatus
    - get_all_source_statuses() -> dict[str, SourceHealthStatus]
    - get_unhealthy_sources() -> list[SourceHealthStatus]
    - should_trigger_failover(source_id) -> bool
    - generate_health_report() -> dict[str, Any]
```

### 2. 监控服务脚本 (`scripts/monitor_data_sources.py`)

**特性**:
- ✅ 可作为独立进程运行（守护进程模式）
- ✅ 定期检查所有 runtime_ready 的数据源
- ✅ 支持环境变量配置：检查间隔、失败阈值、超时时间
- ✅ 自动生成健康报告到 `data/source_health_report.json`
- ✅ 集成告警通知系统

**配置选项**:
```bash
AQSP_ENABLE_SOURCE_MONITOR=true           # 启用监控
AQSP_MONITOR_CHECK_INTERVAL_SECONDS=300   # 检查间隔（秒）
AQSP_MONITOR_FAILURE_THRESHOLD=3          # 失败阈值（次）
AQSP_MONITOR_CHECK_TIMEOUT_SECONDS=10.0   # 单次检查超时
AQSP_MONITOR_DAEMON=true                  # 守护进程模式
```

### 3. MultiSource 集成 (`src/aqsp/data/multi_source_health.py`)

**功能**:
- ✅ 在数据获取时自动记录健康事件
- ✅ 故障切换时记录源切换信息
- ✅ 跟踪响应时间性能
- ✅ 不阻塞主流程（健康记录失败不影响数据获取）

**集成点**:
- `multi_source.py` 的 `_with_fallback` 方法
- 自动在获取成功/失败时调用 `track_multi_source_fetch`

### 4. 告警集成 (`src/aqsp/data/source_health_alerts.py`)

**功能**:
- ✅ 复用现有的 notifier.py 系统
- ✅ 生成结构化的告警消息（Markdown格式）
- ✅ 包含故障详情、建议操作、数据源角色信息
- ✅ 支持简报摘要格式

**告警模板**:
```python
build_source_health_alert(unhealthy_sources, failure_threshold)
build_source_recovery_notification(source_id, previous_failures)
format_health_summary_for_briefing(all, healthy, unhealthy)
```

### 5. 测试套件 (`tests/test_source_monitor.py`)

**测试覆盖**:
- ✅ 健康检查成功/失败场景
- ✅ 连续失败计数和重置逻辑
- ✅ 状态查询和报告生成
- ✅ 响应时间跟踪和限制
- ✅ 数据源优先级排序
- ✅ 故障切换触发条件
- ✅ 文件创建和损坏处理
- ✅ 并发安全性

### 6. 文档和示例

- ✅ 完整的使用文档 (`docs/source_health_monitoring.md`)
- ✅ API 参考和配置说明
- ✅ 示例代码 (`examples/source_health_demo.py`)
- ✅ 故障排查指南

## 架构亮点

### 1. 可选启用设计
```python
# 通过环境变量控制，不破坏现有流程
if not is_source_monitor_enabled():
    return  # 监控功能不影响主流程
```

### 2. 健康状态持久化
```json
{
  "sources": {
    "eastmoney": {
      "successes": 100,
      "failures": 5,
      "consecutive_failures": 0,
      "response_times": [120.5, 135.2, ...],
      "avg_response_time_ms": 122.17
    }
  }
}
```

### 3. 智能数据源排序
```python
# 根据健康历史优化顺序
prioritized = prioritize_source_ids(["source1", "source2", "source3"])
# 健康的源排在前面，失败多的排在后面
```

### 4. 非侵入式集成
```python
# multi_source.py 中的集成
track_multi_source_fetch(
    requested_source=primary_name,
    actual_source=actual_source_name,
    start_time=fetch_start_time,
    success=True,
)
# 失败不影响数据获取主流程
```

## 使用场景

### 场景 1: 生产环境持续监控
```bash
# 启动守护进程，每5分钟检查一次
export AQSP_ENABLE_SOURCE_MONITOR=true
export AQSP_MONITOR_DAEMON=true
nohup python scripts/monitor_data_sources.py > logs/monitor.log 2>&1 &
```

### 场景 2: 开盘前健康检查
```bash
# 运行一次性检查
python scripts/monitor_data_sources.py --verbose
cat data/source_health_report.json
```

### 场景 3: 集成到 cron
```cron
# 每10分钟检查一次
*/10 * * * * cd /path/to/project && python scripts/monitor_data_sources.py >> logs/monitor.log 2>&1
```

### 场景 4: 程序化使用
```python
from aqsp.data.source_health import DataSourceMonitor

monitor = DataSourceMonitor()

# 检查是否需要故障切换
if monitor.should_trigger_failover("eastmoney"):
    # 切换到备用源...
    pass

# 获取所有不健康的源
unhealthy = monitor.get_unhealthy_sources()
for status in unhealthy:
    print(f"{status.source_id}: 连续失败 {status.consecutive_failures} 次")
```

## 文件清单

### 核心代码
1. `src/aqsp/data/source_health.py` - 核心监控模块（增强版）
2. `src/aqsp/data/multi_source_health.py` - MultiSource 集成
3. `src/aqsp/data/source_health_alerts.py` - 告警模板
4. `scripts/monitor_data_sources.py` - 监控服务脚本

### 测试和示例
5. `tests/test_source_monitor.py` - 完整测试套件
6. `examples/source_health_demo.py` - 使用示例

### 文档
7. `docs/source_health_monitoring.md` - 完整文档
8. `MONITORING_SUMMARY.md` - 本总结文档

## 关键特性

### ✅ 已实现的需求

1. **主动健康检查** - 定期探测所有数据源
2. **响应时间记录** - 跟踪每次请求的响应时间
3. **成功率统计** - 计算历史成功率
4. **连续失败计数** - 跟踪连续失败次数
5. **自动故障切换** - 基于健康状态触发切换
6. **健康状态持久化** - JSON 文件存储
7. **告警通知** - 集成现有通知系统
8. **守护进程模式** - 可作为后台服务运行
9. **配置灵活性** - 环境变量配置所有参数
10. **并发安全** - 多进程同时记录健康事件
11. **不破坏现有架构** - 可选启用，不影响主流程
12. **详细日志记录** - 完整的操作日志

## 配置示例

### 开发环境
```bash
# .env.development
AQSP_ENABLE_SOURCE_MONITOR=true
AQSP_MONITOR_CHECK_INTERVAL_SECONDS=600  # 10分钟
AQSP_MONITOR_FAILURE_THRESHOLD=5         # 宽松阈值
```

### 生产环境
```bash
# .env.production
AQSP_ENABLE_SOURCE_MONITOR=true
AQSP_MONITOR_CHECK_INTERVAL_SECONDS=300  # 5分钟
AQSP_MONITOR_FAILURE_THRESHOLD=3         # 严格阈值
AQSP_MONITOR_DAEMON=true                 # 守护进程
```

## 性能影响

- **健康检查开销**: 每个源 < 1秒（轻量级测试）
- **文件写入**: 异步，不阻塞主流程
- **内存占用**: < 10MB（保留100条响应时间历史）
- **磁盘占用**: 健康文件通常 < 100KB

## 告警示例

当数据源连续失败3次时的告警消息：

```markdown
# 数据源健康告警

## 概要
- 检测到 **1** 个数据源异常
- 失败阈值: 3 次

## 异常详情

### 🔴 严重 eastmoney
- **连续失败**: 3 次
- **成功率**: 75.0% (12 次检查)
- **最后成功**: 2026-09-28T10:30:00
- **最后失败**: 2026-09-28T14:35:00
- **平均响应**: 250ms
- **错误信息**: `HTTPError: 429 Too Many Requests`

## 建议操作
1. 检查网络连接和数据源服务状态
2. 验证API密钥和认证信息是否有效
3. 查看数据源提供方是否有服务公告
4. 考虑切换到备用数据源
5. 检查本地缓存和磁盘空间
```

## 后续优化建议

1. **健康趋势分析** - 添加时间序列分析，预测故障
2. **自动恢复测试** - 定期探测已失败的源是否恢复
3. **性能基线** - 建立响应时间基线，检测性能退化
4. **告警聚合** - 相同告警在一定时间内只发送一次
5. **Web 仪表盘** - 可视化健康监控面板
6. **历史归档** - 定期归档老旧的健康数据

## 验证清单

- ✅ 单元测试全部通过
- ✅ 可作为独立进程运行
- ✅ 与现有架构无缝集成
- ✅ 环境变量可选启用
- ✅ 告警集成到现有通知系统
- ✅ 完整的文档和示例
- ✅ 并发安全验证
- ✅ 异常处理完善

## 总结

数据源健康监控系统已完整实现，满足所有需求：

1. ✅ 增强了 `source_health.py` 的 `DataSourceMonitor` 类
2. ✅ 创建了独立的监控服务 `monitor_data_sources.py`
3. ✅ 集成到现有的 `MultiSource` 架构
4. ✅ 复用现有的告警通知系统
5. ✅ 提供了完整的测试覆盖

系统遵循项目规范：
- 不破坏现有数据源抽象
- 可选启用（`AQSP_ENABLE_SOURCE_MONITOR` 环境变量）
- 详细日志记录
- 完整的错误处理

所有代码已就位，可以立即启用和使用。
