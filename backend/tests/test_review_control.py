"""复盘控制面配置模型、文件读写、计划载荷与 API 端点的测试。

测试用独立 FastAPI 实例挂载 router，不依赖 app.py 是否已接线；
VR_DATA_DIR 指向 tmp_path，绝不触碰真实用户数据目录。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import review_control as rc
import review_control_api as api


@pytest.fixture
def tenant_dir(tmp_path):
    """一个已存在的租户目录。"""
    directory = tmp_path / "tenant"
    directory.mkdir()
    return directory


def _write_raw(tenant_dir, text):
    """直接写原始配置文本，用于模拟损坏文件。"""
    (tenant_dir / rc.SETTINGS_FILENAME).write_text(text, "utf-8")


def _valid_payload(**overrides):
    """一份完整合法的配置请求体。"""
    payload = {
        "enabled": True,
        "mode": "auto",
        "run_time": "15:30",
        "channels": ["inapp", "wecom"],
        "prompt": "每日复盘提示词",
        "retention_days": 30,
        "timezone": "Asia/Shanghai",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立挂载 router 的测试客户端，数据根隔离到 tmp_path。"""
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


# ---------- 模型与校验 ----------


def test_default_settings_are_valid():
    settings = rc.default_settings()
    settings.validate()
    assert settings.enabled is False
    assert settings.mode == "manual"
    assert settings.run_time == "15:30"
    assert settings.timezone == "Asia/Shanghai"
    assert settings.channels == []
    assert settings.prompt == ""
    assert settings.retention_days == 90
    assert rc.diff(settings, rc.default_settings()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_time", "25:99"),
        ("run_time", "9:00"),
        ("run_time", "15:60"),
        ("run_time", "15:3"),
        ("run_time", "24:00"),
        ("timezone", "UTC"),
        ("timezone", "America/New_York"),
        ("channels", ["slack"]),
        ("channels", ["inapp", "unknown"]),
        ("prompt", "x" * (rc.MAX_PROMPT_LENGTH + 1)),
        ("retention_days", 0),
        ("retention_days", -1),
        ("retention_days", rc.MAX_RETENTION_DAYS + 1),
        ("mode", "semi"),
    ],
)
def test_validate_rejects_invalid_field(field, value):
    settings = rc.default_settings().model_copy(update={field: value})
    with pytest.raises(rc.SettingsValidationError) as caught:
        settings.validate()
    assert field in caught.value.fields


def test_validate_lists_all_bad_fields():
    settings = rc.default_settings().model_copy(
        update={"run_time": "99:99", "timezone": "UTC", "mode": "semi", "channels": ["bad"]}
    )
    with pytest.raises(rc.SettingsValidationError) as caught:
        settings.validate()
    assert set(caught.value.fields) == {"run_time", "timezone", "mode", "channels"}
    assert "run_time" in str(caught.value)


def test_valid_non_default_settings_pass():
    settings = rc.ReviewSettings(**_valid_payload())
    settings.validate()
    assert settings.enabled is True
    assert settings.mode == "auto"


# ---------- 文件读写 ----------


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        "null",
        '{"enabled": "not_bool"}',
        '{"run_time": "99:99"}',
        '{"unknown_field": 1}',
    ],
)
def test_corrupt_settings_fail_closed(tenant_dir, raw):
    _write_raw(tenant_dir, raw)
    with pytest.raises(rc.SettingsCorruptError):
        rc.load_settings(tenant_dir)
    # 绝不静默回落：损坏文件原样保留，load 不写盘
    assert (tenant_dir / rc.SETTINGS_FILENAME).read_text("utf-8") == raw


def test_save_is_atomic_idempotent_and_overwrites(tenant_dir):
    first = rc.default_settings()
    rc.save_settings(tenant_dir, first)
    target = rc.settings_path(tenant_dir)
    original = target.read_bytes()
    assert json.loads(original) == first.model_dump()
    assert list(tenant_dir.glob("*.tmp")) == []

    # 覆盖幂等：重复写同样内容，字节完全一致
    rc.save_settings(tenant_dir, first.model_copy(deep=True))
    assert target.read_bytes() == original
    assert list(tenant_dir.glob("*.tmp")) == []

    # 覆盖生效：新内容落盘，旧内容被替换
    second = first.model_copy(update={"enabled": True, "retention_days": 7})
    rc.save_settings(tenant_dir, second)
    assert json.loads(target.read_bytes()) == second.model_dump()
    assert target.read_bytes() != original
    assert list(tenant_dir.glob("*.tmp")) == []


