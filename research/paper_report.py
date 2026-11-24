#!/usr/bin/env python3
"""影子账户多策略报告 → 自包含 HTML（不依赖前端/后端，双击即看）。

回答三个问题：
1. 哪个策略在真实前向数据上赚钱了？（净值 + 胜率）
2. 现在的证据够不够硬？（样本 n、t 值、距离 t=2 还差多少笔/多少个交易日）
3. 组合视角 vs 统计视角差多少？（资金约束吃掉多少边际）

数据源：research/paper_arena.json（由 paper_scan.py 每时点写入）
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paper_ledger as pl  # noqa: E402  (sys.path 注入同目录后才可导入)
import paper_market as pm  # noqa: E402  (sys.path 注入同目录后才可导入)
from paper_strategies import STRATEGIES, oversold_pool  # noqa: E402  (sys.path 注入同目录后才可导入)
from mode_card import EVIDENCE_TAG  # noqa: E402  (判决词只有一份，不在这里另造一套)


def verdict_of(sid: str, specs=None) -> tuple[str, str, str]:
    """(判决状态, 判决词, 证据一句话) —— 纯函数。

    🔴 为什么要单独抽出来：这张表原来只印 `hypothesis`，**不印判决**。
    于是 P1 那一行读到的是「唯一在 3y 回测过门的假设」，而全部数字列都是 `—`
    （前向样本还没攒够）—— 一份把已否证策略写成「唯一过门」的报告，
    比没有报告更糟。判决是策略的属性，不是账本的属性（账本里没存 evidence）。
    """
    spec = (STRATEGIES if specs is None else specs).get(sid) or {}
    state = spec.get("evidence_state", "") or "unverified"
    tag = EVIDENCE_TAG.get(state, "⏳未过门")
    return state, tag, spec.get("evidence", "") or "（未声明证据）"


# 判决色沿用页面已有的 .down/.up/.mute，不新增 CSS
_STATE_CLS = {"rejected": "down", "passed": "up"}


def _verdict_html(sid: str) -> str:
    state, tag, why = verdict_of(sid)
    cls = _STATE_CLS.get(state, "mute")
    return ("<div class='%s' style='font-weight:600'>%s</div>"
            "<div class='hyp' style='margin-top:4px'>%s</div>" % (cls, tag, why))


def rejected_exposure(ov: dict, specs=None) -> dict:
    """当前敞口里有多少钱挂在**已被否证**的策略上 —— 纯函数。

    🔴 为什么必须单独算：这张表以前只有两样东西 —— 每行一个判决、底下一句总额度。
    两者**不交叉**，于是就有一个从来没被印出来的事实：六个 sleeve 名义上各 ¥10000、
    看起来是「六个独立假设各押一万」，实际上其中五个已经 `rejected`，
    钱压在上面不是分散，是**同时在赌五个已知负期望的东西**。

    这个数必须由持仓反算（成本法），不能用「6 个里 5 个被否 ⇒ 83%」——
    那算的是假设个数占比，不是风险敞口占比。两个数经常差很远（#601 实测 66.3% vs 50%）。
    """
    total = 0.0
    bad = 0.0
    sids: list[str] = []
    for sid, st in (ov.get("sleeves") or {}).items():
        e = float(st.get("exposure") or 0.0)
        total += e
        if e > 0 and verdict_of(sid, specs)[0] == "rejected":
            bad += e
            sids.append(sid)
    return {"total": round(total, 2), "rejected": round(bad, 2),
            "rejected_pct": round(bad / total * 100, 1) if total > 0 else 0.0,
            "sids": sorted(sids)}


def _rejected_html(ov: dict) -> str:
    rx = rejected_exposure(ov)
    if rx["total"] <= 0:
        return ""
    if rx["rejected"] <= 0:
        return ("<div class='rej'>当前敞口 <b>¥%s</b>，没有一分钱挂在已被否证的策略上。"
                "</div>" % "{:,.0f}".format(rx["total"]))
    return ("<div class='rej'>当前总敞口 <b>¥%s</b>，其中 <b>¥%s（%.1f%%）</b>"
            "挂在<b>已被否证</b>的策略上（%s）。"
            "<span class='mute'>这不是「六个假设各押一万」的分散，"
            "是同时在赌 %d 个已经知道是负期望的东西 —— 撤不撤是资金分配决策，"
            "但这一行必须每天被看见。</span></div>"
            % ("{:,.0f}".format(rx["total"]), "{:,.0f}".format(rx["rejected"]),
               rx["rejected_pct"], " / ".join(rx["sids"]), len(rx["sids"])))


OUT = HERE / "paper_report.html"
CST = timezone(timedelta(hours=8))

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Hiragino Sans GB",sans-serif;background:#fafafa;color:#111318
;padding:32px 24px 64px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:28px;border-bottom:2px solid #111318;padding-bottom:14px;margin-bottom:22px}
.top .lead{min-width:0;flex:1}
.top .lead .sub{font-size:13px;color:#6b7280;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
h1{font-size:26px;letter-spacing:-.5px;font-weight:800;white-space:nowrap}
.top .meta{flex:none;text-align:right;font-size:12px;color:#6b7280;font-variant-numeric:tabular-nums;white-space:nowrap;line-height:1.7}
.claim{background:#111318;color:#fff;padding:16px 20px;border-radius:2px;font-size:13px;line-height:1.7;margin-bottom:24px}
.claim b{color:#ffd166}
.rej{background:#fff5f5;border-left:3px solid #d9001b;padding:12px 16px;font-size:13px;line-height:1.7;margin-bottom:20px}
.rej b{color:#d9001b}
.rej .mute{color:#8b929e;font-weight:400}
.kpis{display:grid;grid-template-columns:1.6fr 1fr 1fr 1fr;gap:1px;background:#e3e5e9;border:1px solid #e3e5e9;margin-bottom:28px}
.kpi{background:#fff;padding:18px 20px}
.kpi .k{font-size:11px;color:#6b7280;letter-spacing:.08em;text-transform:uppercase}
.kpi .v{font-size:30px;font-weight:800;letter-spacing:-1px;margin-top:6px;font-variant-numeric:tabular-nums}
.kpi .s{font-size:11px;color:#9ca3af;margin-top:2px}
h2{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:#6b7280;margin:32px 0 10px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px;background:#fff}
th{text-align:left;font-size:11px;letter-spacing:.06em;color:#6b7280;font-weight:600;padding:10px 8px;border-bottom:1px solid #111318;text-transform:uppercase}
td{padding:11px 8px;border-bottom:1px solid #eceef1;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.sid{font-weight:800;font-family:ui-monospace,monospace;color:#111318}
.sname{font-weight:700}
.hyp{font-size:11px;color:#8b929e;margin-top:2px;max-width:330px}
.up{color:#d9001b;font-weight:700}
.sig{background:#111318;color:#fff;padding:1px 6px;border-radius:2px;font-size:11px;font-weight:700}
.down{color:#0a9e5c;font-weight:700}
.mute{color:#9ca3af}
.bar{position:relative;height:6px;background:#eceef1;width:110px;margin-top:16px;border-radius:0}
.bar i{position:absolute;left:0;top:0;bottom:0;background:#111318}
.bar.full i{background:#0a9e5c}
.bar b{position:absolute;left:0;top:-16px;font-size:10px;color:#6b7280;font-weight:500;white-space:nowrap}
.chart{width:100%;height:210px;background:#fff;border:1px solid #eceef1}
.legend{font-size:11px;color:#6b7280;margin-top:8px;display:flex;gap:18px;flex-wrap:wrap}
.legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:5px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{background:#fff;border:1px solid #eceef1;padding:16px 18px}
.card .hd{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #111318;padding-bottom:8px;margin-bottom:10px}
.card .hd .nm{font-weight:800;font-size:15px}
.card .hd .rt{font-family:ui-monospace,monospace;font-size:13px}
.card ul{list-style:none;font-size:12px}
.card li{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed #f0f1f3}
.card li:last-child{border:none}
.card .empty{color:#b0b5bd;font-size:12px;padding:8px 0}
.note{font-size:12px;color:#6b7280;margin-top:28px;border-top:1px solid #eceef1;padding-top:16px;line-height:1.8}
.note b{color:#111318}
.wpool{font-size:12px;color:#5b6270;line-height:2;margin-bottom:10px}
.wpool b{color:#111318}
.plist{list-style:none;display:grid;grid-template-columns:repeat(4,1fr);gap:2px 18px;font-size:12px}
.plist li{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed #f0f1f3}
.status{font-size:12px;color:#6b7280;margin:-8px 0 22px}
.status b{color:#111318}
.chip{display:inline-block;padding:2px 8px;border-radius:2px;margin-right:6px;font-size:11px}
.chip.on{background:#e8f5ee;color:#0a7a47}
.chip.off{background:#f3f4f6;color:#9ca3af}
.chip.bad{background:#fdecec;color:#b42318}
"""


