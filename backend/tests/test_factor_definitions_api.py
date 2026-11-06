from __future__ import annotations

import app as app_module
from fastapi.testclient import TestClient

import factor_definitions
import factor_validation
import factor_trials
import tenant
from aqsp.research.factor_expression import factor_expression_functions


def test_factor_definition_api_versions_drafts_and_appends_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(factor_definitions.pf, "CACHE_DIR", str(tmp_path))
    client = TestClient(app_module.app)
    created = client.post(
        "/api/aqsp/factor-definitions",
        json={
            "name": "横截面动量",
            "hypothesis": "收盘相对自身均线的位置可能区分后续截面强弱。",
            "expression": "rank(close / ts_mean(close, 5))",
        },
    )

    assert created.status_code == 200
    definition = created.json()["data"]
    assert definition["status"] == "draft"
    assert definition["versions"][0]["version"] == 1

    updated = client.post(
        "/api/aqsp/factor-definitions",
        json={
            "factor_id": definition["id"],
            "expected_revision": 1,
            "name": "横截面动量",
            "hypothesis": "收盘相对自身均线的位置可能区分后续截面强弱。",
            "expression": "rank(close / ts_mean(close, 10))",
        },
    )
    assert updated.status_code == 200
    assert [row["version"] for row in updated.json()["data"]["versions"]] == [1, 2]

    def preview(**kwargs):
        return {
            "trial_id": "trial-api-1",
            "evidence_status": "preview_only",
            "promotion_authorized": False,
            "expression": "rank(close / ts_mean(close, 10))",
        }

    monkeypatch.setattr("factor_trials.run_factor_trial", preview)
    trial = client.post(
        f"/api/aqsp/factor-definitions/{definition['id']}/versions/2/trial",
        json={
            "symbols": [f"{index:06d}" for index in range(20)],
            "signal_start": "2026-09-01",
            "signal_end": "2026-10-30",
            "holding_days": 3,
            "quantile": 5,
        },
    )
    assert trial.status_code == 200
    assert trial.json()["data"]["promotion_authorized"] is False
    listed = client.get("/api/aqsp/factor-definitions")
    assert listed.json()["data"]["lifecycle_write_status"] == "draft_only"
    assert listed.json()["data"]["items"][0]["versions"][1]["trials"][0]["trial_id"] == "trial-api-1"


def test_factor_definition_api_rejects_unsafe_formula_and_stale_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(factor_definitions.pf, "CACHE_DIR", str(tmp_path))
    client = TestClient(app_module.app)
    unsafe = client.post(
        "/api/aqsp/factor-definitions",
        json={
            "name": "bad",
            "hypothesis": "不执行任意代码。",
            "expression": "__import__('os').system('echo unsafe')",
        },
    )
    assert unsafe.status_code == 422

    created = client.post(
        "/api/aqsp/factor-definitions",
        json={"name": "f", "hypothesis": "h", "expression": "close"},
    )
    definition = created.json()["data"]
    first_update = client.post(
        "/api/aqsp/factor-definitions",
        json={
            "factor_id": definition["id"],
            "expected_revision": 1,
            "name": "f",
            "hypothesis": "h2",
            "expression": "close",
        },
    )
    assert first_update.status_code == 200
    stale = client.post(
        "/api/aqsp/factor-definitions",
        json={
            "factor_id": definition["id"],
            "expected_revision": 1,
            "name": "f",
            "hypothesis": "h2",
            "expression": "close",
        },
    )
    assert stale.status_code == 409


def test_factor_expression_editor_api_exposes_supported_catalog_and_checks_syntax():
    client = TestClient(app_module.app)
    catalog = client.get("/api/aqsp/factor-expression-catalog")
    assert catalog.status_code == 200
    assert catalog.json()["data"]["fields"] == sorted(factor_trials.TRIAL_FIELDS)
    assert {row["name"] for row in catalog.json()["data"]["functions"]} == {
        item.name for item in factor_expression_functions()
    }

    valid = client.post(
        "/api/aqsp/factor-expression-check",
        json={"expression": "rank(close / ts_mean(close, 5))"},
    )
    assert valid.status_code == 200
    assert valid.json()["data"] == {"valid": True, "fields": ["close"], "max_lookback": 5}

    invalid = client.post(
        "/api/aqsp/factor-expression-check", json={"expression": "rank(close"}
    )
    assert invalid.status_code == 422
    assert "line" in invalid.json()["detail"] and "column" in invalid.json()["detail"]

    unsupported = client.post(
        "/api/aqsp/factor-expression-check", json={"expression": "__import__('os')"}
    )
    assert unsupported.status_code == 422


def test_factor_validation_api_is_idempotent_and_tenant_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(factor_definitions.pf, "CACHE_DIR", str(tmp_path))
    report = {
        "family_id": "a" * 64,
        "target_trial_id": "trial-api-family",
        "generated_at": "2026-11-02T10:00:00+08:00",
        "status": "blocked",
        "items": [],
        "blockers": ["样本不足"],
        "human_review_required": True,
        "promotion_authorized": False,
        "oos_verified": False,
    }

    def validate(definitions, *, target_trial_id):
        trial_ids = {
            trial.get("trial_id")
            for definition in definitions
            for version in definition.get("versions", [])
            for trial in version.get("trials", [])
        }
        if target_trial_id not in trial_ids:
            raise factor_validation.FactorTrialNotFound("因子试算不存在或已移出当前租户")
        return report

    monkeypatch.setattr(factor_validation, "validate_factor_trial_family", validate)
    client = TestClient(app_module.app)

    def create_definition(user_id, trial_id):
        response = client.post(
            "/api/aqsp/factor-definitions",
            headers={"X-User-Id": user_id},
            json={"name": "f", "hypothesis": "h", "expression": "close"},
        )
        assert response.status_code == 200
        definition = response.json()["data"]
        token = tenant.current_tenant.set(tenant.resolve_tenant_id(user_id, ""))
        try:
            factor_definitions.append_trial(
                definition["id"], 1, {"trial_id": trial_id, "evidence_status": "preview_only"}
            )
        finally:
            tenant.current_tenant.reset(token)

    create_definition("factor-validation-owner", "trial-api-family")
    first = client.post(
        "/api/aqsp/factor-validations",
        headers={"X-User-Id": "factor-validation-owner"},
        json={"trial_id": "trial-api-family"},
    )
    repeated = client.post(
        "/api/aqsp/factor-validations",
        headers={"X-User-Id": "factor-validation-owner"},
        json={"trial_id": "trial-api-family"},
    )

    assert first.status_code == repeated.status_code == 200
    assert first.json()["data"] == repeated.json()["data"]
    assert first.json()["data"]["promotion_authorized"] is False
    owner_runs = client.get(
        "/api/aqsp/factor-definitions", headers={"X-User-Id": "factor-validation-owner"}
    ).json()["data"]["validation_runs"]
    other_runs = client.get(
        "/api/aqsp/factor-definitions", headers={"X-User-Id": "factor-validation-other"}
    ).json()["data"]["validation_runs"]
    assert [row["family_id"] for row in owner_runs] == ["a" * 64]
    assert other_runs == []


def test_factor_validation_api_returns_404_for_unknown_trial(monkeypatch, tmp_path):
    monkeypatch.setattr(factor_definitions.pf, "CACHE_DIR", str(tmp_path))
    client = TestClient(app_module.app)

    response = client.post(
        "/api/aqsp/factor-validations",
        headers={"X-User-Id": "factor-validation-missing"},
        json={"trial_id": "not-found"},
    )

    assert response.status_code == 404
