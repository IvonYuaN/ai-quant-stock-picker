"""P2-8 持仓开仓日期（opened_at）契约测试。

覆盖：
- ``portfolio.add_holding`` 落盘 ``opened_at``：缺省今天（上海时区）；显式传入保留；
  同代码合并保留**最早**的 opened_at（加仓不把老仓误判成当日买入）
- 老数据（P2-8 前录入、无 ``opened_at`` 键）合并后仍缺省 ⇒ 后端按老仓处理（即时可卖）
- ``get_portfolio`` 叠加行返回 ``opened_at``；老数据给 None
- ``POST /api/live-risk/check`` **不传** holding_days 时，从持仓 ``opened_at``
  自动推导 T+1 可卖性（Q3=A）：今天开仓 ⇒ 明日生效；之前开仓 ⇒ 该卖；
  老仓（无 opened_at）⇒ 该卖；显式 holding_days 仍优先（向后兼容）

红线：只读持仓 + 写本地事件簿，不新建风控逻辑、不下单、不联网
（tencent_quote 全部 patch 掉）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import portfolio as pf
from fastapi.testclient import TestClient

import aqsp.notification_runtime as nr
import app as app_module
import alert_events as ae


def _today_shanghai_iso() -> str:
    return datetime.now(pf.BEIJING).strftime("%Y-%m-%d")


def _reset_portfolio() -> None:
    path = pf._pf_file()
    if os.path.exists(path):
        os.remove(path)
    state = os.path.join(pf.CACHE_DIR, "notify-state-live-risk.json")
    if os.path.exists(state):
        os.remove(state)


def _patch_quotes(monkeypatch, price: float = 10.0) -> None:
    """不联网：行情全部走假数据。"""
    monkeypatch.setattr(
        pf.astock,
        "tencent_quote",
        lambda codes: {c: {"name": c, "price": price} for c in codes},
    )


# ---------- portfolio.py 数据层 ----------


def test_add_holding_defaults_opened_at_to_today_shanghai(monkeypatch) -> None:
    _reset_portfolio()
    _patch_quotes(monkeypatch)
    pf.add_holding("600000", 100, 10.0)
    stored = pf._load()["holdings"][0]
    assert stored["opened_at"] == _today_shanghai_iso()


def test_add_holding_keeps_explicit_opened_at(monkeypatch) -> None:
    _reset_portfolio()
    _patch_quotes(monkeypatch)
    pf.add_holding("600000", 100, 10.0, "2026-09-15")
    assert pf._load()["holdings"][0]["opened_at"] == "2026-09-15"


def test_add_holding_merge_keeps_earliest_opened_at(monkeypatch) -> None:
    """同代码加仓：opened_at 取更早的一个（老仓基准不被今天的加仓覆盖 ⇒ 不误判当日买入）。"""
    _reset_portfolio()
    _patch_quotes(monkeypatch)
    pf.add_holding("600000", 100, 10.0, "2026-09-01")
    pf.add_holding("600000", 50, 12.0)  # 缺省今天，加仓
    row = pf._load()["holdings"][0]
    assert row["opened_at"] == "2026-09-01"
    # 加权平均成本仍正确：(100×10 + 50×12) / 150
    assert row["shares"] == 150
    assert row["cost"] == round((100 * 10.0 + 50 * 12.0) / 150, 4)


def test_add_holding_merge_preserves_legacy_missing_opened_at(monkeypatch) -> None:
    """P2-8 前录入的老持仓（无 opened_at 键）加仓后仍缺省 ⇒ 按老仓可卖，不当当日买入。"""
    _reset_portfolio()
    _patch_quotes(monkeypatch)
    with open(pf._pf_file(), "w", encoding="utf-8") as f:
        json.dump({"holdings": [{"code": "600000", "shares": 100, "cost": 10.0}]}, f)
    pf.add_holding("600000", 50, 12.0, "2026-10-01")
    row = pf._load()["holdings"][0]
    assert row.get("opened_at") is None


def test_get_portfolio_returns_opened_at(monkeypatch) -> None:
    _reset_portfolio()
    _patch_quotes(monkeypatch, price=9.5)
    pf.add_holding("600000", 100, 10.0, "2026-10-09")
    rows = pf.get_portfolio()["holdings"]
    assert rows[0]["opened_at"] == "2026-10-09"


def test_get_portfolio_legacy_row_opened_at_none(monkeypatch) -> None:
    """老数据无 opened_at ⇒ 叠加行给 None（前端归一成 ""，按老仓可卖）。"""
    _reset_portfolio()
    _patch_quotes(monkeypatch, price=9.5)
    with open(pf._pf_file(), "w", encoding="utf-8") as f:
        json.dump({"holdings": [{"code": "600000", "shares": 100, "cost": 10.0}]}, f)
    rows = pf.get_portfolio()["holdings"]
    assert rows[0]["opened_at"] is None


# ---------- 端点：从 opened_at 自动推导 T+1（不传 holding_days） ----------


def _pf_data_with_opened_at(today_iso: str) -> dict:
    return {
        "holdings": [
            # 今天开仓：跌 8.3% 破 L1 ⇒ T+1 降格「明日生效」
            {"code": "600000", "name": "浦发", "price": 11.00, "shares": 500,
             "cost": 12.00, "market_value": 5500.0, "pnl": -500.0,
             "pnl_pct": -8.33, "opened_at": today_iso},
            # 之前开仓（10-01）：同跌幅 ⇒ 老仓「该卖」
            {"code": "600036", "name": "招商银行", "price": 26.10, "shares": 300,
             "cost": 28.50, "market_value": 7830.0, "pnl": -720.0,
             "pnl_pct": -8.42, "opened_at": "2026-10-01"},
            # P2-8 前老仓（无 opened_at）：缺省按老仓「该卖」，不误伤
            {"code": "000001", "name": "平安银行", "price": 11.00, "shares": 100,
             "cost": 12.00, "market_value": 1100.0, "pnl": -100.0, "pnl_pct": -8.33},
        ],
        "totals": {"market_value": 14430.0, "cost": 15600.0,
                   "pnl": -1170.0, "pnl_pct": -7.50},
        "closed": [], "realized_pnl": 0,
        "updated": "2026-10-20 11:00", "last_refresh": None,
    }


def _reset_alert_store() -> None:
    path = ae._file()
    if os.path.exists(path):
        os.remove(path)


def _inject_check_positions(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import decision_context
    import position_context_source
    import risk_rules

    data = _pf_data_with_opened_at(_today_shanghai_iso())
    for holding in data["holdings"]:
        holding["quote"] = {"quote_time": "20261023100000"}
    for rule in risk_rules.ensure_rules(data):
        rule["enabled"] = rule["level"] == "L1"
    pf._save(data)
    monkeypatch.setattr(pf, "get_portfolio", lambda: data)
    monkeypatch.setattr(decision_context, "now_shanghai", lambda: datetime(2026, 10, 23, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(position_context_source, "load_position_sectors", lambda codes: {})


def test_live_risk_derives_t1_from_opened_at(monkeypatch) -> None:
    """不传 holding_days ⇒ 后端从 opened_at 推导：今天开=明日生效，之前开/老仓=该卖。"""
    _reset_alert_store()
    _reset_portfolio()
    _inject_check_positions(monkeypatch)
    monkeypatch.setattr(nr, "dispatch_notification_once", lambda *a, **k: [])
    client = TestClient(app_module.app)

    resp = client.post("/api/live-risk/check", json={"notify": False})
    assert resp.status_code == 200
    by_sym = {r["symbol"]: r for r in resp.json()["data"]["reminders"]}
    # 今天开仓 ⇒ T+1 降格
    assert by_sym["600000"]["action_hint"] == "明日生效"
    assert by_sym["600000"]["sellable_now"] is False
    assert "T+1" in by_sym["600000"]["t1_note"]
    # 之前开仓 ⇒ 老仓即时可卖
    assert by_sym["600036"]["action_hint"] == "该卖"
    assert by_sym["600036"]["sellable_now"] is True
    # 老仓（无 opened_at）⇒ 不误伤，即时可卖
    assert by_sym["000001"]["action_hint"] == "该卖"
    assert by_sym["000001"]["sellable_now"] is True


def test_live_risk_explicit_holding_days_overrides_opened_at(monkeypatch) -> None:
    """向后兼容：显式 holding_days 仍优先于 opened_at 推导。"""
    _reset_alert_store()
    _reset_portfolio()
    _inject_check_positions(monkeypatch)
    monkeypatch.setattr(nr, "dispatch_notification_once", lambda *a, **k: [])
    client = TestClient(app_module.app)

    # 显式把 600036 标成当日买入 ⇒ 覆盖其 opened_at（老仓）
    resp = client.post(
        "/api/live-risk/check",
        json={"holding_days": {"600036": 0}, "notify": False},
    )
    assert resp.status_code == 200
    by_sym = {r["symbol"]: r for r in resp.json()["data"]["reminders"]}
    assert by_sym["600036"]["action_hint"] == "明日生效"
    assert by_sym["600036"]["sellable_now"] is False
