"""测试统一配置管理系统"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aqsp.settings import (
    AQSPSettings,
    DatabaseSettings,
    DataSourceSettings,
    DebateSettings,
    DeploymentSettings,
    LLMSettings,
    NotificationSettings,
    RuntimeSettings,
    get_settings,
)


class TestDatabaseSettings:
    """测试数据库配置"""

    def test_default_values(self):
        settings = DatabaseSettings()
        assert settings.source == "sqlite_db"
        assert settings.sqlite_db_path == "/opt/market-data/astocks_raw.db"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AQSP_SOURCE", "custom_db")
        monkeypatch.setenv("AQSP_SQLITE_DB_PATH", "/custom/path.db")

        settings = DatabaseSettings()
        assert settings.source == "custom_db"
        assert settings.sqlite_db_path == "/custom/path.db"


class TestDataSourceSettings:
    """测试数据源配置"""

    def test_default_values(self):
        settings = DataSourceSettings()
        assert settings.enable_online_factors is False
        assert settings.allow_online_fallback is False
        assert settings.max_data_lag_days == 3

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AQSP_ENABLE_ONLINE_FACTORS", "true")
        monkeypatch.setenv("AQSP_ALLOW_ONLINE_FALLBACK", "true")
        monkeypatch.setenv("AQSP_MAX_DATA_LAG_DAYS", "5")

        settings = DataSourceSettings()
        assert settings.enable_online_factors is True
        assert settings.allow_online_fallback is True
        assert settings.max_data_lag_days == 5

    def test_validation_min_data_lag(self):
        """测试 max_data_lag_days 不能为负数"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DataSourceSettings(max_data_lag_days=-1)


class TestNotificationSettings:
    """测试通知配置"""

    def test_default_values(self):
        settings = NotificationSettings()
        assert settings.notify is False
        assert settings.gate_notify is False
        assert settings.notify_mode == "summary"
        assert settings.telegram_bot_token == ""
        assert settings.telegram_chat_id == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AQSP_NOTIFY", "1")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

        settings = NotificationSettings()
        assert settings.notify is True
        assert settings.telegram_bot_token == "test_token"
        assert settings.telegram_chat_id == "123456"

    def test_notify_mode_validation(self):
        """测试 notify_mode 只能是 summary 或 full"""
        settings = NotificationSettings(notify_mode="summary")
        assert settings.notify_mode == "summary"

        settings = NotificationSettings(notify_mode="full")
        assert settings.notify_mode == "full"


class TestLLMSettings:
    """测试 LLM 配置"""

    def test_default_values(self):
        settings = LLMSettings()
        assert settings.enable_llm_briefing is True
        assert settings.llm_provider == "glm"
        assert settings.glm_model == "glm-4.7-flash"
        assert settings.qwen_model == "qwen-turbo"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "qwen")
        monkeypatch.setenv("QWEN_API_KEY", "test_key")
        monkeypatch.setenv("QWEN_MODEL", "qwen-plus")

        settings = LLMSettings()
        assert settings.llm_provider == "qwen"
        assert settings.qwen_api_key == "test_key"
        assert settings.qwen_model == "qwen-plus"

    def test_provider_validation(self):
        """测试 LLM provider 只能是支持的值"""
        valid_providers = [
            "glm",
            "qwen",
            "agnes",
            "siliconflow",
            "deepseek",
            "openai",
            "anthropic",
            "custom",
        ]
        for provider in valid_providers:
            settings = LLMSettings(llm_provider=provider)
            assert settings.llm_provider == provider


