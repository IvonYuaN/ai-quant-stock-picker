"""Shared-entry wiring for the factor lifecycle and saved backtest configs."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as app_module
import factor_definitions as factor_defs
import strategy_backtests as backtests


def _client() -> TestClient:
    return TestClient(app_module.app)


def _new_factor() -> dict:
    return factor_defs.save_draft(
        factor_id=None,
        name="生命周期测试因子",
        hypothesis="验证人工闸门与 revision CAS",
        expression="amount / ts_mean(amount, 5)",
        expected_revision=None,
    )


def test_factor_lifecycle_endpoint_requires_explicit_human_confirmation():
    factor = _new_factor()
    response = _client().post(
        f"/api/aqsp/factor-definitions/{factor['id']}/lifecycle",
        json={
            "expected_revision": factor["revision"],
            "target_status": "watch",
            "evidence": {"human_confirmed": False, "actor": "tester", "manual_review_note": "未确认"},
        },
    )

    assert response.status_code == 422
    assert "人工显式确认" in response.json()["detail"]
    assert factor_defs.list_definitions()[0]["status"] == "draft"


def test_factor_lifecycle_endpoint_moves_draft_to_watch_with_cas():
    factor = _new_factor()
    response = _client().post(
        f"/api/aqsp/factor-definitions/{factor['id']}/lifecycle",
        json={
            "expected_revision": factor["revision"],
            "target_status": "watch",
            "evidence": {
                "human_confirmed": True,
                "actor": "复核人",
                "manual_review_note": "人工确认送观察，等待检验结论",
            },
        },
    )

    assert response.status_code == 200
    updated = response.json()["data"]
    assert updated["status"] == "watch"
    assert updated["revision"] == factor["revision"] + 1
    assert updated["lifecycle_history"][-1]["actor"] == "复核人"

    stale = _client().post(
        f"/api/aqsp/factor-definitions/{factor['id']}/lifecycle",
        json={
            "expected_revision": factor["revision"],
            "target_status": "watch",
            "evidence": {"human_confirmed": True, "actor": "复核人", "manual_review_note": "用旧版本重复提交"},
        },
    )
    assert stale.status_code == 409


def test_factor_lifecycle_endpoint_rejects_illegal_transition():
    factor = _new_factor()
    response = _client().post(
        f"/api/aqsp/factor-definitions/{factor['id']}/lifecycle",
        json={
            "expected_revision": factor["revision"],
            "target_status": "active",
            "evidence": {"human_confirmed": True, "actor": "复核人", "manual_review_note": "跳过观察直接启用"},
        },
    )

    assert response.status_code == 422
    assert "人工闸门" in response.json()["detail"]


def test_factor_definitions_payload_reports_human_gated_lifecycle():
    payload = _client().get("/api/aqsp/factor-definitions").json()["data"]

    assert payload["lifecycle_write_status"] == "human_gated_lifecycle"


def test_saved_config_endpoints_wire_to_module(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        backtests, "list_configs", lambda *a, **k: [{"config_id": "abc", "revision": 1, "name": "保存的参数"}]
    )
    monkeypatch.setattr(
        backtests, "save_config", lambda **kwargs: (calls.append(("save", kwargs)) or {"config_id": "abc", "revision": 1})
    )
    monkeypatch.setattr(
        backtests, "rerun_from_config", lambda **kwargs: (calls.append(("rerun", kwargs)) or {"outcome": "rejected", "config_id": "abc"})
    )
    monkeypatch.setattr(
        backtests, "delete_config", lambda *a, **k: (calls.append(("delete", a)) or True)
    )
    monkeypatch.setattr(
        backtests, "get_config", lambda config_id: {"config_id": config_id, "revision": 1}
    )
    monkeypatch.setattr(
        backtests, "save_config_from_run", lambda **kwargs: {"config_id": "abc", "revision": 1}
    )
    client = _client()

    listed = client.get("/api/aqsp/strategy-backtest-configs")
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["config_id"] == "abc"

    single = client.get("/api/aqsp/strategy-backtest-configs/abc")
    assert single.status_code == 200
    assert single.json()["data"]["config_id"] == "abc"

    from_run = client.post("/api/aqsp/strategy-backtest-configs/save-from-run", json={
        "run_id": "run-1", "name": "从已完成回测保存",
    })
    assert from_run.status_code == 200
    assert from_run.json()["data"]["config_id"] == "abc"

    saved = client.post("/api/aqsp/strategy-backtest-configs", json={
        "name": "保存的参数", "config": {"symbols": ["600000"]},
    })
    assert saved.status_code == 200
    assert saved.json()["data"]["config_id"] == "abc"

    rerun = client.post("/api/aqsp/strategy-backtest-configs/abc/rerun", json={"expected_revision": 1})
    assert rerun.status_code == 200
    assert rerun.json()["data"]["outcome"] == "rejected"

    deleted = client.delete("/api/aqsp/strategy-backtest-configs/abc?expected_revision=1")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert [call[0] for call in calls] == ["save", "rerun", "delete"]


def test_saved_config_endpoint_maps_conflict_to_409(monkeypatch):
    def conflict(**_kwargs):
        raise backtests.StrategyBacktestConfigConflict("配置已被其他操作更新")

    monkeypatch.setattr(backtests, "save_config", conflict)
    response = _client().post("/api/aqsp/strategy-backtest-configs", json={
        "name": "冲突", "config": {},
    })

    assert response.status_code == 409
    assert "配置已被其他操作更新" in response.json()["detail"]
