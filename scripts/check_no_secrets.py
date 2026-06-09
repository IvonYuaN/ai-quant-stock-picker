#!/usr/bin/env python3
"""Fail fast when obvious secrets are present in upload candidate files."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(".").resolve()

SKIP_DIRS = {
    ".git",
    "_external",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "logs",
    "reports",
    "dist",
    "build",
}

SKIP_PREFIXES = (
    "private_data/",
    "A股量化分析数据/",
    "data/archive/",
    "outputs/",
)

SKIP_EXACT = {
    "data/predictions.jsonl",
    "data/paper_trades.jsonl",
    "data/risk_state.json",
    "data/cache.db",
    "data/weight_history.jsonl",
}

SKIP_SUFFIXES = {
    ".pyc",
    ".db",
    ".sqlite",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
}

ALLOW_FILES = {
    "docs/secret-and-upload-policy.md",
    "docs/email-setup.md",
}

CONFIG_SUFFIXES = {
    ".env",
    ".example",
    ".ini",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}

TOKEN_PATTERNS = {
    "github_pat": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "github_ghp": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "github_oauth": re.compile(r"gho_[A-Za-z0-9]{20,}"),
    "gitee_access_token": re.compile(r"(?i)\baccess_token=[0-9a-f]{32,}\b"),
}

SECRET_KEYS = {
    "GITHUB_TOKEN",
    "GITEE_TOKEN",
    "TUSHARE_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "WECHAT_WEBHOOK_URL",
    "FEISHU_WEBHOOK_URL",
    "GENERIC_WEBHOOK_URL",
    "WEBHOOK_URL",
    "SMTP_PASSWORD",
    "AQSP_SMTP_PASSWORD",
    "AQSP_DEPLOY_HOST",
    "AQSP_DEPLOY_USER",
    "AQSP_DEPLOY_PATH",
    "AQSP_DEPLOY_SSH_KEY",
}

SAFE_SECRET_REFERENCES = (
    "${{ secrets.",
    "${{secrets.",
)


def relative_name(path: Path, root: Path = ROOT) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def iter_files(root: Path) -> list[Path]:
    root = root.resolve()
    paths: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        rel = relative_name(path, root)
        parts = set(Path(rel).parts)
        if parts & SKIP_DIRS:
            continue
        if rel in SKIP_EXACT or any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name == ".env":
            continue
        if rel in ALLOW_FILES:
            continue
        paths.append(path)
    return paths


def is_config_like(path: Path) -> bool:
    if path.name == ".env.example":
        return True
    return path.suffix.lower() in CONFIG_SUFFIXES


def find_non_empty_secret_assignments(path: Path, text: str) -> list[str]:
    if not is_config_like(path):
        return []

    findings: list[str] = []
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z0-9_]*(?:TOKEN|PASSWORD|WEBHOOK_URL))\s*[:=]\s*(.*)$", line)
        if match is None:
            continue
        key, raw_value = match.groups()
        value = raw_value.strip().strip("'\"")
        if any(value.startswith(prefix) for prefix in SAFE_SECRET_REFERENCES):
            continue
        if key in SECRET_KEYS and value and value not in {"...", "REPLACE_ME", "<secret>"}:
            findings.append(f"{relative_name(path)}:{line_no}: non-empty {key}")
    return findings


def main() -> int:
    findings: list[str] = []
    for path in iter_files(ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in TOKEN_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative_name(path)}: matched {name}")
        findings.extend(find_non_empty_secret_assignments(path, text))

    if findings:
        print("Secret scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
