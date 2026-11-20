#!/usr/bin/env python3
"""云端一条龙：盘中检测(B格连板) → 企业微信私聊推送（runner 上跑，本地不开机也工作）。

流程（cron 每 3 分钟触发一次）：
  1) 跑同目录 shortline_signal.py --once → JSON（K线核实几板/昨涨停 + 腾讯实时价 + A/B格）
  2) 取 new_hits（增量去重，防刷屏）；无新增 → 静默
  3) 组极简 markdown（代码 名称 现价 几板 策略）→ wecom-cli aibot send 推给授权人
  4) 交易时段守卫：周一~五 09:15~11:30 / 13:00~15:05 CST，午休/盘后静默
  5) 15:05 用 --recap 推当日收盘复盘

推送走 runner 上已授权的 wecom-cli（WECOM_CLI_CONFIG_DIR 指向移植的授权目录），
机器人「股票老狗」→ 授权人私聊，无需建群/Webhook/扫码。
markdown 字段与本机 shortline_push.py 一致；B格可买 / A格一字观察共用同一套列。

用法：
  python3 shortline_cloud.py              # 盘中检测+推（无新增静默）
  python3 shortline_cloud.py --recap      # 收盘复盘（跳过时段守卫）
  python3 shortline_cloud.py --force      # 跳过时段守卫（手工补跑，会真实推送）
  python3 shortline_cloud.py --dry-run    # 只检测+看卡片，绝不推送（验证用，防骚扰）
  python3 shortline_cloud.py --health     # 只看推送健康状态（不检测/不联网/不推送）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIGNAL_PY = HERE / "shortline_signal.py"
WECOM_CONFIG = HERE / "personal"              # 从本机移植的 wecom-cli 授权目录
DB = "/opt/aqsp-runner/data/astocks_raw.db"
CST = timezone(timedelta(hours=8))


def run_detection() -> dict:
    """直接跑同目录检测器（runner 本地，无 ssh），读回 JSON。"""
    tmp = HERE / ("_signal_%s.json" % datetime.now(CST).strftime("%Y%m%d"))
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = "1"
    cmd = [sys.executable, str(SIGNAL_PY), "--db", DB, "--once", "--json", str(tmp)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=150, env=env)
    if p.returncode != 0:
        raise RuntimeError("detection rc=%d: %s" % (p.returncode, (p.stderr or p.stdout)[:400]))
    return json.loads(tmp.read_text(encoding="utf-8"))


# ---------------- 影子账户多策略（arena） ----------------
STATE_DIR = HERE / "state"
# (phase, 起始分钟, 结束分钟, 说明) —— 北京时间；每个时点每天只跑一次（marker 文件锁）
ARENA_WINDOWS = (
    ("auction", 9 * 60 + 26, 9 * 60 + 40, "竞价抢筹"),
    ("morning", 10 * 60 + 0, 10 * 60 + 15, "超跌反抽"),
    ("late", 14 * 60 + 45, 14 * 60 + 58, "尾盘惯性"),
    ("close", 15 * 60 + 5, 15 * 60 + 57, "收盘结算"),   # 兜底：recap 挂了也能结算
)


def _arena_brief() -> str:
    """推送卡片里的影子账户一行摘要（各 sleeve 样本/净值）。"""
    try:
        import paper_ledger as pl
        ov = pl.overview(pl.load())
        parts = []
        for sid in sorted(ov["sleeves"]):
            st = ov["sleeves"][sid]
            if st["n"] == 0 and st["positions"] == 0:
                continue
            t = "超t%.1f" % st["t_exc"] if st["t_exc"] is not None else "超t-"
            parts.append("%s %d笔 %s" % (sid, st["n"], t))
        tail = " ｜ ".join(parts) if parts else "尚无信号"
        return "\n> 影子账户 ¥%.0f(%+.2f%%) %s" % (
            ov["total_nav"], ov["total_ret_pct"], tail)
    except Exception as e:
        return "\n> 影子账户摘要失败: %s" % str(e)[:80]


def arena_board(det: dict) -> None:
    """盘中：把打板检测结果记进 P1（二板）/P2（首板）。"""
    try:
        import paper_scan
        today = datetime.now(CST).strftime("%Y-%m-%d")
        res = paper_scan.run_phase("board", today, det)
        got = {sid: len(r.get("bought", [])) for sid, r in res.get("sleeves", {}).items()}
        print("[arena] board ingest %s" % json.dumps(got, ensure_ascii=False), file=sys.stderr)
    except Exception as e:
        print("[arena] board 记账失败: %s" % e, file=sys.stderr)


def due_phases(hm: int, weekday: int, done: set[str]) -> list[str]:
    """纯函数：给定「当天第几分钟 + 星期几 + 已完成集合」，返回该跑的时点。

    抽出来是为了能单测时点窗口（cron 每 3 分钟一跳，跨窗口边界最容易漏跑）。
    """
    if weekday >= 5:
        return []
    return [p for p, lo, hi, _label in ARENA_WINDOWS if lo <= hm <= hi and p not in done]


def arena_due_phases() -> None:
    """按时点跑 auction/morning/late/close，每个时点当天只跑一次。"""
    now = datetime.now(CST)
    if now.weekday() >= 5:
        return
    hm = now.hour * 60 + now.minute
    today = now.strftime("%Y-%m-%d")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    done = set()
    for phase, _lo, _hi, _lb in ARENA_WINDOWS:
        if (STATE_DIR / ("phase_%s_%s.done" % (today, phase))).exists():
            done.add(phase)
    for phase in due_phases(hm, now.weekday(), done):
        label = dict((p, lb) for p, _lo, _hi, lb in ARENA_WINDOWS)[phase]
        marker = STATE_DIR / ("phase_%s_%s.done" % (today, phase))
        if phase == "close":
            arena_close()
            continue
        try:
            import paper_scan
            res = paper_scan.run_phase(phase, today)
            marker.write_text(json.dumps(res, ensure_ascii=False)[:2000], encoding="utf-8")
            brief = {sid: {"sig": r.get("signals"), "buy": len(r.get("bought", []))}
                     for sid, r in res.get("sleeves", {}).items()}
            print("[arena] %s(%s) %s" % (phase, label, json.dumps(brief, ensure_ascii=False)),
                  file=sys.stderr)
        except Exception as e:
            print("[arena] %s 失败: %s" % (phase, e), file=sys.stderr)


def arena_close(force: bool = False) -> None:
    """收盘：落快照 → 全部 sleeve 结算 → 记净值 → 刷新报告。幂等，靠 marker 去重。"""
    today = datetime.now(CST).strftime("%Y-%m-%d")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    marker = STATE_DIR / ("phase_%s_close.done" % today)
    if marker.exists() and not force:
        return
    try:
        import paper_scan
        res = paper_scan.run_phase("close", today)
        closed = res.get("closed", [])
        print("[arena] close 结算 %d 笔 %s" % (len(closed), res.get("note", "")), file=sys.stderr)
        for c in closed[:20]:
            print("   %s %s %+.1f%% %s" % (c["sleeve"], c["code"], c["ret_pct"], c["reason"]),
                  file=sys.stderr)
        marker.write_text(json.dumps({"phase": "close", "at": datetime.now(CST).isoformat()}),
                          encoding="utf-8")
        refresh_report()
        refresh_export()
    except Exception as e:
        print("[arena] close 失败: %s" % e, file=sys.stderr)


def refresh_report() -> None:
    try:
        import paper_report
        Path(paper_report.OUT).write_text(paper_report.render(), encoding="utf-8")
        print("[arena] 报告已刷新 %s" % paper_report.OUT, file=sys.stderr)
    except Exception as e:
        print("[arena] 报告刷新失败: %s" % e, file=sys.stderr)


def refresh_export() -> None:
    """导出前端 payload（`paper_arena_api.json`）。

    与报告一起在收盘后刷新：两边的数字必须来自同一份账本，
    否则前端和 HTML 报告会各说一套（这类分叉比数字难看更糟）。
    """
    try:
        import paper_export
        text = json.dumps(paper_export.build_payload(), ensure_ascii=False, indent=2)
        Path(paper_export.OUT).write_text(text, encoding="utf-8")
        print("[arena] 前端产物已刷新 %s" % paper_export.OUT, file=sys.stderr)
    except Exception as e:
        print("[arena] 前端产物刷新失败: %s" % e, file=sys.stderr)


def _sh(cmd: list, env: dict | None = None, timeout: int = 60) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout + p.stderr


HEALTH_FILE = HERE / "push_health.json"   # 推送健康状态（只写本地，区分"无信号"vs"推送失败"）


def _record_health(ok: bool, reason: str = "") -> None:
    """记录每次推送尝试结果。连续失败说明授权/通道坏了；无推送尝试=今天没信号。"""
    try:
        h = json.loads(HEALTH_FILE.read_text(encoding="utf-8")) if HEALTH_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        h = {}
    today = datetime.now(CST).strftime("%Y-%m-%d")
    if h.get("date") != today:                 # 跨天重置当日计数
        h = {"date": today, "ok_count": 0, "fail_count": 0}
    h["last_attempt"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    if ok:
        h["ok_count"] = h.get("ok_count", 0) + 1
        h["last_ok"] = h["last_attempt"]
        h["last_error"] = ""
    else:
        h["fail_count"] = h.get("fail_count", 0) + 1
        h["last_error"] = reason[:200]
    HEALTH_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


def wecom_send(md: str) -> bool:
    """经 runner 上已授权的 wecom-cli，以「股票老狗」机器人身份私聊推给授权人。"""
    env = dict(os.environ)
    env["WECOM_CLI_CONFIG_DIR"] = str(WECOM_CONFIG)
    rc, out = _sh(["wecom-cli", "identity", "whoami"], env=env)
    cid = None
    try:
        d = json.loads(out[out.index("{"):])
        ctx = d.get("extra_identity_context", "")
        seg = ctx.split("授权真人用户身份", 1)
        if len(seg) > 1:
            m = re.search(r"ID[:：]\s*([A-Za-z0-9_-]+)", seg[1].split("CLI 调用")[0])
            cid = m.group(1) if m else None
    except Exception:
        pass
    if not cid:
        print("[cloud] 取不到授权人 chat_id，跳过推送: %s" % out[:200], file=sys.stderr)
        _record_health(False, "no_chat_id: " + out[:150])
        return False
    payload = json.dumps({"chat_id": cid, "msg_type": "markdown",
                          "markdown": {"content": md}}, ensure_ascii=False)
    rc, out = _sh(["wecom-cli", "message", "aibot", "send", "--json", payload], env=env)
    ok = '"success": true' in out or '"success":true' in out
    if not ok:
        print("[cloud] wecom send rc=%d: %s" % (rc, out[:300]), file=sys.stderr)
    _record_health(ok, "" if ok else "send rc=%d: %s" % (rc, out[:150]))
    return ok


def _row(h: dict) -> str:
    return "%s %s %7.2f %s板 %s" % (
        h["code"], h.get("name", "")[:4], h["price"], h["board"], h.get("strategy", ""))


def build_markdown(det: dict) -> str:
    """极简卡片：代码 名称 现价 几板 策略。new_hits 增量去重防刷屏。"""
    now = datetime.now(CST).strftime("%H:%M")
    new = det.get("new_hits", [])
    act = [h for h in new if h.get("actionable")]
    obs = [h for h in new if not h.get("actionable")]
    L = ["## 打板盘中 %s 池%d只" % (now, det.get("pool", 0))]
    L.append("✅ 新增可买: " + ("\n" + "\n".join(_row(h) for h in act) if act else "无"))
    if obs:
        L.append("⚪ 一字观察(排不进):")
        L.extend(_row(h) for h in obs)
    L.append("> 只做B格·T+1收兑现·轻仓·炸板止损")
    return "\n".join(L)


def build_recap(det: dict) -> str:
    all_hits = det.get("all_hits", [])
    b = [h for h in all_hits if h.get("actionable")]
    a = [h for h in all_hits if not h.get("actionable")]
    L = ["## 打板收盘复盘 %s" % datetime.now(CST).strftime("%Y-%m-%d")]
    L.append("封二板 B格(可买) %d 只 / 一字 A格(排不进) %d 只" % (len(b), len(a)))
    if b:
        L.append("✅ B格:")
        L.extend(_row(h) for h in b)
    if a:
        L.append("⚪ A格:")
        L.extend(_row(h) for h in a)
    if not all_hits:
        L.append("（当日无触板票）")
    L.append("> 明日 watchlist = 上述涨停票次日再盯二板；只做B格·T+1收兑现")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recap", action="store_true", help="收盘复盘（跳过时段守卫）")
    ap.add_argument("--force", action="store_true", help="跳过时段守卫（手工补跑，会真实推送）")
    ap.add_argument("--dry-run", action="store_true", help="只检测+打印卡片，绝不调用推送（验证用）")
    ap.add_argument("--health", action="store_true", help="只看推送健康状态（不检测/不推送）")
    args = ap.parse_args(argv)

    if args.health:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        if HEALTH_FILE.exists():
            print(HEALTH_FILE.read_text(encoding="utf-8"))
        else:
            print("推送记录：今日无")
        # cron 是否真在跑（区分"无信号"vs"系统没运行"）
        log = HERE / "cron.log"
        if log.exists():
            lines = [ln for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()
                     if today in ln]
            print("\n今日 cron 触发 %d 次；最近: %s" % (len(lines), lines[-1] if lines else "无"))
        else:
            print("\ncron.log 不存在")
        return 0

    if not (args.force or args.recap or args.dry_run):
        now = datetime.now(CST)
        hm = now.hour * 60 + now.minute
        in_trading = now.weekday() < 5 and (
            (9 * 60 + 15) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60 + 5))
        if not in_trading:
            print("[cloud] 非盘中 %s，静默" % now.strftime("%Y-%m-%d %H:%M"), file=sys.stderr)
            return 0

    # 收盘复盘：先结算影子账户（快照+平仓+净值），再组卡片
    if args.recap and not args.dry_run:
        arena_close()

    det = {}
    try:
        det = run_detection()
    except Exception as e:
        print("[cloud] 检测失败（继续跑 arena 时点）: %s" % e, file=sys.stderr)

    ledger_line = ""
    if not args.dry_run:
        if det and not args.recap:
            arena_board(det)          # P1/P2 打板接力记账
        if not args.recap:
            arena_due_phases()        # P3/P4/P5 到点记账
        ledger_line = _arena_brief()

    if not det:
        print("[cloud] 无检测结果，不推", file=sys.stderr)
        return 0

    if args.recap:
        md = build_recap(det) + ledger_line
    else:
        if not det.get("new_hits"):
            print("[cloud] 本轮无新增触板票，不推", file=sys.stderr)
            return 0
        md = build_markdown(det) + ledger_line

    if args.dry_run:
        print("[cloud] DRY-RUN（未推送）\n---\n%s" % md)
        return 0

    ok = wecom_send(md)
    print("[cloud] push %s\n---\n%s" % ("OK" if ok else "FAIL", md))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
