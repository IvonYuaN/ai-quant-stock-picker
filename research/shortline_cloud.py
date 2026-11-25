#!/usr/bin/env python3
"""云端一条龙：盘中检测 + 多模式推荐 → 企业微信私聊推送（runner 上跑，本地不开机也工作）。

流程（cron 每 3 分钟触发一次）：
  1) 跑同目录 shortline_signal.py --once → JSON（K线核实几板/昨涨停 + 腾讯实时价 + A/B格）
  2) 取 new_hits（增量去重，防刷屏）
  3) 到点跑影子账户各时点（09:27 竞价 / 10:00 超跌 / 14:45 尾盘），
     把**每个模式推荐了什么**一并推进卡片 —— 推荐与记账同源，不另造一份口径
  4) 组 markdown → wecom-cli aibot send 推给授权人
  5) 交易时段守卫：周一~五 09:15~11:30 / 13:00~15:05 CST，午休/盘后静默
  6) 15:05 用 --recap 推当日收盘复盘

🔴 历史坑（2026-11-23 修）：P3/P4/P5 一直在记账，但卡片只渲染打板那一段，
   ⇒ 老大看到的推送「永远只有打板」，而账本里竞价/超跌早就买了。
   现在时点信号落 `state/picks_<date>.json`，任何一张卡都带「今日各模式」全貌。

推送走 runner 上已授权的 wecom-cli（WECOM_CLI_CONFIG_DIR 指向移植的授权目录），
机器人「股票老狗」→ 授权人私聊，无需建群/Webhook/扫码。

用法：
  python3 shortline_cloud.py              # 盘中检测+推（无新增且无新模式信号时静默）
  python3 shortline_cloud.py --recap      # 收盘复盘（跳过时段守卫）
  python3 shortline_cloud.py --force      # 跳过时段守卫（手工补跑，会真实推送）
  python3 shortline_cloud.py --dry-run    # 只检测+看卡片，绝不推送（验证用，防骚扰）
  python3 shortline_cloud.py --health     # 只看推送健康状态（不检测/不联网/不推送）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                 # 同目录模块（paper_* / mode_card）可直接 import
import mode_card  # noqa: E402  (sys.path 注入后才可导入)

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


def _sleeve_tag(st: dict) -> str:
    """sleeve 摘要里的「样本」两三个字（纯函数）。

    🔴 `n` 是**已结算**笔数，也就是 t 检验的样本量。打板 sleeve 是 T+1，
    买入当天结算不了，于是出现「P1 0笔」却明明持着 7 只票的场面 ——
    老大看到这一行只会以为打板什么都没干。持仓数必须一起印。
    """
    tag = "%d笔" % st.get("n", 0)
    if st.get("positions"):
        tag += "(%d持)" % st["positions"]
    return tag


def _arena_brief() -> str:
    """推送卡片里的影子账户摘要（各 sleeve 样本/净值 + 真实敞口构成）。"""
    try:
        import paper_ledger as pl
        ov = pl.overview(pl.load())
        parts = []
        for sid in sorted(ov["sleeves"]):
            st = ov["sleeves"][sid]
            if st["n"] == 0 and st["positions"] == 0:
                continue
            t = "超t%.1f" % st["t_exc"] if st["t_exc"] is not None else "超t-"
            parts.append("%s %s %s" % (sid, _sleeve_tag(st), t))
        tail = " ｜ ".join(parts) if parts else "尚无信号"
        line = "\n> 影子账户 ¥%.0f(%+.2f%%) %s" % (
            ov["total_nav"], ov["total_ret_pct"], tail)
        return line + _exposure_line(ov.get("mix") or {})
    except Exception as e:
        return "\n> 影子账户摘要失败: %s" % str(e)[:80]


def _exposure_line(mix: dict) -> str:
    """敞口构成一行（纯函数，便于单测）。

    🔴 为什么必须印在卡片上：`max_positions` 只管「今天买几个」，不管总敞口，
    所以名义 6 个 sleeve 里 3 个打板（50%），实际持仓市值是 66%+。
    这个数每天变化、且只有把持仓摊开才算得出来 —— 不印出来就只能靠人肉核对，
    等于「分析做了但决策者看不到」。重复持仓同理：P1/P6 同日常常买完全相同的票。
    """
    total = float(mix.get("total") or 0.0)
    if total <= 0:
        return ""
    txt = "\n> 敞口 ¥%.0f，打板占 %.0f%%（P1+P2+P6）" % (total, float(mix.get("board_pct") or 0))
    dups = mix.get("dups") or {}
    if dups:
        head = sorted(dups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]
        txt += " ｜ 重复 %d 只（%s）" % (
            len(dups), " ".join("%s:%s" % (c, "/".join(ss)) for c, ss in head))
    doubled = mix.get("doubled") or {}
    if doubled:
        c, ss = sorted(doubled.items())[0]
        txt += " ｜ 单袖双份 %d 只（%s@%s）" % (len(doubled), c, "/".join(ss))
    return txt


def arena_board(det: dict) -> None:
    """盘中：把打板检测结果记进 P1（二板）/P2（首板）。"""
    try:
        import paper_scan
        today = datetime.now(CST).strftime("%Y-%m-%d")
        res = paper_scan.run_phase("board", today, det)
        sleeves = res.get("sleeves") or {}
        got = {sid: len(r.get("bought", [])) for sid, r in sleeves.items()}
        # 🔴 同其他时点一样落 picks：推荐卡片从 picks 渲染，
        # 不落这里打板新票就永远不出现在「今日推荐」里（老大 11-23 点名的病根）。
        record_picks(today, "board", sleeves)
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


def picks_path(date: str) -> Path:
    return STATE_DIR / ("picks_%s.json" % date)


def record_picks(date: str, phase: str, sleeves: dict) -> dict:
    """把本时点各模式的推荐落盘，供后续卡片复用。

    🔴 为什么要落盘：打板卡片每 3 分钟一张，如果每张只写「打板」，
    老大永远看不到 09:27 竞价、10:00 超跌推荐了什么 —— 而账本其实已经买了。
    落一份当天的 picks，任何时刻的卡片都能带出全貌。

    🔴 `bought` 必须**累积**，不能覆盖（11-23 修）：打板 phase 每 3 分钟一轮，
    早上 09:35 买满 5 只之后，后续每一轮的 `bought` 都是空列表。
    原来直接 `cur[sid] = {... "bought": []}` ⇒ 把早上的买入记录抹掉，
    于是 14:12 的卡片只剩 P3/P4 —— 和「只推打板、其他模式完全没有」是
    同一类 bug 的镜像版：账本买了，卡片没字，且不报错、不失败、测试也不红。

    合并规则：按 `code` 去重累积（同一天同一只票只列一次）；
    `at` 保留**首次**推荐时间（推荐时点应是它第一次出现）；
    `signals` 累加（当天总共冒出多少信号）；`skipped` 用最新一轮（原因更近）。
    """
    p = picks_path(date)
    try:
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        cur = {}
    now_hm = datetime.now(CST).strftime("%H:%M")
    for sid, r in (sleeves or {}).items():
        prev = cur.get(sid) or {}
        merged = list(prev.get("bought") or [])
        seen = {str(b.get("code")) for b in merged}
        for b in (r.get("bought") or []):
            c = str(b.get("code"))
            if c not in seen:
                merged.append(b)
                seen.add(c)
        cur[sid] = {
            "name": r.get("name") or prev.get("name", ""),
            "phase": phase,
            "at": prev.get("at") or now_hm,
            "signals": int(prev.get("signals") or 0) + int(r.get("signals") or 0),
            "bought": merged,
            "skipped": list(r.get("skipped") or []),
        }
    try:
        p.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        print("[arena] picks 落盘失败: %s" % e, file=sys.stderr)
    return cur


def today_picks(date: str | None = None) -> dict:
    p = picks_path(date or datetime.now(CST).strftime("%Y-%m-%d"))
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def backfill_picks_from_ledger(date: str, led: dict | None = None) -> dict:
    """从账本反推某个交易日的 picks（补齐本改动上线前的时点）。

    为什么要它：`picks_<date>.json` 是今天才有的新文件。09:27 竞价、10:00 超跌
    在改动上线前就买完了，账本里有仓、picks 文件里没有 ⇒ 当天剩下的卡片
    「今日各模式」是空的，等于没修。账本是权威，从它反推是唯一不猜的办法。

    不推断「为什么没买」以外的任何东西：`unfunded_why` 是 ingest 当时写下的原文。
    """
    if led is None:
        import paper_ledger as pl
        led = pl.load()
    import paper_strategies as ps
    out = {}
    for sid, s in (led.get("sleeves") or {}).items():
        pos = [p for p in (s.get("positions") or []) if p.get("buy_date") == date]
        if not pos:
            continue
        bought = [{"code": p["code"], "name": p.get("name", ""),
                   "price": p.get("buy_price"), "shares": p.get("shares"),
                   "note": p.get("note", "")} for p in pos if p.get("funded")]
        skipped = [{"code": p["code"], "why": p.get("unfunded_why", "")}
                   for p in pos if not p.get("funded")]
        spec = ps.STRATEGIES.get(sid) or {}
        out[sid] = {"name": spec.get("name", sid), "phase": pos[0].get("phase", ""),
                    "at": "backfill", "signals": len(pos),
                    "bought": bought, "skipped": skipped}
    if out:
        picks_path(date).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return out


def done_phases(date: str) -> set[str]:
    """当天已跑过的时点（以 marker 为准，跨进程/跨重启都认）。

    只靠内存里的 `ran` 不够：marker 是历史事实（比如本改动上线前 09:27 已经跑过），
    不读 marker 就会把「已经买过」的模式在卡片上写成「未到点」。
    """
    return {p for p, _lo, _hi, _lb in ARENA_WINDOWS
            if (STATE_DIR / ("phase_%s_%s.done" % (date, p))).exists()}


def pending_modes(done: set[str], picks: dict | None = None) -> list[tuple[str, str]]:
    """还没到时点的模式 —— 卡片写「未到 14:45」，而不是装作今天没有这个模式。

    `picks` 里已有的模式一律不算 pending：账本说它已经跑过了，
    哪怕 marker 因为历史原因丢了也不能倒着写「未到点」。
    """
    import paper_strategies as ps
    have = set((picks or {}).keys())
    out = []
    for phase, _lo, _hi, _label in ARENA_WINDOWS:
        if phase in done or phase == "close":
            continue
        for sid, _spec in ps.by_phase(phase):
            if sid in have:
                continue
            out.append((sid, mode_card.PHASE_CLOCK.get(phase, phase)))
    return out


def arena_due_phases(dry: bool = False) -> list[tuple[str, dict]]:
    """按时点跑 auction/morning/late/close，每个时点当天只跑一次。

    返回**本次真的跑出来的** [(phase, sleeves)]，交给调用方组卡片。
    返回空列表 = 这一跳没有新模式信号 ⇒ 上层按 fail-closed 静默。
    """
    now = datetime.now(CST)
    if now.weekday() >= 5:
        return []
    hm = now.hour * 60 + now.minute
    today = now.strftime("%Y-%m-%d")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    done = done_phases(today)
    ran: list[tuple[str, dict]] = []
    label_of = dict((p, lb) for p, _lo, _hi, lb in ARENA_WINDOWS)
    for phase in due_phases(hm, now.weekday(), done):
        marker = STATE_DIR / ("phase_%s_%s.done" % (today, phase))
        if phase == "close":
            if not dry:
                arena_close()
            continue
        try:
            import paper_scan
            res = paper_scan.run_phase(phase, today, dry=dry)
            sleeves = res.get("sleeves") or {}
            if not dry:
                marker.write_text(json.dumps(res, ensure_ascii=False)[:2000], encoding="utf-8")
                record_picks(today, phase, sleeves)
            brief = {sid: {"sig": r.get("signals"), "buy": len(r.get("bought", []))}
                     for sid, r in sleeves.items()}
            print("[arena] %s(%s)%s %s" % (phase, label_of[phase],
                                           " [dry]" if dry else "",
                                           json.dumps(brief, ensure_ascii=False)),
                  file=sys.stderr)
            if sleeves:
                ran.append((phase, sleeves))
        except Exception as e:
            print("[arena] %s 失败: %s" % (phase, e), file=sys.stderr)
    return ran



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


# ---------------- 推送去重（跨进程） ----------------
# 🔴 2026-11-24 老大点名「一直在推送重复的内容」。
# 根因**不是**渲染错，是去重只在**单次进程内**有效：cron 每 3 分钟起一个新进程，
# `det["new_hits"]` 每轮从零重算 ⇒ 同一批票永远被判成「新增」，同一张卡片反复推。
# 实测 09:45 / 09:48 / 09:51 / 09:54 四张卡片正文逐字相同，只有时间戳在变。
# 修法：把「今天已推过的代码」+「上一张卡片指纹」落盘，跨进程判重。
PUSH_STATE = STATE_DIR          # 与 picks_<date>.json 同目录，收工一眼看全


def card_fingerprint(md: str) -> str:
    """卡片指纹：**剥掉首行时间戳**后取 sha256。

    时间戳（`## 今日推荐 09:45`）每轮必变，算进指纹里就永远不同 ⇒ 去重必然失效。
    只剥首行，不剥正文里的 `##` 小标题，免得把真正的结构差异一起抹平。
    """
    lines = md.splitlines()
    if lines and re.match(r"^##\s.*\d{1,2}:\d{2}", lines[0]):
        lines = lines[1:]
    return hashlib.sha256("\n".join(lines).strip().encode("utf-8")).hexdigest()


def push_state_path(date: str) -> Path:
    return PUSH_STATE / ("push_%s.json" % date)


def load_push_state(date: str) -> dict:
    p = push_state_path(date)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_push_state(date: str, st: dict) -> None:
    try:
        PUSH_STATE.mkdir(parents=True, exist_ok=True)
        push_state_path(date).write_text(
            json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        # 落盘失败只影响去重，不能因此不推真信号
        print("[cloud] 推送状态落盘失败（不影响本轮）: %s" % e, file=sys.stderr)


def decide_push(state: dict, kind: str, md: str, codes, force: bool = False):
    """返回 (是否推送, 原因)。**纯函数**：只读 state，由调用方落盘。

    判据顺序（先强后弱）：
      1. force ⇒ 推（手工补跑）
      2. 出现**今天还没推过的代码** ⇒ 推（真·新增信息）
      3. 卡片指纹与上次不同 ⇒ 推（内容确实变了）
      4. 否则 ⇒ 不推 —— 这就是刷屏的来源
    """
    if force:
        return True, "force"
    seen = set(state.get("codes") or [])
    fresh = [c for c in codes if c and c not in seen]
    if fresh:
        return True, "新增 %d 只" % len(fresh)
    if card_fingerprint(md) != (state.get("hashes") or {}).get(kind):
        return True, "内容变化"
    return False, "内容与上次相同"


def _row(h: dict) -> str:
    """一行打板信号。20cm 票显式打标：

    🔴 回测过门那批（B 格 +1.37%/t19.2）用的是 `load_main_board` 的**纯主板**池，
    实盘检测池从 11-19 起混入创业板 20cm（老大拍板纳入）。两边的止损/涨停幅度、
    样本来源都不同，所以卡片必须把 20cm 标出来 —— 否则账本里一笔 20cm 的盈亏
    会被当成「主板假设被验证/被证伪」，那是错误的归因。
    """
    lim = float(h.get("board_lim") or 0.10)
    tag = "·20cm" if lim >= 0.15 else ""
    return "%s %s %7.2f %s板%s %s" % (
        h["code"], h.get("name", "")[:4], h["price"], h["board"], tag, h.get("strategy", ""))


def build_markdown(det: dict) -> str:
    """打板段（不是整张卡）：代码 名称 现价 几板 策略。new_hits 增量去重防刷屏。

    口径说明与「今日各模式」尾部由 `mode_card.render_card` 统一补，
    这里不自己写 footer —— 两张卡各写一套说明，迟早说岔。
    """
    now = datetime.now(CST).strftime("%H:%M")
    new = det.get("new_hits", [])
    act = [h for h in new if h.get("actionable")]
    obs = [h for h in new if not h.get("actionable")]
    L = ["## 打板盘中 %s 池%d只" % (now, det.get("pool", 0))]
    L.append("✅ 新增可买: " + ("\n" + "\n".join(_row(h) for h in act) if act else "无"))
    if obs:
        L.append("⚪ 一字观察(排不进):")
        L.extend(_row(h) for h in obs)
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
    # 🔴 11-25：这句原来是**行动建议**（「明日 watchlist = 上述涨停票次日再盯二板；
    # 只做B格·T+1收兑现」），而 P1/P2/P6 已全部否证 —— 按可成交口径四个高度全为负
    # （首板 −0.94%/t−16.5 … 4板+ −2.80%/t−8.0，见 test_mode_card 的口径快照）。
    # 复盘段保留「当日触板」这个事实，但不再把否证策略写成明日计划。
    L.append("> 上述为当日触板事实；二板接力已否证，不作次日买入依据")
    # 六模式结算段：收盘复盘不能又只讲打板。
    # 读账本当天 sell_date 的真实成交，0 结算写「未结算」不写 0% 超额。
    try:
        import paper_ledger as pl
        today = datetime.now(CST).strftime("%Y-%m-%d")
        # 🔴 结算段可能返回空串（一个可推模式都没有，见 mode_card.is_pushable）——
        # 判空再 append，否则会在卡片末尾留一行空行。
        seg = mode_card.render_settlement_card(pl.load(), today)
        if seg:
            L.append("\n" + seg)
    except Exception as e:
        print("[cloud] 结算段渲染失败: %s" % e, file=sys.stderr)
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recap", action="store_true", help="收盘复盘（跳过时段守卫）")
    ap.add_argument("--force", action="store_true", help="跳过时段守卫（手工补跑，会真实推送）")
    ap.add_argument("--dry-run", action="store_true", help="只检测+打印卡片，绝不调用推送（验证用）")
    ap.add_argument("--health", action="store_true", help="只看推送健康状态（不检测/不推送）")
    ap.add_argument("--backfill-picks", action="store_true",
                    help="从账本反推当日 picks 落盘（补齐本改动上线前的时点；不检测不推送）")
    args = ap.parse_args(argv)

    if args.backfill_picks:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        got = backfill_picks_from_ledger(today)
        print("[cloud] picks 回填 %s: %s" % (
            today, json.dumps({k: {"buy": len(v["bought"]), "sig": v["signals"]}
                               for k, v in got.items()}, ensure_ascii=False)))
        return 0

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

    ran_phases: list[tuple[str, dict]] = []
    ledger_line = ""
    if not args.recap:
        if det and not args.dry_run:
            arena_board(det)          # P1/P2/P6 打板接力记账
        ran_phases = arena_due_phases(dry=args.dry_run)   # P3/P4/P5 到点记账
    ledger_line = _arena_brief()

    if args.recap:
        if not det:
            print("[cloud] 收盘复盘需要检测结果，本轮跳过", file=sys.stderr)
            return 0
        today = datetime.now(CST).strftime("%Y-%m-%d")
        picks = today_picks(today)
        md = build_recap(det)
        footer = mode_card.render_daily_footer(picks, pending=pending_modes(done_phases(today), picks))
        if footer:
            md += "\n\n" + footer
        md += ledger_line
    else:
        if not det.get("new_hits") and not ran_phases:
            print("[cloud] 本轮既无新增触板、也无新模式信号，不推", file=sys.stderr)
            return 0
        # 🔴 2026-11-23 老大点名：「通知太多废话，还是只通知打板其他策略完全没有」。
        # 触发仍靠新信号，但推送内容换成**六模式今天买了什么**——
        # 之前只有打板票 + 一行笔数 + 口径长句 + 账本摘要，其他模式全被压成「P3 2笔」。
        # 不印口径、不印账本摘要：那些是文档不是推荐。
        today = datetime.now(CST).strftime("%Y-%m-%d")
        picks = today_picks(today)
        body = mode_card.render_picks_only(picks)
        if not body:
            # 🔴 11-25：卡片只推过门模式（mode_card.PUSHABLE_STATES）。
            # 「不推」必须留下正证据，区分两种情况 —— 否则老大问「怎么没通知了」时，
            # 我自己都分不清是没信号、没成交、还是模式全不过门。
            n_fill = sum(len((r or {}).get("bought") or []) for r in picks.values())
            n_ok = sum(1 for s in mode_card.STRATEGIES if mode_card.is_pushable(s))
            if n_fill:
                print("[cloud] %d 笔成交全落在未过门/已否证模式上，按「只推过门」静默"
                      "（过门 %d/%d）" % (n_fill, n_ok, len(mode_card.STRATEGIES)),
                      file=sys.stderr)
            else:
                print("[cloud] 今日无成交，静默", file=sys.stderr)
            return 0
        md = "## 今日推荐 %s\n%s" % (datetime.now(CST).strftime("%H:%M"), body)

    if args.dry_run:
        print("[cloud] DRY-RUN（未推送）\n---\n%s" % md)
        return 0

    # 🔴 跨进程去重（11-24 修刷屏，详见 decide_push 上方注释）
    day = datetime.now(CST).strftime("%Y-%m-%d")
    kind = "recap" if args.recap else "intraday"
    codes = [str(it.get("code")) for rec in (picks or {}).values()
             for it in (rec.get("bought") or [])]
    state = load_push_state(day)
    go, why = decide_push(state, kind, md, codes, force=args.force)
    if not go:
        state["n_skipped"] = int(state.get("n_skipped") or 0) + 1
        save_push_state(day, state)
        print("[cloud] 跳过推送：%s（今日累计跳过 %d 次）" % (why, state["n_skipped"]),
              file=sys.stderr)
        return 0

    ok = wecom_send(md)
    print("[cloud] push %s（%s）\n---\n%s" % ("OK" if ok else "FAIL", why, md))
    if ok:
        state["codes"] = sorted(set(state.get("codes") or []) | {c for c in codes if c})
        state.setdefault("hashes", {})[kind] = card_fingerprint(md)
        state["last_push_at"] = datetime.now(CST).strftime("%H:%M")
        state["n_skipped"] = 0
        save_push_state(day, state)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
