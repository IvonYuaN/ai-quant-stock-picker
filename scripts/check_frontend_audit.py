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
ALLOWED_ADVISORY_URLS = {
    "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
}
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
    payload_text = result.stdout.strip() or result.stderr.strip()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"npm audit did not return JSON: {exc}: {payload_text[:240]}")
    if result.returncode not in (0, 1):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise ValueError(f"npm audit failed: {message or payload_text[:240]}")
    if not isinstance(payload, dict):
        raise ValueError("npm audit JSON must be an object")
    return payload


def _frontend_is_static_spa(frontend_dir: Path) -> tuple[bool, str]:
    source_dir = frontend_dir / "src"
    router_path = source_dir / "router.tsx"
    if not router_path.is_file():
        return False, "frontend/src/router.tsx missing"
    router_text = router_path.read_text(encoding="utf-8")
    if ROUTER_ROUTE_KEYS.search(router_text):
        return False, "router defines loader/action data routes"
    for path in source_dir.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for term in ROUTER_RUNTIME_TERMS:
            if term in text:
                return False, f"{path.relative_to(frontend_dir)} uses {term}"
    return True, "static Vite SPA without React Router data/RSC/SSR APIs"


def _via_items(vulnerability: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in vulnerability.get("via", []):
        if isinstance(item, dict):
            items.append(item)
    return items


def _is_allowed_react_router_effect(
    package: str, vulnerability: dict[str, Any], is_static_spa: bool
) -> bool:
    via = vulnerability.get("via", [])
    return (
        package == "react-router-dom"
        and is_static_spa
        and isinstance(via, list)
        and via == ["react-router"]
    )


def evaluate_audit(payload: dict[str, Any], frontend_dir: Path) -> list[AuditFinding]:
    is_static_spa, static_reason = _frontend_is_static_spa(frontend_dir)
    findings: list[AuditFinding] = []
    vulnerabilities = payload.get("vulnerabilities", {})
    if not isinstance(vulnerabilities, dict):
        raise ValueError("npm audit vulnerabilities must be an object")
    for package, raw_vulnerability in sorted(vulnerabilities.items()):
        if not isinstance(raw_vulnerability, dict):
            continue
        severity = str(raw_vulnerability.get("severity") or "").lower()
        if severity not in BLOCKING_SEVERITIES:
            continue
        via = _via_items(raw_vulnerability)
        if _is_allowed_react_router_effect(package, raw_vulnerability, is_static_spa):
            findings.append(
                AuditFinding(
                    package=package,
                    severity=severity,
                    title="react-router transitive advisory",
                    url="https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
                    allowed=True,
                    reason=static_reason,
                )
            )
            continue
        if not via and raw_vulnerability.get("via"):
            via = [
                {
                    "title": str(raw_vulnerability.get("via")),
                    "url": "",
                    "severity": severity,
                }
            ]
        for item in via:
            title = str(item.get("title") or package)
            url = str(item.get("url") or "")
            allowed = (
                url in ALLOWED_ADVISORY_URLS
                and package in {"react-router", "react-router-dom"}
                and is_static_spa
            )
            reason = static_reason if allowed else "unapproved frontend audit finding"
            findings.append(
                AuditFinding(
                    package=package,
                    severity=severity,
                    title=title,
                    url=url,
                    allowed=allowed,
                    reason=reason,
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_frontend_audit")
    parser.add_argument("--frontend-dir", type=Path, default=Path("frontend"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    frontend_dir = args.frontend_dir.resolve()
    try:
        payload = _run_audit(frontend_dir)
        findings = evaluate_audit(payload, frontend_dir)
    except (OSError, ValueError) as exc:
        print(f"frontend audit failed: {exc}", flush=True)
        return 1
    blocked = [finding for finding in findings if not finding.allowed]
    if args.as_json:
        print(
            json.dumps(
                {
                    "ok": not blocked,
                    "registry": NPM_AUDIT_REGISTRY,
                    "findings": [finding.__dict__ for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif findings:
        for finding in findings:
            status = "ALLOW" if finding.allowed else "BLOCK"
            print(
                f"{status} {finding.package} {finding.severity} "
                f"{finding.url or finding.title}: {finding.reason}"
            )
    else:
        print("frontend audit passed: no high/critical vulnerabilities")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
