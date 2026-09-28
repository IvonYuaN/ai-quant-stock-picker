#!/usr/bin/env python3
"""Run frontend npm audit with a narrow AQSP runtime allowlist."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NPM_AUDIT_REGISTRY = "https://registry.npmjs.org"
ALLOWED_ADVISORY_URLS = {"https://github.com/advisories/GHSA-qwww-vcr4-c8h2"}
BLOCKING_SEVERITIES = {"high", "critical"}
ROUTER_ROUTE_KEYS = re.compile(r"\b(loader|action|clientAction|serverAction)\s*:")
ROUTER_RUNTIME_TERMS = (
    "createStaticHandler",
    "StaticRouter",
    "HydratedRouter",
    "ServerRouter",
    "RSCHydratedRouter",
    "RSCStaticRouter",
    "react-router/dom/server",
    "useFetcher",
    "useSubmit",
    "redirect(",
    "<Form",
)


@dataclass(frozen=True)
class AuditFinding:
    package: str
    severity: str
    title: str
    url: str
    allowed: bool
    reason: str


def _run_audit(frontend_dir: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "npm",
            "audit",
            "--prefix",
            str(frontend_dir),
            "--registry",
            NPM_AUDIT_REGISTRY,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    text = result.stdout.strip() or result.stderr.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"npm audit did not return JSON: {exc}: {text[:240]}") from exc
    if result.returncode not in (0, 1) or not isinstance(payload, dict):
        raise ValueError(f"npm audit failed: {text[:240]}")
    return payload


def _frontend_is_static_spa(frontend_dir: Path) -> tuple[bool, str]:
    source_dir = frontend_dir / "src"
    router_path = source_dir / "router.tsx"
    if not router_path.is_file():
        return False, "frontend/src/router.tsx missing"
    if ROUTER_ROUTE_KEYS.search(router_path.read_text(encoding="utf-8")):
        return False, "router defines loader/action data routes"
    for path in source_dir.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(term in text for term in ROUTER_RUNTIME_TERMS):
            return (
                False,
                f"{path.relative_to(frontend_dir)} uses server/data routing API",
            )
    return True, "static Vite SPA without React Router data/RSC/SSR APIs"


def evaluate_audit(payload: dict[str, Any], frontend_dir: Path) -> list[AuditFinding]:
    static_spa, static_reason = _frontend_is_static_spa(frontend_dir)
    vulnerabilities = payload.get("vulnerabilities", {})
    if not isinstance(vulnerabilities, dict):
        raise ValueError("npm audit vulnerabilities must be an object")
    findings: list[AuditFinding] = []
    for package, raw in sorted(vulnerabilities.items()):
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity") or "").lower()
        if severity not in BLOCKING_SEVERITIES:
            continue
        for via in raw.get("via", []):
            if not isinstance(via, dict):
                continue
            url = str(via.get("url") or "")
            allowed = (
                url in ALLOWED_ADVISORY_URLS
                and package in {"react-router", "react-router-dom"}
                and static_spa
            )
            findings.append(
                AuditFinding(
                    package=package,
                    severity=severity,
                    title=str(via.get("title") or package),
                    url=url,
                    allowed=allowed,
                    reason=static_reason
                    if allowed
                    else "unapproved frontend audit finding",
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_frontend_audit")
    parser.add_argument("--frontend-dir", type=Path, default=Path("frontend"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        findings = evaluate_audit(
            _run_audit(args.frontend_dir.resolve()), args.frontend_dir.resolve()
        )
    except (OSError, ValueError) as exc:
        print(f"frontend audit failed: {exc}", flush=True)
        return 1
    blocked = [item for item in findings if not item.allowed]
    if args.as_json:
        print(
            json.dumps(
                {
                    "ok": not blocked,
                    "registry": NPM_AUDIT_REGISTRY,
                    "findings": [item.__dict__ for item in findings],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif findings:
        for item in findings:
            print(
                f"{'ALLOW' if item.allowed else 'BLOCK'} {item.package} {item.severity} {item.url or item.title}: {item.reason}"
            )
    else:
        print("frontend audit passed: no high/critical vulnerabilities")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
