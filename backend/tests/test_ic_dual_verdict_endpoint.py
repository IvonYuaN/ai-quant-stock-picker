"""双窗因子 IC 判决只读端点契约测试（GET /api/aqsp/ic-dual-verdict）。

监控面（方案 B §六 判据的每日滚动版）：读 runner 回流的
``dual_window_latest.json``，fail-soft 绝不 500。

契约要点：
- 产物存在且含 ``factors`` ⇒ ``available=True``、``latest`` 带内容、``streak_n`` 取产物值。
- 产物缺失（尚未启用/未回流）⇒ ``available=False``、``latest=None``、默认 ``streak_n=5``，仍 200。
- 产物读失败/字段残缺（损坏 JSON）⇒ 同上降级，绝不 500。

测试用 monkeypatch 把 ``aqsp.briefing.closing_review._factor_ic_runtime_root`` 指到
tmp_path 下的 runtime 根（端点是函数体内 ``from ... import``，patch 模块属性即生效），
不依赖真实 pit_cache，CI/本地确定性通过。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import aqsp.briefing.closing_review as cr
import app as app_module

ROUTE = "/api/aqsp/ic-dual-verdict"


def _client() -> TestClient:
    return TestClient(app_module.app)


def _write_latest(root_path, payload: dict) -> None:
    """在 monkeypatch 后的 runtime 根下写出双窗产物。"""
    from pathlib import Path

    target = Path(root_path) / "pit_cache" / "factor_ic"
    target.mkdir(parents=True, exist_ok=True)
    (target / "dual_window_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_ic_dual_verdict_available_when_artifact_present(
    monkeypatch, tmp_path
) -> None:
    """产物存在且含 factors ⇒ available=True、latest 带内容、streak_n 取产物值。"""
    payload = {
        "as_of_b": "2026-09-24",
        "streak_n": 7,
        "factors": [
            {"name": "momentum", "hit": False, "t_a": -2.5, "t_b": -2.1},
            {"name": "htf", "hit": True, "t_a": 2.4, "t_b": 2.6},
        ],
    }
    _write_latest(tmp_path, payload)
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    resp = _client().get(ROUTE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["latest"]["factors"][1]["name"] == "htf"
    assert data["streak_n"] == 7


def test_ic_dual_verdict_fail_soft_when_artifact_absent(
    monkeypatch, tmp_path
) -> None:
    """产物缺失 ⇒ available=False、latest=None、默认 streak_n=5，仍 200 不 500。"""
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    resp = _client().get(ROUTE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert data["latest"] is None
    assert data["streak_n"] == 5


def test_ic_dual_verdict_fail_soft_when_artifact_corrupt(
    monkeypatch, tmp_path
) -> None:
    """产物是损坏 JSON ⇒ 降级 available=False、latest=None，仍 200 不 500。"""
    target = tmp_path / "pit_cache" / "factor_ic"
    target.mkdir(parents=True)
    (target / "dual_window_latest.json").write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    resp = _client().get(ROUTE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert data["latest"] is None


def test_ic_dual_verdict_fail_soft_when_artifact_empty_factors(
    monkeypatch, tmp_path
) -> None:
    """产物存在但 factors 为空 ⇒ available=False（空不视为有数据），streak_n 取产物值。"""
    _write_latest(tmp_path, {"as_of_b": "2026-09-24", "streak_n": 3, "factors": []})
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    resp = _client().get(ROUTE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False  # 空 factors ⇒ 不算可用
    assert data["streak_n"] == 3
