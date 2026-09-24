# Codex深度问题修复清单

**基于深度审查发现的12个隐藏问题**

---

## P0 - 立即修复（今天）

### 任务1: 修复SQLite连接超时和资源泄漏 ⚠️⚠️⚠️

**严重程度**: 最高（导致宝塔面板任务卡死）

#### 1.1 修复 monitor/checker.py
**位置**: `src/aqsp/monitor/checker.py:120-129`

**修复**:
```python
# Before
try:
    import sqlite3
    conn = sqlite3.connect(str(cache_path))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(date) as latest_date 
        FROM ohlcv 
        WHERE symbol != '000300'
    """)
    row = cursor.fetchone()
    conn.close()

# After
try:
    import sqlite3
    conn = sqlite3.connect(str(cache_path), timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MAX(date) as latest_date 
            FROM ohlcv 
            WHERE symbol != '000300'
        """)
        row = cursor.fetchone()
    finally:
        conn.close()
```

#### 1.2 全局修复SQLite超时
**位置**: 约15处，包括：
- `src/aqsp/data/cache.py:49` 及其他位置
- `src/aqsp/data/sqlite_db_source.py:36, 76, 177`
- `src/aqsp/data/tdx_vipdoc_source.py:192`

**修复模式**:
```python
# 搜索所有
sqlite3.connect(

# 统一改为
sqlite3.connect(path, timeout=30.0, check_same_thread=False)
```

**验证**:
```bash
# 修复后验证
grep -rn "sqlite3.connect" src/ | grep -v "timeout"
# 应该没有输出（所有连接都有timeout）
```

---

### 任务2: 修复邮件配置端口转换

**位置**: `src/aqsp/briefing/email_notifier.py:43-48`

**修复**:
```python
# Before
def load_config_from_env() -> EmailConfig | None:
    host = os.getenv("AQSP_SMTP_HOST")
    port = os.getenv("AQSP_SMTP_PORT")
    # ...
    return EmailConfig(
        smtp_host=host,
        smtp_port=int(port),  # ❌ 无保护
        # ...
    )

# After
def load_config_from_env() -> EmailConfig | None:
    host = os.getenv("AQSP_SMTP_HOST")
    port = os.getenv("AQSP_SMTP_PORT")
    # ...
    
    try:
        smtp_port = int(port)
        if not 1 <= smtp_port <= 65535:
            raise ValueError(f"端口必须在1-65535之间")
    except (ValueError, TypeError) as e:
        raise ValueError(f"AQSP_SMTP_PORT配置错误: {port}, 错误: {e}") from None
    
    return EmailConfig(
        smtp_host=host,
        smtp_port=smtp_port,
        # ...
    )
```

---

## P1 - 本周修复

### 任务3: 添加配置边界检查

**位置**: `src/aqsp/config.py:82-85`

**修复**:
```python
# Before
@dataclass
class ScreeningConfig:
    limit: int = field(default_factory=lambda: int(os.getenv("AQSP_LIMIT", "10")))
    max_universe: int = field(default_factory=lambda: int(os.getenv("AQSP_MAX_UNIVERSE", "100")))
    min_avg_amount: float = field(default_factory=lambda: float(os.getenv("AQSP_MIN_AVG_AMOUNT", "50000000")))
    max_data_lag_days: int = field(default_factory=lambda: int(os.getenv("AQSP_MAX_DATA_LAG_DAYS", "3")))

# After
def _validate_limit() -> int:
    val = int(os.getenv("AQSP_LIMIT", "10"))
    if not 1 <= val <= 100:
        raise ValueError(f"AQSP_LIMIT必须在1-100之间，当前: {val}")
    return val

def _validate_max_universe() -> int:
    val = int(os.getenv("AQSP_MAX_UNIVERSE", "100"))
    if not 1 <= val <= 5000:
        raise ValueError(f"AQSP_MAX_UNIVERSE必须在1-5000之间，当前: {val}")
    return val

def _validate_min_avg_amount() -> float:
    val = float(os.getenv("AQSP_MIN_AVG_AMOUNT", "50000000"))
    if val <= 0:
        raise ValueError(f"AQSP_MIN_AVG_AMOUNT必须大于0，当前: {val}")
    return val

def _validate_max_data_lag_days() -> int:
    val = int(os.getenv("AQSP_MAX_DATA_LAG_DAYS", "3"))
    if not 0 <= val <= 30:
        raise ValueError(f"AQSP_MAX_DATA_LAG_DAYS必须在0-30之间，当前: {val}")
    return val

@dataclass
class ScreeningConfig:
    limit: int = field(default_factory=_validate_limit)
    max_universe: int = field(default_factory=_validate_max_universe)
    min_avg_amount: float = field(default_factory=_validate_min_avg_amount)
    max_data_lag_days: int = field(default_factory=_validate_max_data_lag_days)
```

