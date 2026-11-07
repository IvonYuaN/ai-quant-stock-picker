"""Tests for GET /api/aqsp/data-coverage.

端点存在的意义：把「最新日线日期是否真的代表完整市场交易日」暴露出来。
``MAX(trade_date)`` 可能只被一只刷新过的票顶高，消费者据此当作 as-of 会把
残缺横截面喂给选股与回测。这里锁两件事：

1. 完整横截面 ⇒ usable=True 且返回 verified date；
2. 单票哨兵 ⇒ usable=False 且 verified 为 null（fail-closed）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

_SCHEMA = """
CREATE TABLE daily_qfq (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL,
    close_qfq REAL, volume REAL, amount REAL, close REAL
);
"""


def _make_db(path: Path, rows: list[tuple[str, str]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO daily_qfq(ts_code, trade_date, close) VALUES(?,?,1.0)", rows
        )


def _client(db: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("AQSP_SQLITE_DB_PATH", str(db))
    import app

    return TestClient(app.app)


def test_data_coverage_reports_complete_session(tmp_path, monkeypatch):
    db = tmp_path / "astocks_raw.db"
    _make_db(db, [("000001.SZ", "20261106"), ("600000.SH", "20261106")])

    response = _client(db, monkeypatch).get("/api/aqsp/data-coverage")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert data["market_max_date"] == "20261106"
    assert data["market_date_usable"] is True
    assert data["verified_market_date"] == "20261106"
    assert data["coverage_pct"] == 1.0


def test_data_coverage_fails_closed_when_only_one_symbol_is_current(tmp_path, monkeypatch):
    """单票哨兵不得被当成完整交易日。"""
    db = tmp_path / "astocks_raw.db"
    stale = [(f"{i:06d}.SZ", "20260101") for i in range(1, 20)]
    _make_db(db, [("000001.SZ", "20261106"), *stale])

    response = _client(db, monkeypatch).get("/api/aqsp/data-coverage")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["market_max_date"] == "20261106"
    assert data["market_date_usable"] is False
    assert data["verified_market_date"] is None
    assert data["coverage_pct"] < data["min_coverage_ratio"]


def test_data_coverage_excludes_index_rows(tmp_path, monkeypatch):
    """指数行不算市场横截面：三只票 + 一只指数，分母必须仍是 3。"""
    db = tmp_path / "astocks_raw.db"
    _make_db(
        db,
        [
            ("000001.SZ", "20261106"),
            ("600000.SH", "20261106"),
            ("300001.SZ", "20261106"),
            ("000001.SH", "20260101"),  # 上证指数，长期不更新
        ],
    )

    response = _client(db, monkeypatch).get("/api/aqsp/data-coverage")

    data = response.json()["data"]
    assert data["total_symbols"] == 3
    assert data["market_date_usable"] is True
