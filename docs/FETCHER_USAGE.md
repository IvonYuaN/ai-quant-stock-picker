# MultiSourceFetcher 使用指南

集成 Tushare（主数据源）和 Akshare（备用数据源）的统一数据获取器。

## 快速开始

### 基础用法

```python
from datetime import date
from aqsp.data.fetcher import MultiSourceFetcher
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.cache import DataCache

# 创建数据源
primary = AkshareSource()    # 实际应用中使用 Tushare
fallback = AkshareSource()   # Akshare 作为备用

# 创建 fetcher
fetcher = MultiSourceFetcher(primary, fallback)

# 获取数据
symbols = ["000001", "000002"]
start = date(2024, 1, 1)
end = date(2024, 6, 1)

data = fetcher.fetch_daily_data(symbols, start, end, adjust="qfq")

# 检查数据源
for symbol in symbols:
    source = fetcher.get_last_source_used(symbol)
    print(f"{symbol}: {source}")
```

### 使用缓存

```python
from aqsp.data.cache import DataCache

# 使用缓存提高性能
cache = DataCache(db_path="/path/to/cache.db")
fetcher = MultiSourceFetcher(primary, fallback, cache=cache)

# 首次调用会缓存数据
data = fetcher.fetch_daily_data(symbols, start, end)

# 后续调用会使用缓存（如果命中）
data = fetcher.fetch_daily_data(symbols, start, end)
```

## 数据格式

获取的数据标准化为以下列：

| 列名 | 说明 | 数据类型 |
|------|------|---------|
| date | 交易日期 YYYY-MM-DD | string |
| symbol | 股票代码 | string |
| name | 股票名称 | string |
| open | 开盘价 | float |
| high | 最高价 | float |
| low | 最低价 | float |
| close | 收盘价 | float |
| volume | 成交量 | float |
| amount | 成交额 | float |
| suspended | 是否停牌 | bool |
| limit_up | 涨停价 | float |
| limit_down | 跌停价 | float |

## Fallback 机制

### 执行流程

1. **第一阶段**：优先尝试从主数据源（Tushare）获取
   - 如果成功，返回数据并记录日志
   - 如果失败，进入第二阶段

2. **第二阶段**：未获取到的标的从备用源（Akshare）获取
   - 记录 WARNING 级日志，说明进行了 fallback
   - 如果仍失败，抛出 DataError

3. **错误处理**：所有源都失败时
   - 抛出 DataError，包含所有错误信息
   - 用户应捕获异常进行处理

### 日志输出示例

```
INFO: data_fetch_success - symbol=000001, source=tushare, rows=120
WARNING: fallback_to_secondary_source - symbol=000002, primary_source=tushare, fallback_source=akshare, rows=120
ERROR: all_sources_failed - primary_source=tushare, fallback_source=akshare, symbols=['000003']
```

## 源选择策略

### 何时使用主源（Tushare）

- 需要权限配置（API Token）
- 提供最准确的历史数据和复权因子
- 支持更多数据维度（财务数据、融资融券等）

### 何时使用备用源（Akshare）

- 主源不可用或限频
- 需要免配置快速获取
- 对数据时效性要求不极端

## 性能优化

### 1. 使用缓存

```python
# 避免重复调用网络接口
cache = DataCache(db_path="~/.aqsp/data.db")
fetcher = MultiSourceFetcher(primary, fallback, cache=cache)
```

### 2. 批量获取

```python
# 一次性获取多个标的，减少往返次数
symbols = ["000001", "000002", "000003", ...]
data = fetcher.fetch_daily_data(symbols, start, end)
```

### 3. 时间范围优化

```python
# 避免获取不必要的历史数据
from datetime import timedelta
end = date.today()
start = end - timedelta(days=250)  # 约1年
data = fetcher.fetch_daily_data(symbols, start, end)
```

## 错误处理

```python
from aqsp.core.errors import DataError

try:
    data = fetcher.fetch_daily_data(symbols, start, end)
except DataError as e:
    print(f"数据获取失败: {e}")
    # 实现重试逻辑或使用备份数据
    
    # 获取失败的标的列表
    sources = fetcher.get_all_last_sources()
    failed_symbols = [s for s in symbols if s not in sources]
    print(f"失败的标的: {failed_symbols}")
```

## 与现有系统集成

### 替换原有的 fetch_akshare

```python
# 旧代码
from aqsp.data import fetch_akshare
data = fetch_akshare(symbols, days=260)

# 新代码（使用 MultiSourceFetcher）
from aqsp.data.fetcher import create_default_fetcher
fetcher = create_default_fetcher()
end = date.today()
start = end - timedelta(days=500)
data = fetcher.fetch_daily_data(symbols, start, end)
for symbol in data:
    data[symbol] = data[symbol].tail(260).reset_index(drop=True)
```

### 在 MultiSource 中使用

```python
from aqsp.data import MultiSource, SourceFactory
from aqsp.data.fetcher import create_default_fetcher

# MultiSourceFetcher 返回的数据可用于 MultiSource 验证
fetcher = create_default_fetcher()
result = fetcher.fetch_daily_data(symbols, start, end)

# 验证数据一致性（如需要）
# ...
```

## 测试

运行测试套件：

```bash
pytest tests/test_multi_source_fetcher.py -v
```

测试覆盖：

- ✓ 主源成功获取
- ✓ Fallback 到备用源
- ✓ 两个源都失败时的错误处理
- ✓ 混合源（部分主源，部分备用源）
- ✓ 数据格式标准化
- ✓ 中文列名转换
- ✓ 日志记录
- ✓ 缓存集成

## 常见问题

### Q: 为什么有时数据来自不同源？
A: 这是正常的 fallback 行为。如果主源不可用或返回空数据，系统会自动使用备用源。

### Q: 如何禁用 fallback？
A: 修改 `fetch_daily_data` 中的逻辑，或使用单一源初始化 fetcher：
```python
fetcher = MultiSourceFetcher(primary, primary)  # 主源作为主和备用
```

### Q: 缓存何时更新？
A: 每次成功获取数据时自动更新缓存，支持增量更新。

### Q: 如何处理不同源的数据差异？
A: 使用 `MultiSource` 类中的 `_validate_consistency` 方法进行数据验证。

## 相关文件

- `src/aqsp/data/fetcher.py` - MultiSourceFetcher 实现
- `src/aqsp/data/source.py` - DataSource 基类和数据标准
- `src/aqsp/data/cache.py` - 数据缓存层
- `tests/test_multi_source_fetcher.py` - 单元测试

## 参考

- [Tushare 文档](https://tushare.pro)
- [Akshare 文档](https://akshare.akfamily.xyz)
- [数据架构设计](docs/architecture.md)