def _status_line() -> str:
    """流水线状态：今日四个时点有没有真的跑过（读 marker，不猜）。"""
    today = datetime.now(CST).strftime("%Y-%m-%d")
    labels = (("auction", "09:27 竞价"), ("morning", "10:00 早盘"),
              ("late", "14:45 尾盘"), ("close", "15:05 结算"))
    chips = []
    for phase, label in labels:
        ok = (HERE / "state" / ("phase_%s_%s.done" % (today, phase))).exists()
        chips.append("<span class='chip %s'>%s %s</span>" % (
            "on" if ok else "off", label, "已跑" if ok else "待跑"))
    dates = pm.snapshot_dates()
    snap = ("快照 <b>%s</b>（共 %d 日）" % (dates[-1], len(dates))) if dates else "<b>无快照</b>"
    gaps = pm.snapshot_gaps()
    warn = ""
    if gaps:
        warn = " ｜ <span class='chip bad'>快照缺口 %d 处：%s</span>" % (
            len(gaps), "、".join("%s→%s" % g for g in gaps[-2:]))
    return "<div class='status'>今日 %s ｜ %s ｜ %s%s</div>" % (
        today, snap, "".join(chips), warn)


def _watch_pool() -> tuple[str, str]:
    """下一交易日盯盘池（用最新收盘快照算，明确标注「预告·非成交」）。"""
    dates = pm.snapshot_dates()
    if not dates:
        return "<span class='empty'>尚无收盘快照</span>", ""
    latest = dates[-1]
    snap = pm.load_snapshot(latest)
    if not snap:
        return "<span class='empty'>快照读取失败</span>", ""
    rows = snap["rows"]
    up = sum(1 for r in rows.values() if r.get("pct", 0) > 0)
    down = sum(1 for r in rows.values() if r.get("pct", 0) < 0)
    avg = sum(r.get("pct", 0) for r in rows.values()) / max(len(rows), 1)
    zt = sum(1 for c, r in rows.items()
             if r.get("pct", 0) >= pm.board_limit(c) * 100 - 0.3 and r.get("p", 1) > 1.02)
    pool = oversold_pool(rows)
    head = ("<div class='wpool'>数据基准 <b>%s</b> 收盘快照 ｜ 全市场 %d 只 ｜ 涨 %d / 跌 %d ｜ 均涨幅 %+.2f%%<br>"
            "次日涨停池（P1/P2 打板待触发）<b>%d</b> 只 ｜ 次日超跌反抽池（P3 待 10:00 确认）<b>%d</b> 只</div>"
            % (latest, len(rows), up, down, avg, zt, len(pool)))
    top = pool[:8]
    if not top:
        return head, "<div class='empty'>当日无可接的超跌标的（跌幅未达门槛或流动性不足）</div>"
    lis = "".join("<li><span>%s %s</span><span class='down'>%+.1f%%</span></li>" % (
        p["code"], p["name"][:6], p["pct"]) for p in top)
    return head, "<ul class='plist'>%s</ul>" % lis


