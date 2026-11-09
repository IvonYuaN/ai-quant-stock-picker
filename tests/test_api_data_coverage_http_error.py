"""`/api/aqsp/data-coverage` 的失败语义。

端点内部用 SQLite 只读连接读库。WAL 模式下只读连接仍需要 ``-shm`` 的写权限，
拿不到时抛的是 ``sqlite3.OperationalError`` —— 它不是 ``OSError`` 子类，
原先的捕获元组漏了它，于是端点以 500 冒泡，被监控读成「服务崩了」，
而真实原因是「数据依赖不可读」。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("VR_DATA_DIR", "/tmp/aqsp-vibe-coverage-503-test-data")
os.environ.setdefault("VR_REPORTS_DIR", "/tmp/aqsp-vibe-coverage-503-test-reports")

import app as app_module  # noqa: E402


client = TestClient(app_module.app)


def test_data_coverage_when_sqlite_readonly_fails_returns_503(monkeypatch) -> None:
    """readonly 打不开库时必须 503，而不是未捕获异常导致的 500。"""
    import aqsp.data.daily_coverage as coverage_module
    import aqsp.data.source_factory as factory_module

    monkeypatch.setattr(
        factory_module, "resolve_sqlite_db_path", lambda: "/tmp/does-not-matter.db"
    )
    monkeypatch.setattr(
        coverage_module,
        "summarize_daily_coverage",
        lambda *_a, **_k: (_ for _ in ()).throw(
            sqlite3.OperationalError("attempt to write a readonly database")
        ),
    )

    resp = client.get("/api/aqsp/data-coverage")

    assert resp.status_code == 503
    detail = str(resp.json().get("detail", ""))
    assert "readonly database" in detail
