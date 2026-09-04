from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from requests.adapters import HTTPAdapter

from aqsp.core.http import (
    HttpClientConfig,
    _DEFAULT_HTTP_CONFIG,
    _DefaultTimeoutHTTPAdapter,
    build_http_session,
    get_http_config,
)


# ---------------------------------------------------------------------------
# HttpClientConfig
# ---------------------------------------------------------------------------


def test_http_client_config_defaults_are_safe():
    """Default config must be positive and contain at least one status code."""
    cfg = HttpClientConfig()
    assert cfg.connect_timeout > 0
    assert cfg.read_timeout > 0
    assert cfg.max_retries >= 0
    assert cfg.backoff_factor >= 0
    assert cfg.backoff_max >= 0
    assert len(cfg.status_forcelist) > 0
    # default_timeout 必须返回 (connect, read) 元组,供 requests.Session 接受。
    assert cfg.default_timeout == (cfg.connect_timeout, cfg.read_timeout)


def test_http_client_config_validates_ranges():
    """所有数值字段必须有边界校验,避免错配的 yaml 拖垮超时行为。"""
    with pytest.raises(ValueError, match="connect_timeout"):
        HttpClientConfig(connect_timeout=0)
    with pytest.raises(ValueError, match="read_timeout"):
        HttpClientConfig(read_timeout=-1.0)
    with pytest.raises(ValueError, match="max_retries"):
        HttpClientConfig(max_retries=-1)
    with pytest.raises(ValueError, match="backoff_factor"):
        HttpClientConfig(backoff_factor=-0.1)
    with pytest.raises(ValueError, match="backoff_max"):
        HttpClientConfig(backoff_max=-1.0)
    with pytest.raises(ValueError, match="status_forcelist"):
        HttpClientConfig(status_forcelist=())


def test_http_client_config_is_frozen():
    """冻结避免运行时被改,符合 AGENTS.md §3.3 dataclass 约定。"""
    cfg = HttpClientConfig()
    with pytest.raises(Exception):
        cfg.connect_timeout = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_http_session
# ---------------------------------------------------------------------------


def test_build_http_session_mounts_both_schemes():
    """factory 必须为 http 和 https 都挂上自定义 adapter。"""
    session = build_http_session()
    assert isinstance(session.get_adapter("https://x/"), _DefaultTimeoutHTTPAdapter)
    assert isinstance(session.get_adapter("http://x/"), _DefaultTimeoutHTTPAdapter)


def test_build_http_session_default_timeout_tuple():
    """session 级 default timeout 必须是 (connect, read) 元组形态。"""
    cfg = HttpClientConfig(connect_timeout=2.5, read_timeout=4.5)
    session = build_http_session(config=cfg)
    adapter = session.get_adapter("https://x/")
    assert adapter._default_timeout == (2.5, 4.5)


def test_build_http_session_inherits_retry_config():
    """Retry 必须按 config 装配,且 methods 限定为安全子集。"""
    cfg = HttpClientConfig(
        max_retries=4,
        backoff_factor=0.5,
        backoff_max=3.0,
        status_forcelist=(429, 503),
    )
    session = build_http_session(config=cfg)
    retry = session.get_adapter("https://x/").max_retries
    assert retry.total == 4
    assert retry.backoff_factor == 0.5
    assert retry.backoff_max == 3.0
    assert list(retry.status_forcelist) == [429, 503]
    # allowed_methods 应是 frozenset,只含 GET/HEAD/POST
    assert set(retry.allowed_methods) == {"GET", "HEAD", "POST"}


def test_build_http_session_applies_headers():
    """headers 必须被合并到 session.headers,适配 eastmoney/sina/tencent 的 UA。"""
    session = build_http_session(
        config=HttpClientConfig(),
        headers={"User-Agent": "AQSP-test/1.0", "Referer": "https://x.test/"},
    )
    assert session.headers.get("User-Agent") == "AQSP-test/1.0"
    assert session.headers.get("Referer") == "https://x.test/"