---

## P2 - 体验优化

### 任务4: 邮件发送添加重试机制

**位置**: `src/aqsp/briefing/email_notifier.py:270-280`

**修复**:
```python
# Before
try:
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
        # ...
        server.send_message(msg)
        return True
except Exception as e:
    logger.error(f"邮件发送失败: {e}")
    return False

# After
import time

max_retries = 3
for attempt in range(max_retries):
    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
            # ...
            server.send_message(msg)
            logger.info(f"邮件发送成功（第{attempt+1}次尝试）")
            return True
    except smtplib.SMTPException as e:
        if attempt < max_retries - 1:
            wait_seconds = 2 ** attempt  # 指数退避: 1s, 2s, 4s
            logger.warning(f"邮件发送失败（第{attempt+1}次），{wait_seconds}秒后重试: {e}")
            time.sleep(wait_seconds)
        else:
            logger.error(f"邮件发送失败（已重试{max_retries}次）: {e}")
            return False
    except Exception as e:
        logger.error(f"邮件发送遇到非SMTP异常: {e}")
        return False
```

---

### 任务5: 缓存过期时间改为可配置

**位置**: `src/aqsp/data/cache.py:398`

**修复**:
```python
# Before
def clear_expired(self, max_age_hours: int = 168) -> int:

# After
def clear_expired(self, max_age_hours: int | None = None) -> int:
    if max_age_hours is None:
        max_age_hours = int(os.getenv("AQSP_CACHE_MAX_AGE_HOURS", "168"))
    
    if max_age_hours <= 0:
        logger.warning("max_age_hours必须大于0，使用默认值168小时")
        max_age_hours = 168
```

---

### 任务6: 替换异常处理中的pass

**位置**: 
- `src/aqsp/ledger/failure_analysis.py:43-44`
- `src/aqsp/utils/llm_safe.py:71-72`
- `src/aqsp/backtest/walk_forward.py:596-597`

**修复模式**:
```python
# Before
except (json.JSONDecodeError, TypeError):
    pass

# After
except (json.JSONDecodeError, TypeError) as e:
    logger.debug(f"JSON解析失败: {e}")
    return None  # 或其他明确的默认值
```

---

### 任务7: print改为logger

**位置**: `src/aqsp/briefing/email_notifier.py:262`

**修复**:
```python
# Before
except Exception as e:
    print(f"HTML 渲染失败，降级到纯文本: {e}")

# After
except Exception as e:
    logger.warning(f"HTML 渲染失败，降级到纯文本: {e}", exc_info=True)
```

---

## P3 - 长期优化（可选）

### 任务8: 添加日志轮转

**位置**: `src/aqsp/cli.py:2983-2988`

**修复**:
```python
# Before
handler = logging.FileHandler(log_path, encoding="utf-8")

# After
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    log_path,
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,  # 保留5个历史文件
    encoding="utf-8"
)
```

---

## 验证清单

完成修复后运行：

```bash
# 1. 检查SQLite超时
grep -rn "sqlite3.connect" src/ | grep -v "timeout"
# 应该无输出

# 2. 运行测试
pytest tests/ -v

# 3. 测试邮件配置
export AQSP_SMTP_PORT="invalid"
python -m aqsp.cli run --notify
# 应该看到清晰的错误提示

# 4. 测试并发SQLite
# 同时运行两个进程
python -m aqsp.cli run &
python -m aqsp.cli sync &
wait
# 不应该出现"database is locked"错误
```

---

## 预期效果

| 问题 | 修复前 | 修复后 |
|------|--------|--------|
| 宝塔任务卡死 | 经常发生 | ✅ 不再发生 |
| 配置错误崩溃 | 直接退出 | ✅ 友好提示 |
| 邮件发送失败 | 一次失败就放弃 | ✅ 自动重试3次 |
| 资源泄漏 | 逐渐变慢 | ✅ 连接正确关闭 |
| 并发死锁 | 数据库锁定 | ✅ 30秒超时保护 |

---

**修复优先级**: P0 > P1 > P2 > P3

**预估工时**: 
- P0: 2小时（必须立即完成）
- P1: 1小时（本周完成）
- P2: 2小时（可选）
- P3: 1小时（可选）
