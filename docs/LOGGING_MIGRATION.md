# 日志系统迁移指南

本文档说明如何将 AQSP 项目的日志系统从标准库 `logging` 迁移到结构化日志 `structlog`。

## 目录

1. [为什么迁移](#为什么迁移)
2. [核心概念](#核心概念)
3. [快速开始](#快速开始)
4. [迁移步骤](#迁移步骤)
5. [最佳实践](#最佳实践)
6. [常见问题](#常见问题)

## 为什么迁移

### 传统日志的问题

```python
# 传统方式：非结构化文本
logger.info(f"Fetching data for {symbol} from {source}")
# 输出：2026-09-28 10:30:00 INFO Fetching data for 000001 from akshare
```

**缺点：**
- 难以解析和查询
- 无法按字段过滤或聚合
- 格式不一致，容易出错

### 结构化日志的优势

```python
# 结构化方式：JSON Lines 格式
logger.info("data_fetch_started", symbol="000001", source="akshare")
# 输出：{"timestamp":"2026-09-28T10:30:00+08:00","level":"info","event":"data_fetch_started","symbol":"000001","source":"akshare","module":"aqsp.data.akshare","function":"fetch_daily","line":85}
```

**优点：**
- 机器可读，易于解析
- 支持日志聚合和分析工具（ELK、Loki 等）
- 统一格式，便于查询和统计
- 上下文信息更丰富

## 核心概念

### 1. 事件驱动的日志

不要写叙述性文本，用事件名 + 上下文字段：

```python
# ❌ 错误方式
logger.info(f"Successfully fetched {len(data)} rows for {symbol}")

# ✅ 正确方式
logger.info("data_fetched", symbol=symbol, row_count=len(data))
```

### 2. 字段命名规范

- 使用 `snake_case` 命名
- 事件名描述动作：`data_fetched`、`request_failed`、`cache_hit`
- 上下文字段使用具体名称：`symbol`、`source`、`duration_ms`

### 3. 日志级别

- **DEBUG**: 详细的诊断信息（仅开发环境）
- **INFO**: 正常业务事件（数据获取、操作完成）
- **WARNING**: 警告信息（降级、重试）
- **ERROR**: 错误但不影响整体运行（单个股票失败）
- **CRITICAL**: 严重错误，需要立即处理（服务崩溃）

## 快速开始

### 1. 配置日志系统

在应用启动时调用一次：

```python
# src/aqsp/cli.py 或 backend/app.py
from aqsp.core.logging import configure_logging

def main():
    # 配置日志（启动时调用一次）
    configure_logging()  # 默认从环境变量读取配置
    
    # 或者显式指定
    configure_logging(level="INFO", mode="prod")
    
    # 你的应用逻辑...
```

### 2. 获取 logger 并使用

```python
import structlog

logger = structlog.get_logger(__name__)

def fetch_data(symbol: str):
    logger.info("fetch_started", symbol=symbol)
    
    try:
        data = do_fetch(symbol)
        logger.info("fetch_completed", symbol=symbol, rows=len(data))
        return data
    except Exception as e:
        logger.error("fetch_failed", symbol=symbol, exc_info=True)
        raise
```

### 3. 环境变量配置

```bash
# 开发环境：彩色输出
export AQSP_LOG_MODE=dev
export AQSP_LOG_LEVEL=DEBUG

# 生产环境：JSON Lines 格式
export AQSP_LOG_MODE=prod
export AQSP_LOG_LEVEL=INFO
```

## 迁移步骤

### 步骤 1: 导入 structlog

将标准库 `logging` 替换为 `structlog`：

```python
# 旧代码
import logging
logger = logging.getLogger(__name__)

# 新代码
import structlog
logger = structlog.get_logger(__name__)
```

### 步骤 2: 转换日志调用

#### 基本日志

```python
# 旧代码
logger.info(f"Fetching data for {symbol} from {source}")

# 新代码
logger.info("data_fetch_started", symbol=symbol, source=source)
```

#### 带变量的日志

```python
# 旧代码
logger.info(f"Fetched {len(data)} rows in {elapsed:.2f}s")

# 新代码
logger.info("data_fetched", row_count=len(data), duration_sec=round(elapsed, 2))
```

#### 错误日志（带异常）

```python
# 旧代码
try:
    result = risky_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise

# 新代码
try:
    result = risky_operation()
except Exception as e:
    logger.error("operation_failed", exc_info=True)  # 自动捕获异常堆栈
    raise
```

#### 警告日志

```python
# 旧代码
logger.warning(f"Cache miss for {symbol}, falling back to network")

# 新代码
logger.warning("cache_miss", symbol=symbol, fallback="network")
```

### 步骤 3: 重构复杂日志

对于需要多次记录的场景，使用绑定上下文：

```python
# 旧代码
def process_symbols(symbols):
    for symbol in symbols:
        logger.info(f"Processing {symbol}")
        data = fetch(symbol)
        logger.info(f"Fetched {len(data)} rows for {symbol}")
        result = transform(data)
        logger.info(f"Transformed {symbol}")

# 新代码
def process_symbols(symbols):
    for symbol in symbols:
        # 绑定上下文，后续日志自动带上 symbol
        ctx_logger = logger.bind(symbol=symbol)
        
        ctx_logger.info("processing_started")
        data = fetch(symbol)
        ctx_logger.info("data_fetched", row_count=len(data))
        result = transform(data)
        ctx_logger.info("transform_completed")
```

### 步骤 4: 测试验证

1. 设置开发模式查看彩色输出：
   ```bash
   export AQSP_LOG_MODE=dev
   python -m aqsp.cli --help
   ```

2. 设置生产模式验证 JSON 格式：
   ```bash
   export AQSP_LOG_MODE=prod
   python -m aqsp.cli fetch --source akshare 000001
   ```

3. 确保日志包含所有必要字段：
   - `timestamp`
   - `level`
   - `event`
   - `module`
   - `function`
   - `line`
   - 业务上下文字段

## 最佳实践

### 1. 事件命名约定

使用动词 + 名词的格式，描述清楚发生了什么：

```python
# 好的事件名
logger.info("data_fetched", ...)          # 数据获取完成
logger.info("cache_hit", ...)             # 缓存命中
logger.info("request_started", ...)       # 请求开始
logger.info("fallback_triggered", ...)    # 触发降级

# 不好的事件名
logger.info("ok", ...)                    # 太模糊
logger.info("got_data", ...)              # 不够正式
logger.info("process", ...)               # 没说清楚什么过程
```

### 2. 上下文字段选择

记录关键业务信息，避免冗余：

```python
# ✅ 好的上下文
logger.info("data_fetched", 
    symbol="000001",
    source="akshare",
    row_count=100,
    duration_ms=250,
    cache_hit=False
)

# ❌ 冗余上下文
logger.info("data_fetched",
    symbol="000001",
    symbol_name="平安银行",  # 可以从 symbol 查到，不需要重复
    timestamp=now(),        # structlog 自动添加
    module=__name__,        # structlog 自动添加
)
```

### 3. 性能敏感路径

对于高频调用的代码，只在必要时记录日志：

```python
def process_ticks(ticks):
    # ❌ 不要在循环内记录每条数据
    for tick in ticks:
        logger.debug("tick_processed", price=tick.price)  # 太多了
    
    # ✅ 记录汇总信息
    logger.info("ticks_processed", count=len(ticks), duration_ms=elapsed)
```

### 4. 敏感信息保护

不要记录密码、token、密钥等敏感信息：

```python
# ❌ 危险
logger.info("api_called", api_key=api_key)

# ✅ 安全
logger.info("api_called", api_key_prefix=api_key[:8] + "...")
```

### 5. 异常处理

始终使用 `exc_info=True` 记录完整堆栈：

```python
try:
    dangerous_operation()
except Exception as e:
    # ✅ 自动捕获堆栈和异常类型
    logger.error("operation_failed", operation="fetch", exc_info=True)
    raise
```

## 常见问题

### Q1: 如何兼容现有的 logging 调用？

**A**: 使用 `get_stdlib_logger` 获取标准库 logger，它会被 structlog 拦截：

```python
from aqsp.core.logging import get_stdlib_logger

# 返回标准库 Logger，但会被 structlog 处理
logger = get_stdlib_logger(__name__)

# 现有代码不用改，但输出会是结构化的
logger.info("Old style message")
```

**注意**: 这只是过渡方案，建议尽快迁移到 structlog 原生接口。

### Q2: 如何在生产环境查看日志？

**A**: JSON Lines 格式可以用 `jq` 等工具查看：

```bash
# 查看所有错误日志
cat aqsp.log | jq 'select(.level == "error")'

# 查看某个 symbol 的日志
cat aqsp.log | jq 'select(.symbol == "000001")'

# 统计各级别日志数量
cat aqsp.log | jq -r .level | sort | uniq -c
```

或者使用分析脚本：

```bash
python scripts/analyze_logs.py aqsp.log
```

### Q3: 日志太多怎么办？

**A**: 调整日志级别：

```bash
# 生产环境只记录 INFO 及以上
export AQSP_LOG_LEVEL=INFO

# 调试时临时开启 DEBUG
export AQSP_LOG_LEVEL=DEBUG
```

### Q4: 如何轮转日志文件？

**A**: 使用配置脚本：

```bash
# 配置日志轮转（自动压缩、保留 30 天）
./scripts/setup_log_rotation.sh
```

### Q5: 如何过滤第三方库的日志？

**A**: 在 `configure_logging()` 之后调整第三方库日志级别：

```python
import logging

configure_logging()

# 静默噪音较大的库
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
```

## 已迁移的模块

以下模块已完成迁移，可作为参考：

- ✅ `src/aqsp/cli.py` - 主入口
- ✅ `src/aqsp/data/akshare_source.py` - akshare 数据源
- ✅ `src/aqsp/data/baostock_source.py` - baostock 数据源
- ✅ `backend/app.py` - FastAPI 后端

## 待迁移的模块

按优先级排序：

1. **高优先级**（频繁调用、诊断价值高）
   - [ ] `src/aqsp/data/source_factory.py`
   - [ ] `src/aqsp/data/cache.py`
   - [ ] `src/aqsp/ledger/base.py`
   - [ ] `src/aqsp/filters_lethal/pipeline.py`

2. **中优先级**（偶尔调用、有一定诊断价值）
   - [ ] `src/aqsp/portfolio/manager.py`
   - [ ] `src/aqsp/strategy.py`
   - [ ] `src/aqsp/paper.py`

3. **低优先级**（很少调用或已稳定）
   - [ ] `src/aqsp/risk/circuit_breaker.py`
   - [ ] `src/aqsp/regime/hmm_detector.py`

## 贡献指南

迁移新模块时：

1. 遵循本文档的命名和格式规范
2. 更新「已迁移的模块」列表
3. 添加单元测试验证日志输出
4. 提交 PR 时在描述中说明迁移了哪些模块

## 参考资源

- [structlog 官方文档](https://www.structlog.org/)
- [日志最佳实践](https://www.structlog.org/en/stable/standard-library.html)
- [JSON Lines 格式规范](https://jsonlines.org/)
