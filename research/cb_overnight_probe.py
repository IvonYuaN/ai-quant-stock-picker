#!/usr/bin/env python3
"""可转债隔夜策略探针 —— 唯一一个过得了成本门槛、且符合 T+1 的方向。

## 为什么转到转债

股票侧的结论（#594/#595）是结构性的：

    隔夜（收→次日开） −0.079%/天   日内（开→收） +0.111%/天   全段 +0.030%/天

收益几乎全在日内，而 **T+1 恰好禁止当日买当日卖** ⇒ 所有「今天买明天卖」的股票
短线策略只能吃到负隔夜 ⇒ 在 0.15%/笔的成本下必然为负。

转债是 T+0，看起来能绕开。实测**恰好相反**：

    转债 隔夜 +0.047%/天(t3.3)   日内 −0.007%/天(t−0.2)

转债的收益全在隔夜、日内是零 —— 所以 T+0 那条路当场死了（买开盘卖收盘赚不到钱）。
但「收盘买、次日开盘卖」**完全符合 T+1**（隔日卖），吃的正是转债唯一的正收益段。

## 🔴 我自己先犯过一次前视（务必别重犯）

第一版用**今日**成交额选票、算**今日**隔夜收益，得到 +0.265%/天(t5.83)。
成交量本来是被涨跌催出来的 —— 拿今日成交额选今日收益，就是按结果筛样本。
「算出不可能的数字 = 最好用的验伪器」：那一版的全段收益折年化 **215%**，荒谬到自证前视。

改成**昨日**成交额选票后降到 +0.129%/天。本文件的 `--show-lookahead` 会同时打印
两个口径，把差值摆在明面上，防止下次又悄悄犯。

## 可执行口径

- 选票：**昨日**收盘价 > 150 元 且 **昨日**成交额在合格池里排前 1/3
- 买入：**昨日收盘价**（收盘集合竞价成交价）
- 卖出：**今日开盘价**
- 成本：往返 0.04%（纯佣金，转债免印花税）／0.10%（含小额滑点）／0.16%（含较大滑点）

## 🔴 已知偏差（必须一起报）

1. **幸存者偏差**：列表取的是**今天在市**的转债，已强赎/到期的不在样本里。
   强赎债通常先冲到 130~150 再摘牌，方向不明，但样本不完整是事实。
2. **收盘价可成交性**：依赖收盘集合竞价；实际下单会有冲击，日线数据测不出来。
3. **滑点无法从日线标定** —— 这是它不能算「过门」的唯一原因。要过门得用真实盘口
   做纸面成交（每天 14:57 记买一/卖一），积累 20 个交易日即可标定。
4. 全段（收→收）t 只有 0.83：日内噪声大，**只有隔夜腿可赚**，不能拿全段说事。

用法：
    python research/cb_overnight_probe.py --cache research/cb_daily.json --json out.json
    python research/cb_overnight_probe.py --fetch --cache research/cb_daily.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

PRICE_LO = 130.0            # 强赎触发线（连续 30 个交易日中 15 日 ≥ 转股价 130%）
TOP_FRAC = 1.0 / 3          # 昨日成交额前 1/3
COST_LEVELS = [(0.0004, "佣金0.04%"), (0.0008, "含冲击0.08%"), (0.0013, "含冲击0.13%")]
BAD_MOVE = 0.35             # 转债临停 20%/30%，>35% 视为脏数据

# 🔴 切点为什么是 130 而不是 150（走过一次弯路，记下来）：
# 第一版凭"高价债股性强"随手取了 150，结果 t 只有 2.14、样本仅 416 天。
# 阈值扫描后才发现 100/120/130/150/180/220 是**单调梯度**（0.063→0.080→0.106→0.103→0.151→0.124），
# 150 恰好是样本最少、最不稳的那一段。130 同时满足三个条件：
#   ① 有制度先验（强赎线），不是从数据里挑出来的
#   ② 落在单调段上（100→130 单调上升）
#   ③ 样本 1126 天（2021-08 起），t 5.28
# 但扫描本身消耗了自由度 ⇒ 后来又做了「前段拟合 / 后段检验」的样本外切分，
# 样本外 +0.0785%/天 t2.07（明显衰减）。两个数都要报，不能只报好看的。


# ---------------- 取数 ----------------

def _sym(code: str) -> str:
    """转债代码 → 腾讯行情代码。沪市：110/111/113/118/132（都以 11、13 开头）；
    深市：123/127/128（都以 12 开头）。

    🔴 原来写的是 `"sh" if code[0] in "16" else "sz"` —— 把所有 1 开头的都判成沪市。
    深市转债占全池 **51%**（123:103 + 127:58 + 128:1 = 162 / 315）。
    拿 `sh123255` 去请求，腾讯**照样返回历史、而且逐行与 `sz123255` 完全相同**，
    只是**永远少最后一根 K 线**。后果极其隐蔽：

      · 历史统计完全不受影响（所以 #600 的分年/t 值/样本外都还站得住）；
      · 但**最新一天深市转债整批缺席** ⇒ 它们既进不了当天的候选池，
        也从来不出现在「今日兑现」的均值里 —— 而这两件事正是每日清单的全部内容。
    """
    return ("sz" if code.startswith("12") else "sh") + code


def fetch_list() -> dict:
    """东财转债板块列表（push2 主域常 502，delay 域可用），分页 pz=100。"""
    out: dict = {}
    for pn in range(1, 13):
        u = ("http://push2delay.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
             "&fltt=2&invt=2&fid=f3&fs=b:MK0354&fields=f12,f14" % pn)
        req = urllib.request.Request(u, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "http://quote.eastmoney.com/"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore"))
        diff = (d.get("data") or {}).get("diff") or []
        if not diff:
            break
        for x in diff:
            out[x.get("f12")] = x.get("f14")
        time.sleep(0.15)
    return out


def fetch_daily(codes, n=640) -> dict:
    """腾讯日线（不复权）。转债不拆股，raw 即可；单次上限 320，640 会自动截断到可得长度。"""
    out: dict = {}
    for i, code in enumerate(sorted(codes)):
        u = ("http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,"
             % (_sym(code), n))
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
            node = (d.get("data") or {}).get(_sym(code)) or {}
            k = node.get("day") or []
            if len(k) >= 100:
                # 腾讯字段序：[date, open, close, high, low, volume]
                out[code] = [[r[0], float(r[1]), float(r[2]), float(r[5])] for r in k]
        except (OSError, ValueError, IndexError):
            pass
        if i % 60 == 0:
            time.sleep(0.2)
    return out


# ---------------- 计算 ----------------

def build_panel(raw: dict):
    dates = sorted({r[0] for v in raw.values() for r in v})
    di = {d: i for i, d in enumerate(dates)}
    codes = sorted(raw)
    T, N = len(dates), len(codes)
    op = np.full((T, N), np.nan)
    cl = np.full((T, N), np.nan)
    amt = np.full((T, N), np.nan)
    for j, c in enumerate(codes):
        for r in raw[c]:
            t = di[r[0]]
            op[t, j], cl[t, j] = r[1], r[2]
            amt[t, j] = r[3] * r[2]        # 量×价，只用于横截面排序
    prev = np.vstack([np.full((1, N), np.nan), cl[:-1]])
    amt_prev = np.vstack([np.full((1, N), np.nan), amt[:-1]])
    with np.errstate(invalid="ignore", divide="ignore"):
        over = op / prev - 1.0
        intra = cl / op - 1.0
        full = cl / prev - 1.0
    bad = (np.abs(over) > BAD_MOVE) | (np.abs(intra) > BAD_MOVE)
    for a in (over, intra, full):
        a[bad] = np.nan
    return dates, codes, prev, amt, amt_prev, over, intra, full


def select(prev_t, amt_t):
    """返回当日入选列号。`amt_t` 由调用方决定给「昨日」还是「今日」成交额。"""
    ok = np.isfinite(prev_t) & (prev_t > PRICE_LO) & np.isfinite(amt_t)
    c = np.where(ok)[0]
    if c.size < 9:
        return None
    order = np.argsort(amt_t[c])
    k = int(c.size * TOP_FRAC)
    return c[order[c.size - k:]]


def daily_records(a, sel_fn, prev, dates, T):
    """返回 [(date, 日均值, 持仓只数, 均价)]。持仓只数与均价用于估资金需求。"""
    out = []
    for t in range(1, T):
        s = sel_fn(t)
        if s is None:
            continue
        v = a[t, s]
        v = v[np.isfinite(v)]
        if v.size >= 5:
            px = prev[t, s]
            px = px[np.isfinite(px)]
            out.append((dates[t], float(v.mean()), int(v.size),
                        float(px.mean()) if px.size else 0.0))
    return out


def daily_series(a, sel_fn, T):
    out = []
    for t in range(1, T):
        s = sel_fn(t)
        if s is None:
            continue
        v = a[t, s]
        v = v[np.isfinite(v)]
        if v.size >= 5:
            out.append(float(v.mean()))
    return np.asarray(out, dtype=float)


def summarise(x: np.ndarray) -> dict:
    if x.size < 30:
        return {"n_days": int(x.size), "note": "样本<30"}
    t = x.mean() / (x.std(ddof=1) / np.sqrt(x.size))
    return {"n_days": int(x.size), "mean_pct": round(float(x.mean()) * 100, 4),
            "median_pct": round(float(np.median(x)) * 100, 4),
            "win_pct": round(float((x > 0).mean()) * 100, 1), "t": round(float(t), 2)}


def cost_table(x: np.ndarray) -> dict:
    out = {}
    for ct, lab in COST_LEVELS:
        r = x - ct
        eq = np.cumprod(1 + r)
        peak = np.maximum.accumulate(eq)
        sr = (r.mean() / r.std(ddof=1)) * np.sqrt(240) if r.std(ddof=1) > 0 else float("nan")
        out[lab] = {"cost_pct": round(ct * 100, 2),
                    "annual_pct": round(((1 + r.mean()) ** 240 - 1) * 100, 1),
                    "sharpe": round(float(sr), 2),
                    "mdd_pct": round(float((eq / peak - 1).min()) * 100, 1),
                    "worst_day_pct": round(float(r.min()) * 100, 2)}
    return out


def by_year(records: list) -> dict:
    """records = [(date, daily_mean, n_held, avg_price)]。**必须分年看**：

    全样本 +0.106%/天 看着稳，分年才露出逐年衰减（0.170→0.066）。
    只报全样本 = 把一条正在变弱的 alpha 说成稳定的。
    """
    buckets: dict = {}
    for d, v, _n, _p in records:
        buckets.setdefault(d[:4], []).append(v)
    out = {}
    for y in sorted(buckets):
        a = np.asarray(buckets[y], dtype=float)
        if a.size < 40:
            continue
        out[y] = {"n_days": int(a.size), "mean_pct": round(float(a.mean()) * 100, 4),
                  "t": round(float(a.mean() / (a.std(ddof=1) / np.sqrt(a.size))), 2)}
    return out


def oos_split(records: list, cut: str) -> dict:
    """前段拟合 / 后段检验。规则完全相同、不重估 ⇒ 后段就是样本外。"""
    a = np.asarray([v for d, v, _n, _p in records if d < cut], dtype=float)
    b = np.asarray([v for d, v, _n, _p in records if d >= cut], dtype=float)
    out = {"cut": cut}
    for lab, x in (("in_sample", a), ("out_sample", b)):
        if x.size < 40:
            out[lab] = {"n_days": int(x.size), "note": "样本<40"}
            continue
        out[lab] = {"n_days": int(x.size), "mean_pct": round(float(x.mean()) * 100, 4),
                    "t": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(x.size))), 2)}
    return out


def probe(raw: dict, show_lookahead: bool = False, oos_cut: str = "2025-07-01") -> dict:
    dates, codes, prev, amt, amt_prev, over, intra, full = build_panel(raw)
    T = len(dates)
    res: dict = {"as_of": dates[-1], "n_bonds": len(codes),
                 "n_days": T, "price_lo": PRICE_LO, "top_frac": round(TOP_FRAC, 3),
                 "note": "买=昨日收盘 卖=今日开盘；转债免印花税"}

    def sel_exec(t):                    # 可执行：昨日成交额
        return select(prev[t], amt_prev[t])

    def sel_look(t):                    # 前视对照：今日成交额
        return select(prev[t], amt[t])

    for lab, a in (("over", over), ("intra", intra), ("full", full)):
        res[lab] = summarise(daily_series(a, sel_exec, T))
    x = daily_series(over, sel_exec, T)
    recs = daily_records(over, sel_exec, prev, dates, T)
    res["cost"] = cost_table(x)
    res["by_year"] = by_year(recs)
    if recs:
        res["by_year"]["_span"] = "%s ~ %s" % (recs[0][0], recs[-1][0])
        res["capacity"] = {
            "avg_names_per_day": round(float(np.mean([r[2] for r in recs])), 1),
            "avg_price": round(float(np.mean([r[3] for r in recs])), 1),
            "capital_wan_per_day": round(float(np.mean([r[2] for r in recs]))
                                         * 10 * float(np.mean([r[3] for r in recs])) / 1e4, 1),
            "note": "1 手=10 张；只够买 1 手的最小资金"}
    res["oos"] = oos_split(recs, oos_cut)
    if show_lookahead:
        res["lookahead_control"] = {
            "over_today_amt": summarise(daily_series(over, sel_look, T)),
            "full_today_amt": summarise(daily_series(full, sel_look, T)),
            "note": "选票时点用「今日」成交额 = 按结果筛样本，数字会虚高，只作反面教材",
        }
    return res


def _fmt(r: dict) -> str:
    if "mean_pct" not in r:
        return "样本不足(%s)" % r.get("note", "")
    return "%+.4f%%/天 t%+.2f 胜%4.1f%% (n=%d)" % (
        r["mean_pct"], r["t"], r["win_pct"], r["n_days"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="research/cb_daily.json")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--show-lookahead", action="store_true")
    ap.add_argument("--price-lo", type=float, default=PRICE_LO,
                    help="价格门槛。默认 130（强赎线）；做稳健性扫描时才改")
    ap.add_argument("--top-frac", type=float, default=TOP_FRAC)
    ap.add_argument("--oos-cut", default="2025-07-01")
    a = ap.parse_args(argv)

    # 阈值要能被扫描，但**不能悄悄改**：显式覆盖模块常量，输出里会印出来
    globals()["PRICE_LO"] = a.price_lo
    globals()["TOP_FRAC"] = a.top_frac

    p = Path(a.cache)
    if a.fetch or not p.exists():
        lst = fetch_list()
        print("转债列表 %d 只，开始拉日线…" % len(lst))
        raw = fetch_daily(lst.keys())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(raw), encoding="utf-8")
        print("成功 %d 只 → %s" % (len(raw), p))
    raw = json.loads(p.read_text(encoding="utf-8"))

    res = probe(raw, show_lookahead=a.show_lookahead, oos_cut=a.oos_cut)
    print("=" * 78)
    print("转债隔夜策略：昨收买 → 次开卖")
    print("截至 %s ｜ %d 只转债 ｜ 昨收>%.0f元 + 昨日成交额前 %.0f%%"
          % (res["as_of"], res["n_bonds"], res["price_lo"], res["top_frac"] * 100))
    print("=" * 78)
    for lab, name in (("over", "隔夜(可赚)"), ("intra", "日内(赚不到)"), ("full", "全段")):
        print("  %-12s %s" % (name, _fmt(res[lab])))
    print("\n  成本敏感性（隔夜腿）：")
    for lab, v in res["cost"].items():
        print("    %-12s 年化 %+6.1f%%  SR %5.2f  最大回撤 %6.1f%%  最差单日 %+5.2f%%"
              % (lab, v["annual_pct"], v["sharpe"], v["mdd_pct"], v["worst_day_pct"]))
    if res.get("by_year"):
        print("\n  分年（🔴 只报全样本 = 把正在变弱的 alpha 说成稳定的）：")
        for y, v in res["by_year"].items():
            if y == "_span":
                continue
            print("    %s %+.4f%%/天 t%+.2f (n=%d日)" % (y, v["mean_pct"], v["t"], v["n_days"]))
    oos = res.get("oos") or {}
    if "in_sample" in oos and "out_sample" in oos:
        print("\n  样本外切分（规则不变、不重估）%s：" % oos["cut"])
        for lab, name in (("in_sample", "样本内"), ("out_sample", "样本外")):
            v = oos[lab]
            if "mean_pct" in v:
                print("    %-6s %+.4f%%/天 t%+.2f (n=%d日)" % (name, v["mean_pct"], v["t"], v["n_days"]))
    cap = res.get("capacity")
    if cap:
        print("\n  容量：日均 %.1f 只 × 1 手 × 均价 %.0f 元 ≈ %.1f 万元/日"
              % (cap["avg_names_per_day"], cap["avg_price"], cap["capital_wan_per_day"]))
    if "lookahead_control" in res:
        lc = res["lookahead_control"]
        print("\n  🔴 前视对照（反面教材，不可用）：")
        print("    用今日成交额选票 · 隔夜 %s" % _fmt(lc["over_today_amt"]))
        print("    用今日成交额选票 · 全段 %s" % _fmt(lc["full_today_amt"]))
    print()
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("写出 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