def _fmt(v, suffix="", plus=False):
    if v is None:
        return "<span class='mute'>—</span>"
    cls = ""
    if plus and isinstance(v, (int, float)):
        cls = " class='up'" if v > 0 else (" class='down'" if v < 0 else "")
    if isinstance(v, float):
        s = "%+.2f" if plus else "%.2f"
        return ("<span%s>" + s + "%s</span>") % (cls, v, suffix)
    return ("<span%s>%s%s</span>") % (cls, v, suffix)


def _nav_chart(sleeves: dict) -> str:
    """净值曲线：组合等权（黑实线）+ 各 sleeve 细线 + **全市场等权基准**（灰虚线）。

    基准线是必须的：没有它，大盘涨出来的收益会被当成策略的本事。
    """
    series = {}
    for sid, s in sleeves.items():
        for row in s["nav"]:
            series.setdefault(row["date"], {})[sid] = row["ret_pct"]
    if len(series) < 2:
        return "<div class='empty' style='padding:40px;text-align:center;color:#b0b5bd'>净值曲线待积累（至少 2 个交易日）</div>"
    dates = sorted(series)
    colors = {"P1": "#d9001b", "P2": "#e8791f", "P3": "#2f5fd0", "P4": "#7b3fd4",
              "P5": "#0a9e5c", "P6": "#c2185b"}
    allv = [0.0]
    for i, d in enumerate(dates):
        prev = {sid: 0.0 for sid in sleeves}
        if i:
            for sid in sleeves:
                prev[sid] = series[dates[i - 1]].get(sid, 0.0)
        port = sum(series[d].get(sid, prev[sid]) for sid in sleeves) / max(len(sleeves), 1)
        allv.extend(series[d].values())      # 各 sleeve 自己的线也要进坐标范围，否则会被裁掉
        series[d]["__port"] = port
        allv.append(port)
    # 单位必须统一：策略线是「百分比」，基准也换成百分比（之前用小数，会把基准画平在 0 附近）
    bench = [(v / 100.0 - 1) * 100 for v in pm.bench_series(dates, base=100.0)]
    allv.extend(bench)
    lo, hi = min(allv), max(allv)
    span = (hi - lo) or 1.0
    W, H = 1000, 210
    pad = 8

    def xy(i, v):
        x = pad + i / (len(dates) - 1) * (W - 2 * pad)
        y = H - pad - (v - lo) / span * (H - 2 * pad)
        return x, y

    parts = ["<svg viewBox='0 0 %d %d' class='chart' preserveAspectRatio='none'>" % (W, H)]
    y0 = H - pad - (0 - lo) / span * (H - 2 * pad)
    if 0 <= y0 <= H:
        parts.append("<line x1='0' y1='%.1f' x2='%d' y2='%.1f' stroke='#c9ccd2' stroke-dasharray='4 4'/>" % (y0, W, y0))
    for sid in list(sleeves):
        pts = []
        for i, d in enumerate(dates):
            v = series[d].get(sid)
            if v is None:
                continue
            pts.append("%.1f,%.1f" % xy(i, v))
        if len(pts) >= 2:
            parts.append("<polyline points='%s' fill='none' stroke='%s' stroke-width='1.2' opacity='.45'/>" % (
                " ".join(pts), colors.get(sid, "#999")))
    bp = ["%.1f,%.1f" % xy(i, v) for i, v in enumerate(bench)]
    parts.append("<polyline points='%s' fill='none' stroke='#9ca3af' stroke-width='1.5' stroke-dasharray='5 4'/>" % " ".join(bp))
    pts = ["%.1f,%.1f" % xy(i, series[d]["__port"]) for i, d in enumerate(dates)]
    parts.append("<polyline points='%s' fill='none' stroke='#111318' stroke-width='2.2'/>" % " ".join(pts))
    # 轴标：没有刻度线，就没法判断波动幅度
    parts.append("<text x='3' y='14' font-size='11' fill='#9ca3af'>%+.1f%%</text>" % hi)
    parts.append("<text x='3' y='%d' font-size='11' fill='#9ca3af'>%+.1f%%</text>" % (H - 20, lo))
    parts.append("<text x='3' y='%d' font-size='11' fill='#b0b5bd'>%s</text>" % (H - 5, dates[0]))
    parts.append("<text x='%d' y='%d' font-size='11' fill='#b0b5bd' text-anchor='end'>%s</text>" % (W - 3, H - 5, dates[-1]))
    parts.append("</svg>")
    legend = "<div class='legend'><span><i style='background:#111318'></i>组合等权</span>" \
             "<span><i style='background:#9ca3af'></i>全市场等权基准</span>" + "".join(
        "<span><i style='background:%s'></i>%s %s</span>" % (colors.get(sid, "#999"), sid, s["name"])
        for sid, s in sleeves.items()) + "</div>"
    return "".join(parts) + legend


