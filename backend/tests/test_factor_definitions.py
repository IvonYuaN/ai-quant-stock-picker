from __future__ import annotations

import json

import pytest

import factor_definitions
import tenant


@pytest.fixture
def isolated_factor_store(monkeypatch, tmp_path):
    monkeypatch.setattr(factor_definitions.pf, "CACHE_DIR", str(tmp_path))
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def test_factor_draft_saves_immutable_versions_with_compare_and_swap(isolated_factor_store):
    first = factor_definitions.save_draft(
        factor_id=None,
        name="成交额趋势",
        hypothesis="成交额相对自身近期均值上升时，后续截面强弱可能改善。",
        expression="amount / ts_mean(amount, 5)",
        expected_revision=None,
    )
    updated = factor_definitions.save_draft(
        factor_id=first["id"],
        name="成交额趋势",
        hypothesis="成交额相对自身近期均值上升时，后续截面强弱可能改善。",
        expression="amount / ts_mean(amount, 10)",
        expected_revision=1,
    )

    assert first["status"] == "draft"
    assert [version["version"] for version in updated["versions"]] == [1, 2]
    assert updated["versions"][0]["expression"] == "amount / ts_mean(amount, 5)"
    assert updated["versions"][0]["expression_sha256"] != updated["versions"][1]["expression_sha256"]
    assert updated["versions"][1]["fields"] == ["amount"]
    assert updated["versions"][1]["max_lookback"] == 10

    with pytest.raises(factor_definitions.FactorDefinitionConflict, match="已被更新"):
        factor_definitions.save_draft(
            factor_id=first["id"],
            name="成交额趋势",
            hypothesis="新假设",
            expression="rank(close)",
            expected_revision=1,
        )


def test_factor_definition_versions_and_trials_are_tenant_scoped(isolated_factor_store):
    token = tenant.current_tenant.set("local")
    try:
        item = factor_definitions.save_draft(
            factor_id=None,
            name="横截面动量",
            hypothesis="验证个股相对强弱是否与后续分层收益相关。",
            expression="rank(close)",
            expected_revision=None,
        )
        trial = {"trial_id": "trial-1", "evidence_status": "preview_only"}
        factor_definitions.append_trial(item["id"], 1, trial)
    finally:
        tenant.current_tenant.reset(token)

    token = tenant.current_tenant.set("other")
    try:
        assert factor_definitions.list_definitions() == []
        with pytest.raises(factor_definitions.FactorDefinitionConflict):
            factor_definitions.get_version(item["id"], 1)
    finally:
        tenant.current_tenant.reset(token)

    token = tenant.current_tenant.set("local")
    try:
        stored = factor_definitions.get_version(item["id"], 1)
        assert stored["version"]["trials"] == [trial]
    finally:
        tenant.current_tenant.reset(token)


def test_factor_definition_store_fails_closed_on_corruption(isolated_factor_store):
    path = isolated_factor_store / "factor_definitions.json"
    path.write_text(json.dumps({"schema_version": 2, "definitions": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="格式无效"):
        factor_definitions.list_definitions()