def test_build_http_session_trust_env_default_off():
    """无 AQSP_DATA_TRUST_ENV 时 trust_env=False,沿用现有安全默认。"""
    import os

    env = {k: v for k, v in os.environ.items() if k != "AQSP_DATA_TRUST_ENV"}
    with patch.dict(os.environ, env, clear=True):
        session = build_http_session()
        assert session.trust_env is False


def test_build_http_session_trust_env_opt_in():
    """AQSP_DATA_TRUST_ENV=1 时 trust_env=True,允许操作员走显式代理。"""
    import os

    with patch.dict(os.environ, {"AQSP_DATA_TRUST_ENV": "1"}, clear=False):
        session = build_http_session()
        assert session.trust_env is True


# ---------------------------------------------------------------------------
# _DefaultTimeoutHTTPAdapter 注入行为
# ---------------------------------------------------------------------------


def test_default_timeout_adapter_injects_when_caller_omits():
    """调用方未传 timeout、request.timeout 也为 None → 注入 (connect, read)。"""

    class _Req:
        url = "https://x/"
        method = "GET"
        headers = {}
        body = None
        timeout = None

    captured: dict = {}

    def fake_send(self, request, **kwargs):  # noqa: ARG001
        captured["timeout"] = kwargs.get("timeout")
        return None

    cfg = HttpClientConfig(connect_timeout=2.0, read_timeout=3.0)
    session = build_http_session(config=cfg)
    adapter = session.get_adapter("https://x/")

    with patch.object(HTTPAdapter, "send", fake_send):
        adapter.send(_Req(), stream=False, timeout=None)
    assert captured["timeout"] == (2.0, 3.0)


def test_default_timeout_adapter_respects_explicit_caller_timeout():
    """调用方显式传 timeout=2.0 → 必须不被默认覆盖,语义尊重。"""

    class _Req:
        url = "https://x/"
        method = "GET"
        headers = {}
        body = None
        timeout = None

    captured: dict = {}

    def fake_send(self, request, **kwargs):  # noqa: ARG001
        captured["timeout"] = kwargs.get("timeout")
        return None

    session = build_http_session(
        config=HttpClientConfig(connect_timeout=9.0, read_timeout=9.0)
    )
    adapter = session.get_adapter("https://x/")

    with patch.object(HTTPAdapter, "send", fake_send):
        adapter.send(_Req(), stream=False, timeout=2.0)
    assert captured["timeout"] == 2.0


def test_default_timeout_adapter_respects_request_timeout():
    """request.timeout 已设(被 Session.request 同步) → 不再重复注入。"""

    class _Req:
        url = "https://x/"
        method = "GET"
        headers = {}
        body = None
        timeout = 5.0  # Session.request 已写入

    captured: dict = {}

    def fake_send(self, request, **kwargs):  # noqa: ARG001
        captured["timeout"] = kwargs.get("timeout")
        return None

    session = build_http_session(
        config=HttpClientConfig(connect_timeout=9.0, read_timeout=9.0)
    )
    adapter = session.get_adapter("https://x/")

    with patch.object(HTTPAdapter, "send", fake_send):
        adapter.send(_Req(), stream=False, timeout=None)
    # 显式设了 request.timeout=5.0 → send 包装不注入 → kwargs 仍是 None
    assert captured["timeout"] is None


# ---------------------------------------------------------------------------
# get_http_config — yaml 加载与兜底
# ---------------------------------------------------------------------------


def test_get_http_config_missing_file_returns_default(tmp_path: Path):
    """yaml 不存在 → 静默回落默认,启动不能因此被拖垮。"""
    cfg = get_http_config(tmp_path / "no-such.yaml")
    assert cfg == _DEFAULT_HTTP_CONFIG