def render() -> str:
    led = pl.load()
    ov = pl.overview(led)
    sleeves = led["sleeves"]
    today = datetime.now(CST).strftime("%Y-%m-%d %H:%M")

    # 总览表
    rows = []
    for sid, s in sleeves.items():
        st = ov["sleeves"][sid]
        days, need = st["exc_days"], st["n_need_exc"]
        if need and need > 0:
            pct = min(days / need, 1.0) * 100
            bar = "<div class='bar%s'><b>%d/%d 日</b><i style='width:%.0f%%'></i></div>" % (
                " full" if pct >= 100 else "", days, need, pct)
            eta = "还需约 %d 个交易日" % max(need - days, 0)
        elif st["n"]:
            bar = "<div class='bar'><b>%d 日</b><i style='width:0'></i></div>" % days
            eta = ("<span class='down'>超额为负·不可达</span>" if (st["exc_mean"] or 0) <= 0
                   else "<span class='mute'>样本已够</span>")
        else:
            bar = "<span class='mute'>—</span>"
            eta = "<span class='mute'>待积累</span>"
        tcell = ("<span>%.2f</span>" % st["t_exc"]) if st["t_exc"] is not None else "<span class='mute'>—</span>"
        q = st.get("q_exc")
        if q is None:
            qcell = "<span class='mute'>—</span>"
        elif q < 0.10:
            qcell = "<span class='sig'>%.3f</span>" % q
        else:
            qcell = "<span class='mute'>%.3f</span>" % q
        imp = st.get("stop_impact")
        stopcell = ("<span class='mute'>—</span>" if imp is None else
                    ("<span class='down'>%.2fpp</span>" % imp if imp < 0 else
                     "<span class='up'>+%.2fpp</span>" % imp))
        if st.get("stop_n"):
            stopcell += "<div class='hyp' style='margin-top:6px'>%d 笔止损</div>" % st["stop_n"]
        rows.append(
            "<tr><td class='sid'>%s</td><td><div class='sname'>%s</div><div class='hyp'>%s</div></td>"
            "<td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s<div class='hyp' style='margin-top:6px'>%s</div></td><td>%s</td></tr>" % (
                sid, s["name"], s["hypothesis"],
                _verdict_html(sid),
                _fmt(st["exc_mean"], "%", plus=True) if st["exc_mean"] is not None else "<span class='mute'>—</span>",
                _fmt(st["exc_win"], "%") if st["exc_win"] is not None else "<span class='mute'>—</span>",
                tcell, qcell, stopcell, bar, eta,
                _fmt(st["funded_ret"], "%", plus=True) if st["n"] else "<span class='mute'>—</span>"))

    # 每个 sleeve 卡片
    cards = []
    for sid, s in sleeves.items():
        st = ov["sleeves"][sid]
        pos = [p for p in s["positions"] if p["funded"]]
        pos_html = "".join(
            "<li><span>%s %s</span><span>%.2f ×%d</span></li>" % (
                p["code"], p["name"], p["buy_price"], p["shares"]) for p in pos) \
            or "<div class='empty'>空仓</div>"
        recent = [t for t in s["trades"]][-4:][::-1]
        tr_html = "".join(
            "<li><span>%s %s <span class='mute'>%s</span></span><span>%+.1f%%"
            "<span class='mute'> · 超%+.1f%%</span></span></li>" % (
                t["code"], t["name"], t["reason"], t["ret_pct"],
                t.get("excess_pct") if t.get("excess_pct") is not None else 0.0) for t in recent) \
            or "<div class='empty'>尚无平仓</div>"
        uf = st.get("unfunded") or {}
        uf_txt = ""
        if uf:
            top = sorted(uf.items(), key=lambda kv: -kv[1])[:2]
            uf_txt = "｜未成交 " + "、".join("%s×%d" % (k, v) for k, v in top)

        cards.append(
            "<div class='card'><div class='hd'><span class='nm'>%s · %s</span>"
            "<span class='rt %s'>%+.2f%%</span></div>"
            "<div style='font-size:11px;color:#8b929e;margin-bottom:10px'>样本 %d 笔（成交 %d 笔）｜胜率 %s｜持有至 %s%s</div>"
            "<div style='font-size:11px;color:#6b7280;margin:6px 0 2px'>持仓</div><ul>%s</ul>"
            "<div style='font-size:11px;color:#6b7280;margin:12px 0 2px'>最近平仓</div><ul>%s</ul></div>" % (
                sid, s["name"], "up" if st["funded_ret"] > 0 else ("down" if st["funded_ret"] < 0 else ""),
                st["funded_ret"], st["n"], st["funded_n"],
                ("%.0f%%" % st["win"]) if st["win"] is not None else "—",
                "次日开盘" if s["rules"]["exit"] == "t1_open" else "T+1收盘",
                uf_txt, pos_html, tr_html))

    start = led["meta"].get("created_at", "")[:10]
    tot_n = sum(ov["sleeves"][sid]["n"] for sid in sleeves)
    pool_head, pool_list = _watch_pool()
    nav_dates = sorted({r["date"] for s in sleeves.values() for r in s["nav"]})
    bench_txt = ""
    if len(nav_dates) >= 2:
        bs = pm.bench_series(nav_dates, base=100.0)
        if bs and bs[0]:
            bench_txt = " ｜ 基准 %+.2f%%" % ((bs[-1] / bs[0] - 1) * 100)
    head = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>影子账户 · 短线多策略前向验证</title><style>""" + CSS + """</style></head><body>"""
    body = """<div class="wrap">
