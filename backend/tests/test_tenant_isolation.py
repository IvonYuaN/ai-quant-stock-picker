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


def _tid_for(seed: str) -> str:
    """把合成种子走**生产解析路径**（X-User-Id 分支）算出租户 id。

    刻意不用 `crypto.randomUUID()` 那类真实 id：那是 `loadUserId()`（api.ts:70）
    的一次性产物，写死进断言等于把随机值固化成契约。这里用可复现的合成种子
    （test-tenant-a/b），并让 tid 由 `resolve_tenant_id` 现算 —— 这样本测试验的是
    「不同 X-User-Id ⇒ 不同租户桶」这条真实链路，而不是一个硬编码字符串。
    """
    return _tenant.resolve_tenant_id(seed, "")


def test_new_tenant_gets_migrated_copy_of_local_holdings(tmp_path, monkeypatch):
    """新租户首次访问会拿到 local legacy 持仓的**迁移副本**（记录现状，非锁定设计）。

    这是 `_maybe_migrate_to_tenant`（portfolio.py:85-100）的设计意图，其 docstring
    原文：「非 local 租户首次访问时，若自身为空且本地 legacy 持仓存在，复制过来
    （让『从单用户升级到多用户』的老数据无缝落到主人自己的租户桶）」。

    覆盖缺口：既有测试（test_portfolio_isolated_across_tenants）用 k_a/k_b 直接
    add_holding，**期间 tmp_path/portfolio.json（local）从不存在**，而迁移守卫是
    `if os.path.exists(tf) or not os.path.exists(_legacy_local_pf()): return`
    ⇒ local 不存在 ⇒ **迁移分支从未被测过**。本测试补上这一支。

    ⚠️ 本测试固化的是**当前行为**，不是设计契约。若将来决定改为「新租户从空开始、
    不迁移」，需同步更新本测试 —— 它是"记录现状"的测试，不是"锁定设计"的测试。
    """
    _isolate(tmp_path, monkeypatch)
    tid_a = _tid_for("test-tenant-a")

    # local 租户先有一笔持仓（落在 CACHE_DIR 根，即 legacy 位置）
    _with_tenant("local", lambda: pf.add_holding("600519", 100, 8.0))
    assert (tmp_path / "portfolio.json").exists()
    assert not (tmp_path / "users").exists(), "此时还不该有租户目录"

    # 新租户首次访问 ⇒ _load() → _maybe_migrate_to_tenant() 复制一份过去
    a = _with_tenant(tid_a, pf.get_portfolio)
    assert [h["code"] for h in a["holdings"]] == ["600519"]

    # 副本落在 users/<tid>/ 下，**不是**写进 local 根目录
    copied = tmp_path / "users" / tid_a / "portfolio.json"
    assert copied.exists()
    assert (tmp_path / "portfolio.json").exists(), "local 真身不应被搬走"


def test_migrated_copy_does_not_follow_later_local_updates(tmp_path, monkeypatch):
    """④-d：local 后续更新**不会**同步到已存在的租户副本（数据陈旧，非数据丢失）。

    成因是 portfolio.py:93 的守卫 `if os.path.exists(tf): return` —— 语义是
    「这个租户桶已存在就不动」，而非「这个租户桶有自己的持仓就不动」。副本一旦
    建立即永久固化，此后 local 的改动不再传播。

    风险性质：**用户看到的是过期持仓（数据陈旧），不是持仓消失（信任崩塌）**。
    这条测试把该现状显式记录下来，避免后人误判为 bug 而"顺手修掉"。

    ⚠️ 同上：记录现状，非锁定设计。改设计时需同步更新。
    """
    _isolate(tmp_path, monkeypatch)
    tid_a = _tid_for("test-tenant-a")

    _with_tenant("local", lambda: pf.add_holding("600519", 100, 8.0))
    _with_tenant(tid_a, pf.get_portfolio)  # 建立副本

    # local 之后又加了一笔
    _with_tenant("local", lambda: pf.add_holding("000001", 50, 5.0))
    assert [
        h["code"] for h in _with_tenant("local", pf.get_portfolio)["holdings"]
    ] == ["600519", "000001"], "local 自己应看到两笔"

    # 但租户副本仍是建立时那一笔 —— 陈旧，不是丢失
    assert [
        h["code"] for h in _with_tenant(tid_a, pf.get_portfolio)["holdings"]
    ] == ["600519"], "副本不应跟随 local 更新（④-d 现状）"


def test_two_new_tenants_get_separate_copies(tmp_path, monkeypatch):
    """两个新租户各拿一份**互相独立**的副本 —— 迁移只发生一次，之后互不可见。

    明确「这是迁移，不是泄漏」：两个租户都从同一份 local 真身复制，但复制完成后
    各自的目录互不影响（一个改，另一个不变）。这条把「迁移」与「隔离」两件事分开
    断言，避免后人看到"新租户拿到了 local 的数据"就误判为隔离缺陷而"修掉"设计。
    """
    _isolate(tmp_path, monkeypatch)
    tid_a = _tid_for("test-tenant-a")
    tid_b = _tid_for("test-tenant-b")
    assert tid_a != tid_b

    _with_tenant("local", lambda: pf.add_holding("600519", 100, 8.0))

    # 两个租户各自复制一份（此刻内容相同 —— 因为都源自同一份 local 真身）
    _with_tenant(tid_a, pf.get_portfolio)
    _with_tenant(tid_b, pf.get_portfolio)
    assert [h["code"] for h in _with_tenant(tid_a, pf.get_portfolio)["holdings"]] == [
        "600519"
    ]
    assert [h["code"] for h in _with_tenant(tid_b, pf.get_portfolio)["holdings"]] == [
        "600519"
    ]

    # 复制完成后互不影响：A 加仓不应改变 B
    _with_tenant(tid_a, lambda: pf.add_holding("600519", 100, 9.0))
    a = _with_tenant(tid_a, pf.get_portfolio)
    b = _with_tenant(tid_b, pf.get_portfolio)
    assert a["holdings"][0]["shares"] == 200.0
    assert b["holdings"][0]["shares"] == 100.0, "B 不应被 A 的加仓影响"
    # 成本各自独立：A 加仓后成本被加权平均，B 保持原值
    assert b["holdings"][0]["cost"] == 8.0