class TestDebateSettings:
    """测试多 Agent 讨论配置"""

    def test_default_values(self):
        settings = DebateSettings()
        # 既有契约：AQSP_ENABLE_DEBATE 未设时默认启用（config.load_runtime_config 亦然）
        assert settings.enable_debate is True
        assert settings.debate_enable_llm is False
        assert settings.debate_max_rounds == 2
        assert settings.debate_language == "zh-CN"
        # debate_roles 留空表示「按 task 选用预设角色」，而非固定角色串
        assert settings.debate_roles == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
        monkeypatch.setenv("AQSP_DEBATE_MAX_ROUNDS", "3")
        monkeypatch.setenv("AQSP_DEBATE_ROLES", "bull,bear,risk_control")

        settings = DebateSettings()
        assert settings.enable_debate is True
        assert settings.debate_max_rounds == 3
        assert settings.debate_roles == "bull,bear,risk_control"

    def test_validation_min_rounds(self):
        """测试 debate_max_rounds 必须 >= 1"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DebateSettings(debate_max_rounds=0)


class TestDeploymentSettings:
    """测试部署配置"""

    def test_default_values(self):
        settings = DeploymentSettings()
        assert settings.deploy_dashboard is False
        assert settings.deploy_host == ""
        assert settings.deploy_port == 22
        assert settings.deploy_user == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AQSP_DEPLOY_DASHBOARD", "true")
        monkeypatch.setenv("AQSP_DEPLOY_HOST", "example.com")
        monkeypatch.setenv("AQSP_DEPLOY_PORT", "2222")
        monkeypatch.setenv("AQSP_DEPLOY_USER", "deploy_user")

        settings = DeploymentSettings()
        assert settings.deploy_dashboard is True
        assert settings.deploy_host == "example.com"
        assert settings.deploy_port == 2222
        assert settings.deploy_user == "deploy_user"

    def test_port_validation(self):
        """测试端口号必须在有效范围内"""
        from pydantic import ValidationError

        # 有效端口
        settings = DeploymentSettings(deploy_port=22)
        assert settings.deploy_port == 22

        # 无效端口
        with pytest.raises(ValidationError):
            DeploymentSettings(deploy_port=0)

        with pytest.raises(ValidationError):
            DeploymentSettings(deploy_port=99999)


class TestRuntimeSettings:
    """测试运行时配置"""

    def test_default_values(self):
        settings = RuntimeSettings()
        assert settings.symbols == ""
        assert settings.walkforward_symbols == ""
        assert settings.mode == "close"
        assert settings.limit == 10
        assert settings.max_universe == 0
        assert settings.min_avg_amount == 50000000.0
        assert settings.ledger == "data/predictions.jsonl"
        assert settings.research_engine == "auto"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AQSP_SYMBOLS", "000001,000002")
        monkeypatch.setenv("AQSP_MODE", "open")
        monkeypatch.setenv("AQSP_LIMIT", "20")
        monkeypatch.setenv("AQSP_MAX_UNIVERSE", "500")

        settings = RuntimeSettings()
        assert settings.symbols == "000001,000002"
        assert settings.mode == "open"
        assert settings.limit == 20
        assert settings.max_universe == 500

    def test_validation_min_limit(self):
        """测试 limit 必须 >= 1"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RuntimeSettings(limit=0)

    def test_validation_min_universe(self):
        """测试 max_universe 必须 >= 0"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RuntimeSettings(max_universe=-1)

    def test_mode_validation(self):
        """测试 mode 只能是 open 或 close"""
        settings = RuntimeSettings(mode="open")
        assert settings.mode == "open"

        settings = RuntimeSettings(mode="close")
        assert settings.mode == "close"


class TestAQSPSettings:
    """测试主配置类"""

    def test_default_initialization(self):
        settings = AQSPSettings()
        assert isinstance(settings.database, DatabaseSettings)
        assert isinstance(settings.data_source, DataSourceSettings)
        assert isinstance(settings.notification, NotificationSettings)
        assert isinstance(settings.llm, LLMSettings)
        assert isinstance(settings.debate, DebateSettings)
        assert isinstance(settings.deployment, DeploymentSettings)
        assert isinstance(settings.runtime, RuntimeSettings)

    def test_from_yaml(self, tmp_path: Path):
        """测试从 YAML 文件加载"""
        yaml_file = tmp_path / "test_settings.yaml"
        config = {
            "database": {"source": "test_db", "sqlite_db_path": "/test/path.db"},
            "runtime": {"mode": "open", "limit": 15, "max_universe": 100},
            "llm": {"llm_provider": "qwen", "enable_llm_briefing": False},
        }

        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        settings = AQSPSettings.from_yaml(yaml_file)
        assert settings.database.source == "test_db"
        assert settings.database.sqlite_db_path == "/test/path.db"
        assert settings.runtime.mode == "open"
        assert settings.runtime.limit == 15
        assert settings.llm.llm_provider == "qwen"
        assert settings.llm.enable_llm_briefing is False

    def test_from_yaml_file_not_found(self, tmp_path: Path):
        """测试 YAML 文件不存在时抛出异常"""
        yaml_file = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            AQSPSettings.from_yaml(yaml_file)

    def test_env_overrides_yaml(self, tmp_path: Path, monkeypatch):
        """测试环境变量优先级高于 YAML"""
        yaml_file = tmp_path / "test_settings.yaml"
        config = {"runtime": {"limit": 10}}

        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        # 环境变量设置不同的值
        monkeypatch.setenv("AQSP_LIMIT", "20")

        settings = AQSPSettings.from_env_and_yaml(yaml_file)
        # 环境变量应该优先
        assert settings.runtime.limit == 20


class TestGetSettings:
    """测试全局配置获取函数"""

    def test_get_settings_returns_same_instance(self):
        """测试 get_settings() 返回缓存的实例"""
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_get_settings_force_reload(self):
        """测试强制重新加载"""
        settings1 = get_settings()
        settings2 = get_settings(force_reload=True)
        # 强制重新加载后是新实例
        assert settings1 is not settings2

    def test_get_settings_with_yaml(self, tmp_path: Path):
        """测试使用自定义 YAML 文件"""
        yaml_file = tmp_path / "custom.yaml"
        config = {"runtime": {"limit": 99}}

        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        get_settings.cache_clear()  # 清除缓存
        settings = get_settings(yaml_file)
        assert settings.runtime.limit == 99

    def test_auto_detect_env(self, tmp_path: Path, monkeypatch):
        """测试自动检测环境"""
        # 创建 dev 配置
        dev_yaml = tmp_path / "config" / "settings.dev.yaml"
        dev_yaml.parent.mkdir(parents=True, exist_ok=True)
        config = {"runtime": {"limit": 5}}

        with open(dev_yaml, "w", encoding="utf-8") as f:
            yaml.dump(config, f)

        # 切换工作目录
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AQSP_ENV", "dev")

        get_settings.cache_clear()
        settings = get_settings()
        # 应该加载 dev 配置
        assert settings.runtime.limit == 5


class TestConfigurationIntegration:
    """集成测试"""

    def test_production_like_config(self, monkeypatch):
        """测试生产环境配置"""
        monkeypatch.setenv("AQSP_ENV", "prod")
        monkeypatch.setenv("AQSP_SQLITE_DB_PATH", "/prod/data.db")
        monkeypatch.setenv("AQSP_ALLOW_ONLINE_FALLBACK", "false")
        monkeypatch.setenv("AQSP_MODE", "close")
        monkeypatch.setenv("AQSP_SYMBOLS", "")
        monkeypatch.setenv("AQSP_MAX_UNIVERSE", "0")

        get_settings.cache_clear()
        settings = get_settings()

        # 生产环境关键配置验证
        assert settings.data_source.allow_online_fallback is False
        assert settings.runtime.mode == "close"
        assert settings.runtime.symbols == ""
        assert settings.runtime.max_universe == 0
        assert settings.database.sqlite_db_path == "/prod/data.db"

    def test_development_config(self, monkeypatch):
        """测试开发环境配置"""
        monkeypatch.setenv("AQSP_ENV", "dev")
        monkeypatch.setenv("AQSP_ALLOW_ONLINE_FALLBACK", "true")
        monkeypatch.setenv("AQSP_MAX_UNIVERSE", "300")

        get_settings.cache_clear()
        settings = get_settings()

        # 开发环境可以更宽松
        assert settings.data_source.allow_online_fallback is True
        assert settings.runtime.max_universe == 300

    def test_all_notification_channels_configurable(self, monkeypatch):
        """测试所有通知渠道都可配置"""
        channels = {
            "TELEGRAM_BOT_TOKEN": "telegram_token",
            "TELEGRAM_CHAT_ID": "123456",
            "SERVERCHAN_SENDKEY": "serverchan_key",
            "WECHAT_WEBHOOK_URL": "https://wechat.example.com",
            "FEISHU_WEBHOOK_URL": "https://feishu.example.com",
            "GENERIC_WEBHOOK_URL": "https://generic.example.com",
            "BARK_URL": "https://bark.example.com",
            "PUSHPLUS_TOKEN": "pushplus_token",
            "DINGTALK_WEBHOOK_URL": "https://dingtalk.example.com",
            "DINGTALK_SECRET": "dingtalk_secret",
            "DISCORD_WEBHOOK_URL": "https://discord.example.com",
            "SLACK_WEBHOOK_URL": "https://slack.example.com",
        }

        for key, value in channels.items():
            monkeypatch.setenv(key, value)

        settings = NotificationSettings()

        assert settings.telegram_bot_token == "telegram_token"
        assert settings.telegram_chat_id == "123456"
        assert settings.serverchan_sendkey == "serverchan_key"
        assert settings.wechat_webhook_url == "https://wechat.example.com"
        assert settings.feishu_webhook_url == "https://feishu.example.com"
        assert settings.generic_webhook_url == "https://generic.example.com"
        assert settings.bark_url == "https://bark.example.com"
        assert settings.pushplus_token == "pushplus_token"
        assert settings.dingtalk_webhook_url == "https://dingtalk.example.com"
        assert settings.dingtalk_secret == "dingtalk_secret"
        assert settings.discord_webhook_url == "https://discord.example.com"
        assert settings.slack_webhook_url == "https://slack.example.com"

    def test_all_llm_providers_configurable(self, monkeypatch):
        """测试所有 LLM provider 都可配置"""
        providers_config = {
            "GLM_API_KEY": "glm_key",
            "GLM_MODEL": "glm-4-flash",
            "QWEN_API_KEY": "qwen_key",
            "QWEN_MODEL": "qwen-max",
            "AGNES_API_KEY": "agnes_key",
            "AGNES_MODEL": "agnes-2.0",
            "SILICONFLOW_API_KEY": "silicon_key",
            "SILICONFLOW_MODEL": "Qwen/Qwen2.5-7B",
            "DEEPSEEK_API_KEY": "deepseek_key",
            "OPENAI_API_KEY": "openai_key",
            "OPENAI_MODEL": "gpt-4",
            "ANTHROPIC_API_KEY": "anthropic_key",
            "ANTHROPIC_MODEL": "claude-3-opus",
        }

        for key, value in providers_config.items():
            monkeypatch.setenv(key, value)

        settings = LLMSettings()

        assert settings.glm_api_key == "glm_key"
        assert settings.glm_model == "glm-4-flash"
        assert settings.qwen_api_key == "qwen_key"
        assert settings.qwen_model == "qwen-max"
        assert settings.agnes_api_key == "agnes_key"
        assert settings.agnes_model == "agnes-2.0"
        assert settings.siliconflow_api_key == "silicon_key"
        assert settings.siliconflow_model == "Qwen/Qwen2.5-7B"
        assert settings.deepseek_api_key == "deepseek_key"
        assert settings.openai_api_key == "openai_key"
        assert settings.openai_model == "gpt-4"
        assert settings.anthropic_api_key == "anthropic_key"
        assert settings.anthropic_model == "claude-3-opus"
