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
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import aqsp.briefing.closing_review as cr
import app as app_module

ROUTE = "/api/aqsp/ic-dual-verdict"


def _client() -> TestClient:
    return TestClient(app_module.app)


def _hours_ago_iso(hours: float) -> str:
    """now(UTC) − hours 的 UTC ISO `…Z`（与 producer `run_at` 同格式）。"""
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        "as_of_b": "2099-01-01",
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
    _write_latest(tmp_path, {"as_of_b": "2099-01-01", "streak_n": 3, "factors": []})
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    resp = _client().get(ROUTE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False  # 空 factors ⇒ 不算可用
    assert data["streak_n"] == 3


# ── 新鲜度护栏（M5 补：与 fetch 双窗段 quarantine 双保险，堵「静默失效≠健康」） ──

def test_ic_dual_verdict_fresh_run_at_is_available(
    monkeypatch, tmp_path
) -> None:
    """run_at 距今 2h（< 默认 36h）⇒ available=True、stale=False。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(2),
            "as_of_b": "2099-01-01",
            "streak_n": 5,
            "factors": [{"name": "momentum", "hit": False, "t_a": -0.3, "t_b": -2.1}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is True
    assert data["stale"] is False
    assert data["latest"]["factors"][0]["name"] == "momentum"


def test_ic_dual_verdict_stale_run_at_is_suppressed(
    monkeypatch, tmp_path
) -> None:
    """run_at 距今 48h（> 默认 36h）⇒ 自降 available=False + stale=True，
    但 latest 仍保留供观测（前端据此降级，不再把陈旧判决当「最新」）。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(48),
            "as_of_b": "2099-01-01",
            "streak_n": 5,
            "factors": [{"name": "momentum", "hit": False, "t_a": -0.3, "t_b": -2.1}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is False  # 陈旧 ⇒ 抑制
    assert data["stale"] is True
    assert data["latest"]["factors"][0]["name"] == "momentum"  # latest 仍在


def test_ic_dual_verdict_stale_overridable_via_env(
    monkeypatch, tmp_path
) -> None:
    """48h 产物 + DUAL_MAX_AGE_HOURS=60（放宽上限，与 fetch 侧同 env）⇒ 转新鲜 available=True。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(48),
            "as_of_b": "2099-01-01",
            "streak_n": 5,
            "factors": [{"name": "momentum", "hit": False, "t_a": -0.3, "t_b": -2.1}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))
    monkeypatch.setenv("DUAL_MAX_AGE_HOURS", "60")

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is True  # 48h < 60h ⇒ 不压制
    assert data["stale"] is False


def test_ic_dual_verdict_no_timestamp_not_suppressed(
    monkeypatch, tmp_path
) -> None:
    """缺 run_at/generated_at ⇒ 判不出龄（fail-safe，不压制）⇒ available=True、stale=False。"""
    _write_latest(
        tmp_path,
        {
            "as_of_b": "2099-01-01",
            "streak_n": 5,
            "factors": [{"name": "htf", "hit": True, "t_a": 2.4, "t_b": 2.6}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is True
    assert data["stale"] is False


# ── 数据最新日护栏（data_as_of：堵「run_at 新鲜 + 数据冻死」静默窗口） ──

def test_ic_dual_verdict_stale_as_of_b_is_suppressed(
    monkeypatch, tmp_path
) -> None:
    """as_of_b 远落后于阈值（源库冻死）+ 新鲜 run_at(2h) ⇒ 第(2)道命中：
    即便 run_at 不超龄，也因数据旧被抑制 ⇒ available=False + stale=True；latest 仍保留。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(2),
            "as_of_b": "2000-01-01",
            "streak_n": 5,
            "factors": [{"name": "momentum", "hit": False, "t_a": -0.3, "t_b": -2.1}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is False
    assert data["stale"] is True
    assert data["latest"]["as_of_b"] == "2000-01-01"  # latest 仍保留供观测


def test_ic_dual_verdict_fresh_as_of_b_is_available(
    monkeypatch, tmp_path
) -> None:
    """as_of_b 远在未来（数据最新日 >= 阈值）+ 新鲜 run_at(2h) ⇒ 两道均未命中：
    available=True + stale=False。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(2),
            "as_of_b": "2099-01-01",
            "streak_n": 5,
            "factors": [{"name": "momentum", "hit": False, "t_a": -0.3, "t_b": -2.1}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is True
    assert data["stale"] is False


def test_ic_dual_verdict_missing_as_of_b_not_suppressed(
    monkeypatch, tmp_path
) -> None:
    """缺 as_of_b（第(2)道判不出）⇒ fail-safe 不压制：available=True + stale=False
    （与缺时间戳同语义，不把「判不出」当「陈旧」）。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(2),
            "streak_n": 5,
            "factors": [{"name": "htf", "hit": True, "t_a": 2.4, "t_b": 2.6}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is True
    assert data["stale"] is False


def test_ic_dual_verdict_stale_as_of_b_overridable_via_env(
    monkeypatch, tmp_path
) -> None:
    """as_of_b 陈旧("2000-01-01") + DUAL_MAX_LAG_TRADING_DAYS=99999（放宽滞后上限）⇒
    阈值被推到极久远过去，as_of_b 不再落后 ⇒ 转新鲜 available=True + stale=False。"""
    _write_latest(
        tmp_path,
        {
            "run_at": _hours_ago_iso(2),
            "as_of_b": "2000-01-01",
            "streak_n": 5,
            "factors": [{"name": "momentum", "hit": False, "t_a": -0.3, "t_b": -2.1}],
        },
    )
    monkeypatch.setattr(cr, "_factor_ic_runtime_root", lambda: str(tmp_path))
    monkeypatch.setenv("DUAL_MAX_LAG_TRADING_DAYS", "99999")

    data = _client().get(ROUTE).json()["data"]
    assert data["available"] is True
    assert data["stale"] is False
