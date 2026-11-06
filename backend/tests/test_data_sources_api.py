from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as app_module


def test_data_sources_endpoint_returns_static_capabilities_without_auth_probe(monkeypatch):
    from aqsp.data import registry, source_health, source_readiness

    def inspect(entry, *, probe_auth: bool = False):
        assert probe_auth is False
        return SimpleNamespace(
            auth_kind="none",
            auth_status="not_required",
            auth_message="无需登录或 token。",
            auth_checked_at="",
            active_probe=False,
            workload_fit={
                "live_short": "fallback",
                "walkforward": "avoid",
                "pit": "avoid",
            },
        )

    monkeypatch.setattr(source_readiness, "inspect_source_readiness", inspect)
    monkeypatch.setattr(
        registry,
        "local_data_status",
        lambda _entry, *, project_root=None: "not_required",
    )
    monkeypatch.setattr(
        source_health,
        "read_source_health",
        lambda: {
            "sources": {
                "tencent": {
                    "successes": 4,
                    "failures": 1,
                    "last_success": "2026-10-31T09:00:00+08:00",
                    "last_failure": "2026-10-31T08:00:00+08:00",
                }
            }
        },
    )

    response = TestClient(app_module.app).get("/api/aqsp/data-sources")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["probe_auth"] is False
    assert payload["workloads"] == ["live_short", "walkforward", "pit"]
    tencent = next(item for item in payload["sources"] if item["id"] == "tencent")
    assert tencent["supports_daily"] is True
    assert tencent["workload_fit"]["live_short"] == "fallback"
    assert tencent["local_status"] == "not_required"
    assert tencent["health"]["successes"] == 4
    assert tencent["auth"]["active_probe"] is False
