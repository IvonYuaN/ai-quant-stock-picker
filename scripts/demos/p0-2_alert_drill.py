#!/usr/bin/env python3
"""P0-2 M0 冷启动演练：实盘持仓 → LIVE 风控 → 该卖提醒，全链可复现证据。

设计约束（零污染 / 确定性 / 零真发）：
- `VR_DATA_DIR` 重定向到项目内 `.workbuddy/scratch/alert-drill-<ts>/`，
  **不碰老大真实 `~/.vibe-research` 持仓簿 / 告警簿**。
- 行情**确定性注入**（不赌本地行情源对持仓代码是否拉得到）⇒ 结果可复现。
- 外发层 `aqsp.notification_runtime.notify_markdown_via_config` patch 成哨兵：
  **零真发**（不惊扰老大），但完整走 `dispatch_notification_once` 的去重状态机，
  证明「该卖提醒外发接线 + per-(symbol,rule) 去重 + 降级」这三段真的被走到。

覆盖 5 只真实形态持仓（L1 8% 生产阈值）：
  600000  cost 10.00 / price 9.00  (-10%)  老仓 days=5  ⇒ L1 触发 + 可卖（该卖）
  600036  cost 10.00 / price 9.00  (-10%)  当日买入 days=0 ⇒ L1 触发 + T+1 明日生效
  000001  cost 10.00 / price 9.90  (-1%)   全绿          ⇒ 不产（无依据不产）
  000002  cost 10.00 / 缺价                  缺行情        ⇒ 跳过（无法判定）
  600001  cost -1.00 / price 9.90  负成本（融券）        ⇒ 跳过（无正成本基准）

产出：`.workbuddy/scratch/alert-drill-<ts>/drill-evidence.json`
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # scripts/demos/xxx.py ⇒ 项目根
SCRATCH = PROJECT / ".workbuddy" / "scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)
DRILL_DIR = SCRATCH / f"alert-drill-{int(time.time())}"
DRILL_DIR.mkdir(parents=True, exist_ok=True)

# 隔离：alert_event / portfolio / notify-state 全部落 scratch，不碰真实数据。
os.environ["VR_DATA_DIR"] = str(DRILL_DIR)
# 演练非 pytest ⇒ 显式禁真发双保险（生产代码在测试态也有同样抑制，这里纯保险）。
os.environ.pop("AQSP_ALLOW_REAL_NOTIFICATIONS", None)

# backend 裸名 import（tenant / portfolio / alert_events）+ aqsp 包。
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "backend"))

# ---- 哨兵外发层：零真发，但完整走 dispatch_notification_once 的去重状态机 ----
from aqsp import notifier as _notifier  # noqa: E402
from aqsp import notification_runtime as _nr  # noqa: E402

_OUTBOUND_CALLS: list[dict] = []


def _sentinel_notify(markdown, *, mode, summary_markdown=None, **_kw):
    """patch `notify_markdown_via_config`：零真发，返回 ok 哨兵结果并记录。

    该函数是 `dispatch_notification_once` 调用的底层通道聚合（notifier.py 的 10 通道
    在此聚合）；patch 它 = 外发「接线被走到」但「一条消息没真发」。
    """
    _OUTBOUND_CALLS.append(
        {
            "mode": mode,
            "markdown_head": (summary_markdown or markdown or "").splitlines()[:4],
        }
    )
    return [_notifier.NotifyResult(channel="sentinel", ok=True, detail="drill: 零真发哨兵")]


# 装上 patch：底层通道哨兵化，状态机 / 去重逻辑全部真走。
_nr.notify_markdown_via_config = _sentinel_notify

OUTBOX = {
    "drill_dir": str(DRILL_DIR),
    "isolated_vr_data_dir": os.environ["VR_DATA_DIR"],
    "outbound_calls": _OUTBOUND_CALLS,
    "notes": [],
}


def main() -> None:
    # ---- ① LIVE 风控入口（#430 合 main 的 aqsp.risk.live_risk）----
    from aqsp.risk.live_risk import run_live_risk_check

    holdings = [
        {"code": "600000", "shares": 1000, "cost": 10.00},  # 老仓，-10% ⇒ L1 该卖
        {"code": "600036", "shares": 2000, "cost": 10.00},  # 当日买入，-10% ⇒ L1 明日生效
        {"code": "000001", "shares": 500, "cost": 10.00},   # 全绿 -1% ⇒ 不产
        {"code": "000002", "shares": 300, "cost": 10.00},   # 缺价 ⇒ 跳过
        {"code": "600001", "shares": 400, "cost": -1.00},   # 负成本（融券）⇒ 跳过
    ]
    prices = {"600000": 9.00, "600036": 9.00, "000001": 9.90, "600001": 9.90}  # 000002 故意缺
    holding_days = {"600000": 5, "600036": 0, "000001": 3, "600001": 3}  # 600036 当日买入

    reminders = run_live_risk_check(
        holdings, prices, holding_days=holding_days, price_history=None, stop_config=None
    )
    OUTBOX["reminders"] = [r.to_dict() for r in reminders]

    # ---- ② alert_event 落库 + 去重（#431 合 main 的 backend/alert_events.py）----
    import alert_events

    new_events = alert_events.upsert_from_reminders([r.to_dict() for r in reminders])
    OUTBOX["new_events_first"] = new_events

    # 去重实证：同批再 upsert 一次 ⇒ 同键 OPEN 已存在，不新增（只刷现价）
    new_events_again = alert_events.upsert_from_reminders([r.to_dict() for r in reminders])
    OUTBOX["dedup_second_upsert_added"] = len(new_events_again)  # 期望 0

    inbox = alert_events.list_events()
    OUTBOX["inbox_after_first"] = {
        "events": inbox,
        "open_count": sum(1 for e in inbox if e.get("status") == "OPEN"),
    }

    # ---- ③ 状态机：OPEN→HANDLED / OPEN→IGNORED(ignore_streak+1) / 终态 409 ----
    state_trace = []
    for e in new_events:
        eid = e["id"]
        ok_handled = alert_events.transition(eid, "handled", note="演练：手动标记已处理")
        state_trace.append({"id": eid, "handled": bool(ok_handled), "status": (ok_handled or {}).get("status")})
        # 终态再 transition ⇒ 必须 None（前端据此 409）
        terminal_again = alert_events.transition(eid, "ignored")
        state_trace.append({"id": eid, "terminal_reopener": terminal_again})
        OUTBOX["state_trace"] = state_trace

    # IGNORED 的 ignore_streak 实证：造一条新的再 ignored
    # 造一条独立新键（换止损位）来验 ignore_streak，不干扰上面的 HANDLED
    fresh = [
        {
            "symbol": "600036",
            "level": "L5",
            "current_price": 9.40,
            "threshold_price": 9.42,  # 与 L1 的 9.20 不同键 ⇒ 独立事件
            "pnl_pct": -0.06,
            "sellable_now": False,
            "action_hint": "观察",
            "t1_note": "T+1 限制：当日买入，今日不可卖出",
            "basis_json": {"rule": "动态止损位 9.42", "actual": "现价 9.40", "evidence": "L5"},
        }
    ]
    fresh_events = alert_events.upsert_from_reminders(fresh)
    OUTBOX["fresh_l5_event"] = fresh_events
    if fresh_events:
        fid = fresh_events[0]["id"]
        alert_events.transition(fid, "ignored", note="演练：忽略观察信号")
        OUTBOX["ignore_streak_after_ignored"] = alert_events.list_events(symbol="600036")

    # ---- ④ 外发接线（#431 app.py /api/live-risk/check 的 outbox 段）：复刻其 loop ----
    # 复刻 app.py:728-740 的 per-event 外发：kind=per-(symbol,level,threshold) 走
    # dispatch_notification_once 的去重状态机，底层通道已是哨兵（零真发）。
    import portfolio as pf

    state_path = os.path.join(pf.CACHE_DIR, "notify-state-live-risk.json")
    md = "# 该卖提醒-演练\n\n- L1｜600000 该卖（成本 10.00×0.92=9.20；现价 9.00）"

    # 第 1 次：新 kind ⇒ 走 dispatch（哨兵 ok），写 sent 状态
    r1 = _nr.dispatch_notification_once(
        md, mode="summary", prefix="live-risk",
        kind="live-risk|600000|L1|9.2", state_path=state_path,
    )
    # 第 2 次：同 kind 同 content hash ⇒ 去重状态机判「已发」跳过（返回 []）
    r2 = _nr.dispatch_notification_once(
        md, mode="summary", prefix="live-risk",
        kind="live-risk|600000|L1|9.2", state_path=state_path,
    )
    OUTBOX["outbound"] = {
        "first_dispatch_results": [
            {"channel": x.channel, "ok": x.ok, "detail": x.detail} for x in r1
        ],
        "second_duplicate_dispatch_results": [
            {"channel": x.channel, "ok": x.ok, "detail": x.detail} for x in r2
        ],
        "second_is_skipped_duplicate": len(r2) == 0,
        "state_file_exists": os.path.exists(state_path),
        "state_file": (
            json.loads(Path(state_path).read_text()) if os.path.exists(state_path) else None
        ),
    }
    OUTBOX["notes"].append(
        "外发：第 1 次 kind=live-risk|600000|L1|9.2 由哨兵 ok 并写 sent 状态；"
        "第 2 次同 kind 被去重状态机跳过（返回 []）⇒ per-(symbol,rule) 去重实证。"
    )

    # ---- 结论自检 ----
    sym_levels = sorted((r["symbol"], r["level"]) for r in OUTBOX["reminders"])
    OUTBOX["conclusion"] = {
        "reminders_produced": len(OUTBOX["reminders"]),
        "symbols_level": sym_levels,
        "expect": ["('600000', 'L1')", "('600036', 'L1')"],
        "expect_pass": sym_levels == sorted([("600000", "L1"), ("600036", "L1")]),
        "t1_note_on_600036": any(
            r["symbol"] == "600036" and r.get("t1_note") for r in OUTBOX["reminders"]
        ),
        "no_basis_not_produced": all(
            r["basis_json"].get("rule") and r["basis_json"].get("actual")
            for r in OUTBOX["reminders"]
        ),
        "dedup_holds": OUTBOX["dedup_second_upsert_added"] == 0,
        "inbox_open_count": OUTBOX["inbox_after_first"]["open_count"],
        "outbound_wired_zero_real": OUTBOX["outbound"]["second_is_skipped_duplicate"],
    }

    out_path = DRILL_DIR / "drill-evidence.json"
    out_path.write_text(json.dumps(OUTBOX, ensure_ascii=False, indent=2))

    c = OUTBOX["conclusion"]
    ok = (
        c["expect_pass"]
        and c["t1_note_on_600036"]
        and c["no_basis_not_produced"]
        and c["dedup_holds"]
        and c["inbox_open_count"] >= 2
        and c["outbound_wired_zero_real"]
    )
    print("=" * 60)
    print(f"该卖提醒冷启动演练 — 证据: {out_path}")
    print("=" * 60)
    for k, v in c.items():
        print(f"  {k:24s}: {v}")
    print(f"\n  ==> 全链 {'PASS ✅' if ok else 'FAIL ❌'}（零污染: 数据全在 {DRILL_DIR}）")
    print("\n  产出的该卖提醒：")
    for r in OUTBOX["reminders"]:
        print(
            f"    [{r['level']}] {r['symbol']} 现价 {r['current_price']} 止损位 "
            f"{r['threshold_price']} 盈亏 {round(r['pnl_pct'] * 100, 2)}% "
            f"可卖={r['sellable_now']} 「{r['action_hint']}」"
            + (f"（{r['t1_note']}）" if r["t1_note"] else "")
        )
    print("\n  外发（哨兵零真发）：")
    ob = OUTBOX["outbound"]
    print(f"    第1次 dispatch: {ob['first_dispatch_results']}")
    print(f"    第2次去重跳过: {ob['second_is_skipped_duplicate']}（同 kind 不重发）")
    print(f"    状态文件: {'已写' if ob['state_file_exists'] else '未写'} {state_path}")

    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
