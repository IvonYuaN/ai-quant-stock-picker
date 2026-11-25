#!/usr/bin/env python3
"""盘中「二板确认」实时检测器 —— 给通知系统喂信号（研究侧，不碰生产）。

逻辑（与 shortline_exec.py 的「③ 追二板」过门结论一致）：
  - 输入：昨日涨停的主板 watchlist（来自 forward_watchlist，或本脚本自读库）
  - 盘中轮询腾讯实时行情：哪只「今天触板」(现价 >= 今涨停价) = 二板确认
  - 触发 → 输出一行可推送信号：「<code> 二板确认 @现价，买二板价、T+1 收盘兑现」
  - 只报一次（去重），避免盘中反复刷屏

腾讯 qt.gtimg.cn 字段（split "~"，0-based）：
  [1]=名 [2]=代码 [3]=现价 [4]=昨收 [5]=今开 [30]=时间戳 [31]=涨跌额 [32]=涨跌幅%
  [33]=最高 [34]=最低。主板 10% 涨停价 = round(昨收*1.10, 2)；北交/创业 20% 不适用（已排）。
触板判定：现价 >= 今涨停价*0.998（贴板即算，防小数尾差）；同时要求 今开>昨收（高开）才算"二板"。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.request
from pathlib import Path


def _bj_today() -> str:
    """北京时间日期（runner 是 UTC，盘中/收盘判定必须用 CST，否则 08:00 前会错一天）。"""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).strftime("%Y-%m-%d")


def _load_probe():
    for c in (Path(__file__).resolve().parent / "shortline_probe.py",
              Path(__file__).resolve().parents[1] / "research" / "shortline_probe.py"):
        if c.exists():
            p = c
            break
    spec = importlib.util.spec_from_file_location("shortline_probe", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["shortline_probe"] = m
    spec.loader.exec_module(m)
    return m


def _alive_mask(cv, recent: list):
    """近 N 个截面**任一天有价** = 在市（非停牌）。

    🔴 不能只用最新截面（`cv[-1]`）：库里某天数据残缺时（实测 runner 11-18 只有
    1385/5567 只票有价），会把前一天还在正常交易的票当成停牌误杀 —— 实测候选池
    从 111 缩到 65，二板信号直接漏掉四成，而且**看不出来**（池子小不会报错）。
    报价拿不到的票 `detect_second_board` 本来就跳过，这里不需要二次把关。
    """
    import numpy as np
    return np.isfinite(cv[recent]).any(axis=0)


def _board_limit_for(code: str) -> float:
    """该票涨停幅度（先验，与 `shortline_probe.board_limit` 同口径）。

    ⚠️ 已知限制：ST 不细分（ST 实际 5%）。按 10% 算会让 ST 票既不触发也不会误触发
    到离谱的程度，但严格说应当单独处理 —— 留待后续。
    """
    if code.startswith(("30", "31", "688", "689")):
        return 0.20
    return 0.10


# 东财涨幅榜：按 f3（涨跌幅）降序，1 请求即可覆盖当日全部涨停票。
# 字段：f2 现价 / f3 涨跌幅 / f12 代码 / f14 名称 / f15 高 / f16 低 / f17 开 / f18 昨收
_EM_LIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2"
    "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f2,f3,f12,f14,f15,f16,f17,f18"
)


def _em_rows_to_pool(items: list) -> list:
    """把东财涨幅榜的原始行筛成「今日涨停池」（纯函数，便于单测）。

    🔴 关键：**涨幅 ≥9.8% 不等于涨停**。20cm 的票涨 10% 只是半路，必须按
    「该票自己的涨停价」判 —— 实测 301052 果麦文化当日涨 16.82%（昨收 32.88，
    涨停价 39.46，现价 38.41）并不在板上。按涨幅一刀切会把它当成涨停买进去。
    """
    out = []
    for it in items or []:
        code = str(it.get("f12") or "")
        if len(code) != 6 or not code.isdigit():
            continue
        if not code.startswith(("60", "00", "30", "31")):   # 与库种子同口径：排科创/北交
            continue
        try:
            cur = float(it.get("f2") or 0)
            pre = float(it.get("f18") or 0)
        except (TypeError, ValueError):
            continue                                        # 停牌行 f2 是 "-"
        if cur <= 0 or pre <= 0:
            continue
        lim = _board_limit_for(code)
        if cur < round(pre * (1.0 + lim), 2) * 0.998:       # 未真正涨停
            continue
        out.append({
            "code": code,
            "tencent_id": ("sh" if code.startswith("6") else "sz") + code,
            "board_lim": lim,
            "seed": True,
        })
    return out


def fetch_limit_up_pool(page_size: int = 150, timeout: float = 12.0) -> list:
    """从东财涨幅榜取「今日涨停池」（1 请求），**不依赖本地库的最近截面**。

    🔴 为什么必须有这条路径：库经常缺最新一个交易日（BaoStock 更新连续失败），
    而「二板接力」恰恰需要知道「昨天谁涨停」。用库的近 N 截面当种子会
    **系统性漏掉所有昨日首板** —— 实测 2026-11-20：
      · 全市场主板涨停 28 只，种子池（近 5 截面并集）只覆盖 2 只；
      · 当日真正的主板二板是 000838 / 002579，**两只都不在种子里**，漏失 100%；
      · 原因：它们 11-19 涨停，而库最新截面停在 11-18，且它们在 11-12~11-18
        没涨停过 ⇒ 进不了种子。库种子只能捞到 3 板以上的票。
    涨幅榜按涨幅降序，取前 150 条足够覆盖当日全部涨停（当日 31 只）。

    返回结构与 `build_watchlist` 一致，供 `detect_second_board` 直接消费；
    `board` 不在此处推断（由 `fill_board_for_hits` 用 K 线链权威重算）。
    """
    import json
    import urllib.request

    req = urllib.request.Request(_EM_LIST_URL % page_size,
                                 headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    items = ((json.loads(raw).get("data") or {}).get("diff")) or []
    return _em_rows_to_pool(items)


def build_watchlist(db: str, start: str = "20231101", include_chuangye: bool = True,
                    recent_days: int = 5) -> list:
    """昨涨停池 **种子**（主板+创业，排科创/北交；成色排序）。
    返回 tencent_id/code 列表（K 线核实前的候选池，喂给 build_live_watchlist）。
    关键：候选 = **库最近 recent_days 个截面的涨停并集**（缺截面容错——库停在 11-17 时，
    11-13/16/17 涨停过的 code 都会进种子），再由 K 线判「昨日是否真涨停 + 几板」。
    这样库缺 11-18 也能把 11-17 涨停的真视通放进种子，K 线再把它认成 3 板。
    """
    import numpy as np
    sl = _load_probe()
    P = sl.load_pick_universe(db, start, include_chuangye=include_chuangye)
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    cols = P["cols"]
    col_lim = P["col_lim"]
    T, N = cv.shape

    thr = np.where(col_lim >= 0.15, 0.198, 0.098)                  # 板块感知 涨停阈值
    # 全矩阵「每日是否涨停」（前复权环比，沿时间轴）
    ratio = cv / np.roll(cv, 1, axis=0) - 1.0
    ratio[:, 0] = np.nan
    is_zt = ratio >= thr[None, :]                                     # (T,N)

    # 候选 = 最近 recent_days 个截面的涨停并集（缺截面容错，K 线再核实闸门/几板）
    recent = list(range(max(T - recent_days, 1), T))
    cand = np.zeros(N, dtype=bool)
    for k in recent:
        cand |= np.isfinite(is_zt[k]) & is_zt[k]
    cand &= _alive_mask(cv, recent)                                   # 排除长期停牌（近 N 截面全无价）

    col = np.where(cand)[0]
    out = []
    for j in col:
        ts = cols[j]                                # 如 002187.SZ / 300124.SZ
        sym = ts.split(".")[0]
        exch = ts.split(".")[1].lower()             # sz/sh
        out.append({
            "code": sym,
            "tencent_id": exch + sym,               # sz002187 / sh600519
            "board_lim": round(float(col_lim[j]), 2),  # 该票涨停幅度(0.10/0.20)
            "seed": True,                            # 标记：来自库种子，待 K 线核实
        })
    return out


def fetch_quotes(tencent_ids: list[str]) -> dict:
    """批量取腾讯实时行情。返回 {tencent_id: {cur,pre,open,chg_pct,high,low,ts}}。"""
    if not tencent_ids:
        return {}
    q = ",".join(tencent_ids)
    url = "http://qt.gtimg.cn/q=" + q
    raw = urllib.request.urlopen(url, timeout=8).read().decode("gbk", "ignore")
    out = {}
    for m in re.finditer(r'v_([a-z]{2}\d{6})="([^"]+)"', raw):
        tid = m.group(1)
        parts = m.group(2).split("~")
        if len(parts) < 35:
            continue
        try:
            # [9]=买一价 [10]=买一量(手) [19]=卖一价 [20]=卖一量(手) —— 封板时买一量=封单量
            out[tid] = {
                "name": parts[1], "cur": float(parts[3]), "pre": float(parts[4]),
                "open": float(parts[5]), "chg_pct": float(parts[32]),
                "high": float(parts[33]), "low": float(parts[34]), "ts": parts[30],
                "bid1_px": float(parts[9]) if parts[9] else 0.0,
                "bid1_vol": int(float(parts[10])) if parts[10] else 0,   # 封单量(手)
                "ask1_vol": int(float(parts[20])) if parts[20] else 0,   # 卖一量(封死时=0)
            }
        except (ValueError, IndexError):
            continue
    return out


# 腾讯 K 线多域名（单域名被 WAF 拦 501 时切换）。注意：K 线请求必须"少而精"——
# 只对已触板的少数票拉，且当日缓存，绝不每轮对全种子池全量拉（会触发 WAF 风控）。
_KLINE_HOSTS = [
    "http://web.ifzq.gtimg.cn",
    "https://web.ifzq.gtimg.cn",
    "http://ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
]

# 进程内 WAF 冷却：某域名 501 后，COOLDOWN 秒内不再用它（避免加深黑名单）
_KLINE_COOLDOWN: dict[str, float] = {}
_KLINE_COOLDOWN_SEC = 300


def _kline_cache_path() -> Path:
    """当日 K 线缓存文件（北京时间跨天自动换文件）。"""
    return Path("/tmp/shortline_kline_cache_%s.json" % _bj_today())


def load_kline_cache() -> dict:
    p = _kline_cache_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_kline_cache(cache: dict) -> None:
    try:
        _kline_cache_path().write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def fetch_kline(tencent_ids: list[str], days: int = 15, cache: dict | None = None) -> dict:
    """取腾讯前复权日K。返回 {tencent_id: {"dates":[...],"closes":[...]}}（升序）。
    - 命中当日缓存直接返回，不发请求（核心：防 WAF）；
    - 未命中的才请求，多域名轮换 + 501 冷却退避；
    - 只有成功取到的才写缓存。"""
    out: dict[str, dict] = {}
    now = time.monotonic()
    cache = cache if cache is not None else {}
    need: list[str] = []
    for tid in tencent_ids:
        if cache.get(tid, {}).get("closes"):
            out[tid] = cache[tid]
        else:
            need.append(tid)
            out[tid] = {"dates": [], "closes": []}
    for tid in need:
        # ① 腾讯多域名（含冷却退避）
        got = False
        for base in _KLINE_HOSTS:
            if now - _KLINE_COOLDOWN.get(base, -1e9) < _KLINE_COOLDOWN_SEC:
                continue                            # 该域名还在冷却
            url = "%s/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (base, tid, days)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
                d = json.loads(raw)
                node = d.get("data", {}).get(tid, {})
                arr = node.get("qfqday") or node.get("day") or []
                if arr:
                    out[tid] = {"dates": [str(r[0]) for r in arr],
                                "closes": [float(r[2]) for r in arr]}
                    cache[tid] = out[tid]
                    got = True
                    break                           # 成功即停
                _KLINE_COOLDOWN[base] = now
            except (ValueError, KeyError, OSError, json.JSONDecodeError):
                _KLINE_COOLDOWN[base] = now         # 501/超时 → 冷却该域名
                continue
        # ② 腾讯全挂 → 新浪 K 线容灾（不同厂商，腾讯 WAF 无关）
        if not got:
            sina = _fetch_kline_sina(tid, days)
            if sina.get("closes"):
                out[tid] = sina
                cache[tid] = sina
    return out


def _fetch_kline_sina(tid: str, days: int = 15) -> dict:
    """新浪日K容灾源。返回同 {dates,closes} 结构（前复权用 qfq 需另接口；这里用不复权，
    近 15 日无除权则环比一致；几板判定足够）。新浪最新一根=已收盘日（不含今日半根）。"""
    try:
        url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%%20_%s=/"
               "CN_MarketDataService.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (tid, tid, days))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
        m = re.search(r"=\((.*)\)", raw, re.S)
        if not m:
            return {"dates": [], "closes": []}
        arr = json.loads(m.group(1))
        return {"dates": [str(r["day"])[:10] for r in arr],
                "closes": [float(r["close"]) for r in arr]}
    except (ValueError, KeyError, OSError, json.JSONDecodeError, re.error):
        return {"dates": [], "closes": []}


def trailing_zt_run(closes: list, thr: float = 0.098) -> int:
    """从最新一根往回数「连续涨停」天数（含最新一根）。
    前复权环比 close[i]/close[i-1] >= 1+thr 记为涨停。thr 板块感知：主板 0.098 / 创业 0.198。
    用于直接算出「几板」，替代库里因缺截面而失真的 board。"""
    n = len(closes)
    cnt = 0
    for i in range(n - 1, 0, -1):
        prev = closes[i - 1]
        if not prev or prev <= 0:
            break
        if closes[i] / prev - 1.0 >= thr:
            cnt += 1
        else:
            break
    return cnt


def build_live_watchlist(seed_codes: list, klines: dict, today: str | None = None) -> list:
    """K 线核实的实时「昨涨停」watchlist：彻底摆脱「库最新截面」。
    - 候选闸门 = **昨日（K 线倒数第二根，盘中会自动剥掉今日半根）刚封板**，K 线日期权威；
    - board = **截至昨的连续几板**（K 线链往回数），今日触板时 detect 再 +1。
    库缺 11-18 也能正确把真视通认成 2 板候选、今日触板=3 板。
    seed_codes: tencent_id 列表(库近期涨停 code 兜底)；klines: fetch_kline 结果。
    """
    today = today or _bj_today()
    out = []
    for tid in seed_codes:
        kl = klines.get(tid, {})
        closes_all = kl.get("closes", [])
        dates = kl.get("dates", [])
        if len(closes_all) < 3:
            continue
        # 盘中时 tencent 会回今日半根；剥掉它，留下「截至昨」的链（日期对齐，防错）
        if dates and dates[-1] == today:
            closes = closes_all[:-1]
        else:
            closes = closes_all
        if len(closes) < 2:
            continue
        sym = tid[2:]                                  # sz002771 -> 002771
        lim = 0.20 if sym.startswith(("30", "31")) else 0.10
        thr = 0.198 if lim >= 0.15 else 0.098
        yest_zt = closes[-1] > 0 and (closes[-1] / closes[-2] - 1.0) >= thr
        if not yest_zt:                                  # 只收「昨日刚封」的票
            continue
        board_y = trailing_zt_run(closes, thr)           # 截至昨的连续几板
        out.append({
            "code": sym, "tencent_id": tid,
            "board": int(max(board_y, 1)),               # 截至昨连续几板（K 线权威）
            "board_lim": round(lim, 2),
            "close_T-1": round(closes[-1], 2),
            "kline_days": len(closes_all),
            "yest_zt": True,
        })
    return out


def fill_board_for_hits(hits: list, quote_map: dict, kcache: dict) -> None:
    """只对【已触板】的少数 hits 拉 K 线算准确几板（带当日缓存，防 WAF）。
    触板判定已由报价完成（零 K 线），这里只补 board/strategy 文案里的几板数。
    真视通今日 3 板 = K 线链往回数（含今日触板那根）。"""
    if not hits:
        return
    tids = [h["tencent_id"] for h in hits]
    kl = fetch_kline(tids, days=15, cache=kcache)
    save_kline_cache(kcache)
    for h in hits:
        d = kl.get(h["tencent_id"], {})
        closes = d.get("closes", [])
        dates = d.get("dates", [])
        if len(closes) >= 2:
            thr = 0.198 if h.get("board_lim", 0.10) >= 0.15 else 0.098
            # 统一口径：先把【今日】那根剥掉，数「截至昨」的连续板，再 +1（今日触板已由报价确认）。
            # 不能信今日半根的 close——盘中 K 线常滞后于报价，触板瞬间今日根可能还没到涨停价，
            # 直接 trailing_zt_run(含今日) 会漏算今日那板（真视通会显示 2 而非 3）。
            if dates and dates[-1] == _bj_today():
                closes_upto_yest = closes[:-1]
                src = "tencent"
            else:
                closes_upto_yest = closes          # 新浪本就截至昨
                src = "sina"
            run_yest = trailing_zt_run(closes_upto_yest, thr) if closes_upto_yest else 0
            board = max(run_yest + 1, 1)            # 今日触板 = 截至昨 + 1
            h["board"] = board
            h["kline_src"] = src
            if h.get("actionable"):
                stop = int(round(h.get("board_lim", 0.10) * 50))
                # 🔴 不能说 B 格「可买」：B 格的定义是**收盘封死在涨停**，
                # 按那个价成交就是前视（可成交口径下四个高度全为负，见 #655）。
                strat = "放量%d板·B格收盘封死·%s T+1兑现·炸板-%d%%" % (
                    board, h.get("seal_tag", ""), stop)
            else:
                strat = "一字%d板·A格排不进·%s(观察)" % (board, h.get("seal_tag", ""))
            h["strategy"] = strat
            h["msg"] = "%s @%.2f（今涨停%.2f）" % (strat, h["price"], h["limit_up"])
        # K 线暂缺（WAF 冷却）→ 保留报价推算的 board，不虚报为 0


def seal_level(bid1_vol: int, limit_up: float) -> tuple[str, str]:
    """封单强度客观标注（非买卖建议，只描述盘口事实）。
    返回 (档位, 简签)。阈值按封单金额分档（手×100股×价），跨板块可比：
      厚(≥3000万) / 中(1000~3000万) / 薄(<1000万)。
    薄封单=容易排进但封板不牢、次日溢价存疑；厚封单=难排进但封得死。这是客观两面，不替用户下结论。"""
    amt = bid1_vol * 100 * limit_up
    if amt >= 3e7:
        return "厚", "封单%.0f万" % (amt / 1e4)
    if amt >= 1e7:
        return "中", "封单%.0f万" % (amt / 1e4)
    if amt > 0:
        return "薄", "封单%.0f万" % (amt / 1e4)
    return "无", "封单0"


def detect_second_board(watch: list, quotes: dict) -> list:
    """返回今日「连板确认」的票，并标注成交格（涨停价按板块：主板10%/创业20%）：
      - cell=B：今开>昨收(高开) 且 今开没顶死一字(open<今涨停*0.998) 且 现价触/贴板
                 → 盘中放量封板，可排进、可成交 → 要推的 actionable 信号
      - cell=A：开盘就顶死一字(open≥今涨停*0.998) → 排不进（买不到），仅观察
    几板 = 优先用真实 K 线链重算的 board_now（含今日触板，补全库里缺截面）；
    K 线缺失时退回 库 board + 今日再封板 = board+1。
    """
    hits = []
    for w in watch:
        q = quotes.get(w["tencent_id"])
        if not q or q["pre"] <= 0:
            continue
        lim = w.get("board_lim", 0.10)
        limit_up = round(q["pre"] * (1.0 + lim), 2)              # 板块感知 今涨停价
        open_gap = q["open"] > q["pre"]                            # 高开
        near_board = q["cur"] >= limit_up * 0.998                  # 现价贴/触板
        open_sealed = q["open"] >= limit_up * 0.998                # 开盘顶死一字
        if not (open_gap and near_board):
            continue
        board_yest = w.get("board", 1)                 # 截至昨连续几板（K 线链权威，剥掉今日半根）
        board_now = board_yest + 1                       # 今日确认触板 = 第 N+1 板
        cell = "A_一字排不进" if open_sealed else "B_放量可成交"
        # 封单强度：客观盘口事实，帮你判断"排不排得进"（非买卖建议）
        lvl, seal_tag = seal_level(q.get("bid1_vol", 0), limit_up)
        sealed_now = q.get("ask1_vol", 0) == 0 and q.get("bid1_px", 0) >= limit_up * 0.998
        # 策略文案（统一极简字段，卡片只取这几列）——封单简签并入策略，不新增列
        if open_sealed:
            strategy = f"一字{board_now}板·A格排不进·{seal_tag}(观察)"
        else:
            stop = int(round(lim * 50))
            strategy = f"放量{board_now}板·B格收盘封死·{seal_tag} T+1兑现·炸板-{stop}%"
        hits.append({
            "code": w["code"], "name": q["name"], "tencent_id": w["tencent_id"],
            "price": q["cur"], "pre": q["pre"], "open": q["open"], "limit_up": limit_up,
            "board": board_now, "board_lim": round(lim, 2),
            "cell": cell, "strategy": strategy,
            "seal_vol": q.get("bid1_vol", 0), "seal_amt_wan": round(q.get("bid1_vol", 0) * 100 * limit_up / 1e4, 0),
            "seal_level": lvl, "seal_tag": seal_tag, "sealed_now": bool(sealed_now),
            "chg_pct": round(q["chg_pct"], 2), "ts": q["ts"],
            "msg": f"{strategy} @{q['cur']:.2f}（今涨停{limit_up:.2f}）",
            "actionable": not open_sealed,
        })
    return hits


def _dedup_key(h: dict) -> str:
    """去重键 = 代码 + 成交格。A格(一字观察)与B格(放量可买)是两类不同信号：
    一只票先一字A格被报，随后开板转B格=可买时机，必须再推一次，不能被纯 code 去重吞掉。"""
    return "%s:%s" % (h.get("code"), "B" if h.get("actionable") else "A")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="astocks_raw.db 路径（runner 上 /opt/aqsp-runner/data/）")
    ap.add_argument("--watch", default=None, help="forward_watchlist JSON（可替代自读库）")
    ap.add_argument("--once", action="store_true", help="跑一次即退出（供 cron/automation 用）")
    ap.add_argument("--interval", type=int, default=60, help="轮询秒数（盘中每 N 秒查一次）")
    ap.add_argument("--json", default=None, help="把本轮结果写 JSON（供推送脚本读）")
    args = ap.parse_args(argv)

    # 候选种子：**东财涨幅榜 = 当日真涨停池**（1 请求，权威，不依赖库最新截面）。
    # 库「近 N 截面涨停并集」只作兜底 —— 库常缺最新一天，用它当主源会漏掉
    # 全部「昨日首板、今日二板」的票（实测漏失 100%，详见 fetch_limit_up_pool 注释）。
    # 触板判定仍用【批量报价】(1 请求拿全)，K 线只对【已触板】的少数票拉(带当日缓存)。
    if args.watch:
        seed_watch = [{"code": w.get("code"), "tencent_id": w.get("tencent_id"),
                       "board_lim": round(0.20 if str(w.get("code", "")).startswith(("30", "31")) else 0.10, 2)}
                      for w in json.loads(Path(args.watch).read_text(encoding="utf-8"))["list"]]
        pool_src = "watch_file"
    else:
        seed_watch, pool_src = [], "em_board"
        try:
            seed_watch = fetch_limit_up_pool()
        except Exception as exc:            # 东财不可用不能让整轮检测崩掉，回落库种子
            print("[warn] 东财涨幅榜不可用（%s），回落库种子" % type(exc).__name__, flush=True)
        if not seed_watch:
            seed_watch = build_watchlist(args.db)
            pool_src = "db_seed"
    seed_tids = [w["tencent_id"] for w in seed_watch if w.get("tencent_id")]
    kcache = load_kline_cache()

    seen: set = set()          # 已报过的 (代码,格类型)，按天跨天自动重置
    state_file = Path("/tmp/shortline_seen_%s.txt" % time.strftime("%Y%m%d"))
    if state_file.exists():
        # 兼容旧纯 code 记录：转成 A/B 双键，避免升级当天重复推（新键都带 : 分隔符）
        for tok in state_file.read_text().split():
            if ":" in tok:
                seen.add(tok)
            else:
                seen.update((tok + ":A", tok + ":B"))
    round_n = 0
    while True:
        round_n += 1
        quotes = fetch_quotes(seed_tids)                          # 1 请求拿全池报价（零 K 线）
        all_hits = detect_second_board(seed_watch, quotes)        # 报价判触板（现价≥涨停价&高开）
        fill_board_for_hits(all_hits, quotes, kcache)             # 只对触板票拉 K 线算几板(缓存)
        hits = [h for h in all_hits if _dedup_key(h) not in seen]  # 增量：同 code 的 A→B 转换会放行
        ts = time.strftime("%H:%M:%S")
        print("[%s] 第%d轮 [%s] 池%d 只 / 实取%d / 触板%d / 新增%d %s" % (
            ts, round_n, pool_src, len(seed_watch), len(quotes), len(all_hits), len(hits),
            ("; ".join(h["code"] + " " + h["msg"] for h in hits) if hits else "（无新增）")), flush=True)
        payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "round": round_n,
                   "pool": len(seed_watch), "pool_src": pool_src,
                   "all_hits": all_hits, "new_hits": hits}
        if args.json:
            Path(args.json).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        if hits:
            seen.update(_dedup_key(h) for h in hits)
            state_file.write_text(" ".join(sorted(seen)))
        if args.once or not seed_watch:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
