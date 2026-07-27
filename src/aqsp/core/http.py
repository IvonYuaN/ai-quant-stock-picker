from __future__ import annotations

import os
import sys
import urllib.request
from typing import Any

import requests


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


def requests_session_without_implicit_proxy(
    env_key: str = "AQSP_DATA_TRUST_ENV",
) -> requests.Session:
    """Return a requests session that avoids implicit system proxy discovery.

    Keep this as the common path for project-owned data/download clients.  An
    operator can still opt in with ``AQSP_DATA_TRUST_ENV=1`` when a controlled
    proxy is explicitly required.
    """
    session = requests.Session()
    session.trust_env = trust_environment_proxy_enabled(env_key)
    return session
