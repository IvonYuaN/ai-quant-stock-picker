"""多用户隔离：不同租户（API Key / X-User-Id）的持仓与研报互不串号。

回归背景：原 portfolio.py / myreports.py 用服务端单文件（~/.vibe-research 下），
一旦站点被多人访问（共用一把 VR_API_KEY）就会互相看到/覆盖对方数据。
现按请求级租户切分目录：
- local 租户（未设 VR_API_KEY 的单用户部署）→ 沿用原始根路径，向后兼容旧数据；
- 设了 VR_API_KEY → 每个不同 Key 一个租户目录；同一 Key 下带 X-User-Id 再细分。
"""

from __future__ import annotations

import tenant as _tenant
import portfolio as pf
import myreports as mr
import astock


def _with_tenant(tid, fn):
    tok = _tenant.current_tenant.set(tid)
    try:
        return fn()
    finally:
        _tenant.current_tenant.reset(tok)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(mr, "_DATA_DIR", tmp_path)
    pf._MIGRATED_TENANTS.clear()
    mr._MIGRATED_TENANTS.clear()
    monkeypatch.setattr(
        astock, "tencent_quote", lambda codes: {c: {"price": 10.0} for c in codes}
    )


def test_portfolio_isolated_across_tenants(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _with_tenant("k_a", lambda: pf.add_holding("600519", 100, 8.0))
    _with_tenant("k_b", lambda: pf.add_holding("000001", 50, 5.0))

    a = _with_tenant("k_a", pf.get_portfolio)
    b = _with_tenant("k_b", pf.get_portfolio)
    assert [h["code"] for h in a["holdings"]] == ["600519"]
    assert [h["code"] for h in b["holdings"]] == ["000001"]
    # 文件确实落在各自 users/<tid>/ 目录，互不串号
    assert (tmp_path / "users" / "k_a" / "portfolio.json").exists()
    assert (tmp_path / "users" / "k_b" / "portfolio.json").exists()


def test_local_tenant_uses_root_path_backward_compat(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _with_tenant("local", lambda: pf.add_holding("600519", 1, 8.0))
    # local 租户不进 users/ 子目录，直接落在 CACHE_DIR 根（兼容旧版 ~/.vibe-research/portfolio.json）
    assert (tmp_path / "portfolio.json").exists()
    assert not (tmp_path / "users").exists()


def test_myreports_isolated_across_tenants(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    b64 = "aGVsbG8="  # "hello"
    _with_tenant("k_a", lambda: mr.save_report("a.txt", b64))
    _with_tenant("k_b", lambda: mr.save_report("b.txt", b64))
    a = _with_tenant("k_a", mr.list_reports)
    b = _with_tenant("k_b", mr.list_reports)
    assert [r["name"] for r in a] == ["a.txt"]
    assert [r["name"] for r in b] == ["b.txt"]


def test_resolve_tenant_id_priority():
    assert _tenant.resolve_tenant_id("alice", "sekret") == "u_" + _tenant.tenant_hash(
        "alice"
    )
    assert _tenant.resolve_tenant_id("", "sekret") == "k_" + _tenant.tenant_hash("sekret")
    assert _tenant.resolve_tenant_id("", "") == "local"
