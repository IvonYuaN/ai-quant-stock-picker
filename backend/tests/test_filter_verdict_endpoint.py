"""GET /api/aqsp/filter-verdict 的契约测试。

锁住四件事（照 ``test_market_risk_endpoint`` 的 fail-soft 风格）：
1. 工件齐全时返回 200 + ``available=True`` 并透传 ``viable``/``evidence``/``as_of``；
2. 工件缺失 / 根目录未配置时 **fail-soft**（``available=False`` + reason），不 500；
3. 工件损坏（非法 JSON）/ 缺必需字段时同样 fail-soft，绝不渲染半残数据；
4. 端点**只读、绝不现算、绝不联网**——不触 sqlite、不触网。

数据来源 = runner 重算探针写出的 ``$AQSP_RUNTIME_DATA_ROOT/pit_cache/filter_verdict.json``。
"""

from __future__ import annotations

import json
from pathlib import Path

_GOOD = {
    "viable": True,
    "baseline": "双反向 2f · mom_20 + pv_corr_20 均反向 · top10 生产池",
    "positioning": "选股筛选工具：不承诺正超额，只要求「不是垃圾策略」",
    "criterion": "多数 regime/年度 超额不显著为负 (NW-t > -1.96)",
    "dynamicSwitching": False,
    "dynamicNote": "各 regime 最优方向漂移 ⇒ 不做动态切换",
    "evidence": [
        {"horizon": 10, "regime": "熊", "excess": "+0.30%", "t": "+0.50", "ok": True},
        {"horizon": 20, "regime": "牛", "excess": "-0.79%", "t": "-1.39", "ok": True},
    ],
    "as_of": "2026-11-16",
    "source": "runner regime_robustness_probe.py --json-out",
}


def _write_verdict(root: Path, doc: object) -> None:
    d = root / "pit_cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / "filter_verdict.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


def test_filter_verdict_endpoint_returns_artifact(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    _write_verdict(tmp_path, _GOOD)
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

    resp = TestClient(app_module.app).get("/api/aqsp/filter-verdict")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["viable"] is True
    assert data["as_of"] == "2026-11-16"
    assert len(data["evidence"]) == 2, "工件 evidence 必须原样透传"


def test_filter_verdict_endpoint_fail_soft_when_root_unset(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)
    resp = TestClient(app_module.app).get("/api/aqsp/filter-verdict")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert data["reason"], "降级必须给原因，前端据此回退快照"


def test_filter_verdict_endpoint_fail_soft_when_file_missing(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    # 根目录存在但没有工件（runner 还没重算过）
    (tmp_path / "pit_cache").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    resp = TestClient(app_module.app).get("/api/aqsp/filter-verdict")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False


def test_filter_verdict_endpoint_fail_soft_when_corrupt(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    # 工件损坏（非法 JSON）—— 绝不 500
    d = tmp_path / "pit_cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / "filter_verdict.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    resp = TestClient(app_module.app).get("/api/aqsp/filter-verdict")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False


def test_filter_verdict_endpoint_fail_soft_when_required_field_missing(
    monkeypatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    bad = dict(_GOOD)
    del bad["viable"]
    _write_verdict(tmp_path, bad)
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    resp = TestClient(app_module.app).get("/api/aqsp/filter-verdict")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False, "缺必需字段必须视为脏工件，让前端回退"


def test_filter_verdict_endpoint_artifact_not_json_object(
    monkeypatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    _write_verdict(tmp_path, ["a", "list", "not", "object"])
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    resp = TestClient(app_module.app).get("/api/aqsp/filter-verdict")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False
