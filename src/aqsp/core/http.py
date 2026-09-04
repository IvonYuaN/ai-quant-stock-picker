from __future__ import annotations

import os
import sys
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def urlopen_no_macos_proxy(
    request: urllib.request.Request | str, *, timeout: float
) -> Any:
    """Open a URL without triggering macOS SystemConfiguration proxy lookup.

    macOS proxy discovery goes through ``_scproxy``/SystemConfiguration.  If a
    Python worker was created by ``fork`` from a multi-threaded parent, touching
    that API on the child side can terminate the interpreter with EXC_GUARD
    before Python can raise a normal exception.  Project probes do not need the
    system proxy; disabling it here keeps local checks deterministic and avoids
    leaving crash-report residuals.
    """
    if sys.platform == "darwin":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def env_flag_enabled(name: str) -> bool:
    """Return true when an env flag explicitly enables an opt-in behavior."""
    return str(os.getenv(name, "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def trust_environment_proxy_enabled(env_key: str = "AQSP_DATA_TRUST_ENV") -> bool:
    """Project default for network clients: no implicit system proxy lookup."""
    return env_flag_enabled(env_key)


# ---------------------------------------------------------------------------
# HTTP client configuration (PR #76)
# ---------------------------------------------------------------------------
#
# A股数据源在限流窗/被 GFW 临时拉黑时若没有 read timeout 兜底,会触发两个失败模式:
# 1. 单次 socket 挂死 → 抓取 worker 永远不返回 → ``ThreadPoolExecutor`` 退
#    出期被 ``_python_exit`` join 卡死(PR #75 已修复进程退出);
# 2. 业务层在重试循环里反复被打断,触发雪崩。本模块为全仓的 ``requests.Session``
#    提供**集中默认**:
#   * session 级别默认 (connect, read) timeout —— 调用方未传 timeout 时自动注入;
#   * ``HTTPAdapter(max_retries=Retry(...))`` —— 429/5xx 自动指数退避;
#   * ``trust_env`` 仍受 ``AQSP_DATA_TRUST_ENV`` 控制(沿用原行为);
#   * 超时/重试参数从 ``config/data_sources.yaml`` 的 ``http`` 块读,
#     缺块或格式坏掉时 fallback 到 ``_DEFAULT_HTTP_CONFIG``(不报错、不拖垮启动).


@dataclass(frozen=True)
class HttpClientConfig:
    """Per-Session HTTP defaults — single source of truth for adapters."""

    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    max_retries: int = 2
    backoff_factor: float = 0.3
    backoff_max: float = 2.0
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)
    trust_env_key: str = "AQSP_DATA_TRUST_ENV"

    def __post_init__(self) -> None:
        if self.connect_timeout <= 0:
            raise ValueError(f"connect_timeout 必须 > 0,收到 {self.connect_timeout}")
        if self.read_timeout <= 0:
            raise ValueError(f"read_timeout 必须 > 0,收到 {self.read_timeout}")
        if self.max_retries < 0:
            raise ValueError(f"max_retries 必须 >= 0,收到 {self.max_retries}")
        if self.backoff_factor < 0:
            raise ValueError(f"backoff_factor 必须 >= 0,收到 {self.backoff_factor}")
        if self.backoff_max < 0:
            raise ValueError(f"backoff_max 必须 >= 0,收到 {self.backoff_max}")
        if not self.status_forcelist:
            raise ValueError("status_forcelist 不可为空")

    @property
    def default_timeout(self) -> tuple[float, float]:
        """requests 接受的 ``(connect, read)`` 元组,作为 session-level 兜底。"""
        return (self.connect_timeout, self.read_timeout)


_DEFAULT_HTTP_CONFIG = HttpClientConfig()


class _DefaultTimeoutHTTPAdapter(HTTPAdapter):
    """``HTTPAdapter`` 在调用方未传 ``timeout`` 时注入 session 默认值。

    优先以 ``request.timeout`` 为准;只有 ``PreparedRequest.timeout`` 为 ``None``
    才注入默认 ``(connect, read)``,避免覆盖调用方显式传入的更短/更长超时。
    """

    def __init__(
        self, *, max_retries: Retry, default_timeout: tuple[float, float]
    ) -> None:
        super().__init__(max_retries=max_retries)
        self._default_timeout = default_timeout

    def send(self, request, **kwargs):  # type: ignore[override]
        if kwargs.get("timeout") is None and getattr(request, "timeout", None) is None:
            kwargs["timeout"] = self._default_timeout
        return super().send(request, **kwargs)


def build_http_session(
    *,
    config: HttpClientConfig | None = None,
    headers: Mapping[str, str] | None = None,
) -> requests.Session:
    """Return a ``requests.Session`` with project-wide HTTP defaults applied.

    The session is mounted with ``_DefaultTimeoutHTTPAdapter`` for both
    ``http://`` and ``https://`` schemes; every adapter call inherits the
    configured ``Retry`` policy plus the session-level default timeout.
    Callers may still pass an explicit ``timeout=`` to ``Session.get``/``post``
    to override the default for that specific call.
    """
    cfg = config or _DEFAULT_HTTP_CONFIG
    retry = Retry(
        total=cfg.max_retries,
        backoff_factor=cfg.backoff_factor,
        backoff_max=cfg.backoff_max,
        status_forcelist=list(cfg.status_forcelist),
        allowed_methods=frozenset(["GET", "HEAD", "POST"]),
        raise_on_status=False,
    )
    adapter = _DefaultTimeoutHTTPAdapter(
        max_retries=retry,
        default_timeout=cfg.default_timeout,
    )
    session = requests.Session()
    session.trust_env = trust_environment_proxy_enabled(cfg.trust_env_key)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if headers:
        session.headers.update(dict(headers))
    return session


# 项目内不再推荐直接 ``requests.get`` / ``requests.post`` —— 一律改走
# ``build_http_session``;保留 ``requests_session_without_implicit_proxy`` 是
# 为了不破坏潜在外部调用方,内部逻辑与 ``build_http_session(config=...)`` 相同
# 但无 retry/timeout 注入,只用于极少数不能挂 retry 的边缘场景。


def requests_session_without_implicit_proxy(
    env_key: str = "AQSP_DATA_TRUST_ENV",
) -> requests.Session:
    """Legacy thin wrapper — prefer ``build_http_session`` in new code."""
    session = requests.Session()
    session.trust_env = trust_environment_proxy_enabled(env_key)
    return session


def get_http_config(path: str | Path = "config/data_sources.yaml") -> HttpClientConfig:
    """从 ``data_sources.yaml`` 的 ``http`` 块读 HttpClientConfig。

    读不到 / 解析失败 / 字段缺失 → 静默回落到 ``_DEFAULT_HTTP_CONFIG``,
    确保启动不被 IO 错误拖垮。``HttpClientConfig.__post_init__`` 会做
    范围校验,这里不做第二层防御。
    """
    p = Path(path)
    if not p.is_file():
        return _DEFAULT_HTTP_CONFIG
    try:
        import yaml  # pyyaml 已是项目依赖

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (ImportError, OSError, ValueError, yaml.YAMLError):
        return _DEFAULT_HTTP_CONFIG
    block = data.get("http") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return _DEFAULT_HTTP_CONFIG
    try:
        status = block.get("status_forcelist") or list(
            _DEFAULT_HTTP_CONFIG.status_forcelist
        )
        if not isinstance(status, (list, tuple)):
            status = list(_DEFAULT_HTTP_CONFIG.status_forcelist)
        return HttpClientConfig(
            connect_timeout=float(
                block.get("connect_timeout", _DEFAULT_HTTP_CONFIG.connect_timeout)
            ),
            read_timeout=float(
                block.get("read_timeout", _DEFAULT_HTTP_CONFIG.read_timeout)
            ),
            max_retries=int(block.get("max_retries", _DEFAULT_HTTP_CONFIG.max_retries)),
            backoff_factor=float(
                block.get("backoff_factor", _DEFAULT_HTTP_CONFIG.backoff_factor)
            ),
            backoff_max=float(
                block.get("backoff_max", _DEFAULT_HTTP_CONFIG.backoff_max)
            ),
            status_forcelist=tuple(int(x) for x in status),
            trust_env_key=str(
                block.get("trust_env_key", _DEFAULT_HTTP_CONFIG.trust_env_key)
            ),
        )
    except (TypeError, ValueError):
        return _DEFAULT_HTTP_CONFIG


# Re-export commonly used names to make ``from aqsp.core.http import build_http_session`` ergonomic.
__all__ = [
    "HttpClientConfig",
    "build_http_session",
    "get_http_config",
    "env_flag_enabled",
    "requests_session_without_implicit_proxy",
    "trust_environment_proxy_enabled",
    "urlopen_no_macos_proxy",
]


# Suppress unused import warning for ``field`` — kept for downstream dataclass
# extension consistency (mirrors ``dataclass(frozen=True, kw_only=True)`` pattern).
_ = field
