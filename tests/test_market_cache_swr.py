"""market._cached 的 stale-while-revalidate 契约测试。

把后台刷新打桩（不真起线程），锁定三条路径：
- 新鲜（age < _TTL）：直接返回，不重算、不触发刷新；
- 过期但在 _STALE_TTL 内：立即返回旧值，触发一次后台刷新；
- 超过 _STALE_TTL：同步重算（不服务过旧数据）。
"""
from __future__ import annotations

import os
import sys
import time

# backend/market.py 用的是**绝对** import（`import astock` / `import gstock`），
# 生产由 uvicorn 把 backend/ 放到 sys.path 上。因此不能按包 `backend.market` 导入
# （会 ModuleNotFoundError: No module named 'astock'），需照生产同样方式
# 先把 backend/ 加入 sys.path，再以顶层模块导入。
_BACKEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import market  # noqa: E402  —— 必须在补完 sys.path 之后导入


def test_fresh_returns_cached_without_recompute(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(market, "_REFRESHING", set())
    refreshes = []
    monkeypatch.setattr(
        market, "_refresh_in_background", lambda k, f, v: refreshes.append(k)
    )

    calls = []

    def fn():
        calls.append(1)
        return {"v": 1}

    # 首次：计算并写盘
    assert market._cached("k", fn) == {"v": 1}
    assert len(calls) == 1
    # 新鲜窗口内（强制 ts 很新）：直接返回，不再调用 fn，不触发刷新
    market._write_cache("k", {"v": 1})  # ts=now
    assert market._cached("k", fn) == {"v": 1}
    assert len(calls) == 1
    assert refreshes == []


def test_stale_returns_old_value_and_triggers_background_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(market, "_REFRESHING", set())
    refreshes = []
    monkeypatch.setattr(
        market, "_refresh_in_background", lambda k, f, v: refreshes.append(k)
    )

    # 手写一个「过期但在 stale 窗口内」的缓存：age 介于 _TTL 与 _STALE_TTL 之间
    old = {"ts": time.time() - (market._TTL + 100), "val": {"v": "stale"}}
    (tmp_path / "k.json").write_text(
        __import__("json").dumps(old), encoding="utf-8"
    )

    calls = []
    assert market._cached("k", lambda: calls.append(1) or {"v": "fresh"}) == {"v": "stale"}
    # 立即返回旧值，不阻塞（fn 未被同步调用）
    assert calls == []
    # 触发了一次后台刷新
    assert refreshes == ["k"]


def test_beyond_stale_recomputes_synchronously(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(market, "_REFRESHING", set())
    refreshes = []
    monkeypatch.setattr(
        market, "_refresh_in_background", lambda k, f, v: refreshes.append(k)
    )

    # 手写一个「超过 stale 窗口」的缓存：age > _STALE_TTL
    old = {"ts": time.time() - (market._STALE_TTL + 100), "val": {"v": "too_old"}}
    (tmp_path / "k.json").write_text(
        __import__("json").dumps(old), encoding="utf-8"
    )

    # 过旧不服务：同步重算，返回新值；且不触发后台刷新（已同步处理）
    assert market._cached("k", lambda: {"v": "fresh"}) == {"v": "fresh"}
    assert refreshes == []
