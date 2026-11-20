#!/usr/bin/env python3
"""短线多策略定义（影子账户用）——每个策略一个独立假设，互不共享参数。

🔴 纪律（老大原话「不准用回测数据来欺骗」「不要只用一个策略」）：
- 每个策略的阈值是**教科书/常识先验**，不是在这 3 年数据上拟合出来的最优值；
- **一策略一版本，永不因亏损调参**（调了就变成回测过拟合，账本也失去前向验证意义）；
- 每个策略只回答一个问题：这个假设在真实未来数据里成立吗。
- 参数说明里凡是「先验」都写清楚来源，不假装是「已验证」。

五个 sleeve（各自独立 ¥10000 虚拟账户，同一套执行/成本口径）：

| id | 名称       | 假设（经济逻辑）                                      | 出手时点 | 卖出     |
|----|-----------|------------------------------------------------------|---------|----------|
| P1 | 二板接力   | 情绪接力：二板封住=资金认可，次日溢价                     | 盘中触板 | T+1 收盘 |
| P2 | 首板接力   | 同族对照：板数越少跟随资金越弱，看板数是否真的影响溢价      | 盘中触板 | T+1 收盘 |
| P3 | 超跌反抽   | 短期反转：单日暴跌=散户过度反应，次日修复                  | 10:00   | T+1 收盘 |
| P4 | 竞价抢筹   | 集合竞价资金意图：高开+竞价放量=有资金愿在高位接             | 09:27   | T+1 收盘 |
| P5 | 尾盘惯性   | 隔夜动量：尾盘强势收在高位，次日开盘有惯性                   | 14:45   | 次日开盘 |

为什么是这五类：它们是 A 股短线里**逻辑互不相同**的四种驱动（情绪接力/反转/资金意图/动量），
而不是同一逻辑的五个参数版本 —— 参数版本会一起活或一起死，起不到分散验证的作用。
"""
from __future__ import annotations


# 全市场流动性下限（先验：日均成交 5000 万以下、流通市值 15 亿以下的票，
# 冲击成本与滑点会把薄边际吃光，且容易是庄股）
MIN_YEST_AMOUNT = 5000.0     # 万元
MIN_FLOAT_MV = 15.0          # 亿元


