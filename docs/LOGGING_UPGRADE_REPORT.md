# 结构化日志升级完成报告

## 概述

已成功将 AQSP 项目的日志系统升级为结构化日志（structlog），支持 JSON Lines 格式输出、日志轮转和自动化分析。

## 已完成的工作

### 1. 添加依赖 ✅

**文件**: `pyproject.toml`

已添加 structlog 依赖：
```toml
"structlog>=24.0,<25.0",
```

### 2. 创建日志配置模块 ✅

**文件**: `src/aqsp/core/logging.py`

**功能**:
- 支持开发模式（彩色输出）和生产模式（JSON Lines）
- 环境变量配置：`AQSP_LOG_LEVEL`、`AQSP_LOG_MODE`
- 自动添加上下文字段：timestamp, level, event, module, function, line
- 向后兼容标准库 logging

**API**:
```python
from aqsp.core.logging import configure_logging, get_logger

# 配置日志（应用启动时调用一次）
configure_logging()

# 获取 logger
logger = get_logger(__name__)

# 使用结构化日志
logger.info("data_fetched", symbol="000001", row_count=100)
```

### 3. 创建日志轮转脚本 ✅

**文件**: `scripts/setup_log_rotation.sh`

**功能**:
- 自动检测操作系统（macOS/Linux）
- macOS: 配置 newsyslog
- Linux: 配置 logrotate
- 按日轮转，保留 30 天
- 自动压缩旧日志（.gz）

**使用**:
```bash
./scripts/setup_log_rotation.sh
# 或指定日志目录
./scripts/setup_log_rotation.sh /var/log/aqsp
```

### 4. 创建迁移指南 ✅

**文件**: `docs/LOGGING_MIGRATION.md`

**内容**:
- 为什么迁移（结构化日志的优势）
- 核心概念（事件驱动、字段命名规范）
- 快速开始（3 步配置）
- 迁移步骤（逐步转换现有代码）
- 最佳实践（事件命名、上下文选择、性能优化）
- 常见问题解答
- 已迁移和待迁移模块清单

### 5. 迁移关键模块 ✅

#### 5.1 主入口 - `src/aqsp/cli.py`

**改动**:
```python
# 添加导入
import structlog
from aqsp.core.logging import configure_logging

# 在 main() 函数开头配置日志
def main(argv: list[str] | None = None) -> int:
    configure_logging()
    # ... 原有逻辑
```

#### 5.2 数据源 - `src/aqsp/data/akshare_source.py`

**改动**:
```python
# 替换导入
import structlog

# 替换 logger
_logger = structlog.get_logger(__name__)

# 原有日志调用保持兼容（后续可逐步改为结构化调用）
```

#### 5.3 数据源 - `src/aqsp/data/baostock_source.py`

**改动**:
```python
import structlog
_logger = structlog.get_logger(__name__)
```

#### 5.4 后端 API - `backend/app.py`

**改动**:
```python
import structlog

# 启动时配置日志（带降级处理）
try:
    from aqsp.core.logging import configure_logging
    configure_logging()
    logger = structlog.get_logger(__name__)
    logger.info("backend_started", version="0.1.3")
except ImportError:
    # aqsp 包未安装时回退到标准日志
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
```

### 6. 创建日志分析脚本 ✅

**文件**: `scripts/analyze_logs.py`

**功能**:
- 解析 JSON Lines 日志
- 按级别统计
- 提取错误和警告详情
- 时间范围过滤
- 字段值统计
- 生成简报（控制台或 JSON）

**使用示例**:
```bash
# 分析单个日志
python scripts/analyze_logs.py /var/log/aqsp/aqsp.log

# 只看错误
python scripts/analyze_logs.py aqsp.log --level error

# 指定时间范围
python scripts/analyze_logs.py aqsp.log --since "2026-09-27" --until "2026-09-28"

# 导出为 JSON
python scripts/analyze_logs.py aqsp.log --output report.json
```

### 7. 添加测试 ✅

**文件**: `tests/test_logging.py`

**覆盖范围**:
- 日志配置测试（默认配置、指定级别、开发/生产模式）
- 从环境变量读取配置
- 结构化日志功能（基本日志、上下文绑定、异常记录）
- 向后兼容性测试
- 日志输出格式验证

## 项目规范遵守情况

### ✅ 不破坏现有日志功能
- 已迁移的模块保持标准库 logging 接口兼容
- backend/app.py 添加了降级处理，aqsp 包未安装时回退到标准日志
- 未迁移的模块继续使用原有日志系统

### ✅ 提供平滑迁移路径
- 创建详细的迁移指南 `docs/LOGGING_MIGRATION.md`
- 提供示例代码和最佳实践
- 支持渐进式迁移：模块可以逐个迁移
- `get_stdlib_logger()` 提供过渡方案

### ✅ 添加测试
- 创建 `tests/test_logging.py` 覆盖核心功能
- 测试配置、日志记录、上下文绑定、异常处理
- 使用 pytest fixture 保证测试隔离

