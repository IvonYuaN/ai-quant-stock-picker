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


def _new_draft() -> dict:
    return factor_definitions.save_draft(
        factor_id=None,
        name="横截面动量",
        hypothesis="验证个股相对强弱是否与后续分层收益相关。",
        expression="rank(close)",
        expected_revision=None,
    )


def _to_watch(item: dict, note: str = "人工送观察，等待样本外检验") -> dict:
    return factor_definitions.transition_status(
        factor_id=item["id"],
        expected_revision=item["revision"],
        target_status="watch",
        evidence={"human_confirmed": True, "actor": "reviewer-a", "manual_review_note": note},
    )


def _diagnostic_run(family_id: str = "a" * 64) -> dict:
    return {
        "family_id": family_id,
        "status": "diagnostic_only",
        "generated_at": "2026-11-02T10:00:00+08:00",
        "blockers": [],
        "family_size": 2,
        "common_dates": 80,
        "human_review_required": True,
        "promotion_authorized": False,
        "oos_verified": False,
    }


def _active_evidence(note: str = "已人工核对 IC 与样本区间，确认可进入观察后启用") -> dict:
    return {"human_confirmed": True, "actor": "reviewer-b", "manual_review_note": note}


def test_factor_lifecycle_moves_draft_to_watch_to_active_to_retired_when_human_confirmed(isolated_factor_store):
    draft = _new_draft()
    assert draft["status"] == "draft"
    assert draft["revision"] == 1

    watch = _to_watch(draft)
    assert watch["status"] == "watch"
    assert watch["revision"] == 2
    assert len(watch["lifecycle_history"]) == 1
    first = watch["lifecycle_history"][0]
    assert first["from"] == "draft" and first["to"] == "watch"
    assert first["actor"] == "reviewer-a"
    assert "+08:00" in first["changed_at"]

    active = factor_definitions.transition_status(
        factor_id=watch["id"],
        expected_revision=watch["revision"],
        target_status="active",
        evidence=_active_evidence(),
    )
    assert active["status"] == "active"
    assert active["lifecycle_history"][-1]["evidence_kind"] == "manual_review"

    retired = factor_definitions.transition_status(
        factor_id=active["id"],
        expected_revision=active["revision"],
        target_status="retired",
        evidence={"human_confirmed": True, "actor": "reviewer-c", "manual_review_note": "因子失效，人工退役"},
    )
    assert retired["status"] == "retired"
    assert retired["revision"] == 4

    stored = factor_definitions.get_version(draft["id"], 1)["definition"]
    assert stored["status"] == "retired"
    assert [row["to"] for row in stored["lifecycle_history"]] == ["watch", "active", "retired"]


def test_factor_lifecycle_rejects_illegal_transitions_when_path_is_not_allowed(isolated_factor_store):
    draft = _new_draft()
    base = {"factor_id": draft["id"], "expected_revision": draft["revision"]}

    with pytest.raises(factor_definitions.FactorLifecycleError, match="不允许从"):
        factor_definitions.transition_status(
            **base, target_status="active", evidence=_active_evidence(),
        )
    with pytest.raises(factor_definitions.FactorLifecycleError, match="目标生命周期状态无效"):
        factor_definitions.transition_status(
            **base, target_status="enabled", evidence={"human_confirmed": True, "actor": "r"},
        )

    watch = _to_watch(draft)
    active = factor_definitions.transition_status(
        factor_id=watch["id"], expected_revision=watch["revision"],
        target_status="active", evidence=_active_evidence(),
    )
    retired = factor_definitions.transition_status(
        factor_id=active["id"], expected_revision=active["revision"],
        target_status="retired", evidence={"human_confirmed": True, "actor": "r"},
    )
    with pytest.raises(factor_definitions.FactorLifecycleError, match="不允许从「退役」"):
        factor_definitions.transition_status(
            factor_id=retired["id"], expected_revision=retired["revision"],
            target_status="watch", evidence={"human_confirmed": True, "actor": "r"},
        )


