"""Canonical Vibe-Research entrypoint and legacy static-page guardrails."""

from __future__ import annotations

import html
import os
from pathlib import Path

from aqsp.utils.jsonl_io import atomic_write_text

DEFAULT_PUBLIC_DASHBOARD_URL = "https://lh.ifidy.cn"


def public_dashboard_url() -> str:
    """Return the one user-facing Dashboard URL."""
    value = os.getenv("AQSP_DASHBOARD_PUBLIC_URL", DEFAULT_PUBLIC_DASHBOARD_URL)
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ValueError("AQSP_DASHBOARD_PUBLIC_URL must include http:// or https://")
    return value


def render_legacy_redirect(*, target_url: str | None = None) -> str:
    """Render a small migration page instead of exposing a stale static UI."""
    target = target_url or public_dashboard_url()
    safe_target = html.escape(target, quote=True)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="aqsp-dashboard-entry" content="canonical-research-surface">
  <meta http-equiv="refresh" content="0;url={safe_target}">
  <title>AQSP Dashboard 已迁移</title>
</head>
<body>
  <p>AQSP Dashboard 已迁移到当前实时研究看板。</p>
  <p><a href="{safe_target}">进入 Vibe-Research 研究看板</a></p>
  <script>window.location.replace({target!r});</script>
</body>
</html>
'''


def write_dashboard_artifact(output_path: Path, html_text: str) -> Path:
    """Write an archive and make the conventional index a canonical redirect."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.name != "index.html":
        atomic_write_text(output_path, html_text)
        return output_path

    archive_path = output_path.with_name("archive.html")
    atomic_write_text(archive_path, html_text)
    atomic_write_text(output_path, render_legacy_redirect())
    return archive_path


def write_agent_archive_guard(output_path: Path) -> None:
    """Retire the standalone Agent page in favor of the main Dashboard."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, render_legacy_redirect())
