"""Canonical AQSP entrypoint and legacy static-page guardrails."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

from aqsp.utils.jsonl_io import atomic_write_text

DEFAULT_PUBLIC_DASHBOARD_URL = "https://lh.ifidy.cn"
CANONICAL_HEALTH_PATH = "/api/health"
LEGACY_HEALTH_PATH = "/_stcore/health"
# The static shell uses the full product title; the hydrated React page keeps
# the product name and AQSP provenance in separate visible regions.
CANONICAL_ENTRY_MARKERS = ("AQSP", "研究工作台")
CANONICAL_ENTRY_MARKER_GROUPS = (
    CANONICAL_ENTRY_MARKERS,
    ("AQSP", "当前研究"),
    ("AQSP", "当天研究"),
)
CANONICAL_DASHBOARD_ARTIFACT_NAMES = frozenset(("index.html", "archive.html"))
LEGACY_ENTRY_MARKERS = (
    "AQSP 日期任务研究台",
    "短线决策看板",
    "新手看板",
    "agents.html",
    "dashboard_beginner.py",
    "archive.html",
    "streamlit",
)

EntryKind = Literal["canonical", "legacy", "redirect", "unknown"]
HealthKind = Literal["canonical", "legacy", "unknown"]


def _validate_url(value: str, *, setting_name: str) -> str:
    """Accept only absolute HTTP(S) URLs for the single public entry."""
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{setting_name} must include an absolute http:// or https:// URL"
        )
    if parsed.username or parsed.password:
        raise ValueError(f"{setting_name} must not include URL credentials")
    return normalized


def public_dashboard_url() -> str:
    """Return the one user-facing Dashboard URL."""
    value = os.getenv("AQSP_DASHBOARD_PUBLIC_URL", DEFAULT_PUBLIC_DASHBOARD_URL)
    return _validate_url(value, setting_name="AQSP_DASHBOARD_PUBLIC_URL")


def public_research_health_url(*, base_url: str | None = None) -> str:
    """Return the canonical AQSP health endpoint for the public entry."""
    base = _validate_url(
        base_url or public_dashboard_url(),
        setting_name="AQSP_DASHBOARD_PUBLIC_URL",
    )
    return urljoin(f"{base}/", CANONICAL_HEALTH_PATH.lstrip("/"))


def classify_entry_text(text: str) -> EntryKind:
    """Classify rendered HTML/text without treating a legacy redirect as live."""
    haystack = text.casefold()
    if (
        'name="aqsp-dashboard-entry"' in haystack
        and "canonical-research-surface" in haystack
    ):
        return "redirect"
    if any(
        all(marker.casefold() in haystack for marker in markers)
        for markers in CANONICAL_ENTRY_MARKER_GROUPS
    ):
        return "canonical"
    if any(marker.casefold() in haystack for marker in LEGACY_ENTRY_MARKERS):
        return "legacy"
    return "unknown"


def classify_health_text(text: str) -> HealthKind:
    """Distinguish the AQSP API health contract from Streamlit's plain ``ok``."""
    compact = "".join(text.casefold().split())
    if '"ok":true' in compact and "aqsp-api" in compact:
        return "canonical"
    if compact == "ok" or "_stcore" in compact or "streamlit" in compact:
        return "legacy"
    return "unknown"


def render_legacy_redirect(*, target_url: str | None = None) -> str:
    """Render a small migration page instead of exposing a stale static UI."""
    target = _validate_url(
        target_url or public_dashboard_url(),
        setting_name="target_url",
    )
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
  <p><a href="{safe_target}">进入 AQSP 研究工作台</a></p>
  <script>window.location.replace({target!r});</script>
</body>
</html>
'''


def write_dashboard_artifact(output_path: Path, html_text: str) -> Path:
    """Write only canonical dashboard artifacts and preserve index migration."""
    if output_path.name not in CANONICAL_DASHBOARD_ARTIFACT_NAMES:
        allowed = ", ".join(sorted(CANONICAL_DASHBOARD_ARTIFACT_NAMES))
        raise ValueError(
            f"dashboard artifact must use one of: {allowed}; got {output_path.name!r}"
        )

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
