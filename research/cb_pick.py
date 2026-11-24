#!/usr/bin/env python3
"""转债隔夜：每日候选清单 + 纸面账本。

> 🔴 **判决（2026-11-24，#609/#610）：这条策略已关灯，本文件降级为「零成本观察样本」。**
> 理由三层：① 好看的数字都在拿不到的组合里（全池 244 只要 54 万）；② ¥1 万实买的
> +0.1391%/天 拆开是 **β 2.32、α 仅 +0.0268%（t1.34）**；③ 唯一能真下单的转债 ETF
> +0.0356%/天，在 ¥1 万上被「5 元最低佣金 = 往返 0.10%」打成 **年化 −14.3%**。
> **所以不要改它的选票规则、不要再加探针** —— 关灯之后优化它没有意义。
> 保留它只为：留一份「机械执行长什么样」的样本，以及将来若要复盘时口径可复现。

`cb_overnight_probe.py` 回答「这个方向成不成立」，本文件回答「今天该买哪几只、
按规则机械做过去 20 天长什么样」。

## 规则（与探针完全一致，不另设参数）

1. 昨日收盘价 > 130 元
2. 昨日成交额排在合格池前 1/3
3. 昨日收盘买入，今日开盘卖出

🔴 **关于 130 元这个门槛的准确说法**：制度上的强赎触发看的是「正股价格 ≥ 转股价的 130%
（连续 30 个交易日中 15 日）」，**不是转债价格 130 元**。130 元是市场惯例里「进入强赎风险区」
的价格带 —— 转债价格由转股价值 + 溢价率决定，正股涨到强赎线时转债通常已在 130 元以上。
所以它是**经验等价物，不是制度条款**。之所以选它，是因为阈值扫描显示 100~220 元全正、
单调，而 130 恰好①落在单调段 ②样本最长 ③有明确的产业解释（强赎博弈），
不是从数据里挑出来的孤立尖峰。

## 为什么单独做一个文件

探针只出统计量，看不出「机械执行是什么体验」：每天买几只、需要多少钱、
有没有买不到的日子。而老大要的是能用的东西，不是又一组 t 值。

## 🔴 已知偏差（必须随清单一起来）

- **幸存者偏差**：池子是今日在市转债，已强赎/到期的不在样本里
- **方向真实但在衰减**：样本外（cut 2025-07）t 2.27，未过 2.5 门槛
- **本质是微观结构收割**：全段收益为负（−0.094%/天），赚的是「收盘压低、开盘回归」那一段
- 因此**不上真钱**，只做零成本纸面跟踪

用法：
    python research/cb_pick.py                     # 今日清单 + 上一批兑现
    python research/cb_pick.py --fetch             # 先重拉行情（收盘后跑）
    python research/cb_pick.py --replay 20         # 再加最近 20 个交易日的回放
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

CST = timezone(timedelta(hours=8))
PRICE_LO = 130.0
TOP_FRAC = 1.0 / 3
MIN_NAMES = 9              # 合格池少于 9 只 ⇒ 前 1/3 不足 3 只，当天不做
LOT_VALUE = 10             # 转债 1 手 = 10 张
CAPITAL = 10_000.0         # 一个 sleeve 的钱（与股票账本一致，不另设一套）


def build(raw: dict):
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
            amt[t, j] = r[3] * r[2]
    return dates, codes, op, cl, amt


def pick(t: int, cl, amt, codes):
    """在参考日 t 收盘时点选票。返回 [(code, 昨收, 昨日成交额亿)]，按成交额降序。"""
    ok = np.isfinite(cl[t]) & (cl[t] > PRICE_LO) & np.isfinite(amt[t]) & (amt[t] > 0)
    idx = np.where(ok)[0]
    if idx.size < MIN_NAMES:
        return None
    order = np.argsort(amt[t, idx])
    k = max(1, int(idx.size * TOP_FRAC))
    sel = idx[order[idx.size - k:]]
    out = [(codes[j], float(cl[t, j]), float(amt[t, j]) / 1e8) for j in sel]
    out.sort(key=lambda r: -r[2])
    return out


def realized(sel, op_row, codes):
    """参考日选出的票，用**卖出日开盘价**算毛收益。返回 (收益列表, 缺开盘价的代码)。

    🔴 缺数据的票必须**单独返回**、不能静默跳过：这批票往往不是随机缺失
    （新债、停牌、取数失败），在它们身上算不出收益就等于把它们从分母里抹掉，
    是最经典的高估路径。调用方有义务把这个数字印出来。
    """
    idx = {c: j for j, c in enumerate(codes)}
    rets, missing = [], []
    for code, px, _amt in sel:
        j = idx[code]
        o = op_row[j]
        if not np.isfinite(o) or o <= 0:
            missing.append(code)
            continue
        rets.append(o / px - 1.0)
    return rets, missing


def budget_pick(sel, capital: float = CAPITAL) -> tuple[list, float, list]:
    """按成交额降序依次买 1 手（10 张），买满为止。返回 (买到, 剩余现金, 没买到)。

    🔴 为什么必须算这个：规则选出来的是「合格池前 1/3」的**全部**票，回测里它们是
    **等权全部持有**。可是 ¥10000 只能买 4~5 只 —— 买到的和等权持有的**不是同一个组合**。
    只报「+0.109%/天」而不报「¥10000 实买之后还剩多少」，就是把不可执行的组合当成结论。

    规则细节（必须写死，否则每次跑出来的组合都不一样）：**按成交额降序**逐只尝试，
    买得起就买 1 手、买不起**跳过继续往下**（不是「买到第一只买不起的就停」，
    也不是「只取前 k 只」）—— 这才是人拿到清单会做的事。
    """
    bought, cash, skipped = [], float(capital), []
    for row in sel:
        cost = float(row[1]) * LOT_VALUE
        if cost <= cash:
            bought.append(row)
            cash -= cost
        else:
            skipped.append(row)
    return bought, cash, skipped


def metrics(rets) -> dict:
    """一组毛收益 → {均值%, 最差%, 最好%}。空列表返回 None 值，不造 0。"""
    if not rets:
        return {"mean_pct": None, "worst_pct": None, "best_pct": None}
    return {"mean_pct": round(float(np.mean(rets)) * 100, 4),
            "worst_pct": round(float(np.min(rets)) * 100, 2),
            "best_pct": round(float(np.max(rets)) * 100, 2)}


def replay(dates, codes, op, cl, amt, n: int, capital: float = CAPITAL) -> dict:
    """回放最近 n 个「有次日开盘价」的交易日。

    每行同时给两个口径：
      · `gross_pct`     = **等权全部持有**（回测口径，选几只买几只，不管钱）
      · `gross_cap_pct` = **¥capital 实买**（成交额降序买满为止），配 `n_bought`
    两者的差 = 「策略的统计口径」与「一个 ¥10000 账户真能做到的」之间的差。
    """
    rows = []
    for t in range(len(dates) - 1, 0, -1):
        if len(rows) >= n:
            break
        if not np.isfinite(op[t]).any():
            continue                    # 今天还在盘中/无开盘价，跳过
        sel = pick(t - 1, cl, amt, codes)
        if sel is None:
            continue
        rets, skip = realized(sel, op[t], codes)
        if not rets:
            continue
        bought, cash, _skipped = budget_pick(sel, capital)
        brets, _bmiss = realized(bought, op[t], codes) if bought else ([], [])
        mv = metrics(rets)
        bv = metrics(brets)
        rows.append({"buy_date": dates[t - 1], "sell_date": dates[t],
                     "n_pick": len(sel), "n_filled": len(rets), "n_skip": len(skip),
                     "gross_pct": mv["mean_pct"],
                     "worst_pct": mv["worst_pct"], "best_pct": mv["best_pct"],
                     "n_bought": len(bought), "cash_left": round(cash, 0),
                     "gross_cap_pct": bv["mean_pct"],
                     "capital_wan": round(sum(px for _, px, _ in bought) * LOT_VALUE / 1e4, 2)})
    rows.reverse()
    return {"rows": rows, "n": len(rows), "capital": capital}


def merge_cache(old: dict, new: dict) -> dict:
    """把新拉到的日线并进旧缓存（按日期去重，新值覆盖旧值）。

    🔴 为什么要 merge 而不是直接覆盖：`fetch_daily` 一次只请求 640 根，
    而腾讯单次上限 320，实际只能拿到 2 年多。**直接覆盖会把 6 年历史砍成 2.4 年**
    —— 我自己就这么踩过一次：`--fetch` 一跑，1126 天的样本变 591 天，
    t 值从 5.28 掉到 3.16，而这是数据变短，不是 alpha 变弱。
    合并的另一个好处是只补最新那根，历史一根不动。
    """
    out = {c: list(rows) for c, rows in old.items()}
    for c, rows in new.items():
        if c not in out:
            out[c] = list(rows)
            continue
        by_date = {r[0]: r for r in out[c]}
        for r in rows:
            by_date[r[0]] = r
        out[c] = [by_date[d] for d in sorted(by_date)]
    return out


def fetch_cache(cache: Path, names_path: Path) -> None:
    """复用 `cb_overnight_probe.py` 里已验证的取数（东财 delay 域 + 腾讯日线）。

    不在这里重写一份：取数是最容易悄悄错的地方（东财 clist 单页上限 100、
    腾讯 volume 单位、push2 主域 502 要用 delay 域），两份代码就是两个 bug 面。
    """
    import cb_overnight_probe as probe          # 同目录，运行时才导入（测试不触网）

    lst = None
    try:
        lst = probe.fetch_list()
    except Exception as e:
        # 东财 clist 偶发 502（已知问题）。名单本身几乎不变，没必要为一次限流
        # 把整条取数链断掉 —— 有旧名单就用旧名单，只重拉日线。
        # 🔴 但**必须有旧名单**：拿不到名单就真的失败，不许静默产出空池。
        if names_path.exists():
            lst = json.loads(names_path.read_text(encoding="utf-8"))
            print("名单接口失败（%s），沿用旧名单 %d 只" % (str(e)[:60], len(lst)))
    if not lst:
        print("取名单失败且无旧名单可沿用，放弃", file=sys.stderr)
        raise SystemExit(2)
    print("转债列表 %d 只，开始拉日线…" % len(lst))
    raw = probe.fetch_daily(lst.keys())
    # 🔴 合并，不是覆盖：`fetch_daily` 只拿得到 2 年多，覆盖会把长历史砍掉。
    old = {}
    if cache.exists():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            old = {}                      # 缓存损坏就当没有，别为旧文件放弃本次取数
    merged = merge_cache(old, raw)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(merged), encoding="utf-8")
    names_path.write_text(json.dumps(lst, ensure_ascii=False), encoding="utf-8")
    print("成功 %d 只（并入后 %d 只，历史最深 %d 根）→ %s（名称表 → %s）"
          % (len(raw), len(merged), max((len(v) for v in merged.values()), default=0),
             cache, names_path))


def _fmt_list(sel, names) -> str:
    parts = []
    for code, px, amt in sel:
        nm = (names or {}).get(code) or ""
        parts.append("%s %s %.2f (%.1f亿)" % (code, nm[:4], px, amt))
    return " / ".join(parts)


def anchor(dates, cl) -> int:
    """最后一个**有收盘价**的交易日 —— 那天收盘就是「今日清单」的买入时点。

    🔴 原来取的是「最后一个有**开盘价**的日期，再减一」，于是 15:05 收盘后跑，
    给出的仍然是「昨天该买什么、今天兑现多少」。对一个每日清单来说这是致命的：
    老大照着看，永远看不到「今天收盘该买哪几只」。
    """
    t = len(dates) - 1
    while t > 0 and not np.isfinite(cl[t]).any():
        t -= 1
    return t


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def now_hm() -> int:
    d = datetime.now(CST)
    return d.hour * 60 + d.minute


def session_closed(hm: int) -> bool:
    """A 股 15:00 收盘。`hm` = 当天第几分钟。

    🔴 为什么要判这个：腾讯日线在**盘中**也会给出一根「今天」的 K 线，
    但它的 close 是**当前最新价**、volume 是**开盘至今的累计量**，不是收盘价。
    不判就会把 13:23 的实时价当成「今日收盘买入价」写进清单 —— 而这份清单
    唯一的价值就是「收盘价买」。收盘前跑只能算预览。
    """
    return hm >= 15 * 60


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="research/cb_daily.json")
    ap.add_argument("--names", default="research/cb_names.json")
    ap.add_argument("--fetch", action="store_true", help="强制重拉行情（否则缓存缺失时才拉）")
    ap.add_argument("--replay", type=int, default=0, help="回放最近 N 个交易日")
    ap.add_argument("--capital", type=float, default=CAPITAL,
                    help="一个账户的钱（默认 ¥10000，与股票账本 sleeve 一致）")
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    cache, names_path = Path(a.cache), Path(a.names)
    if a.fetch or not cache.exists():
        fetch_cache(cache, names_path)
    raw = json.loads(cache.read_text(encoding="utf-8"))
    names = {}
    if names_path.exists():
        names = json.loads(names_path.read_text(encoding="utf-8"))
    dates, codes, op, cl, amt = build(raw)
    ref = anchor(dates, cl)
    today = today_cst()

    print("=" * 74)
    print("转债隔夜 · 每日清单（收盘买 → 次日开盘卖）")
    print("规则：昨收 >%.0f 元 + 昨日成交额前 %.0f%%" % (PRICE_LO, TOP_FRAC * 100))
    print("=" * 74)

    # ---------------- 一、今日清单 ----------------
    sel = pick(ref, cl, amt, codes)
    # 盘中跑：腾讯日线的「今天」那根是实时价 + 不完整成交量，不是收盘价
    preview = (dates[ref] == today) and not session_closed(now_hm())
    print("\n【今日清单】%s %s买入 → 次日开盘卖出"
          % (dates[ref], "📌预览（尚未收盘，按实时价）" if preview else "收盘"))
    if preview:
        print("  🔴 现在 %02d:%02d 还没收盘：下面按**盘中实时价**算，成交量也不完整，"
              % divmod(now_hm(), 60))
        print("     只能当预览。15:00 后请重跑 --fetch 取定盘价 —— 收盘价才是可成交价。")
    if dates[ref] != today:
        # 缓存不是今天的：收盘后没重拉，或者今天不是交易日。两种都必须说清，
        # 否则照着一个三天前的清单去买，是最典型的「看起来在跟踪、其实在空转」。
        print("  ⚠️ 缓存最新只到 %s（今天 %s）；若今天已收盘，请先 --fetch" % (dates[ref], today))
    if sel is None:
        print("  合格池不足 %d 只 ⇒ 当天不做（不硬选几只凑单）" % MIN_NAMES)
    else:
        bought, cash, skipped = budget_pick(sel, a.capital)
        print("  规则选 %d 只（合格池前 1/3 等权），全部买满需 %.1f 万元"
              % (len(sel), sum(p for _, p, _ in sel) * LOT_VALUE / 1e4))
        print("  ⇒ ¥%.0f 实买 %d 只（成交额降序、买满为止），花掉 ¥%.0f，剩现金 ¥%.0f，"
              "买不起 %d 只"
              % (a.capital, len(bought), sum(p for _, p, _ in bought) * LOT_VALUE,
                 cash, len(skipped)))
        if skipped:
            print("  ⚠️ 回测口径是「%d 只等权全部持有」；¥%.0f 只能买 %d 只 ⇒ "
                  "**两者不是同一个组合**，收益不能直接套用" % (len(sel), a.capital, len(bought)))
        if len(bought) < 3:
            # 真实发生的：成交额最大的是 884 元的福新转债，一手 8840 元 ⇒ ¥10000 只买得起 1 只。
            # 「成交额降序」和「资金有限」撞在一起时，高价妖债会把整个仓位吃成单票赌博。
            print("  🔴 只买得起 %d 只 ⇒ 单票权重 %.0f%%，这不是策略设计的组合，是高集中度赌博"
                  % (len(bought),
                     (max((p for _, p, _ in bought), default=0) * LOT_VALUE
                      / a.capital) * 100))
        print("  " + _fmt_list(bought, names))

    # ---------------- 二、上一批兑现 ----------------
    if ref > 0:
        prev = pick(ref - 1, cl, amt, codes)
        print("\n【上一批兑现】%s 买 → %s 开盘卖" % (dates[ref - 1], dates[ref]))
        if prev is None:
            print("  当日合格池不足 %d 只，没有建仓" % MIN_NAMES)
        else:
            rets, missing = realized(prev, op[ref], codes)
            if not rets:
                print("  选 %d 只，但一只都没有开盘价 ⇒ 无法算兑现" % len(prev))
            else:
                pv = metrics(rets)
                pbought, _pcash, _pskip = budget_pick(prev, a.capital)
                brets, _bmiss = realized(pbought, op[ref], codes) if pbought else ([], [])
                bv = metrics(brets)
                print("  等权全部持有（%d 只）：均值 %+.3f%%，最差 %+.2f%%，最好 %+.2f%%"
                      % (len(rets), pv["mean_pct"], pv["worst_pct"], pv["best_pct"]))
                if brets:
                    print("  ¥%.0f 实买（%d 只）：均值 %+.3f%%" % (
                        a.capital, len(brets), bv["mean_pct"]))
                if missing:
                    # 🔴 不能让「19 只候选」和「均值」之间静默缩水：
                    # 缺数据的票往往不是随机缺失（新债、停牌、取数失败），
                    # 在它们身上算不出收益就等于把它们从分母里抹掉。
                    print("  ⚠️ 只有 %d/%d 只有开盘价，%d 只缺数据未计入均值（%s）"
                          % (len(rets), len(prev), len(missing),
                             "/".join(missing[:5]) + ("…" if len(missing) > 5 else "")))

    if a.replay:
        r = replay(dates, codes, op, cl, amt, a.replay, a.capital)
        print("\n" + "=" * 74)
        print("纸面账本 · 最近 %d 个交易日（每只 1 手，等权）" % r["n"])
        print("=" * 74)
        print("  %-11s %5s %5s %9s %8s %6s %9s" %
              ("买入日", "选", "成交", "等权收益", "最差单只", "实买", "¥实买收益"))
        for row in r["rows"]:
            print("  %-11s %5d %5d %+8.3f%% %+7.2f%% %6d %+8.3f%%" % (
                row["buy_date"], row["n_pick"], row["n_filled"],
                row["gross_pct"], row["worst_pct"], row["n_bought"],
                row["gross_cap_pct"] if row["gross_cap_pct"] is not None else float("nan")))
        g = np.asarray([row["gross_pct"] for row in r["rows"]], dtype=float) / 100
        gc = np.asarray([row["gross_cap_pct"] if row["gross_cap_pct"] is not None else 0.0
                         for row in r["rows"]], dtype=float) / 100
        if g.size >= 5:
            eq = np.cumprod(1 + g)
            eqc = np.cumprod(1 + gc)
            print("\n  区间毛收益累计 %+.2f%%（%.1f 个交易日），胜率 %.0f%%"
                  % ((eq[-1] - 1) * 100, g.size, (g > 0).mean() * 100))
            print("  扣 0.04%% 佣金后累计 %+.2f%%"
                  % ((np.cumprod(1 + g - 0.0004)[-1] - 1) * 100))
            print("  ⇒ ¥%.0f 实买口径累计 %+.2f%%（扣佣金 %+.2f%%），日均买 %.1f 只"
                  % (a.capital, (eqc[-1] - 1) * 100,
                     (np.cumprod(1 + gc - 0.0004)[-1] - 1) * 100,
                     float(np.mean([row["n_bought"] for row in r["rows"]]))))
            if abs((eqc[-1] - 1) - (eq[-1] - 1)) > 0.0005:
                print("     ⚠️ 两个口径差 %.2fpp —— 回测的「等权全部持有」不是 ¥%.0f 能执行的组合"
                      % (((eqc[-1] - 1) - (eq[-1] - 1)) * 100, a.capital))

    print("\n  🔴 偏差：幸存者偏差未排除；样本外 t2.27 未过门；全段收益为负 ⇒ 只做纸面跟踪，不上真钱。")
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"ref_date": dates[ref], "sell_date": "次日开盘",
             "picks": [{"code": c, "name": names.get(c, ""), "prev_close": p, "amt_yi": v}
                       for c, p, v in (sel or [])]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("  写出 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
