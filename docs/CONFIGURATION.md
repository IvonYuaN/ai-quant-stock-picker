# AQSP 配置管理指南

本文档说明 AQSP 项目的统一配置管理系统。

## 概述

AQSP 使用 `pydantic-settings` 实现统一的配置管理，支持：

- **类型安全**：所有配置项都有类型检查和验证
- **多数据源**：支持环境变量、.env 文件、YAML 配置文件
- **优先级控制**：环境变量 > YAML 配置 > 默认值
- **环境切换**：开发/生产环境配置分离
- **文档完善**：所有配置项都有说明和默认值

## 配置优先级

配置加载顺序（后者覆盖前者）：

1. **默认值**：代码中定义的默认值
2. **YAML 配置文件**：`config/settings.{env}.yaml`
3. **环境变量**：操作系统环境变量或 `.env` 文件

### 示例

```yaml
# config/settings.dev.yaml
runtime:
  limit: 10
```

```bash
# .env 或命令行
export AQSP_LIMIT=20
```

最终生效：`limit=20`（环境变量优先）

## 配置文件

### 目录结构

```
config/
├── settings.dev.yaml      # 开发环境配置
├── settings.prod.yaml     # 生产环境配置
├── blacklist.yaml         # 黑名单配置
├── factor_config.yaml     # 因子配置
└── ...

.env                        # 本地环境变量（不提交）
.env.example               # 环境变量模板
```

### 环境切换

通过 `AQSP_ENV` 环境变量切换配置文件：

```bash
# 使用开发环境配置
export AQSP_ENV=dev
aqsp run

# 使用生产环境配置
export AQSP_ENV=prod
aqsp run

# 或使用自定义配置文件
aqsp run --config config/settings.custom.yaml
```

## 配置分组

### 1. 数据库配置 (DatabaseSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| source | AQSP_SOURCE | str | sqlite_db | 数据源类型 |
| sqlite_db_path | AQSP_SQLITE_DB_PATH | str | /opt/market-data/astocks_raw.db | SQLite 数据库路径（必须是不复权 raw 库） |

**生产要求**：
- 必须使用不复权 raw 库
- qfq/hfq 仅用于展示或辅助研究

### 2. 数据源配置 (DataSourceSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| enable_online_factors | AQSP_ENABLE_ONLINE_FACTORS | bool | false | 是否启用在线因子 |
| allow_online_fallback | AQSP_ALLOW_ONLINE_FALLBACK | bool | false | 是否允许在线数据回退 |
| max_data_lag_days | AQSP_MAX_DATA_LAG_DAYS | int | 3 | 最大数据滞后天数 |

**生产要求**：
- `allow_online_fallback` 必须为 `false`（fail-closed）
- 不允许静默回退到公网临时源

### 3. 通知配置 (NotificationSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| notify | AQSP_NOTIFY | bool | false | 是否启用通知 |
| gate_notify | AQSP_GATE_NOTIFY | bool | false | 是否启用门控通知 |
| notify_mode | AQSP_NOTIFY_MODE | str | summary | 通知模式（summary/full） |
| notify_summary_fallback_full | AQSP_NOTIFY_SUMMARY_FALLBACK_FULL | bool | false | 摘要模式失败时是否回退到完整模式 |

**支持的通知渠道**：

| 渠道 | 环境变量 | 说明 |
|------|----------|------|
| Telegram | TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID | Telegram 机器人 |
| Server酱 | SERVERCHAN_SENDKEY | Server酱推送 |
| 企业微信 | WECHAT_WEBHOOK_URL | 企业微信群机器人 |
| 飞书 | FEISHU_WEBHOOK_URL | 飞书群机器人 |
| 钉钉 | DINGTALK_WEBHOOK_URL, DINGTALK_SECRET | 钉钉群机器人 |
| Discord | DISCORD_WEBHOOK_URL | Discord Webhook |
| Slack | SLACK_WEBHOOK_URL | Slack Webhook |
| Bark | BARK_URL | Bark iOS 推送 |
| PushPlus | PUSHPLUS_TOKEN | PushPlus 推送 |
| 通用 | GENERIC_WEBHOOK_URL | 通用 Webhook |

**配置任意一个即可启用通知。**

### 4. LLM 配置 (LLMSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| enable_llm_briefing | ENABLE_LLM_BRIEFING | bool | true | 是否启用 LLM 增强简报 |
| llm_provider | LLM_PROVIDER | str | glm | LLM 提供商 |

**支持的 LLM Provider**：

#### 方案 1：智谱 GLM（推荐首选）

```bash
LLM_PROVIDER=glm
GLM_API_KEY=your_api_key
GLM_MODEL=glm-4.7-flash
```

- **注册**：https://open.bigmodel.cn/
- **特点**：永久免费不限量，中文理解优秀，适合 A 股分析

#### 方案 2：通义千问