def test_save_rejects_invalid_settings_without_writing(tenant_dir):
    bad = rc.default_settings().model_copy(update={"run_time": "99:99"})
    with pytest.raises(rc.SettingsValidationError):
        rc.save_settings(tenant_dir, bad)
    assert list(tenant_dir.iterdir()) == []


def test_missing_tenant_dir_has_clear_semantics(tmp_path):
    missing = tmp_path / "no_such_tenant"
    with pytest.raises(rc.TenantDirectoryError):
        rc.load_settings(missing)
    with pytest.raises(rc.TenantDirectoryError):
        rc.save_settings(missing, rc.default_settings())
    assert not missing.exists()


def test_tenant_isolation(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    rc.save_settings(dir_a, rc.default_settings().model_copy(update={"enabled": True}))
    assert rc.load_settings(dir_a).enabled is True
    assert rc.load_settings(dir_b).enabled is False
    assert not (dir_b / rc.SETTINGS_FILENAME).exists()


def test_load_returns_default_when_file_absent(tenant_dir):
    assert rc.load_settings(tenant_dir) == rc.default_settings()


# ---------- diff ----------


def test_diff_reports_only_changed_fields():
    old = rc.default_settings()
    new = old.model_copy(update={"enabled": True, "retention_days": 30})
    changes = rc.diff(old, new)
    assert [c["field"] for c in changes] == ["enabled", "retention_days"]
    assert changes[0] == {"field": "enabled", "old": False, "new": True}
    assert changes[1] == {"field": "retention_days", "old": 90, "new": 30}


def test_diff_empty_when_unchanged():
    assert rc.diff(rc.default_settings(), rc.default_settings()) == []


def test_diff_covers_all_mutable_fields():
    old = rc.default_settings()
    new = rc.ReviewSettings(**_valid_payload(run_time="09:05"))
    fields = [c["field"] for c in rc.diff(old, new)]
    # timezone 两边相同，不应出现在 diff 里
    assert "timezone" not in fields
    assert set(fields) == set(rc.ReviewSettings.model_fields) - {"timezone"}


# ---------- 计划载荷 ----------


@pytest.mark.parametrize(
    ("builder", "action"),
    [(rc.plan_archive, "archive"), (rc.plan_delete, "delete")],
)
def test_plan_payload_requires_confirmation(builder, action):
    plan = builder(["r1", "r2"])
    assert plan["action"] == action
    assert plan["require_confirmation"] is True
    assert plan["executed"] is False
    assert plan["total"] == 2
    assert plan["pending"] == 2
    assert plan["rejected"] == 0
    for item in plan["items"]:
        assert item["require_confirmation"] is True
        assert item["status"] == "pending_confirmation"
        assert item["action"] == action


def test_plan_rejects_empty_ids():
    with pytest.raises(rc.PlanError):
        rc.plan_archive([])


def test_plan_marks_traversal_ids_rejected(tenant_dir):
    plan = rc.plan_delete(["ok_1", "../etc/passwd"], tenant_dir=tenant_dir)
    assert plan["executed"] is False
    assert plan["require_confirmation"] is True
    statuses = {item["id"]: item["status"] for item in plan["items"]}
    assert statuses["ok_1"] == "pending_confirmation"
    assert statuses["../etc/passwd"] == "rejected"
    assert plan["pending"] == 1
    assert plan["rejected"] == 1


def test_plan_does_not_touch_any_file(tenant_dir):
    keep = tenant_dir / "keep.txt"
    keep.write_text("keep", "utf-8")
    before = sorted(p.name for p in tenant_dir.iterdir())
    rc.plan_delete(["keep", "rpt_1"], tenant_dir=tenant_dir)
    after = sorted(p.name for p in tenant_dir.iterdir())
    assert before == after
    assert keep.read_text("utf-8") == "keep"


# ---------- 路径穿越 ----------


@pytest.mark.parametrize(
    "bad",
    ["../escape", "../../etc", "..", "a/b", "/etc", "users/../../x", "", "a.b", "abc..def"],
)
def test_resolve_tenant_dir_rejects_traversal(tmp_path, bad):
    with pytest.raises(rc.PathTraversalError):
        rc.resolve_tenant_dir(tmp_path, bad)


def test_resolve_tenant_dir_scopes_to_base(tmp_path):
    resolved = rc.resolve_tenant_dir(tmp_path, "u_abc123")
    assert resolved == (tmp_path / "users" / "u_abc123").resolve()
    assert tmp_path.resolve() in resolved.parents
    assert rc.resolve_tenant_dir(tmp_path, "local") == tmp_path.resolve()


@pytest.mark.parametrize("bad", ["../x", "../../etc/passwd", "a/b", "/etc", "..", ""])
def test_safe_report_path_rejects_traversal(tenant_dir, bad):
    with pytest.raises(rc.PathTraversalError):
        rc.safe_report_path(tenant_dir, bad)


def test_safe_report_path_within_tenant(tenant_dir):
    target = rc.safe_report_path(tenant_dir, "rpt_001")
    assert target.parent == tenant_dir.resolve()


# ---------- API 端点 ----------


def test_api_get_returns_default(client):
    response = client.get("/api/lab/review-control")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["exists"] is False
    assert data["settings"]["enabled"] is False
    assert data["settings"]["timezone"] == "Asia/Shanghai"


def test_api_put_returns_diff_and_persists(client, tmp_path):
    payload = _valid_payload()
    response = client.put("/api/lab/review-control", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["saved"] is True
    assert data["settings"] == payload
    assert {c["field"] for c in data["diff"]} == {
        "enabled", "mode", "channels", "prompt", "retention_days"
    }
    again = client.get("/api/lab/review-control").json()["data"]
    assert again["settings"] == payload
    assert again["exists"] is True
    assert (tmp_path / rc.SETTINGS_FILENAME).exists()


def test_api_put_invalid_returns_422_with_fields(client):
    response = client.put(
        "/api/lab/review-control", json=_valid_payload(run_time="99:99", timezone="UTC")
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert set(detail["fields"]) == {"run_time", "timezone"}


@pytest.mark.parametrize("action", ["archive", "delete"])
def test_api_post_plan(client, action):
    response = client.post(
        "/api/lab/review-control", json={"action": action, "report_ids": ["r1", "r2"]}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["action"] == action
    assert data["require_confirmation"] is True
    assert data["executed"] is False
    assert len(data["items"]) == 2


def test_api_post_rejects_unknown_action(client):
    response = client.post(
        "/api/lab/review-control", json={"action": "purge", "report_ids": ["r1"]}
    )
    assert response.status_code == 422


def test_api_post_marks_traversal_rejected(client):
    response = client.post(
        "/api/lab/review-control",
        json={"action": "delete", "report_ids": ["ok_1", "../secret"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["executed"] is False
    statuses = {item["id"]: item["status"] for item in data["items"]}
    assert statuses["../secret"] == "rejected"
    assert statuses["ok_1"] == "pending_confirmation"


def test_api_post_plan_never_deletes_files(client, tmp_path):
    keep = tmp_path / "keep.txt"
    keep.write_text("keep", "utf-8")
    response = client.post(
        "/api/lab/review-control", json={"action": "delete", "report_ids": ["keep", "r1"]}
    )
    assert response.status_code == 200
    assert keep.exists()
    assert keep.read_text("utf-8") == "keep"


def test_api_rejects_traversal_tenant(client, monkeypatch):
    monkeypatch.setattr(api, "_current_tenant_id", lambda: "../../etc")
    response = client.get("/api/lab/review-control")
    assert response.status_code == 400


def test_api_tenant_isolation(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_current_tenant_id", lambda: "tenant_a")
    response = client.put("/api/lab/review-control", json=_valid_payload())
    assert response.status_code == 200

    monkeypatch.setattr(api, "_current_tenant_id", lambda: "tenant_b")
    other = client.get("/api/lab/review-control").json()["data"]
    assert other["exists"] is False
    assert other["settings"]["enabled"] is False
    assert (tmp_path / "users" / "tenant_a" / rc.SETTINGS_FILENAME).exists()
    assert not (tmp_path / "users" / "tenant_b" / rc.SETTINGS_FILENAME).exists()


def test_api_corrupt_settings_fail_closed(client, tmp_path):
    broken = tmp_path / rc.SETTINGS_FILENAME
    broken.write_text("{broken", "utf-8")
    assert client.get("/api/lab/review-control").status_code == 409
    assert client.put("/api/lab/review-control", json=_valid_payload()).status_code == 409
    assert broken.read_text("utf-8") == "{broken"