def _num(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


# ---------------- P3 超跌反抽 ----------------

def _oversold_ok(code: str, y: dict) -> bool:
    """P3 的「昨日」门槛：跌够 + 没跌停 + 流动性够。scan 与报告预告共用同一判据。"""
    limit = 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10
    if _num(y, "pct") > -limit * 100 * 0.7:
        return False
    y_close, y_ld = _num(y, "p"), _num(y, "ld")
    if y_ld > 0 and y_close <= y_ld * 1.002:
        return False                       # 跌停收盘 = 还有跌停排队，不接
    return _num(y, "amt") >= MIN_YEST_AMOUNT and _num(y, "fmv") >= MIN_FLOAT_MV


def oversold_pool(yest_rows: dict) -> list[dict]:
    """给定一份收盘快照，返回「下一交易日 P3 会盯的池子」（只是池子，不是买点）。"""
    out = []
    for code, y in (yest_rows or {}).items():
        if _oversold_ok(code, y):
            out.append({"code": code, "name": y.get("n", ""), "pct": _num(y, "pct"),
                        "amt": _num(y, "amt"), "fmv": _num(y, "fmv")})
    out.sort(key=lambda s: s["pct"])
    return out


def scan_oversold(ctx: dict) -> list[dict]:
    """昨日暴跌 + 今日不崩 ⇒ 买；T+1 收盘卖。

    先验依据：短期反转（short-term reversal）是 A 股最稳的异象之一，散户结构放大过度反应。
    阈值 = 昨日跌幅达到该板块跌停幅度的 70%（主板 −7%、创业/科创 −14%），
    今日 10:00 落在 −1%~+4%（不接下跌中的刀、也不追已经反弹到位的）。
    """
    out = []
    yest = ctx.get("yest") or {}
    for code, q in (ctx.get("quotes") or {}).items():
        y = yest.get(code)
        if not y:
            continue                       # 无昨日数据 ⇒ 不猜
        if not _oversold_ok(code, y):
            continue
        pct = _num(q, "pct")
        if pct < -1.0 or pct > 4.0:
            continue
        price = _num(q, "price")
        if price <= 0:
            continue
        out.append({"code": code, "name": q.get("name", ""), "price": price,
                    "note": "昨日%+.1f%% → 今%+.1f%%" % (_num(y, "pct"), pct),
                    "rank_key": _num(y, "pct")})
    out.sort(key=lambda s: s["rank_key"])          # 跌得最狠的优先
    return out


# ---------------- P4 竞价抢筹 ----------------

def scan_auction(ctx: dict) -> list[dict]:
    """集合竞价高开 + 竞价额占昨日成交额 ≥2% ⇒ 9:30 开盘价买；T+1 收盘卖。

    先验依据：竞价是唯一能看到「隔夜资金态度」的窗口；高开 2~7%（不涨停）且竞价放量，
    说明有资金愿意在比昨收更高的位置真金白银接货（不是挂单虚高）。
    2% 竞价额占比 ≈ 竞价换手活跃，先验取自「竞价额 > 昨日成交额 1/50」的常见经验值。
    """
    out = []
    yest = ctx.get("yest") or {}
    for code, q in (ctx.get("quotes") or {}).items():
        y = yest.get(code)
        if not y:
            continue
        y_amt = _num(y, "amt")
        if y_amt < MIN_YEST_AMOUNT or _num(y, "fmv") < MIN_FLOAT_MV:
            continue
        pct = _num(q, "pct")
        limit = 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10
        if pct < 2.0 or pct > 7.0 or pct >= limit * 100 - 1:
            continue
        open_px = _num(q, "open") or _num(q, "price")
        vol = _num(q, "vol")                       # 竞价成交量（手）
        auction_amt = _num(q, "amount") or open_px * vol * 100 / 10000.0
        if auction_amt <= 0:
            continue
        ratio = auction_amt / y_amt
        if ratio < 0.02:
            continue
        out.append({"code": code, "name": q.get("name", ""), "price": open_px,
                    "note": "高开%+.1f%% 竞价额%.0f万(占昨%.1f%%)" % (pct, auction_amt, ratio * 100),
                    "rank_key": ratio})
    out.sort(key=lambda s: -s["rank_key"])         # 竞价占比最高优先
    return out


# ---------------- P5 尾盘惯性 ----------------

def scan_late(ctx: dict) -> list[dict]:
    """14:45 强势未涨停 + 放量 ⇒ 买；次日开盘卖（吃隔夜/开盘惯性）。

    先验依据：隔夜动量效应 —— 收盘前仍站在高位且量能放大的票，次日开盘常有惯性溢价。
    阈值 3%~7%：低于 3% 没有强度，高于 7% 接近涨停（次日高开空间被压缩且易炸板）。
    量比 ≥1.5 过滤「无量的假强势」，换手 2%~15% 过滤「刚开板没人接」和「换手过高=出货」。
    """
    out = []
    yest = ctx.get("yest") or {}
    for code, q in (ctx.get("quotes") or {}).items():
        y = yest.get(code)
        if not y:
            continue
        if _num(y, "fmv") < MIN_FLOAT_MV:
            continue
        pct = _num(q, "pct")
        limit = 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10
        if pct < 3.0 or pct > 7.0 or pct >= limit * 100 - 0.3:
            continue
        if _num(q, "vol_ratio") < 1.5:
            continue
        to = _num(q, "turnover")
        if to < 2.0 or to > 15.0:
            continue
        price = _num(q, "price")
        if price <= 0:
            continue
        out.append({"code": code, "name": q.get("name", ""), "price": price,
                    "note": "涨%+.1f%% 量比%.1f 换手%.1f%%" % (pct, _num(q, "vol_ratio"), to),
                    "rank_key": pct})
    out.sort(key=lambda s: -s["rank_key"])         # 最强优先
    return out


# ---------------- P1/P2 打板接力（信号来自 shortline_signal.py）----------------

def from_board_hits(hits: list[dict], board: int) -> dict:
    """把打板检测结果转成策略信号。

    🔴 成交口径（最容易骗人的一步，分两层记）：
    - A 格（开盘一字）⇒ 直接丢。verdict §四-C 明确「排不进」，假装买到就是骗自己。
    - B 格（open 没顶死一字 + 盘中摸板）⇒ **照记**，但打 `sealed` 标记：
      · `sealed=False`（触板那一刻卖一还有货）⇒ **主判样本**，这是真能买到的；
      · `sealed=True`（触板即封死，卖一=0）⇒ 账本照记但**只作上界参考**，
        不进主统计 —— 因为 1.5 万手封单前面排队，你实际买不到。

    为什么不在入口就把 sealed 删掉：删了就永远只剩个位数样本，看不出任何东西；
    分层记既能出保守判据、又能保留「如果排得进会怎样」的对照。
    """
    out = []
    for h in hits or []:
        if h.get("cell") != "B_放量可成交":
            continue                       # A 格一字排不进
        if int(h.get("board") or 0) != board:
            continue
        out.append({"code": h["code"], "name": h.get("name", ""),
                    "price": float(h.get("price") or 0),
                    "sealed": bool(h.get("sealed_now")),
                    "note": "%d板·%s" % (board, (h.get("seal_tag") or h.get("seal") or "")[:12])})
    out.sort(key=lambda s: (s["sealed"], -s["price"]))   # 能买到的排前面，优先占仓
    return {"signals": out, "blocked_sealed": [s["code"] for s in out if s["sealed"]]}


# ---------------- 注册表 ----------------

STRATEGIES: dict[str, dict] = {
    "P1": {
        "name": "二板接力",
        "hypothesis": "情绪接力：二板封住=资金认可，次日有溢价（唯一在 3y 回测过门的假设，本账本做前向验证；只记「有卖单」那一 tick 的成交）",
        "phase": "board",
        "scan": None,
        "exit": "t1_close",
        "stop_pct": 0.05,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P2": {
        "name": "首板接力",
        "hypothesis": "同族对照：首板参与资金更杂、次日抛压更重，看板数是否真的决定溢价",
        "phase": "board",
        "scan": None,
        "exit": "t1_close",
        "stop_pct": 0.05,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P3": {
        "name": "超跌反抽",
        "hypothesis": "短期反转：单日暴跌=散户过度反应，次日修复（学术最扎实的短线异象）",
        "phase": "morning",
        "scan": scan_oversold,
        "exit": "t1_close",
        "stop_pct": 0.05,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P4": {
        "name": "竞价抢筹",
        "hypothesis": "集合竞价高开+放量=有资金愿在高位真金接货，日内延续",
        "phase": "auction",
        "scan": scan_auction,
        "exit": "t1_close",
        "stop_pct": 0.05,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P5": {
        "name": "尾盘惯性",
        "hypothesis": "隔夜动量：尾盘强势收高，次日开盘有惯性溢价（不持仓过夜到收盘，只吃开盘）",
        "phase": "late",
        "scan": scan_late,
        "exit": "t1_open",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        "max_positions": 5,
    },
}

PHASE_LABEL = {
    "auction": "09:27 竞价",
    "morning": "10:00 早盘",
    "late": "14:45 尾盘",
    "close": "15:05 收盘",
    "board": "盘中触板",
}


def by_phase(phase: str) -> list[tuple[str, dict]]:
    return [(sid, s) for sid, s in STRATEGIES.items() if s["phase"] == phase]
