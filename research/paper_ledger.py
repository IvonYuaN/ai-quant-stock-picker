#!/usr/bin/env python3
"""影子账户多策略账本引擎 —— 真实信号 + 真实价格 + 虚拟资金，前向验证「短线到底能不能赚钱」。

🔴 诚实定位（写给老大，也写给未来的自己）：
- 不是真下单（项目红线禁下单逻辑、无券商接口），是「若按规则执行会怎样」的账本。
- **没有任何回测数字进入本账本**：每一笔都是「当日真实报价 → 次日真实收盘/开盘」。
- 两个视角同时记：
  · **组合视角**（有资金约束）：¥10000/sleeve，单笔 20%，最多 5 只 —— 你实际能拿到的结果。
  · **统计视角**（无资金约束）：当天所有合格信号等权记一笔 —— 只为攒样本算 t 值，
    否则 5 个仓位会把样本卡在每天 5 笔、永远等不到显著。

规则（每个 sleeve 独立记账，参数见 paper_strategies.py）：
- 买入：策略信号触发时的真实报价价（打板=触板价，竞价=开盘价，尾盘=14:45 价）。
- 卖出：`t1_close` = 次一交易日收盘；`t1_open` = 次一交易日开盘。
- 止损：`stop_pct` 触发则按止损价（跳空则按开盘价，取更差者）。
- 成本：单边 1%（往返 2%），A 股 100 股整手。

数据落点：research/paper_arena.json（研究侧，纯本地，不碰生产/front api）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_strategies import STRATEGIES

try:                                    # 基准模块（缺失时降级为「不记超额」，绝不造假）
    import paper_market as _pm
except Exception:                       # pragma: no cover
    _pm = None

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "paper_arena.json"
CST = timezone(timedelta(hours=8))

INITIAL_CAPITAL = 10_000.0
COST_ONE_WAY = 0.01
LOT = 100
VERSION = 2
MAX_STUCK_DAYS = 10      # 连续一字跌停卖不掉的最长顺延天数（超了按收盘强平，并标注是乐观上界）


def _today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _new_sleeve(sid: str, spec: dict) -> dict:
    return {
        "id": sid, "name": spec["name"], "hypothesis": spec["hypothesis"],
        "capital": INITIAL_CAPITAL, "cash": INITIAL_CAPITAL,
        "rules": {"exit": spec["exit"], "stop_pct": spec["stop_pct"],
                  "position_pct": spec["position_pct"], "max_positions": spec["max_positions"],
                  "cost_one_way": COST_ONE_WAY, "phase": spec["phase"]},
        "positions": [], "trades": [], "nav": [], "ingested": [],
    }


def _blank() -> dict:
    return {"meta": {"version": VERSION, "initial_capital": INITIAL_CAPITAL,
                     "cost_round_trip": COST_ONE_WAY * 2,
                     "created_at": datetime.now(CST).isoformat(),
                     "note": "前向纸上交易，无回测成分"},
            "sleeves": {sid: _new_sleeve(sid, spec) for sid, spec in STRATEGIES.items()}}


def _migrate(old: dict) -> dict:
    """旧版单策略账本（扁平 cash/positions/trades/daily_nav）→ 并入 P1 sleeve，历史不丢。"""
    led = _blank()
    if not old.get("positions") and not old.get("trades") and not old.get("daily_nav"):
        return led
    s = led["sleeves"]["P1"]
    s["cash"] = old.get("cash", INITIAL_CAPITAL)
    for p in old.get("positions", []):
        p["funded"] = True
        p["phase"] = "board"
        s["positions"].append(p)
    for t in old.get("trades", []):
        t.setdefault("funded", True)
        t.setdefault("exit", "t1_close")
        s["trades"].append(t)
    for r in old.get("daily_nav", []):
        s["nav"].append({"date": r["date"], "nav": r["nav"],
                         "ret_pct": r.get("return_pct", 0.0)})
    s["ingested"] = list(old.get("handled_signals", []))
    led["meta"]["migrated_from_v1"] = True
    return led


def load() -> dict:
    if LEDGER.exists():
        try:
            raw = json.loads(LEDGER.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _blank()
        if raw.get("meta", {}).get("version") == VERSION:
            # 补齐新增 sleeve（后续加策略不丢已有数据）
            for sid, spec in STRATEGIES.items():
                raw["sleeves"].setdefault(sid, _new_sleeve(sid, spec))
            return raw
        return _migrate(raw)
    legacy = HERE / "paper_ledger.json"          # v1 单策略账本
    if legacy.exists():
        try:
            return _migrate(json.loads(legacy.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return _blank()


def save(led: dict) -> None:
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- 买入 ----------------

def ingest(led: dict, sid: str, signals: list[dict], date: str, phase: str) -> dict:
    """把策略信号记入 sleeve：按排序依次占用资金，占不到仓的也照记（统计视角）。"""
    s = led["sleeves"][sid]
    rules = s["rules"]
    bought, skipped = [], []
    for sig in signals:
        key = "%s|%s|%s" % (sig["code"], date, phase)
        if key in s["ingested"]:
            continue
        s["ingested"].append(key)
        price = float(sig.get("price") or 0)
        if price <= 0:
            continue
        slots_left = rules["max_positions"] - len([p for p in s["positions"] if p["funded"]])
        budget = min(s["cash"], s["capital"] * rules["position_pct"])
        shares = 0
        if slots_left > 0:
            shares = math.floor(budget / (price * (1 + COST_ONE_WAY)) / LOT) * LOT
        funded = shares >= LOT and shares * price * (1 + COST_ONE_WAY) <= s["cash"] + 1e-6
        why = ""
        # 🔴 `sig["sealed"]` 不参与「买不买」的判断（11-22 修）：
        # 回测 B 格 =「open 没顶死 + 摸板 + 收停板」，「收停板」在盘中就是 sealed=True，
        # 若在这里判死，等于把回测唯一过门的作业集全部排除，P1/P2 永远 0 成交。
        # sealed 只落进 position 的 `sealed_entry` 字段，供事后拆「保守口径」。
        if not funded:
            if slots_left <= 0:
                why = "仓位已满"
            elif shares < LOT:
                # ¥1 万本金 + 20% 仓位 = 单笔 ¥2000，只能买 1 手 ≤¥19.8 的票。
                # 这不是 bug，是小资金的真实约束——高价票照记信号但买不进。
                why = "单笔¥%.0f买不起1手(价%.1f)" % (budget, price)
            else:
                why = "现金不足"
        if funded:
            cost = shares * price * (1 + COST_ONE_WAY)
            s["cash"] -= cost
        else:
            shares = 0
            cost = 0.0
            skipped.append({"code": sig["code"], "why": why})
        s["positions"].append({
            "code": sig["code"], "name": sig.get("name", ""), "buy_date": date,
            "phase": phase, "buy_price": round(price, 3), "shares": shares,
            "cost": round(cost, 2), "funded": funded, "unfunded_why": why,
            "sealed_entry": bool(sig.get("sealed")),
            "stop_price": round(price * (1 - rules["stop_pct"]), 3) if rules["stop_pct"] else 0.0,
            "note": sig.get("note", ""),
        })
        if funded:
            bought.append({"code": sig["code"], "name": sig.get("name", ""),
                           "price": price, "shares": shares, "note": sig.get("note", "")})
    return {"bought": bought, "skipped": skipped, "signals": len(signals)}


# ---------------- 结算 ----------------

def _sealed_limit_down(row: dict) -> bool:
    """一字跌停：全天一个价且就在跌停价上 ⇒ 卖不出去（打板策略真正的尾部风险）。"""
    try:
        h, lo, p = float(row["h"]), float(row["l"]), float(row["p"])
        ld = float(row.get("ld") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    if ld <= 0 or abs(h - lo) > 0.005:
        return False
    return p <= ld * 1.002


def _bench(d1: str, d2: str) -> float | None:
    if _pm is None:
        return None
    try:
        return _pm.bench_between(d1, d2)
    except Exception:
        return None


def settle(led: dict, snap_rows: dict, date: str) -> list[dict]:
    """把所有 buy_date < date 的仓位按 sleeve 规则平掉（买当日不卖，T+1 生效）。

    🔴 一字跌停卖不掉 ⇒ 顺延到下一交易日，最多 10 天。这是打板策略真实的尾部风险，
    不做这一步，P1/P2 的收益会被系统性高估（回测最容易在这里骗人）。
    """
    closed = []
    for sid, s in led["sleeves"].items():
        rules = s["rules"]
        remain, done = [], []
        for p in s["positions"]:
            if p["buy_date"] >= date:
                remain.append(p)
                continue
            row = snap_rows.get(p["code"])
            if not row:
                # 数据缺失（停牌/退市）：超过 10 天按成本平掉，不让仓位永久悬空
                age = (datetime.strptime(date, "%Y-%m-%d")
                       - datetime.strptime(p["buy_date"], "%Y-%m-%d")).days
                if age <= 10:
                    remain.append(p)
                    continue
                done.append(_close(s, p, p["buy_price"], date, "无数据·按成本平"))
                continue
            if _sealed_limit_down(row):
                p["stuck_days"] = p.get("stuck_days", 0) + 1
                if p["stuck_days"] <= MAX_STUCK_DAYS:
                    remain.append(p)                 # 卖不掉，只能继续持有
                    continue
                done.append(_close(s, p, float(row["p"]), date,
                                   "连续跌停%d日·按收盘强平(乐观上界)" % p["stuck_days"]))
                continue
            o = float(row.get("o") or 0)
            c = float(row.get("p") or 0)
            lo = float(row.get("l") or 0)
            if rules["exit"] == "t1_open" and o > 0:
                done.append(_close(s, p, o, date, "次日开盘兑现"))
                continue
            price, reason = c, "T+1收盘兑现"
            if rules["stop_pct"] and p["stop_price"] > 0 and lo > 0 and lo <= p["stop_price"]:
                price = min(p["stop_price"], o) if o > 0 else p["stop_price"]
                reason = "止损-%.0f%%" % (rules["stop_pct"] * 100)
            if price <= 0:
                remain.append(p)
                continue
            done.append(_close(s, p, price, date, reason))
        s["positions"] = remain
        closed.extend([{"sleeve": sid, **c} for c in done])
    return closed


def _close(s: dict, p: dict, sell_price: float, date: str, reason: str) -> dict:
    ret = sell_price / p["buy_price"] - 1 - COST_ONE_WAY * 2
    if p["funded"] and p["shares"]:
        proceeds = p["shares"] * sell_price * (1 - COST_ONE_WAY)
        pnl = proceeds - p["cost"]
        s["cash"] += proceeds
        ret = pnl / p["cost"] if p["cost"] else 0.0
    else:
        pnl = 0.0
    b = _bench(p["buy_date"], date)
    excess = round((ret - b) * 100, 2) if b is not None else None
    s["trades"].append({
        "code": p["code"], "name": p["name"], "buy_date": p["buy_date"],
        "buy_price": p["buy_price"], "sell_date": date, "sell_price": round(sell_price, 3),
        "shares": p["shares"], "funded": p["funded"], "ret_pct": round(ret * 100, 2),
        "bench_pct": round(b * 100, 2) if b is not None else None,
        "excess_pct": excess,
        "pnl": round(pnl, 2), "reason": reason, "exit": s["rules"]["exit"],
        "note": p.get("note", ""), "unfunded_why": p.get("unfunded_why", ""),
        "stuck_days": p.get("stuck_days", 0), "sealed_entry": p.get("sealed_entry", False),
    })
    return {"code": p["code"], "name": p["name"], "ret_pct": round(ret * 100, 2),
            "excess_pct": excess, "pnl": round(pnl, 2), "reason": reason,
            "funded": p["funded"]}


# ---------------- 净值 ----------------

def mark_nav(led: dict, snap_rows: dict, date: str) -> None:
    for sid, s in led["sleeves"].items():
        pos_val = 0.0
        for p in s["positions"]:
            row = snap_rows.get(p["code"])
            px = float(row.get("p") or 0) if row else p["buy_price"]
            pos_val += p["shares"] * (px or p["buy_price"])
        nav = s["cash"] + pos_val
        item = {"date": date, "nav": round(nav, 2), "cash": round(s["cash"], 2),
                "pos_value": round(pos_val, 2),
                "ret_pct": round((nav / s["capital"] - 1) * 100, 2)}
        if s["nav"] and s["nav"][-1]["date"] == date:
            s["nav"][-1] = item
        else:
            s["nav"].append(item)


# ---------------- 统计 ----------------

def _tstat(vals: list[float]) -> tuple[float | None, float, int]:
    """返回 (t, std, n)。std 太小说明样本退化了，t 无意义。"""
    n = len(vals)
    if n < 2:
        return None, 0.0, n
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    std = math.sqrt(var)
    t = mean / (std / math.sqrt(n)) if std > 1e-4 else None
    return t, std, n


def sleeve_stats(s: dict) -> dict:
    """统计视角。

    🔴 关键口径：**t 值按「交易日」聚类算，不按「笔」算**。
    同一天的一批信号高度相关（同涨同跌的系统性行情），把它们当独立样本，
    样本量会虚高十几倍、几天就"显著"——那是自欺，和用回测骗自己没区别。
    所以这里先把同一天的收益平均成「当日策略收益」，再用天数做 t 检验。
    """
    # 🔴 主判口径 = 回测 B 格口径，**含 sealed_entry**（11-22 修）。
    # 回测 B 格 =「open 没顶死 + 摸板 + 收停板」，收停板在盘中即 sealed=True；
    # 若在这里排除 sealed，等于把回测唯一过门的那批（+1.37%/t19.2/n23571）全杀光
    # —— 那验的根本不是同一个假设。
    # sealed 仍单独计数（sealed_n / sealed_trades），随时可拆「剔除封死」的保守口径。
    usable = list(s["trades"])
    sealed_trades = sum(1 for t in usable if t.get("sealed_entry"))
    rets = [t["ret_pct"] / 100 for t in usable]
    n = len(rets)
    funded = [t for t in usable if t["funded"]]
    nav = s["nav"][-1]["nav"] if s["nav"] else s["capital"]

    why_count: dict[str, int] = {}
    for p in s["positions"]:
        if not p["funded"]:
            w = p.get("unfunded_why", "") or "未成交"
            why_count[w] = why_count.get(w, 0) + 1
    for tr in usable:
        if not tr["funded"]:
            w = tr.get("unfunded_why", "") or "未成交"
            why_count[w] = why_count.get(w, 0) + 1

    base = {
        "n": n, "funded_n": len(funded), "funded_nav": round(nav, 2),
        "sealed_n": len(s.get("sealed_codes", [])),   # 累计「检测时已封死」的票数（唯一计）
        "sealed_trades": sealed_trades,               # 其中已结算、进主统计的笔数
        "funded_ret": round((nav / s["capital"] - 1) * 100, 2),
        "funded_win": round(sum(1 for t in funded if t["ret_pct"] > 0) / len(funded) * 100, 1) if funded else None,
        "unfunded": why_count,
    }
    if n == 0:
        return {**base, "mean": None, "std": None, "t": None, "win": None, "n_need": None,
                "days": 0, "day_mean": None, "day_std": None, "t_day": None,
                "n_need_days": None, "daily_rate": 0.0,
                "exc_mean": None, "exc_win": None, "t_exc": None,
                "exc_days": 0, "n_need_exc": None}

    mean = sum(rets) / n
    t_all, std_all, _ = _tstat(rets)
    wins = sum(1 for r in rets if r > 0)

    by_day: dict[str, list[float]] = {}
    for t in usable:
        by_day.setdefault(t["sell_date"], []).append(t["ret_pct"] / 100)
    daily = [sum(v) / len(v) for v in by_day.values()]
    day_mean = sum(daily) / len(daily) if daily else None
    t_day, day_std, n_days = _tstat(daily)

    # 超额口径（策略收益 − 全市场等权基准）：这才是 alpha，绝对收益里混着 beta
    exc_by_day: dict[str, list[float]] = {}
    for t in usable:
        if t.get("excess_pct") is None:
            continue
        exc_by_day.setdefault(t["sell_date"], []).append(t["excess_pct"] / 100)
    exc_daily = [sum(v) / len(v) for v in exc_by_day.values()]
    exc_mean = sum(exc_daily) / len(exc_daily) if exc_daily else None
    t_exc, exc_std, n_exc_days = _tstat(exc_daily)
    n_need_exc = None
    if exc_mean and exc_mean > 0 and exc_std > 1e-6:
        n_need_exc = int(math.ceil((2.0 * exc_std / exc_mean) ** 2))

    n_need_days = None
    if day_mean and day_mean > 0 and day_std > 1e-6:
        n_need_days = int(math.ceil((2.0 * day_std / day_mean) ** 2))

    return {**base,
            "mean": round(mean * 100, 2), "std": round(std_all * 100, 2),
            "t": round(t_all, 2) if t_all is not None else None,
            "win": round(wins / n * 100, 1),
            "n_need": int(math.ceil((2.0 * std_all / mean) ** 2)) if mean > 0 and std_all > 1e-6 else None,
            "days": n_days,
            "day_mean": round(day_mean * 100, 2) if day_mean is not None else None,
            "day_std": round(day_std * 100, 2),
            "t_day": round(t_day, 2) if t_day is not None else None,
            "n_need_days": n_need_days,
            "exc_mean": round(exc_mean * 100, 2) if exc_mean is not None else None,
            "exc_win": round(sum(1 for v in exc_daily if v > 0) / len(exc_daily) * 100, 1) if exc_daily else None,
            "t_exc": round(t_exc, 2) if t_exc is not None else None,
            "exc_days": n_exc_days,
            "n_need_exc": n_need_exc,
            "daily_rate": round(n / n_days, 2) if n_days else 0.0}


def _p_two_sided(t: float) -> float:
    """正态近似双尾 p 值。样本少时偏乐观，但只用于多重检验排序，够用。"""
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


def fdr_qvalues(ts: dict, m_total: int | None = None) -> dict:
    """BH-FDR 校正：同时测 N 个策略时，把「撞运气」的概率算进去。

    用途：5 个策略里挑最好的那个，t=2 并不等于显著（大约 4 次里就有 1 次是假的）。
    这里输出 q 值，q<0.1 才算扛住了多重检验。
    """
    items = [(sid, _p_two_sided(t)) for sid, t in ts.items() if t is not None]
    if not items:
        return {}
    items.sort(key=lambda kv: kv[1])
    # m 用「计划检验的策略总数」而不是「已有数据的个数」：我们确实一次性测了 5 个，
    # 只是有些还没样本 —— 只按有数据的算会低估多重检验惩罚，等于偷偷放宽门槛。
    m = m_total or len(items)
    q: dict = {}
    prev = 1.0
    for rank, (sid, p) in reversed(list(enumerate(items, start=1))):
        val = min(prev, p * m / rank)
        q[sid] = round(val, 4)
        prev = val
    return q


def overview(led: dict) -> dict:
    rows = {}
    for sid, s in led["sleeves"].items():
        st = sleeve_stats(s)
        st.update({"name": s["name"], "hypothesis": s["hypothesis"],
                   "positions": len([p for p in s["positions"] if p["funded"]]),
                   "cash": round(s["cash"], 2)})
        rows[sid] = st
    q = fdr_qvalues({sid: r["t_exc"] for sid, r in rows.items()}, m_total=len(rows))
    for sid, r in rows.items():
        r["q_exc"] = q.get(sid)
    total_nav = sum(r["funded_nav"] for r in rows.values())
    total_cap = sum(led["sleeves"][sid]["capital"] for sid in rows)
    return {"sleeves": rows, "total_capital": total_cap, "total_nav": round(total_nav, 2),
            "total_ret_pct": round((total_nav / total_cap - 1) * 100, 2) if total_cap else 0.0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--init", action="store_true", help="重置账本（清空重来）")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    if args.init:
        LEDGER.unlink(missing_ok=True)
    led = load()
    if args.summary or True:
        ov = overview(led)
        print(json.dumps(ov, ensure_ascii=False, indent=2))
    save(led)
    return 0


if __name__ == "__main__":
    sys.exit(main())
