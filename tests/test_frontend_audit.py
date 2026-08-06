from __future__ import annotations

from pathlib import Path

from scripts.check_frontend_audit import evaluate_audit


REACT_ROUTER_ADVISORY = {
    "source": 1124282,
    "name": "react-router",
    "dependency": "react-router",
    "title": "React Router: RSC Mode CSRF Bypass Allows Action Execution Before 400 Response",
    "url": "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
    "severity": "high",
    "range": ">=7.12.0 <8.3.0",
}


def _write_static_frontend(root: Path, router_text: str | None = None) -> Path:
    frontend = root / "frontend"
    source = frontend / "src"
    source.mkdir(parents=True)
    (source / "router.tsx").write_text(
        router_text
        or "import { createBrowserRouter } from 'react-router-dom';\n"
        "export const router = createBrowserRouter([{ path: '/', element: <div /> }]);\n",
        encoding="utf-8",
    )
    return frontend


def test_frontend_audit_allows_known_react_router_rsc_when_static_spa(
    tmp_path: Path,
) -> None:
    frontend = _write_static_frontend(tmp_path)
    payload = {
        "vulnerabilities": {
            "react-router": {
                "severity": "high",
                "via": [REACT_ROUTER_ADVISORY],
            },
            "react-router-dom": {
                "severity": "high",
                "via": ["react-router"],
            },
        }
    }

    findings = evaluate_audit(payload, frontend)

    assert findings
    assert all(finding.allowed for finding in findings)
    assert {finding.package for finding in findings} == {
        "react-router",
        "react-router-dom",
    }


def test_frontend_audit_blocks_known_react_router_rsc_when_data_route_present(
    tmp_path: Path,
) -> None:
    frontend = _write_static_frontend(
        tmp_path,
        "import { createBrowserRouter } from 'react-router-dom';\n"
        "export const router = createBrowserRouter([{ path: '/', loader: async () => null, element: <div /> }]);\n",
    )
    payload = {
        "vulnerabilities": {
            "react-router": {
                "severity": "high",
                "via": [REACT_ROUTER_ADVISORY],
            }
        }
    }

    findings = evaluate_audit(payload, frontend)

    assert findings
    assert not findings[0].allowed
    assert "unapproved" in findings[0].reason


def test_frontend_audit_blocks_unapproved_high_dependency(tmp_path: Path) -> None:
    frontend = _write_static_frontend(tmp_path)
    payload = {
        "vulnerabilities": {
            "postcss": {
                "severity": "high",
                "via": [
                    {
                        "title": "PostCSS path traversal",
                        "url": "https://github.com/advisories/GHSA-r28c-9q8g-f849",
                    }
                ],
            }
        }
    }

    findings = evaluate_audit(payload, frontend)

    assert findings
    assert findings[0].package == "postcss"
    assert not findings[0].allowed