<div class="top"><div class="lead"><h1>影子账户 · 短线多策略</h1>
<div class="sub">真实信号 + 真实价格 + 虚拟资金 ｜ 前向验证，无回测成分</div></div>
<div class="meta">起算 %s<br>生成 %s<br>虚拟本金 ¥%s（%d 个策略各 ¥10000）</div></div>
%s
__REJBLOCK__

<div class="claim"><b>诚实声明</b>　这不是真下单，也无法保证赚钱。账本记的是「若严格按规则执行，今天会怎样」。<br>
每个策略的阈值是<b>先验设定、一次定死、永不因亏损调参</b>——调了就变成回测过拟合，账本也就不再有前向验证的意义。<br>
样本没到统计显著之前，任何「这个策略赚了」的结论都只能算观察，不算证据。</div>

<div class="kpis">
<div class="kpi"><div class="k">组合虚拟净值</div><div class="v">¥%.2f</div><div class="s">累计 %+.2f%%（%d 策略等权）%s</div></div>
<div class="kpi"><div class="k">累计样本</div><div class="v">%d</div><div class="s">笔（含未占仓信号）</div></div>
<div class="kpi"><div class="k">已运行</div><div class="v">%d</div><div class="s">个交易日</div></div>
<div class="kpi"><div class="k">扛住多重检验的策略</div><div class="v">%d</div><div class="s">q&lt;0.10 且超额为正</div></div>
</div>

