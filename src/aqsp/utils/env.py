from __future__ import annotations

import os
from pathlib import Path


def read_env_value(path: str | Path, key: str) -> str:
    env_path = Path(path)
    if not env_path.exists():
        return ""
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def read_project_env_value(key: str, *, env_name: str = "AQSP_ENV_FILE") -> str:
    override_path = os.getenv(env_name, "").strip()
    if override_path:
        return read_env_value(override_path, key)
    return read_env_value(".env", key)
