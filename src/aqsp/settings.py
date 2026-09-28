"""统一配置管理系统 - 使用 pydantic-settings

优先级：环境变量 > YAML 配置文件 > 默认值

使用方式：
    from aqsp.settings import get_settings

    settings = get_settings()
    db_path = settings.database.sqlite_db_path
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

try:
    from pydantic import AliasChoices, Field, field_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    raise ImportError(
        "pydantic-settings is required. Install with: pip install pydantic-settings"
    )

import yaml


class DatabaseSettings(BaseSettings):
    """数据库配置"""

    model_config = SettingsConfigDict(env_prefix="AQSP_")

    source: str = Field(default="sqlite_db", description="数据源类型")
    sqlite_db_path: str = Field(
        default="/opt/market-data/astocks_raw.db",
        description="SQLite 数据库路径（必须是不复权 raw 库）",
    )


class DataSourceSettings(BaseSettings):
    """数据源配置"""

    model_config = SettingsConfigDict(env_prefix="AQSP_")

    enable_online_factors: bool = Field(
        default=False, description="是否启用在线因子"
    )
    allow_online_fallback: bool = Field(
        default=False, description="是否允许在线数据回退（生产必须为 false）"
    )
    max_data_lag_days: int = Field(
        default=3, ge=0, description="最大数据滞后天数"
    )


class NotificationSettings(BaseSettings):
    """通知配置"""

    model_config = SettingsConfigDict(env_prefix="AQSP_")

    notify: bool = Field(default=False, description="是否启用通知")
    gate_notify: bool = Field(default=False, description="是否启用门控通知")
    notify_mode: Literal["summary", "full"] = Field(
        default="summary", description="通知模式"
    )
    notify_summary_fallback_full: bool = Field(
        default=False, description="摘要模式失败时是否回退到完整模式"
    )

    # 通知渠道配置（env 名沿用全仓既有约定：无 AQSP_ 前缀；同时兼容 AQSP_ 前缀写法）
    telegram_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "AQSP_TELEGRAM_BOT_TOKEN"),
        description="Telegram Bot Token",
    )
    telegram_chat_id: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_CHAT_ID", "AQSP_TELEGRAM_CHAT_ID"),
        description="Telegram Chat ID",
    )
    serverchan_sendkey: str = Field(
        default="",
        validation_alias=AliasChoices("SERVERCHAN_SENDKEY", "AQSP_SERVERCHAN_SENDKEY"),
        description="Server酱 SendKey",
    )
    wechat_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("WECHAT_WEBHOOK_URL", "AQSP_WECHAT_WEBHOOK_URL"),
        description="企业微信 Webhook URL",
    )
    feishu_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("FEISHU_WEBHOOK_URL", "AQSP_FEISHU_WEBHOOK_URL"),
        description="飞书 Webhook URL",
    )
    generic_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("GENERIC_WEBHOOK_URL", "AQSP_GENERIC_WEBHOOK_URL"),
        description="通用 Webhook URL",
    )
    bark_url: str = Field(
        default="",
        validation_alias=AliasChoices("BARK_URL", "AQSP_BARK_URL"),
        description="Bark 推送 URL",
    )
    pushplus_token: str = Field(
        default="",
        validation_alias=AliasChoices("PUSHPLUS_TOKEN", "AQSP_PUSHPLUS_TOKEN"),
        description="PushPlus Token",
    )
    dingtalk_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("DINGTALK_WEBHOOK_URL", "AQSP_DINGTALK_WEBHOOK_URL"),
        description="钉钉 Webhook URL",
    )
    dingtalk_secret: str = Field(
        default="",
        validation_alias=AliasChoices("DINGTALK_SECRET", "AQSP_DINGTALK_SECRET"),
        description="钉钉签名密钥",
    )
    discord_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("DISCORD_WEBHOOK_URL", "AQSP_DISCORD_WEBHOOK_URL"),
        description="Discord Webhook URL",
    )
    slack_webhook_url: str = Field(
        default="",
        validation_alias=AliasChoices("SLACK_WEBHOOK_URL", "AQSP_SLACK_WEBHOOK_URL"),
        description="Slack Webhook URL",
    )


class LLMSettings(BaseSettings):
    """LLM 配置"""

    model_config = SettingsConfigDict(env_prefix="")

    enable_llm_briefing: bool = Field(
        default=True, alias="ENABLE_LLM_BRIEFING", description="是否启用 LLM 增强简报"
    )
    llm_provider: Literal[
        "glm", "qwen", "agnes", "siliconflow", "deepseek", "openai", "anthropic", "custom"
    ] = Field(default="glm", alias="LLM_PROVIDER", description="LLM 提供商")

    # GLM 配置
    glm_api_key: str = Field(default="", alias="GLM_API_KEY")
    glm_model: str = Field(default="glm-4.7-flash", alias="GLM_MODEL")

    # 通义千问配置
    qwen_api_key: str = Field(default="", alias="QWEN_API_KEY")
    qwen_model: str = Field(default="qwen-turbo", alias="QWEN_MODEL")

    # Agnes AI 配置
    agnes_api_key: str = Field(default="", alias="AGNES_API_KEY")
    agnes_model: str = Field(default="agnes-2.0-flash", alias="AGNES_MODEL")

    # 硅基流动配置
    siliconflow_api_key: str = Field(default="", alias="SILICONFLOW_API_KEY")
    siliconflow_model: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct", alias="SILICONFLOW_MODEL"
    )
    siliconflow_free_only: bool = Field(
        default=True, alias="SILICONFLOW_FREE_ONLY", description="仅使用免费模型"
    )

    # DeepSeek 配置
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")

    # OpenAI 配置
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # Anthropic 配置
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-3-5-haiku-20241022", alias="ANTHROPIC_MODEL"
    )

    # 自定义配置
    custom_base_url: str = Field(
        default="http://localhost:11434/v1", alias="CUSTOM_BASE_URL"
    )
    custom_model: str = Field(default="llama3.1", alias="CUSTOM_MODEL")
    api_key: str = Field(default="dummy", alias="API_KEY")


class DebateSettings(BaseSettings):
    """多 Agent 讨论配置"""

    model_config = SettingsConfigDict(env_prefix="AQSP_")

    enable_debate: bool = Field(
        default=True,
        description="是否启用多 Agent 讨论（默认启用，对齐 AQSP_ENABLE_DEBATE 旧默认 true）",
    )
    debate_enable_llm: bool = Field(
        default=False, description="是否在讨论中启用 LLM"
    )
    debate_max_rounds: int = Field(default=2, ge=1, description="最大讨论轮数")
    debate_language: str = Field(default="zh-CN", description="讨论语言")
    debate_roles: str = Field(
        default="",
        description="讨论角色（逗号分隔）。留空 = 按 task 选用预设角色，对齐旧 AQSP_DEBATE_ROLES 语义",
    )
    debate_role_llm: str = Field(default="", description="角色 LLM 映射")
    debate_role_providers: str = Field(default="", description="角色 Provider 映射")
    debate_role_models: str = Field(default="", description="角色模型映射")
    enable_auto_evolution: bool = Field(
        default=False, description="是否启用自动演化"
    )


class DeploymentSettings(BaseSettings):
    """部署配置"""

    model_config = SettingsConfigDict(env_prefix="AQSP_")

    deploy_dashboard: bool = Field(default=False, description="是否部署仪表板")
    deploy_host: str = Field(default="", description="部署主机")
    deploy_port: int = Field(default=22, ge=1, le=65535, description="SSH 端口")
    deploy_user: str = Field(default="", description="部署用户")
    deploy_path: str = Field(default="", description="部署路径")
    deploy_ssh_key: str = Field(default="", description="SSH 私钥")


class RuntimeSettings(BaseSettings):
    """运行时配置"""

    model_config = SettingsConfigDict(env_prefix="AQSP_")

    # 股票代码配置
    symbols: str = Field(default="", description="A 股代码（逗号分隔）")
    walkforward_symbols: str = Field(
        default="", description="Walkforward 专用标的池（逗号分隔）"
    )

    # 运行模式配置
    mode: Literal["open", "close"] = Field(
        default="close", description="运行模式：open=开盘前/盘中，close=尾盘/收盘后"
    )
    limit: int = Field(default=10, ge=1, description="候选数量限制")
    max_universe: int = Field(default=0, ge=0, description="最大股票池大小（0=全市场）")
    min_avg_amount: float = Field(
        default=50000000.0, ge=0.0, description="最小平均成交额"
    )

    # 文件路径配置
    ledger: str = Field(default="data/predictions.jsonl", description="预测账本路径")
    paper_ledger: str = Field(
        default="data/paper_trades.jsonl", description="模拟交易账本路径"
    )
    report: str = Field(default="reports/latest.md", description="报告路径")
    output_csv: str = Field(default="reports/latest.csv", description="输出 CSV 路径")
    paper_report: str = Field(default="reports/paper.md", description="模拟交易报告路径")
    dashboard: str = Field(
        default="dist/dashboard/index.html", description="仪表板 HTML 路径"
    )
    dashboard_html: str = Field(
        default="dist/dashboard/index.html", description="仪表板 HTML 路径（别名）"
    )
    dashboard_db: str = Field(
        default="dist/dashboard/aqsp.db", description="仪表板数据库路径"
    )

    # 研究引擎配置
    research_engine: str = Field(default="auto", description="研究引擎")

    # 外部 Token（用于研究采集/数据源）
    github_token: str = Field(default="", description="GitHub Token")
    gitee_token: str = Field(default="", description="Gitee Token")
    tushare_token: str = Field(default="", description="Tushare Token")


def _merge_env_over_yaml(sub_cls: type[BaseSettings], yaml_section: dict) -> BaseSettings:
    """合并「YAML 段」与「环境变量」，环境变量优先。

    pydantic-settings 中，以 init kwargs 传入的值优先级高于 env；子配置若直接用
    ``sub_cls(**yaml_section)`` 构造，会把 env 压制掉。这里改为：分别构造
    「YAML 版」与「仅 env+默认 版」，逐字段比较——若某字段的 env 值与默认值不同，
    说明 env 显式设置了它，环境变量胜出；否则采用 YAML 值。
    """
    yaml_inst = sub_cls(**yaml_section)
    env_inst = sub_cls()
    merged: dict = {}
    for name, field in sub_cls.model_fields.items():
        try:
            default = field.get_default(call_default_factory=True)
        except Exception:  # pragma: no cover - 防御性
            default = None
        env_val = getattr(env_inst, name)
        merged[name] = env_val if env_val != default else getattr(yaml_inst, name)
    return sub_cls(**merged)


class AQSPSettings(BaseSettings):
    """AQSP 主配置类 - 聚合所有子配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    data_source: DataSourceSettings = Field(default_factory=DataSourceSettings)
    notification: NotificationSettings = Field(default_factory=NotificationSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    debate: DebateSettings = Field(default_factory=DebateSettings)
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "AQSPSettings":
        """从 YAML 文件加载配置

        Args:
            yaml_path: YAML 配置文件路径

        Returns:
            AQSPSettings 实例
        """
        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {yaml_path}")

        with open(yaml_path, encoding="utf-8") as f:
            config_dict = yaml.safe_load(f) or {}

        return cls(**config_dict)

    @classmethod
    def from_env_and_yaml(
        cls, yaml_path: str | Path | None = None
    ) -> "AQSPSettings":
        """从环境变量和 YAML 文件加载配置（环境变量优先）

        Args:
            yaml_path: YAML 配置文件路径（可选）

        Returns:
            AQSPSettings 实例
        """
        if yaml_path is None:
            # 仅当显式设置 AQSP_ENV 时才自动探测对应环境配置；
            # 未设置 ⇒ 只用环境变量 + 默认值（避免生产机未设 AQSP_ENV 时静默加载 dev 配置）。
            env = os.getenv("AQSP_ENV")
            if not env:
                return cls()
            yaml_path = Path(f"config/settings.{env.lower()}.yaml")

        yaml_config: dict = {}
        if Path(yaml_path).exists():
            with open(yaml_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                yaml_config = loaded

        if not yaml_config:
            # 无 YAML：仅使用环境变量和默认值
            return cls()

        # 有 YAML：逐段合并，环境变量优先
        section_map = {
            "database": DatabaseSettings,
            "data_source": DataSourceSettings,
            "notification": NotificationSettings,
            "llm": LLMSettings,
            "debate": DebateSettings,
            "deployment": DeploymentSettings,
            "runtime": RuntimeSettings,
        }
        kwargs = {}
        for section, sub_cls in section_map.items():
            section_data = yaml_config.get(section) or {}
            if not isinstance(section_data, dict):
                section_data = {}
            kwargs[section] = _merge_env_over_yaml(sub_cls, section_data)
        return cls(**kwargs)


@lru_cache
def get_settings(
    yaml_path: str | Path | None = None, force_reload: bool = False
) -> AQSPSettings:
    """获取全局配置实例（带缓存）

    Args:
        yaml_path: YAML 配置文件路径（可选，默认根据 AQSP_ENV 自动检测）
        force_reload: 是否强制重新加载（清除缓存）

    Returns:
        AQSPSettings 实例

    Examples:
        >>> settings = get_settings()
        >>> settings = get_settings("config/settings.prod.yaml")
        >>> settings = get_settings(force_reload=True)
    """
    if force_reload:
        get_settings.cache_clear()

    return AQSPSettings.from_env_and_yaml(yaml_path)