## 使用方法

### 开发环境

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置开发模式（彩色输出）
export AQSP_LOG_MODE=dev
export AQSP_LOG_LEVEL=DEBUG

# 3. 运行应用
python -m aqsp.cli screen --source akshare --symbols 000001
```

### 生产环境

```bash
# 1. 配置生产模式（JSON Lines）
export AQSP_LOG_MODE=prod
export AQSP_LOG_LEVEL=INFO
export AQSP_LOG_DIR=/var/log/aqsp

# 2. 创建日志目录
mkdir -p /var/log/aqsp

# 3. 配置日志轮转
./scripts/setup_log_rotation.sh /var/log/aqsp

# 4. 运行应用（日志输出到文件）
python -m aqsp.cli run > /var/log/aqsp/aqsp.log 2>&1
```

### 日志分析

```bash
# 查看错误统计
python scripts/analyze_logs.py /var/log/aqsp/aqsp.log

# 使用 jq 查询 JSON 日志
cat /var/log/aqsp/aqsp.log | jq 'select(.level == "error")'
cat /var/log/aqsp/aqsp.log | jq 'select(.symbol == "000001")'
```

## 日志格式示例

### 开发模式（彩色输出）
```
2026-09-28T10:30:00+08:00 [info     ] data_fetch_started         symbol=000001 source=akshare module=aqsp.data.akshare function=fetch_daily line=85
2026-09-28T10:30:01+08:00 [info     ] data_fetched               symbol=000001 row_count=100 duration_ms=250 module=aqsp.data.akshare function=fetch_daily line=125
```

### 生产模式（JSON Lines）
```json
{"timestamp":"2026-09-28T10:30:00+08:00","level":"info","event":"data_fetch_started","symbol":"000001","source":"akshare","module":"aqsp.data.akshare","function":"fetch_daily","line":85}
{"timestamp":"2026-09-28T10:30:01+08:00","level":"info","event":"data_fetched","symbol":"000001","row_count":100,"duration_ms":250,"module":"aqsp.data.akshare","function":"fetch_daily","line":125}
```

## 后续工作建议

### 优先级 1 - 高频模块迁移
- [ ] `src/aqsp/data/source_factory.py` - 数据源工厂
- [ ] `src/aqsp/data/cache.py` - 缓存层
- [ ] `src/aqsp/ledger/base.py` - 台账基础

### 优先级 2 - 添加结构化日志调用
目前已迁移的模块只是替换了 logger 获取方式，但日志调用仍是旧格式。建议逐步改为结构化调用：

**当前**:
```python
_logger.info(f"Fetched {len(data)} rows for {symbol}")
```

**改进**:
```python
_logger.info("data_fetched", symbol=symbol, row_count=len(data))
```

### 优先级 3 - 日志聚合
考虑接入日志聚合工具：
- **ELK Stack** (Elasticsearch + Logstash + Kibana)
- **Grafana Loki** (轻量级日志聚合)
- **云服务** (阿里云 SLS、AWS CloudWatch)

## 测试验证

```bash
# 运行日志模块测试
pytest tests/test_logging.py -v

# 测试日志配置
AQSP_LOG_MODE=dev python -c "from aqsp.core.logging import configure_logging; configure_logging(); import structlog; logger = structlog.get_logger('test'); logger.info('test', field='value')"

# 测试日志分析脚本
echo '{"timestamp":"2026-09-28T10:30:00+08:00","level":"info","event":"test","field":"value"}' > /tmp/test.log
python scripts/analyze_logs.py /tmp/test.log
```

## 文件清单

### 新增文件
- `src/aqsp/core/logging.py` - 日志配置模块
- `scripts/setup_log_rotation.sh` - 日志轮转配置脚本
- `scripts/analyze_logs.py` - 日志分析脚本
- `docs/LOGGING_MIGRATION.md` - 迁移指南
- `tests/test_logging.py` - 日志模块测试

### 修改文件
- `pyproject.toml` - 添加 structlog 依赖
- `src/aqsp/cli.py` - 添加日志配置调用
- `src/aqsp/data/akshare_source.py` - 迁移到 structlog
- `src/aqsp/data/baostock_source.py` - 迁移到 structlog
- `backend/app.py` - 添加日志配置（带降级处理）

## 总结

本次升级成功将 AQSP 项目的日志系统升级为现代化的结构化日志方案，具备以下特性：

1. **结构化输出**: JSON Lines 格式，易于解析和查询
2. **环境适配**: 开发模式彩色输出，生产模式机器可读
3. **自动轮转**: 按日归档，自动压缩，保留 30 天
4. **分析工具**: 提供日志分析脚本，快速诊断问题
5. **平滑迁移**: 向后兼容，不破坏现有功能
6. **完善文档**: 详细的迁移指南和最佳实践
7. **测试覆盖**: 核心功能有单元测试保障

项目现在具备了生产级的日志基础设施，为后续的监控、告警和问题诊断提供了坚实基础。
