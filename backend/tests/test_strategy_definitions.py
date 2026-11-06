from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
import strategy_definitions
import tenant


@pytest.fixture
def strategy_store(monkeypatch, tmp_path):
    monkeypatch.setattr(strategy_definitions.pf, "CACHE_DIR", tmp_path)
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _save(*, expected_revision: int | None = None):
    return strategy_definitions.save_strategy(
        strategy_id="ma_volume_breakout",
        name="均线放量突破",
        description="收盘站上 MA20 且量比大于阈值",
        expression="close > ma20 and volume_ratio > 1.5",
        enabled=True,
        expected_revision=expected_revision,
    )


def test_strategy_definitions_store_revision_hash_and_delete(strategy_store):
    created = _save()
    assert created["revision"] == 1
    assert created["requires_pit_price_basis"] is True
    assert created["max_lookback"] == 20
    assert len(created["expression_sha256"]) == 64
    assert strategy_definitions.list_strategies() == [created]

    updated = strategy_definitions.save_strategy(
        strategy_id=created["id"],
        name=created["name"],
        description=created["description"],
        expression="close > ma20 and volume_ratio > 2",
        enabled=False,
        expected_revision=1,
    )

    assert updated["revision"] == 2
    assert updated["enabled"] is False
    assert updated["expression_sha256"] != created["expression_sha256"]
    with pytest.raises(strategy_definitions.StrategyDefinitionConflict):
        _save(expected_revision=1)
    assert strategy_definitions.delete_strategy(created["id"], expected_revision=2)
    assert strategy_definitions.list_strategies() == []


def test_strategy_score_expression_is_validated_and_part_of_revision_hash(strategy_store):
    created = strategy_definitions.save_strategy(
        strategy_id="scored_strategy",
        name="评分策略",
        description="",
        expression="close > ma20",
        score_expression="rank(volume_ratio) + rank(ret_5)",
        enabled=True,
        expected_revision=None,
    )

    assert created["score_expression_fields"] == ["ret_5", "volume_ratio"]
    assert created["score_requires_pit_price_basis"] is True
    revised = strategy_definitions.save_strategy(
        strategy_id="scored_strategy",
        name="评分策略",
        description="",
        expression="close > ma20",
        score_expression="rank(volume_ratio)",
        enabled=True,
        expected_revision=1,
    )
    assert revised["revision"] == 2
    assert revised["expression_sha256"] != created["expression_sha256"]
    with pytest.raises(ValueError, match="numeric"):
        strategy_definitions.save_strategy(
            strategy_id="invalid_score",
            name="无效评分",
            description="",
            expression="close > ma20",
            score_expression="close > ma20",
            enabled=True,
            expected_revision=None,
        )


def test_strategy_definitions_are_tenant_scoped(strategy_store):
    created = _save()
    token = tenant.current_tenant.set("other-user")
    try:
        assert strategy_definitions.list_strategies() == []
        with pytest.raises(strategy_definitions.StrategyDefinitionConflict):
            _save(expected_revision=1)
    finally:
        tenant.current_tenant.reset(token)
    assert strategy_definitions.list_strategies() == [created]


@pytest.mark.parametrize(
    "expression",
    ["close", "future_return > 0", "close.shift(-1) > close", "1 > 0"],
)
def test_strategy_definitions_reject_unlisted_or_non_boolean_expression(
    strategy_store, expression
):
    with pytest.raises(ValueError):
        strategy_definitions.save_strategy(
            strategy_id="unsafe",
            name="无效策略",
            description="",
            expression=expression,
            enabled=True,
            expected_revision=None,
        )


def test_strategy_definitions_enforce_tenant_strategy_count(strategy_store, monkeypatch):
    monkeypatch.setattr(strategy_definitions, "MAX_STRATEGIES", 1)
    _save()
    with pytest.raises(strategy_definitions.StrategyDefinitionConflict, match="最多保存 1 个"):
        strategy_definitions.save_strategy(
            strategy_id="another_strategy",
            name="第二个策略",
            description="",
            expression="volume_ratio > 1",
            enabled=True,
            expected_revision=None,
        )


def test_strategy_definitions_api_persists_revisions_and_isolates_tenants(
    strategy_store,
):
    client = TestClient(app_module.app)
    owner = {"X-User-Id": "strategy-definition-owner"}
    other = {"X-User-Id": "strategy-definition-other"}
    payload = {
        "id": "ma_volume_breakout",
        "name": "均线放量突破",
        "description": "收盘站上 MA20 且量比大于阈值",
        "expression": "close > ma20 and volume_ratio > 1.5",
        "enabled": True,
    }

    options = client.get("/api/aqsp/strategy-definitions/options", headers=owner)
    assert options.status_code == 200
    assert "kdj_j" in options.json()["data"]["fields"]
    created = client.post("/api/aqsp/strategy-definitions", json=payload, headers=owner)
    assert created.status_code == 200
    assert created.json()["data"]["revision"] == 1
    assert client.get("/api/aqsp/strategy-definitions", headers=owner).json()["data"]["items"]
    assert client.get("/api/aqsp/strategy-definitions", headers=other).json()["data"]["items"] == []

    updated = client.post(
        "/api/aqsp/strategy-definitions",
        json={**payload, "enabled": False, "expected_revision": 1},
        headers=owner,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["revision"] == 2
    stale = client.post(
        "/api/aqsp/strategy-definitions",
        json={**payload, "expected_revision": 1},
        headers=owner,
    )
    assert stale.status_code == 409
    assert client.delete(
        "/api/aqsp/strategy-definitions/ma_volume_breakout?expected_revision=2",
        headers=owner,
    ).status_code == 200


def test_strategy_definitions_api_rejects_unsafe_expression(strategy_store):
    response = TestClient(app_module.app).post(
        "/api/aqsp/strategy-definitions",
        json={
            "id": "unsafe_strategy",
            "name": "Unsafe",
            "expression": "close.shift(-1) > close",
        },
    )
    assert response.status_code == 422
