"""Tests for GET /api/aqsp/signals（历史信号列表：复盘页数据源）。

策略（与 test_events_endpoint.py 同构）：
- tmp 目录写 predictions.jsonl → monkeypatch AQSP_LEDGER → FastAPI TestClient 断言
  200、字段映射（id / strategies / reasons / win / return_pct）与新→旧顺序；
- limit 越界 → 422；
- 台账缺失 → 200 + 空列表（fail-soft，绝不 500）；
- 未结算（pending）信号 → win/return_pct 为 null（不冒充"已出结果"）。

⚠️ 每条信号的 `id` 是台账行自身的 uuid —— 复盘记录的 `signal_id` 引用的就是它，
   两者必须同源（否则复盘和信号对不上号）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

LEDGER_ROWS = [
    # 最旧（应排在最后）—— 已结算：有 win / return_pct
    {
        "id": "sig-old",
        "signal_date": "2026-09-01",
        "symbol": "600519",
        "name": "贵州茅台",
        "rating": "A",
        "score": 18.5,
        "status": "validated",
        "strategies": "momentum,triple_rise",
        "reasons": ["动量突破", "三日连涨"],
        "win": True,
        "return_pct": 3.21,
    },
    # 最新（应排最前）—— 未结算：win / return_pct 必须为 null
    {
        "id": "sig-new",
        "signal_date": "2026-09-22",
        "symbol": "000001",
        "name": "平安银行",
        "rating": "B",
        "score": 12.0,
        "status": "pending",
        "strategies": "momentum",
        "reasons": [],
        "win": None,
        "return_pct": None,
    },
]


@pytest.fixture()
def ledger_file(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "predictions.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in LEDGER_ROWS) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_LEDGER", str(p))
    return p


def test_signals_endpoint_maps_ledger_fields(ledger_file) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    r = client.get("/api/aqsp/signals?limit=10")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["count"] == 2
    signals = data["signals"]
    # 新→旧
    assert [s["id"] for s in signals] == ["sig-new", "sig-old"]
    newest = signals[0]
    assert newest["signal_date"] == "2026-09-22"
    assert newest["symbol"] == "000001"
    assert newest["status"] == "pending"
    assert newest["win"] is None and newest["return_pct"] is None
    assert newest["strategies"] == ["momentum"]
    assert newest["reasons"] == []
    settled = signals[1]
    assert settled["win"] is True
    assert settled["return_pct"] == 3.21
    assert settled["strategies"] == ["momentum", "triple_rise"]
    assert settled["reasons"] == ["动量突破", "三日连涨"]


def test_signals_endpoint_limit_and_since(ledger_file) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    r = client.get("/api/aqsp/signals?limit=1&since=2026-09-10")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["count"] == 1
    assert data["signals"][0]["id"] == "sig-new"


def test_signals_endpoint_rejects_out_of_range_limit(ledger_file) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    assert client.get("/api/aqsp/signals?limit=0").status_code == 422
    assert client.get("/api/aqsp/signals?limit=1001").status_code == 422


def test_signals_endpoint_missing_ledger_is_fail_soft(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AQSP_LEDGER", str(tmp_path / "nope.jsonl"))
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    r = client.get("/api/aqsp/signals")
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 0