def test_factor_promotion_requires_evidence_and_blocks_automatic_promotion_when_unconfirmed(isolated_factor_store):
    draft = _new_draft()
    watch = _to_watch(draft)
    base = {"factor_id": watch["id"], "expected_revision": watch["revision"], "target_status": "active"}
    run = _diagnostic_run()
    factor_definitions.append_validation_run(run)

    with pytest.raises(factor_definitions.FactorLifecycleError, match="人工显式确认"):
        factor_definitions.transition_status(**base)
    # 完整检验报告也不能自动晋级（禁止自动晋级逻辑）
    with pytest.raises(factor_definitions.FactorLifecycleError, match="禁止依据检验结果自动晋级"):
        factor_definitions.transition_status(**base, evidence={
            "human_confirmed": False, "actor": "system", "validation_family_id": run["family_id"],
        })
    with pytest.raises(factor_definitions.FactorLifecycleError, match="缺少操作人"):
        factor_definitions.transition_status(**base, evidence={
            "human_confirmed": True, "manual_review_note": "具体人工确认记录内容",
        })
    with pytest.raises(factor_definitions.FactorLifecycleError, match="晋级为启用必须提供"):
        factor_definitions.transition_status(**base, evidence={
            "human_confirmed": True, "actor": "r", "manual_review_note": "好",
        })
    with pytest.raises(factor_definitions.FactorLifecycleError, match="不存在或已移出当前租户"):
        factor_definitions.transition_status(**base, evidence={
            "human_confirmed": True, "actor": "r", "validation_family_id": "f" * 64,
        })

    blocked = _diagnostic_run("b" * 64)
    blocked.update({"status": "blocked", "blockers": ["样本不足"]})
    factor_definitions.append_validation_run(blocked)
    with pytest.raises(factor_definitions.FactorLifecycleError, match="被阻断"):
        factor_definitions.transition_status(**base, evidence={
            "human_confirmed": True, "actor": "r", "validation_family_id": blocked["family_id"],
        })

    authorized = _diagnostic_run("c" * 64)
    authorized["promotion_authorized"] = True
    factor_definitions.append_validation_run(authorized)
    with pytest.raises(factor_definitions.FactorLifecycleError, match="异常地声明了晋级授权"):
        factor_definitions.transition_status(**base, evidence={
            "human_confirmed": True, "actor": "r", "validation_family_id": authorized["family_id"],
        })

    # 所有失败都不改状态、不递增 revision
    current = factor_definitions.list_definitions()[0]
    assert current["status"] == "watch"
    assert current["revision"] == watch["revision"]

    # 有效报告 + 人工确认才允许晋级
    promoted = factor_definitions.transition_status(**base, evidence={
        "human_confirmed": True, "actor": "r", "validation_family_id": run["family_id"],
    })
    assert promoted["status"] == "active"
    assert promoted["lifecycle_history"][-1]["evidence_kind"] == "validation_report"


def test_factor_lifecycle_is_tenant_scoped_when_other_tenant_tries_to_transition(isolated_factor_store):
    token = tenant.current_tenant.set("local")
    try:
        draft = _new_draft()
    finally:
        tenant.current_tenant.reset(token)

    token = tenant.current_tenant.set("other")
    try:
        assert factor_definitions.list_definitions() == []
        with pytest.raises(factor_definitions.FactorDefinitionConflict, match="已移出当前租户"):
            factor_definitions.transition_status(
                factor_id=draft["id"], expected_revision=draft["revision"],
                target_status="watch", evidence={"human_confirmed": True, "actor": "intruder"},
            )
    finally:
        tenant.current_tenant.reset(token)

    token = tenant.current_tenant.set("local")
    try:
        assert factor_definitions.list_definitions()[0]["status"] == "draft"
    finally:
        tenant.current_tenant.reset(token)


def test_factor_lifecycle_revision_cas_conflict_is_rejected_when_stale(isolated_factor_store):
    draft = _new_draft()
    watch = _to_watch(draft)  # revision 由 1 递增到 2
    with pytest.raises(factor_definitions.FactorDefinitionConflict, match="已被更新"):
        factor_definitions.transition_status(
            factor_id=watch["id"], expected_revision=1, target_status="active",
            evidence=_active_evidence(),
        )
    stored = factor_definitions.list_definitions()[0]
    assert stored["status"] == "watch"
    assert stored["revision"] == watch["revision"]