def test_get_http_config_malformed_yaml_returns_default(tmp_path: Path):
    """yaml 解析失败 → 静默回落默认,避免 IO 错误炸启动。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(":\n  - : :", encoding="utf-8")
    cfg = get_http_config(bad)
    assert cfg == _DEFAULT_HTTP_CONFIG


def test_get_http_config_missing_block_returns_default(tmp_path: Path):
    """yaml 存在但没 http 块 → 回落默认。"""
    p = tmp_path / "no_http.yaml"
    p.write_text("version: 1.0\nfallback_order: {}\n", encoding="utf-8")
    cfg = get_http_config(p)
    assert cfg == _DEFAULT_HTTP_CONFIG


def test_get_http_config_full_block_picks_up_values(tmp_path: Path):
    """完整 http 块 → 字段必须被解析并验证。"""
    p = tmp_path / "with_http.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "http": {
                    "connect_timeout": 3.0,
                    "read_timeout": 7.0,
                    "max_retries": 5,
                    "backoff_factor": 0.1,
                    "backoff_max": 1.5,
                    "status_forcelist": [429, 503],
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = get_http_config(p)
    assert cfg.connect_timeout == 3.0
    assert cfg.read_timeout == 7.0
    assert cfg.max_retries == 5
    assert cfg.backoff_factor == 0.1
    assert cfg.backoff_max == 1.5
    assert cfg.status_forcelist == (429, 503)


def test_get_http_config_invalid_types_fall_back(tmp_path: Path):
    """字段类型错(字符串混入) → 兜底默认,而不是让生产启动炸。"""
    p = tmp_path / "bad_types.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "http": {
                    "connect_timeout": "not-a-number",
                    "max_retries": "three",
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = get_http_config(p)
    assert cfg == _DEFAULT_HTTP_CONFIG


def test_get_http_config_partial_block_fills_defaults(tmp_path: Path):
    """只填部分字段 → 未填的字段从默认补齐,符合 '缺啥用啥' 原则。"""
    p = tmp_path / "partial.yaml"
    p.write_text(
        yaml.safe_dump({"http": {"read_timeout": 4.0}}),
        encoding="utf-8",
    )
    cfg = get_http_config(p)
    assert cfg.read_timeout == 4.0
    # 未填字段 = 默认
    assert cfg.connect_timeout == _DEFAULT_HTTP_CONFIG.connect_timeout
    assert cfg.max_retries == _DEFAULT_HTTP_CONFIG.max_retries


# ---------------------------------------------------------------------------
# 集成 — 各 adapter 拿到的是带 retry + default timeout 的 session
# ---------------------------------------------------------------------------


def test_eastmoney_session_has_retry_and_default_timeout():
    """东财 adapter 必须继承 retry + default timeout (PR #76 集成保证)。"""
    from aqsp.data.eastmoney_source import EastmoneySource

    src = EastmoneySource()
    adapter = src._session.get_adapter("https://x/")
    assert isinstance(adapter, _DefaultTimeoutHTTPAdapter)
    assert adapter.max_retries.total == _DEFAULT_HTTP_CONFIG.max_retries
    assert adapter._default_timeout == _DEFAULT_HTTP_CONFIG.default_timeout


def test_sina_session_has_retry_and_default_timeout():
    from aqsp.data.sina_source import SinaSource

    src = SinaSource()
    adapter = src._session.get_adapter("https://x/")
    assert adapter.max_retries.total == _DEFAULT_HTTP_CONFIG.max_retries
    assert adapter._default_timeout == _DEFAULT_HTTP_CONFIG.default_timeout


def test_tencent_session_has_retry_and_default_timeout():
    from aqsp.data.tencent_source import TencentSource

    src = TencentSource()
    adapter = src._session.get_adapter("https://x/")
    assert adapter.max_retries.total == _DEFAULT_HTTP_CONFIG.max_retries
    assert adapter._default_timeout == _DEFAULT_HTTP_CONFIG.default_timeout


def test_sina_fundamental_session_has_retry_and_default_timeout():
    from aqsp.data.sina_fundamental import SinaFundamentalSource

    src = SinaFundamentalSource()
    adapter = src._session.get_adapter("https://x/")
    assert adapter.max_retries.total == _DEFAULT_HTTP_CONFIG.max_retries
    assert adapter._default_timeout == _DEFAULT_HTTP_CONFIG.default_timeout