<h2>策略横向对比</h2>
<table><tr><th>ID</th><th>策略 / 假设</th><th>判决（已做的证据）</th><th>超额均收益</th><th>超额胜率</th><th>t（按日聚类）</th><th>q（FDR）</th><th>止损影响</th><th>统计进度</th><th>组合收益</th></tr>
%s</table>

<h2>下一交易日盯盘池 <span style="font-size:10px;color:#b0b5bd;letter-spacing:0">预告·尚未成交</span></h2>
<div class="card" style="border-color:#e3e5e9">%s%s</div>

<h2>净值曲线</h2>
%s

<h2>各策略明细</h2>
<div class="cards">%s</div>

<div class="note"><b>两个口径为什么要分开记</b><br>
「信号均收益 / t 值」用<b>全部合格信号</b>（含没抢到仓位的）等权统计——这是判断假设真假的样本；
「组合收益」只算实际买到的那 5 个仓位——这是 ¥10000 真实能拿到的结果。仓位数一卡，样本就永远攒不起来，
所以两者必须分开，否则要么等一年，要么用回测骗自己。<br><br>
<b>t 值怎么读</b>　t≥2 才能说「有点证据」（约 95%% 置信）；t&lt;1 就是纯噪声。<b>t 按交易日聚类</b>——
同一天 30 个信号只算 1 个观测，否则系统性行情会让「样本量」虚高十几倍、几天就假显著。
统计进度条走满只是<b>可以下结论</b>，不是「一定会赚」——结论也可能是「显著为负，判死」。<br><br>
<b>成本口径</b>　单边 0.15%%、往返 0.3%%（佣金2.5bp×2 + 印花税10bp + 过户费 + 收盘竞价滑点余量）；
11-23 之前的老成交按当时的单边 1%% 落账，每笔自带费率、不互相反算。A 股 100 股整手；
打板 A 格（一字板）一律不买，因为排不进队。<br><br>
<b>止损口径</b>　11-23 起<b>不设止损</b>：过门的回测数字（P1 h1 +3.06%%、B 格 +1.37%%）本身就是无止损口径，
账本若加 -5%% 会让前向永远追不上回测。实测 -5%% 档触发率 25.9%%、每笔少 0.21pp，
因为 A 股 T+1 下固定百分比止损是「有下行无保护」：跳空穿线照亏、砸破回升砍在坑里。<br><br>
<b>¥1 万的硬约束（这一条很重要）</b>　本金 1 万、单笔 20%% = 每笔 ¥2000，只够买 1 手 **≤¥19.8** 的票。
价格更高的票信号照记、但记成「买不起 1 手」——这不是程序 bug，是小资金的真实约束。
所以「组合收益」天然被低价股样本主导，判断策略好坏要看<b>信号均收益 + t 值</b>那一列，别看组合收益。<br><br>
<b>为什么所有判据都是「超额」而不是「收益」</b>　策略赚的可能是大盘的钱（beta），不是本事（alpha）。
所以每笔都减掉同期全市场等权收益，t 值也算在超额上；净值曲线里那条灰虚线就是基准。
<b>只看绝对收益，牛市里每个策略都像神。</b><br><br>
<b>打板只买 B 格</b>　A 格（开盘一字）排不进队，记成观察、不进成交样本。
B 格 =「集合竞价没顶死 + 盘中摸板」，说明确实存在可买时点，<b>照买</b>。
检测到它的那一刻通常已经封死，但回测过门的那批本来就全是「收停板」，
所以封死只作 <code>sealed_entry</code> 标记、单独计数，<b>不拦买入</b> ——
拦了就等于把过门的作业集整个抹掉，账本会连着几个月 0 成交。<br><br>
<b>一字跌停卖不掉</b>　打板最狠的亏损来自 T+1 直接一字跌停——挂单也卖不出，只能次日继续跌。
账本对这种情况按「顺延持有」处理（最多 10 天，超了按收盘强平并标注为乐观上界），
不做这一步，P1/P2 的收益会被系统性高估。<br><br>
<b>多策略一起测的代价</b>　同时跑 __M_PLANNED__ 个假设，其中至少有一个「碰巧 t≥2」的概率约 __FWER__%%
（1−0.95^__M_PLANNED__）—— 也就是说，「挑最像样的那个」很容易挑到撞运气撞出来的。
所以最终判定不会只看单个 t 值：要么 t≥2.5，要么做 BH-FDR 多重检验校正，且要有第二段样本延续。
<b>先看谁显著为负（判死），比看谁显著为正更可靠。</b>
</div></div></body></html>""" % (
        start, today, "{:,.0f}".format(ov["total_capital"]), len(sleeves),
        _status_line(),
        ov["total_nav"], ov["total_ret_pct"], len(sleeves), bench_txt,
        tot_n, max(len(s["nav"]) for s in sleeves.values()) if sleeves else 0,
        sum(1 for sid in sleeves if (ov["sleeves"][sid]["q_exc"] or 1) < 0.10 and (ov["sleeves"][sid]["exc_mean"] or 0) > 0),
        "".join(rows), pool_head, pool_list,
        _nav_chart(sleeves), "".join(cards))
    # 多重检验的 m 必须跟着 sleeve 数走，不能写死：
    # 每加一个 sleeve，判据就更严一点，这页面的说法也要跟着变，否则会写着一个过期的门槛。
    m_planned = len(sleeves)
    body = (body.replace("__REJBLOCK__", _rejected_html(ov))
                .replace("__M_PLANNED__", str(m_planned))
                .replace("__FWER__", "%.0f" % ((1 - 0.95 ** m_planned) * 100)))
    return head + body


def main() -> int:
    html = render()
    OUT.write_text(html, encoding="utf-8")
    print("报告已生成:", OUT, "(%d 字节)" % len(html))
    return 0


if __name__ == "__main__":
    sys.exit(main())
