"""行业榜的两层脆弱性：上游偶发空 diff + 空结果被缓存 5 分钟。

prod 实测（2026-10-19）：同一分钟内 `/api/industry?top=21` 返回
`{"top":[],"bottom":[],"total":0}`（41 字节），而 `top=22/23/24` 正常返回
4000+ 字节；前端默认用的 `top=20` 当时正被一份空结果占着缓存。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
import astock

_EMPTY = {"top": [], "bottom": [], "total": 0}


def _rows(n: int = 3) -> dict:
    top = [
        {
            "rank": i + 1,
            "name": f"行业{i}",
            "change_pct": 3.0 - i,
            "code": f"BK{i:04d}",
            "up_count": 10,
            "down_count": 2,
        }
        for i in range(n)
    ]
    return {"top": top, "bottom": top[::-1], "total": n}


@pytest.fixture()
def no_sleep(monkeypatch):
    """重试间隔归零，测试不等真实 sleep。"""
    monkeypatch.setattr(astock, "_INDUSTRY_RETRY_SLEEP", 0)


# --------------------------------------------------------------------------
# astock.industry_comparison：空结果重试一次
# --------------------------------------------------------------------------


def test_returns_first_success_without_retry(monkeypatch, no_sleep) -> None:
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _rows()

    monkeypatch.setattr(astock, "_industry_comparison_once", fake)
    out = astock.industry_comparison(3)

    assert out["total"] == 3
    assert len(calls) == 1  # 成功就不重试


def test_retries_once_when_first_attempt_is_empty(monkeypatch, no_sleep) -> None:
    """东财偶发空 diff：第一次空、第二次正常 ⇒ 不该把面板判死。"""
    seq = [_EMPTY, _rows()]

    def fake(top_n):
        return seq.pop(0)

    monkeypatch.setattr(astock, "_industry_comparison_once", fake)
    out = astock.industry_comparison(3)
    assert out["total"] == 3


def test_gives_up_after_max_attempts(monkeypatch, no_sleep) -> None:
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _EMPTY

    monkeypatch.setattr(astock, "_industry_comparison_once", fake)
    out = astock.industry_comparison(3)

    assert out == _EMPTY
    assert len(calls) == astock._INDUSTRY_ATTEMPTS  # 有限次，不是死循环


# --------------------------------------------------------------------------
# /api/industry：空结果不缓存（否则抖动会污染 5 分钟）
# --------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    app_module._DC_CACHE.clear()
    yield TestClient(app_module.app)
    app_module._DC_CACHE.clear()


def test_endpoint_does_not_cache_empty_result(client, monkeypatch, no_sleep) -> None:
    seq = [_EMPTY, _rows(5)]

    def fake(top_n):
        return seq.pop(0)

    monkeypatch.setattr(astock, "industry_comparison", fake)

    first = client.get("/api/industry?top=5").json()
    assert first["data"]["total"] == 0

    # 源已恢复：必须立刻拿到真数据，而不是等 300s 缓存过期
    second = client.get("/api/industry?top=5").json()
    assert second["data"]["total"] == 5


def test_endpoint_caches_non_empty_result(client, monkeypatch, no_sleep) -> None:
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _rows(5)

    monkeypatch.setattr(astock, "industry_comparison", fake)

    client.get("/api/industry?top=5")
    client.get("/api/industry?top=5")
    assert len(calls) == 1  # 第二次命中缓存，不打上游


def test_endpoint_keeps_cache_key_per_top(client, monkeypatch, no_sleep) -> None:
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _rows(top_n)

    monkeypatch.setattr(astock, "industry_comparison", fake)

    client.get("/api/industry?top=5")
    client.get("/api/industry?top=6")
    assert calls == [5, 6]  # top 不同即不同 key，不能串味
