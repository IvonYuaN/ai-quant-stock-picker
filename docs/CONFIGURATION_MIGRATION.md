# 配置系统迁移指南

本指南帮助你从旧的配置方式迁移到新的 `pydantic-settings` 统一配置管理系统。

## 概述

**迁移状态**：向后兼容，逐步迁移

- ✅ 新配置系统已部署，但旧代码仍然可用
- ✅ `load_runtime_config()` 内部已重构使用新 settings
- ✅ 现有代码无需立即修改
- 📋 建议逐步迁移到新 API 以获得更好的类型安全和开发体验

## 安装依赖

```bash
# 安装 pydantic-settings
pip install pydantic-settings

# 或重新安装项目（已添加到 dependencies）
pip install -e .
```

## 迁移步骤

### 1. 现有代码（无需修改）

旧代码继续工作，内部已自动切换到新配置系统：

```python
from aqsp.config import load_runtime_config, load_debate_runtime_config

# 这些函数仍然可用，内部已使用新配置系统
config = load_runtime_config()
debate_config = load_debate_runtime_config()

# 访问配置
limit = config.limit
mode = config.mode
```

**向后兼容保证**：
- 所有现有代码继续工作
- API 签名不变
- 返回的数据结构不变

### 2. 推荐的新代码写法

新代码建议使用 `get_settings()` 以获得更好的类型安全：

```python
from aqsp.settings import get_settings

# 获取全局配置实例
settings = get_settings()

# 访问配置（带类型提示）
limit = settings.runtime.limit  # IDE 会提供自动补全
mode = settings.runtime.mode
db_path = settings.database.sqlite_db_path
llm_provider = settings.llm.llm_provider
```

### 3. 逐步迁移策略

**阶段 1：保持现状**（当前）
- 旧代码不动，继续使用 `load_runtime_config()`
- 新功能使用 `get_settings()`

**阶段 2：渐进迁移**（建议）
- 修改模块时，顺便改用 `get_settings()`
- 不需要一次性全部修改

**阶段 3：完全迁移**（未来）
- 所有代码迁移到新 API
- 可选：废弃 `load_runtime_config()`（但保持很长时间）

## 配置访问对照表

### 运行时配置

| 旧代码 | 新代码 |
|--------|--------|
| `config.symbols` | `settings.runtime.symbols` (str, 需要 split) |
| `config.walkforward_symbols` | `settings.runtime.walkforward_symbols` (str, 需要 split) |
| `config.mode` | `settings.runtime.mode` |
| `config.limit` | `settings.runtime.limit` |
| `config.max_universe` | `settings.runtime.max_universe` |
| `config.min_avg_amount` | `settings.runtime.min_avg_amount` |
| `config.research_engine` | `settings.runtime.research_engine` |

**注意**：新配置中 `symbols` 和 `walkforward_symbols` 是字符串，需要自行 split：

```python
# 旧代码
symbols = config.symbols  # tuple[str, ...]

# 新代码
symbols = tuple(
    item.strip() 
    for item in settings.runtime.symbols.split(",") 
    if item.strip()
)
```

### 数据源配置

| 旧代码 | 新代码 |
|--------|--------|
| `config.max_data_lag_days` | `settings.data_source.max_data_lag_days` |
| `config.enable_online_factors` | `settings.data_source.enable_online_factors` |
| `config.allow_online_fallback` | `settings.data_source.allow_online_fallback` |

### 数据库配置

| 旧代码 | 新代码 |
|--------|--------|
| `os.getenv("AQSP_SQLITE_DB_PATH")` | `settings.database.sqlite_db_path` |
| `os.getenv("AQSP_SOURCE")` | `settings.database.source` |

### 通知配置

| 旧代码 | 新代码 |
|--------|--------|
| `config.notify` | `settings.notification.notify` |
| `config.notify_mode` | `settings.notification.notify_mode` |
| `os.getenv("TELEGRAM_BOT_TOKEN")` | `settings.notification.telegram_bot_token` |
| `os.getenv("TELEGRAM_CHAT_ID")` | `settings.notification.telegram_chat_id` |

### LLM 配置

| 旧代码 | 新代码 |
|--------|--------|
| `os.getenv("LLM_PROVIDER")` | `settings.llm.llm_provider` |
| `os.getenv("ENABLE_LLM_BRIEFING")` | `settings.llm.enable_llm_briefing` |
| `os.getenv("GLM_API_KEY")` | `settings.llm.glm_api_key` |
| `os.getenv("QWEN_API_KEY")` | `settings.llm.qwen_api_key` |

### 多 Agent 讨论配置

| 旧代码 | 新代码 |
|--------|--------|
| `config.enable_debate` | `settings.debate.enable_debate` |
| `config.enable_auto_evolution` | `settings.debate.enable_auto_evolution` |
| `debate_config.enable_llm` | `settings.debate.debate_enable_llm` |
| `debate_config.max_rounds` | `settings.debate.debate_max_rounds` |
| `debate_config.language` | `settings.debate.debate_language` |

## 迁移示例

### 示例 1：数据加载模块

**旧代码**：
```python
import os
from aqsp.config import load_runtime_config

def load_data():
    config = load_runtime_config()
    db_path = os.getenv("AQSP_SQLITE_DB_PATH", "/opt/market-data/astocks_raw.db")
    
    if config.max_data_lag_days > 5:
        raise ValueError("Data too stale")
    
    return load_from_db(db_path, config.symbols)
```

