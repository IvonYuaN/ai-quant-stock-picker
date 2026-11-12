"""GET /api/market-risk 的契约测试。

锁住三件事：
1. 正常路径返回 200 + 完整字段，且 `usage=risk_warning_only`
   —— 防止消费方（前端/日报）把它当 alpha 信号用；
2. 库不可用 / 样本不足时**fail-soft**（available=False），不 500；
3. `as_of` 缺省走库内最新交易日。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np


def _make_db(path: Path, n_days: int = 320, n_symbols: int = 400, drift: float = -0.01) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT, close REAL)")
    rows = []
    for s in range(n_symbols):
        px = 10.0
        for d in range(n_days):
            px *= 1 + drift + 0.002 * np.sin((d + s) / 3.0)
            rows.append((f"{600000 + s}.SH", f"2026{(d // 28) + 1:02d}{d % 28 + 1:02d}", px))
    con.executemany("INSERT INTO daily_qfq VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def test_market_risk_endpoint_returns_snapshot(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    db = tmp_path / "down.db"
    _make_db(db)
    monkeypatch.setattr(
        "aqsp.data.source_factory.resolve_sqlite_db_path", lambda: str(db)
    )

    resp = TestClient(app_module.app).get("/api/market-risk")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["level"] in {"low", "medium", "high"}
    assert data["usage"] == "risk_warning_only", "必须声明只做风险提示"
    assert data["trend_20"] < 0  # 构造的是下跌序列
    assert data["reasons"], "高风险必须给出原因"
    assert data["n_symbols"] >= 300


def test_market_risk_endpoint_fail_soft_when_db_missing(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    monkeypatch.setattr("aqsp.data.source_factory.resolve_sqlite_db_path", lambda: None)
    resp = TestClient(app_module.app).get("/api/market-risk")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False


def test_market_risk_endpoint_fail_soft_when_sample_too_small(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import app as app_module

    db = tmp_path / "tiny.db"
    _make_db(db, n_days=40, n_symbols=400)
    monkeypatch.setattr(
        "aqsp.data.source_factory.resolve_sqlite_db_path", lambda: str(db)
    )
    resp = TestClient(app_module.app).get("/api/market-risk")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False