```bash
LLM_PROVIDER=qwen
QWEN_API_KEY=your_api_key
QWEN_MODEL=qwen-turbo  # 或 qwen-plus, qwen-max
```

- **注册**：https://dashscope.console.aliyun.com/
- **额度**：新用户 500 万 tokens，免费 180 天
- **特点**：阿里大模型，中文能力强

#### 方案 3：Agnes AI

```bash
LLM_PROVIDER=agnes
AGNES_API_KEY=your_api_key
AGNES_MODEL=agnes-2.0-flash
```

- **官网**：https://agnes-ai.com/
- **特点**：OpenAI 兼容接口，适合直接接入

#### 方案 4：硅基流动

```bash
LLM_PROVIDER=siliconflow
SILICONFLOW_API_KEY=your_api_key
SILICONFLOW_MODEL=Qwen/Qwen2.5-7B-Instruct
SILICONFLOW_FREE_ONLY=true  # 启用免费模型白名单护栏
```

- **注册**：https://cloud.siliconflow.cn/
- **额度**：注册送 14 元，部分 7B 小模型永久免费
- **特点**：聚合平台，可调用 Qwen/GLM/Llama 等多种模型

#### 方案 5：DeepSeek（付费）

```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_api_key
```

- **注册**：https://platform.deepseek.com/
- **价格**：输入 2 元/百万 tokens，输出 8 元/百万 tokens
- **特点**：国产最强推理模型，复杂分析场景首选

#### 其他 Provider

```bash
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key
OPENAI_MODEL=gpt-4o-mini

# Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_api_key
ANTHROPIC_MODEL=claude-3-5-haiku-20241022

# 自定义（如本地 Ollama）
LLM_PROVIDER=custom
CUSTOM_BASE_URL=http://localhost:11434/v1
CUSTOM_MODEL=llama3.1
API_KEY=dummy
```

### 5. 多 Agent 讨论配置 (DebateSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| enable_debate | AQSP_ENABLE_DEBATE | bool | false | 是否启用多 Agent 讨论 |
| debate_enable_llm | AQSP_DEBATE_ENABLE_LLM | bool | false | 是否在讨论中启用 LLM |
| debate_max_rounds | AQSP_DEBATE_MAX_ROUNDS | int | 2 | 最大讨论轮数 |
| debate_language | AQSP_DEBATE_LANGUAGE | str | zh-CN | 讨论语言 |
| debate_roles | AQSP_DEBATE_ROLES | str | bull,bear,... | 讨论角色（逗号分隔） |

**默认角色**：
- `bull`：多头
- `bear`：空头
- `risk_control`：风控
- `sector_leader`：行业领先
- `policy_sensitive`：政策敏感
- `northbound`：北向资金

**说明**：
- 当前多 Agent 讨论主链路是多角色规则引擎
- LLM 仅用于摘要增强，不参与核心打分
- 生产环境建议 `debate_enable_llm=false`

### 6. 部署配置 (DeploymentSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| deploy_dashboard | AQSP_DEPLOY_DASHBOARD | bool | false | 是否部署仪表板 |
| deploy_host | AQSP_DEPLOY_HOST | str | "" | 部署主机 |
| deploy_port | AQSP_DEPLOY_PORT | int | 22 | SSH 端口 |
| deploy_user | AQSP_DEPLOY_USER | str | "" | 部署用户 |
| deploy_path | AQSP_DEPLOY_PATH | str | "" | 部署路径 |
| deploy_ssh_key | AQSP_DEPLOY_SSH_KEY | str | "" | SSH 私钥 |

**安全要求**：
- 真实值只放 GitHub Actions Secrets
- 不提交到版本控制

### 7. 运行时配置 (RuntimeSettings)

| 配置项 | 环境变量 | 类型 | 默认值 | 说明 |
|--------|----------|------|--------|------|
| symbols | AQSP_SYMBOLS | str | "" | A 股代码（逗号分隔） |
| walkforward_symbols | AQSP_WALKFORWARD_SYMBOLS | str | "" | Walkforward 专用标的池 |
| mode | AQSP_MODE | str | close | 运行模式（open/close） |
| limit | AQSP_LIMIT | int | 10 | 候选数量限制 |
| max_universe | AQSP_MAX_UNIVERSE | int | 0 | 最大股票池大小（0=全市场） |
| min_avg_amount | AQSP_MIN_AVG_AMOUNT | float | 50000000 | 最小平均成交额 |

**文件路径配置**：

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| ledger | AQSP_LEDGER | data/predictions.jsonl | 预测账本路径 |
| paper_ledger | AQSP_PAPER_LEDGER | data/paper_trades.jsonl | 模拟交易账本路径 |
| report | AQSP_REPORT | reports/latest.md | 报告路径 |
| output_csv | AQSP_OUTPUT_CSV | reports/latest.csv | 输出 CSV 路径 |
| paper_report | AQSP_PAPER_REPORT | reports/paper.md | 模拟交易报告路径 |
| dashboard | AQSP_DASHBOARD | dist/dashboard/index.html | 仪表板 HTML 路径 |
| dashboard_db | AQSP_DASHBOARD_DB | dist/dashboard/aqsp.db | 仪表板数据库路径 |