def test_factor_lifecycle_write_failure_leaves_state_intact_when_retry_succeeds(isolated_factor_store, monkeypatch):
    draft = _new_draft()
    original_save = factor_definitions._save
    calls = {"n": 0}

    def flaky_save(path: str, payload: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("模拟磁盘写入失败")
        original_save(path, payload)

    monkeypatch.setattr(factor_definitions, "_save", flaky_save)
    with pytest.raises(OSError):
        factor_definitions.transition_status(
            factor_id=draft["id"], expected_revision=draft["revision"], target_status="watch",
            evidence={"human_confirmed": True, "actor": "r", "manual_review_note": "送观察记录"},
        )
    monkeypatch.setattr(factor_definitions, "_save", original_save)

    stored = factor_definitions.list_definitions()[0]
    assert stored["status"] == "draft"
    assert stored["revision"] == draft["revision"]
    assert "lifecycle_history" not in stored

    retried = factor_definitions.transition_status(
        factor_id=draft["id"], expected_revision=draft["revision"], target_status="watch",
        evidence={"human_confirmed": True, "actor": "r", "manual_review_note": "送观察记录"},
    )
    assert retried["status"] == "watch"
    assert retried["revision"] == draft["revision"] + 1


def test_factor_validation_observation_records_drift_only_when_watch_or_active(isolated_factor_store):
    draft = _new_draft()
    observation = {
        "family_id": "d" * 64,
        "status": "diagnostic_only",
        "generated_at": "2026-11-02T10:00:00+08:00",
        "blockers": [],
        "family_size": 2,
        "common_dates": 80,
    }
    with pytest.raises(factor_definitions.FactorLifecycleError, match="只有观察或启用状态"):
        factor_definitions.record_validation_observation(
            factor_id=draft["id"], expected_revision=draft["revision"], observation=observation,
        )

    watch = _to_watch(draft)
    recorded = factor_definitions.record_validation_observation(
        factor_id=watch["id"], expected_revision=watch["revision"], observation=observation,
    )
    assert recorded["status"] == "watch"  # 不改生命周期状态
    assert recorded["last_validation"]["family_id"] == "d" * 64
    assert recorded["last_validation"]["promotion_authorized"] is False
    assert recorded["last_validation"]["human_review_required"] is True
    assert recorded["last_validation"]["oos_verified"] is False
    assert "+08:00" in recorded["last_validation"]["observed_at"]
    assert recorded["revision"] == watch["revision"] + 1

    with pytest.raises(factor_definitions.FactorDefinitionConflict, match="已被更新"):
        factor_definitions.record_validation_observation(
            factor_id=watch["id"], expected_revision=watch["revision"], observation=observation,
        )


def test_factor_validation_observation_rejects_malformed_payloads_when_fields_missing(isolated_factor_store):
    draft = _new_draft()
    watch = _to_watch(draft)
    malformed = [
        {},
        {"family_id": "short", "status": "diagnostic_only", "generated_at": "x"},
        {"family_id": "d" * 64, "status": "active", "generated_at": "x"},
        {"family_id": "d" * 64, "status": "blocked", "generated_at": ""},
    ]
    for payload in malformed:
        with pytest.raises(ValueError):
            factor_definitions.record_validation_observation(
                factor_id=watch["id"], expected_revision=watch["revision"], observation=payload,
            )
    assert "last_validation" not in factor_definitions.list_definitions()[0]


def test_factor_formula_edit_is_frozen_when_status_is_not_draft(isolated_factor_store):
    draft = _new_draft()
    _to_watch(draft)
    with pytest.raises(ValueError, match="请先人工降级回草稿"):
        factor_definitions.save_draft(
            factor_id=draft["id"], name="改名", hypothesis="改假设",
            expression="rank(close)", expected_revision=draft["revision"],
        )


def test_factor_definition_store_rejects_unknown_lifecycle_status_when_loaded(isolated_factor_store):
    path = isolated_factor_store / "factor_definitions.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "definitions": [{"id": "x", "status": "enabled", "revision": 1, "versions": []}],
        "validation_runs": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="未知生命周期状态"):
        factor_definitions.list_definitions()

