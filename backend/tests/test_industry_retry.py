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
# /api/industry：空结果只占 60 秒负缓存，不占满 5 分钟
# --------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    app_module._DC_CACHE.clear()
    yield TestClient(app_module.app)
    app_module._DC_CACHE.clear()


def _age_cache(key: tuple[str, str], seconds: float) -> None:
    """把某条缓存的时间戳往前推，模拟已经过了 N 秒（不真睡）。"""
    ts, val = app_module._DC_CACHE[key]
    app_module._DC_CACHE[key] = (ts - seconds, val)


def test_empty_result_does_not_hold_full_ttl(client, monkeypatch, no_sleep) -> None:
    """空结果按 60s 过期：源恢复后不该再等满 5 分钟。"""
    seq = [_EMPTY, _rows(5)]

    def fake(top_n):
        return seq.pop(0)

    monkeypatch.setattr(astock, "industry_comparison", fake)

    assert client.get("/api/industry?top=5").json()["data"]["total"] == 0

    # 刚过负缓存 TTL，必须重新取数并拿到真数据
    _age_cache(("industry", "5"), app_module._INDUSTRY_NEG_TTL + 1)
    assert client.get("/api/industry?top=5").json()["data"]["total"] == 5


def test_empty_result_still_negatively_cached(client, monkeypatch, no_sleep) -> None:
    """空结果仍进负缓存：源真故障时不能每次请求都透传上游。"""
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _EMPTY

    monkeypatch.setattr(astock, "industry_comparison", fake)

    client.get("/api/industry?top=5")
    client.get("/api/industry?top=5")
    assert len(calls) == 1  # 60s 内第二次命中负缓存


def test_non_empty_result_holds_full_ttl(client, monkeypatch, no_sleep) -> None:
    """非空结果照旧吃满 5 分钟，负缓存改动不能把正常路径也缩短。"""
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _rows(5)

    monkeypatch.setattr(astock, "industry_comparison", fake)

    client.get("/api/industry?top=5")
    # 已过 60s 负缓存 TTL，但非空结果应继续命中 300s 缓存
    _age_cache(("industry", "5"), app_module._INDUSTRY_NEG_TTL + 1)
    client.get("/api/industry?top=5")
    assert len(calls) == 1


def test_negative_ttl_is_shorter_than_normal(client) -> None:
    """常量关系本身要守住：负缓存必须比正常 TTL 短，否则这次修复就是空转。"""
    assert app_module._INDUSTRY_NEG_TTL < app_module._INDUSTRY_TTL


def test_endpoint_keeps_cache_key_per_top(client, monkeypatch, no_sleep) -> None:
    calls: list[int] = []

    def fake(top_n):
        calls.append(top_n)
        return _rows(top_n)

    monkeypatch.setattr(astock, "industry_comparison", fake)

    client.get("/api/industry?top=5")
    client.get("/api/industry?top=6")
    assert calls == [5, 6]  # top 不同即不同 key，不能串味
