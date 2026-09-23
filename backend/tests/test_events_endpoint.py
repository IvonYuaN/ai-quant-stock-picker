"""Tests for GET /api/events (事件日历端点：解禁预警 + 近 5 日龙虎榜).

策略（与 test_catalyst_endpoint.py 同构）：
- tmp 目录写 pit_cache/{lockup,longhubang}.csv → monkeypatch AQSP_RUNTIME_DATA_ROOT
  → FastAPI TestClient 断言 200、字段与事件数；
- 缓存缺失 → 200 + 空结构（fail-soft，绝不 500）；
- 非法代码 → 400；
- 数值列缺失（NaN）→ 200 且该字段为 null（不是 NaN，也不会拖垮整个响应）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from aqsp.core.time import today_shanghai

_TODAY = today_shanghai()

# CSV 列与 dataclass 字段一致（lockup.LockupItem / longhubang.LongHubangItem）
LOCKUP_CSV_HEADER = "symbol,name,plan_date,lockup_shares,ratio,lockup_type"
LONGHUBANG_CSV_HEADER = (
    "trade_date,symbol,name,close_price,change_rate,"
    "buy_amount,sell_amount,net_amount,interpretation"
)


def _write_pit_cache(tmp_root: Path, lockup_rows: list[str], lhb_rows: list[str]) -> None:
    pit = tmp_root / "pit_cache"
    pit.mkdir(parents=True, exist_ok=True)
    (pit / "lockup.csv").write_text(
        "\n".join([LOCKUP_CSV_HEADER, *lockup_rows]) + "\n", encoding="utf-8"
    )
    (pit / "longhubang.csv").write_text(
        "\n".join([LONGHUBANG_CSV_HEADER, *lhb_rows]) + "\n", encoding="utf-8"
    )


def _default_rows() -> tuple[list[str], list[str]]:
    unlock_day = (_TODAY + timedelta(days=7)).isoformat()
    lhb_day = (_TODAY - timedelta(days=2)).isoformat()
    lockup_rows = [f"600000,某公司,{unlock_day},5000.0,0.05,定增"]
    lhb_rows = [f"{lhb_day},600000,某公司,10.5,5.2,8000.0,3000.0,5000.0,机构净买"]
    return lockup_rows, lhb_rows


def _try_import_app() -> Any:
    try:
        import sys

        # TestClient 依赖 httpx；缺失时路由集成测试应跳过而非报错。
        from fastapi.testclient import TestClient  # noqa: F401

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from backend.app import app  # noqa: F401

        return app
    except Exception as exc:  # noqa: BLE001 — 缺可选依赖，路由集成测试跳过
        return exc


_APP = _try_import_app()
_ROUTE_AVAILABLE = not isinstance(_APP, Exception)


def _client():
    from fastapi.testclient import TestClient

    return TestClient(_APP)


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过路由集成测试",
)
def test_events_endpoint_serves_events(tmp_path, monkeypatch):
    lockup_rows, lhb_rows = _default_rows()
    _write_pit_cache(tmp_path, lockup_rows, lhb_rows)
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

    resp = _client().get("/api/events?code=600000")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["symbol"] == "600000"
    assert data["as_of"] == _TODAY.isoformat()
    assert data["unlock_horizon_days"] == 30
    assert data["longhubang_lookback_days"] == 5
    assert data["has_unlock_data"] is True
    assert data["has_longhubang_data"] is True

    assert len(data["upcoming_unlocks"]) == 1
    unlock = data["upcoming_unlocks"][0]
    assert unlock["event_type"] == "lockup_expiry"
    assert unlock["days_until"] == 7
    assert unlock["severity"] == "medium"  # ratio=0.05 ∈ [3%, 10%)

    assert len(data["recent_longhubang"]) == 1
    lhb = data["recent_longhubang"][0]
    assert lhb["event_type"] == "longhubang"
    assert lhb["days_ago"] == 2
    assert lhb["net_amount"] == 5000.0


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过路由集成测试",
)
def test_events_endpoint_failsoft_when_cache_missing(tmp_path, monkeypatch):
    # 指向不含 pit_cache 的空目录：缺缓存 ≠ 没事件，但响应必须是 200 + 空结构。
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

    resp = _client().get("/api/events?code=600000")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["symbol"] == "600000"
    assert data["has_unlock_data"] is False
    assert data["has_longhubang_data"] is False
    assert data["upcoming_unlocks"] == []
    assert data["recent_longhubang"] == []


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过路由集成测试",
)
def test_events_endpoint_rejects_bad_code(monkeypatch):
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)

    for bad in ("60000", "6000001", "abc123", ""):
        resp = _client().get(f"/api/events?code={bad}")
        assert resp.status_code == 400, f"code={bad!r} 应返回 400"


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过路由集成测试",
)
def test_events_endpoint_handles_missing_numeric_cells(tmp_path, monkeypatch):
    # 数值列整列缺失时 pandas 给 NaN；JSON 序列化不得出现 NaN（allow_nan=False 会炸），
    # 应收敛成 null，且不影响其他事件正常返回。
    lockup_rows, _ = _default_rows()
    lhb_day = (_TODAY - timedelta(days=1)).isoformat()
    lhb_rows = [f"{lhb_day},600000,某公司,10.5,5.2,,,,"]

    _write_pit_cache(tmp_path, lockup_rows, lhb_rows)
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

    resp = _client().get("/api/events?code=600000")
    assert resp.status_code == 200
    body = resp.text
    assert "NaN" not in body, "响应体不得含字面 NaN"
    data = resp.json()["data"]
    assert len(data["recent_longhubang"]) == 1
    assert data["recent_longhubang"][0]["net_amount"] is None
