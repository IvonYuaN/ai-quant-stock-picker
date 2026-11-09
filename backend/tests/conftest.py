"""backend 测试全局夹具：隔离网络依赖。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "backend"))


@pytest.fixture(autouse=True)
def _disable_ex_right_guard(monkeypatch):
    """除权除息保护会额外拉一次复权行情：测试默认关闭，专项用例自行开启。"""
    import live_risk_service

    monkeypatch.setattr(live_risk_service, "EX_RIGHT_GUARD_ENABLED", False)