**外部 Token**：

| 配置项 | 环境变量 | 说明 |
|--------|----------|------|
| github_token | GITHUB_TOKEN | GitHub Token（用于研究采集） |
| gitee_token | GITEE_TOKEN | Gitee Token |
| tushare_token | TUSHARE_TOKEN | Tushare Token |

**生产要求**：
- `symbols` 留空走全市场池
- 不要用 300 只烟雾测试池上线
- `walkforward_symbols` 建议填"历史库里覆盖完整"的票
- `max_universe=0` 或 `>= full-market gate 下限`

## 使用方式

### 1. Python 代码中使用

```python
from aqsp.settings import get_settings

# 获取配置（自动根据 AQSP_ENV 加载）
settings = get_settings()

# 访问配置项
db_path = settings.database.sqlite_db_path
limit = settings.runtime.limit
llm_provider = settings.llm.llm_provider

# 使用自定义配置文件
settings = get_settings("config/settings.custom.yaml")

# 强制重新加载
settings = get_settings(force_reload=True)
```

### 2. 从 YAML 文件加载

```python
from aqsp.settings import AQSPSettings

# 仅从 YAML 加载
settings = AQSPSettings.from_yaml("config/settings.prod.yaml")

# 从 YAML + 环境变量加载（推荐）
settings = AQSPSettings.from_env_and_yaml("config/settings.prod.yaml")
```

### 3. 环境变量配置

```bash
# 设置环境
export AQSP_ENV=prod

# 数据库配置
export AQSP_SQLITE_DB_PATH=/path/to/astocks_raw.db

# 运行时配置
export AQSP_MODE=close
export AQSP_LIMIT=20

# LLM 配置
export LLM_PROVIDER=glm
export GLM_API_KEY=your_api_key

# 通知配置
export AQSP_NOTIFY=true
export TELEGRAM_BOT_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id

# 运行
aqsp run
```

## 迁移指南

### 从旧配置迁移

旧代码使用 `load_runtime_config()` 的地方，逐步迁移到新的 `get_settings()`：

**旧代码**：
```python
from aqsp.config import load_runtime_config

config = load_runtime_config()
limit = config.limit
```

**新代码**：
```python
from aqsp.settings import get_settings

settings = get_settings()
limit = settings.runtime.limit
```

### 向后兼容

`src/aqsp/config.py` 中的 `load_runtime_config()` 保持不变，内部已重构使用新的 settings。

现有代码无需立即修改，可以逐步迁移。

## 配置验证

使用 pydantic 的验证功能：

```python
from aqsp.settings import AQSPSettings
from pydantic import ValidationError

try:
    settings = AQSPSettings(
        runtime={"limit": -1}  # 无效值
    )
except ValidationError as e:
    print(e)
    # ValidationError: limit必须 >= 1
```

## 最佳实践

### 1. 安全性

- **不提交敏感信息**：API Keys、Tokens、SSH Keys 只放 `.env` 或环境变量
- **使用 .env.example**：提供模板，不包含真实值
- **GitHub Actions Secrets**：部署配置使用 Secrets

### 2. 环境隔离

- **开发环境**：`AQSP_ENV=dev`，允许在线回退，小股票池
- **生产环境**：`AQSP_ENV=prod`，fail-closed，全市场池

### 3. 配置分层

```
环境变量（临时覆盖）
    ↓
YAML 配置（环境专用）
    ↓
代码默认值（兜底）
```

### 4. 配置检查

```bash
# 检查当前配置
python -c "from aqsp.settings import get_settings; import json; print(json.dumps(get_settings().model_dump(), indent=2, ensure_ascii=False))"
```

## 常见问题

### Q: 如何切换 LLM Provider？

```bash
# 方法 1：环境变量
export LLM_PROVIDER=qwen
export QWEN_API_KEY=your_key

# 方法 2：修改 YAML
# config/settings.dev.yaml
llm:
  llm_provider: qwen
```

### Q: 生产环境必须配置哪些项？

最小配置：
```bash
export AQSP_ENV=prod
export AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db
export AQSP_ALLOW_ONLINE_FALLBACK=false  # 必须 false
```

### Q: 如何验证配置是否正确？

```bash
# 运行测试
pytest tests/test_settings.py

# 或检查配置加载
python -m aqsp.settings
```

### Q: pydantic-settings 未安装？

```bash
pip install pydantic-settings
# 或
pip install -e .  # 项目已添加依赖
```

## 参考资料

- [Pydantic Settings 文档](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [项目配置示例](.env.example)
- [开发环境配置](../config/settings.dev.yaml)
- [生产环境配置](../config/settings.prod.yaml)
