"""P0-2 该卖提醒端点契约测试（POST /api/live-risk/check、/api/alerts、PATCH）。

覆盖：
- /api/live-risk/check 注入实盘持仓 + 现行情，跑现成风控引擎产该卖提醒
  （老仓 L1 该卖 / 当日买入 L1 明日生效，Q3=A 语义），落 alert_event 簿
- 站内去重：同键同止损位二次 check ⇒ new_events 为空
- /api/alerts 收件箱 + PATCH handled/ignored 状态机（OPEN→HANDLED→409）
- 外发接线：dispatch_notification_once 被调用（monkeypatch 防真发），
  未配置通道时 notify_suppressed=True 不报错

红线（PRD 7.3）：端点只读持仓 + 写本地事件簿 + 复用现成 notifier，
不新建风控逻辑、不碰回测路径、不下单。
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

import aqsp.notification_runtime as nr
import app as app_module
import portfolio as pf
from aqsp.notifier import NotifyResult


def _make_client() -> TestClient:
    return TestClient(app_module.app)


def _reset_alert_store() -> None:
    """跨用例隔离：清掉 tmp 下固化的 alert_events.json（conftest 指 VR_DATA_DIR→tmp）。"""
    import alert_events

    path = alert_events._file()
    if os.path.exists(path):
        os.remove(path)
    state = os.path.join(pf.CACHE_DIR, "notify-state-live-risk.json")
    if os.path.exists(state):
        os.remove(state)


def _inject_holdings_and_prices(monkeypatch) -> None:
    """注入实盘持仓 + 现行情（不联网）：
    - 600000 老仓 cost 12.00 / 现价 11.00（跌 8.3% < -8% 生产阈值 ⇒ L1 触发）
    - 600036 当日买入 cost 28.50 / 现价 26.10（跌 8.4% ⇒ L1 触发，T+1 降格）
    """
    pf_data = {
        "holdings": [
            {"code": "600000", "name": "浦发", "price": 11.00,
             "shares": 500, "cost": 12.00, "market_value": 5500.0,
             "pnl": -500.0, "pnl_pct": -8.33},
            {"code": "600036", "name": "招商银行", "price": 26.10,
             "shares": 300, "cost": 28.50, "market_value": 7830.0,
             "pnl": -720.0, "pnl_pct": -8.42},
        ],
        "totals": {"market_value": 13330.0, "cost": 14250.0,
                   "pnl": -920.0, "pnl_pct": -6.45},
        "closed": [], "realized_pnl": 0,
        "updated": "2026-10-20 11:00", "last_refresh": None,
    }
    monkeypatch.setattr(pf, "get_portfolio", lambda: pf_data)


def test_live_risk_check_produces_reminders_and_persists(
    monkeypatch,
) -> None:
    """老仓 600000 L1 该卖（sellable_now=True）；当日买入 600036 L1 明日生效（Q3=A）。"""
    _reset_alert_store()
    _inject_holdings_and_prices(monkeypatch)
    monkeypatch.setattr(
        nr, "dispatch_notification_once",
        lambda *a, **k: [NotifyResult("wechat", True, "HTTP 200")],
    )
    client = _make_client()
    # holding_days：600000 老仓(5)、600036 当日(0)
    resp = client.post(
        "/api/live-risk/check",
        json={"holding_days": {"600000": 5, "600036": 0}, "notify": True},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    by_sym = {r["symbol"]: r for r in data["reminders"]}
    # 老仓 600000：L1 该卖、今日可卖
    assert "600000" in by_sym and by_sym["600000"]["level"] == "L1"
    assert by_sym["600000"]["action_hint"] == "该卖"
    assert by_sym["600000"]["sellable_now"] is True
    # 当日买入 600036：L1 明日生效 + T+1 标注（Q3=A）
    assert "600036" in by_sym and by_sym["600036"]["level"] == "L1"
    assert by_sym["600036"]["action_hint"] == "明日生效"
    assert by_sym["600036"]["sellable_now"] is False
    assert "T+1" in by_sym["600036"]["t1_note"]
    # 站内入库：2 条 OPEN 新事件
    assert len(data["new_events"]) == 2
    assert all(e["status"] == "OPEN" for e in data["new_events"])
    # 外发：dispatch 被调用（patch 返回 1 条），未抑制
    assert data["notify"] and data["notify_suppressed"] is False
    # 无依据不变量：每条 basis_json 齐备 rule+actual
    for e in data["new_events"]:
        assert e["basis_json"].get("rule") and e["basis_json"].get("actual")


def test_live_risk_check_dedups_on_recheck(
    monkeypatch,
) -> None:
    """同一持仓同止损位二次 check ⇒ 站内去重，new_events 为空。"""
    _reset_alert_store()
    _inject_holdings_and_prices(monkeypatch)
    monkeypatch.setattr(nr, "dispatch_notification_once", lambda *a, **k: [])
    client = _make_client()
    first = client.post(
        "/api/live-risk/check",
        json={"holding_days": {"600000": 5, "600036": 0}, "notify": False},
    ).json()["data"]
    assert len(first["new_events"]) == 2
    second = client.post(
        "/api/live-risk/check",
        json={"holding_days": {"600000": 5, "600036": 0}, "notify": False},
    ).json()["data"]
    # 二次：同键同止损位已 OPEN ⇒ 不新增
    assert second["new_events"] == []
    # 但提醒仍产出（风控位没变，风控本身照常判）
    assert len(second["reminders"]) == 2


def test_alerts_list_and_transition_state_machine(
    monkeypatch,
) -> None:
    """入库后收件箱可见；OPEN→HANDLED 后二次操作 409。"""
    _reset_alert_store()
    _inject_holdings_and_prices(monkeypatch)
    monkeypatch.setattr(nr, "dispatch_notification_once", lambda *a, **k: [])
    client = _make_client()
    client.post("/api/live-risk/check", json={"notify": False})

    got = client.get("/api/alerts").json()["data"]
    assert got["open_count"] == 2
    event_id = got["events"][0]["id"]

    ok = client.patch(f"/api/alerts/{event_id}", params={"action": "handled"})
    assert ok.status_code == 200
    assert ok.json()["data"]["status"] == "HANDLED"

    # 终态再操作 ⇒ 409
    again = client.patch(f"/api/alerts/{event_id}", params={"action": "handled"})
    assert again.status_code == 409

    # 忽略态：ignore_streak +1
    _reset_alert_store()
    _inject_holdings_and_prices(monkeypatch)
    client.post("/api/live-risk/check", json={"notify": False})
    iid = client.get("/api/alerts").json()["data"]["events"][0]["id"]
    ign = client.patch(f"/api/alerts/{iid}", params={"action": "ignored", "note": "假摔"})
    assert ign.status_code == 200
    assert ign.json()["data"]["status"] == "IGNORED"
    assert ign.json()["data"]["ignore_streak"] == 1
    assert ign.json()["data"]["handled_note"] == "假摔"


def test_live_risk_check_no_channel_suppressed_not_error(
    monkeypatch,
) -> None:
    """未配置任何通道（dispatch 返回空）⇒ notify_suppressed=True，不报错、不 500。"""
    _reset_alert_store()
    _inject_holdings_and_prices(monkeypatch)
    monkeypatch.setattr(nr, "dispatch_notification_once", lambda *a, **k: [])
    client = _make_client()
    resp = client.post(
        "/api/live-risk/check",
        json={"notify": True},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["notify"] == []
    assert data["notify_suppressed"] is True


def test_live_risk_check_empty_holdings_ok(
    monkeypatch,
) -> None:
    """无持仓 ⇒ 空提醒，200 不报错。"""
    _reset_alert_store()
    monkeypatch.setattr(
        pf, "get_portfolio",
        lambda: {"holdings": [], "totals": {}, "closed": [],
                 "realized_pnl": 0, "updated": "", "last_refresh": None},
    )
    client = _make_client()
    resp = client.post("/api/live-risk/check", json={"notify": False})
    assert resp.status_code == 200
    assert resp.json()["data"]["reminders"] == []
    assert resp.json()["data"]["new_events"] == []