**新代码**：
```python
from aqsp.settings import get_settings

def load_data():
    settings = get_settings()
    
    if settings.data_source.max_data_lag_days > 5:
        raise ValueError("Data too stale")
    
    symbols = tuple(
        s.strip() for s in settings.runtime.symbols.split(",") if s.strip()
    )
    
    return load_from_db(settings.database.sqlite_db_path, symbols)
```

### 示例 2：通知模块

**旧代码**：
```python
import os
from aqsp.config import load_runtime_config

def send_notification(message: str):
    config = load_runtime_config()
    if not config.notify:
        return
    
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if token and chat_id:
        send_telegram(token, chat_id, message)
```

**新代码**：
```python
from aqsp.settings import get_settings

def send_notification(message: str):
    settings = get_settings()
    if not settings.notification.notify:
        return
    
    if settings.notification.telegram_bot_token and settings.notification.telegram_chat_id:
        send_telegram(
            settings.notification.telegram_bot_token,
            settings.notification.telegram_chat_id,
            message
        )
```

### 示例 3：LLM 模块

**旧代码**：
```python
import os

def get_llm_client():
    provider = os.getenv("LLM_PROVIDER", "glm")
    
    if provider == "glm":
        api_key = os.getenv("GLM_API_KEY")
        model = os.getenv("GLM_MODEL", "glm-4.7-flash")
    elif provider == "qwen":
        api_key = os.getenv("QWEN_API_KEY")
        model = os.getenv("QWEN_MODEL", "qwen-turbo")
    
    return create_client(provider, api_key, model)
```

**新代码**：
```python
from aqsp.settings import get_settings

def get_llm_client():
    settings = get_settings()
    
    if settings.llm.llm_provider == "glm":
        api_key = settings.llm.glm_api_key
        model = settings.llm.glm_model
    elif settings.llm.llm_provider == "qwen":
        api_key = settings.llm.qwen_api_key
        model = settings.llm.qwen_model
    
    return create_client(settings.llm.llm_provider, api_key, model)
```

## 新功能优势

### 1. 类型安全

```python
# 旧代码：没有类型提示
config = load_runtime_config()
limit = config.limit  # IDE 不知道类型

# 新代码：完整类型提示
settings = get_settings()
limit = settings.runtime.limit  # IDE 知道是 int，提供自动补全
```

### 2. 验证

```python
from aqsp.settings import RuntimeSettings
from pydantic import ValidationError

try:
    # 自动验证配置
    settings = RuntimeSettings(limit=-1)
except ValidationError as e:
    print(e)  # limit 必须 >= 1
```

### 3. YAML 配置文件

```yaml
# config/settings.dev.yaml
runtime:
  mode: open
  limit: 20
  max_universe: 300

database:
  sqlite_db_path: /path/to/dev.db
```

```python
# 加载 YAML 配置
settings = get_settings("config/settings.dev.yaml")
```

### 4. 环境隔离

```bash
# 开发环境
export AQSP_ENV=dev
aqsp run  # 自动加载 config/settings.dev.yaml

# 生产环境
export AQSP_ENV=prod
aqsp run  # 自动加载 config/settings.prod.yaml
```

## 常见问题

### Q: 必须立即迁移所有代码吗？

**A**: 不需要。旧代码继续工作，可以逐步迁移。

### Q: 迁移会破坏现有功能吗？

**A**: 不会。`load_runtime_config()` 内部已使用新配置系统，但 API 保持不变。

### Q: 如何测试新配置是否工作？

**A**: 运行测试套件：

```bash
pytest tests/test_settings.py -v
```

### Q: 如果 pydantic-settings 未安装会怎样？

**A**: 自动回退到旧实现，不会报错。但建议安装以获得新功能。

### Q: 新旧配置可以混用吗？

**A**: 可以。同一个文件里可以同时使用 `load_runtime_config()` 和 `get_settings()`。

### Q: 如何查看当前配置？

**A**: 使用以下命令：

```bash
python -c "
from aqsp.settings import get_settings
import json
settings = get_settings()
print(json.dumps(settings.model_dump(), indent=2, ensure_ascii=False))
"
```

## 迁移清单

用这个清单追踪你的迁移进度：

- [ ] 安装 `pydantic-settings` 依赖
- [ ] 阅读 `docs/CONFIGURATION.md` 了解新配置系统
- [ ] 运行 `pytest tests/test_settings.py` 确认新系统工作
- [ ] （可选）创建 `config/settings.dev.yaml` 和 `config/settings.prod.yaml`
- [ ] 新代码使用 `get_settings()` 代替 `os.getenv()`
- [ ] 逐步迁移旧模块（不着急，慢慢来）
- [ ] 验证迁移后的功能正常

## 获取帮助

- 配置详细说明：`docs/CONFIGURATION.md`
- 测试示例：`tests/test_settings.py`
- 配置模板：`config/settings.dev.yaml`, `config/settings.prod.yaml`
- 环境变量模板：`.env.example`

## 迁移时间表（建议）

| 阶段 | 时间 | 任务 |
|------|------|------|
| 阶段 0 | 已完成 | 部署新配置系统，保持向后兼容 |
| 阶段 1 | 当前 | 新功能使用新 API，旧代码不动 |
| 阶段 2 | 1-2 个月 | 修改模块时顺便迁移 |
| 阶段 3 | 3-6 个月 | 大部分代码已迁移 |
| 阶段 4 | 6-12 个月 | 考虑废弃旧 API（但仍保持） |

**注意**：没有强制时间表，按自己的节奏迁移即可。
