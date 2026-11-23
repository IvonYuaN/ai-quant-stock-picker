#!/usr/bin/env python3
"""短线多策略定义（影子账户用）——每个策略一个独立假设，互不共享参数。

🔴 纪律（老大原话「不准用回测数据来欺骗」「不要只用一个策略」）：
- 每个策略的阈值是**教科书/常识先验**，不是在这 3 年数据上拟合出来的最优值；
- **一策略一版本，永不因亏损调参**（调了就变成回测过拟合，账本也失去前向验证意义）；
- 每个策略只回答一个问题：这个假设在真实未来数据里成立吗。
- 参数说明里凡是「先验」都写清楚来源，不假装是「已验证」。

六个 sleeve（各自独立 ¥10000 虚拟账户，同一套执行/成本口径）：

| id | 名称       | 假设（经济逻辑）                                      | 出手时点 | 卖出     |
|----|-----------|------------------------------------------------------|---------|----------|
| P1 | 二板接力   | 情绪接力：二板封住=资金认可，次日溢价                     | 盘中触板 | T+1 收盘 |
| P2 | 首板接力   | 同族对照：板数越少跟随资金越弱，看板数是否真的影响溢价      | 盘中触板 | T+1 收盘 |
| P3 | 超跌反抽   | 短期反转：单日暴跌=散户过度反应，次日修复                  | 10:00   | T+1 收盘 |
| P4 | 竞价抢筹   | 集合竞价资金意图：高开+竞价放量=有资金愿在高位接             | 09:27   | T+1 收盘 |
| P5 | 尾盘惯性   | 隔夜动量：尾盘强势收在高位，次日开盘有惯性                   | 14:45   | 次日开盘 |
| P6 | 二板·跳空top2 | P1 的选股增强：二板里隔夜跳空最大的 2 只，溢价更集中     | 盘中触板 | T+1 收盘 |

为什么是这些：P1~P5 是 A 股短线里**逻辑互不相同**的四种驱动（情绪接力/反转/资金意图/动量），
而不是同一逻辑的五个参数版本 —— 参数版本会一起活或一起死，起不到分散验证的作用。

P6 与它们不同性质：它是**同一个假设(情绪接力)的选股口径变体**，刻意与 P1 并存对照。
理由不是「多试几个总能中一个」，而是「判读所需天数」——
二板全取要 185 个交易日才看得出 t≥2，跳空 top2 只要 15 个。前向账本最贵的是时间，
所以必须有一个能快速判死的 sleeve。它和 P1 唯一的差别是**选哪两只**，
成交/成本/退出/仓位全部共用 ⇒ 结论可以干净地归因到「选股口径」。
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


# ---------------- P1/P2/P6 打板接力（信号来自 shortline_signal.py）----------------

def _board_candidates(hits: list[dict], board: int) -> list[dict]:
    """B 格 + 指定板数 ⇒ 候选，并附 `gap`（隔夜跳空幅度）。

    🔴 **成交口径只在这里定义一次**：P1/P2/P6 都从这个入口取候选，差别只在
    「排序 + 截断」。若各自复制一份过滤，某天在其中一个函数里放行了 A 格或改了
    板数判据，两个 sleeve 验的就不是同一个假设了 —— 而账本上没有指纹能发现这件事。

    `gap` 缺 `pre`/`open` 时记 0（不猜）。P6 会把它排到后面，
    而不是因为「字段缺失」被当成跳空 +∞ 挤进 top2。
    """
    out = []
    for h in hits or []:
        if h.get("cell") != "B_放量可成交":
            continue                       # A 格一字排不进
        if int(h.get("board") or 0) != board:
            continue
        # 🔴 20cm（创业板/科创）不进打板 sleeve 的成交集（2026-11-23 拍板）：
        # 3y 过门的 B 格 +1.37%/t19.2 来自 load_main_board 的**纯主板 60/00**池，
        # 20cm 的涨停幅度、止损幅度（-10% vs -5%）、隔夜跳空分布全不一样。
        # 让它进账本 = 拿一个没被回测验证过的假设去冒「已过门」的名，
        # 归因时又分不开，最后把两族的盈亏混成一个 t 值 —— 这是最隐蔽的自欺。
        # 推送里照常显示（老大 11-19 拍板纳入观察），但不记账、不下单。
        if float(h.get("board_lim") or 0.10) >= 0.15:
            continue
        pre = float(h.get("pre") or 0)
        open_px = float(h.get("open") or 0)
        gap = (open_px / pre - 1.0) if (pre > 0 and open_px > 0) else 0.0
        out.append({"code": h["code"], "name": h.get("name", ""),
                    "price": float(h.get("price") or 0), "gap": gap,
                    "sealed": bool(h.get("sealed_now")),
                    "note": "%d板·%s" % (board, (h.get("seal_tag") or h.get("seal") or "")[:12])})
    return out


def from_board_hits(hits: list[dict], board: int) -> dict:
    """把打板检测结果转成策略信号（P1 全取二板 / P2 全取首板）。

    🔴 成交口径（最容易骗人的一步）：
    - A 格（开盘一字）⇒ 直接丢。verdict §四-C 明确「排不进」，假装买到就是骗自己。
    - B 格（open 没顶死一字 + 盘中摸板）⇒ **全部照记并买入**，但打 `sealed` 标记。

    🔴 为什么 `sealed=True` 也必须买（11-22 修，原实现直接 shares=0 是错的）：
    回测 B 格的定义是「**open 没顶死 + 摸板 + 收停板**」——
    「收停板」在盘中就表现为 `sealed=True`（卖一=0、买一在涨停）。
    也就是说，回测唯一过门的那批（B 格 +1.37%/t19.2/n23571）**全部都是 sealed=True**。
    原实现在 ingest 层把 sealed 判成「封死排不进」直接不买，
    等于把回测的作业集整个抹掉 ⇒ P1/P2 从起跑起 0 成交、资金 100% 闲置、
    验的根本不是同一个假设。

    真实约束靠 `cell` 就已经卡住了（A 格一字才排不进）；
    B 格的「open 没顶死」说明集合竞价确实成交了、盘中存在可买时点。
    `sealed` 只表示「**我检测到它的那一刻**已经封住」，不表示「全天买不到」，
    所以它只作**分组标记**（`sealed_entry` 字段 + `sealed_n` 计数），不决定买不买。

    🔴🔴 11-23 更正（推翻上面整段的经济结论）：`+1.37%/t19.2/n23571` 及整个 B 格家族
    都是**语义前视**产物 —— B 格要求「收盘封死」，而封死时收盘价买不到；
    按真正可成交的「摸板即买」口径，首板 **−0.94%/t−16.5**、2板 **−2.30%/t−20.1**。
    见 `research/executable_entry_probe.py`。所以本函数当前在**照买一个负期望策略**，
    保留它只为跑负向对照（`evidence_state=rejected`），**不得再作为候选上线**。
    要救活必须先有「封单量/排队位置」数据来预判回封 —— 库里没有，禁调参。
    """
    out = _board_candidates(hits, board)
    # 排序：检测时还没封死的排前面（离「买到」更近，优先占仓），封死的排后面。
    # 🔴 排序只影响资金分配顺序，不影响「买不买」—— 两者都要买。
    out.sort(key=lambda s: (s["sealed"], -s["price"]))
    return {"signals": out, "sealed_codes": [s["code"] for s in out if s["sealed"]]}


def from_board_hits_top_gap(hits: list[dict], board: int = 2, top_n: int = 2) -> dict:
    """二板里按**隔夜跳空幅度**取前 top_n 只（P6）。

    🔴 为什么按跳空排，而不是按封单量 / 价格 / 涨幅：
    shortline 归因给出的样本效率差得很远 —— 二板全取 +0.44%/t3.87（要 185 个交易日
    才判得动），而只取跳空最大的 top2 是 +2.73%/t13.75（15 个交易日就够）。
    经济逻辑：跳空幅度 = 隔夜资金愿意在**更高位置**真金接货的强度。
    它比「收盘封单量」更硬 —— 封单可以挂假（撤单成本为零），开盘价必须花钱成交。

    🔴 为什么不是「挑出来的那个点」：top1 +2.95%/t10.68、top2 +2.73%/t13.75、
    top8 +1.24%/t9.31 是一条**单调平台**，不是孤立尖峰。参数换一格结论不变，
    才算先验成立；只有一个格子好、隔壁就塌，那叫过拟合。

    tie-break 用 `code`：同一天两只跳空幅度相同是常事（都是一个涨停价开盘）。
    若按输入顺序决定谁占仓，回放就不可复现 —— 输入顺序取决于网络返回顺序。

    🔴 **实盘作业集 ≠ 回测作业集（已知偏差，刻意不修）**：
    跳空幅度在 09:30 就固定了，但「哪几只摸了板」是盘中陆续冒出来的。
    回测的 top2 = 用**全天**候选按 gap 取前二；实盘只能按**到达顺序**累计买入 ——
    早盘只有 1 只摸板时也会先买它，哪怕后来者 gap 更大（买到就退不回去）。
    这不是前视，是任何真实执行都要吃的约束；但两边的作业集因此并不相同，
    所以 P6 的账本数字**不能当成 +2.73% 的复现**，只能当成一次独立的前向检验。
    要让两边等价，就得等全天收完再按事后排名买 —— 那是前视，宁可不做。
    """
    n = max(1, int(top_n or 1))
    out = _board_candidates(hits, board)
    out.sort(key=lambda s: (-s["gap"], s["code"]))
    out = out[:n]
    for s in out:
        s["note"] = "%s·跳空%+.1f%%" % (s["note"], s["gap"] * 100)
    return {"signals": out, "sealed_codes": [s["code"] for s in out if s["sealed"]]}


def board_signals(spec: dict, hits: list[dict]) -> dict:
    """按注册表里声明的口径把检测结果转成信号 —— 新增 sleeve 只改注册表，不改这里。

    `board` 缺失时**报错而不是静默返回空**：静默空集合会被上游记成
    「今天没有信号」，而账本分不清「真的没有」和「口径没配上」。
    """
    if "board" not in spec:
        raise KeyError("board 阶段策略 %r 未声明 board 板数" % (spec.get("name") or spec))
    board = int(spec["board"])
    top_n = spec.get("top_n")
    if top_n:
        return from_board_hits_top_gap(hits, board=board, top_n=int(top_n))
    return from_board_hits(hits, board)


# ---------------- 注册表 ----------------

# 🔴🔴 11-23 撤回：打板家族（P1/P2/P6）的「过门」是**前视产物**，证据见
#   `research/executable_entry_probe.py` + `research/out_executable_entry_probe.json`。
#   根因一句话：B 格的定义是 `收盘价 ≥ 涨停价 × 0.998`，即**收盘封死在涨停**。
#   封死 = 后面排着巨量买单 ⇒ **收盘价买不到**，但统计却按「收盘价买入」记收益。
#   同一份数据、同一批信号，换成任何能成交的价（700 交易日，扣 0.15% 往返）：
#                    买收盘涨停价(前视) | 摸板即买(可执行) | 次日开盘买(可执行)
#       首板           +1.46% / t33.5  |   −0.94% / t−16.5 |  −0.07% / t−0.8
#       2板            +1.91% / t19.6  |   −2.30% / t−20.1 |  −0.74% / t−5.7
#       3板            +2.10% / t11.0  |   −3.26% / t−13.7 |  −1.38% / t−6.3
#       4板+           +2.20% / t 8.4  |   −2.80% / t −8.0 |  −2.29% / t−5.8
#   差异可精确对账（2 板）：摸板 64220 格，回封 40031(62.3%) 均 +1.91%、
#   炸板 24189(37.7%) 均 −8.40%；0.623×1.91 + 0.377×(−8.40) = −1.97%
#   ≈ 实测「摸板即买」−2.30%（差 0.33pp 来自按日等权 vs 按格加权）。
#   「是否回封」要等收盘才知道 ⇒ 用它筛样本 = 拿结果反推信号。
#   ⇒ 三个模式一律置 `rejected`，账本继续跑作**负向对照**（不再是候选策略）。
#   ⇒ 结论：**打板 lane 无可执行 alpha**。要救它必须先有「封单量/排队位置」这类
#      能预判回封的数据，现有库里没有，因此**不靠调参去救**（禁亏损调参）。
#
# 🔴🔴 同日更根本的一条：**日频短线在这套数据里的生存门槛是「每笔 >0.15% 可执行超额」**。
#   `research/turnover_cost_probe.py`（主板 3283 只 / 731 交易日 / 等权截面）实测：
#       隔夜（收→次日开）  −0.079%/天 (t−3.3)
#       日内（开→收）      +0.111%/天 (t+2.2)   ← T+1 禁止当日了结，赚不到
#       全  （收→次日收）  +0.030%/天 (t+0.5)   ← 与 0 无统计差别
#   ⇒ A 股收益几乎全在日内，隔夜净流出；而 T+1 恰好禁止你赚日内 ⇒ 只能承担负隔夜。
#   ⇒ 日频全换手成本 0.15%/次 × 240 ≈ **36%/年**，而日均税前只有 +0.030% ⇒ 必然为负。
#   ⇒ 所以「因子不够多」是误诊：门槛在成本，任何日频信号都必须先跨过 0.15%/笔。
#   P5「尾盘惯性」正是**专门去买那段负隔夜**（涨得越多隔夜越负：7~10% 档 −0.91%），
#   属方向性错误，不是参数问题 ⇒ 置 rejected，禁再调参救活。
#   唯一残留方向（记录、不宣称成立）：**持有多日**把日内收益累积、把成本摊薄到
#   0.15%/天数 —— 那是中线/主战场 lane，不是短线。
#
# 🔴 止损为什么全设 0（11-23 用 700 交易日实测后定的，不是拍脑袋）：
#   research/stop_sensitivity_probe.py 对同一批历史信号回放止损档位，
#   严格二板 B 格（可成交，6004 格）结果：
#     无止损 +1.431%/笔(t9.38) | -10% +1.210% | -8% +0.974% | -5% +0.925% | -3% +0.842%
#   触发率：-5% 档有 **43.9%** 的笔在次日盘中破位，而多数随后收盘回升
#   ⇒ -5% 止损吃掉 0.506pp/笔（相对损失 35%）。
#   成因是 A 股 T+1 结构让「固定百分比止损」变成**有下行无保护**：
#     · 跳空低开超过止损线 ⇒ 只能按开盘价成交，一样亏，保护不了；
#     · 盘中砸破止损线后回升 ⇒ 砍在最低点，纯损失。
#   更硬的理由：**过门的数字本身是无止损口径**（严格二板 +2.09%、首板 top5 +2.08%）。
#   账本若加 -5%，前向验证与回测就不同口径 ⇒ 永远无法判读「前向是否复现回测」。
#   ⚠️ 去掉止损 ≠ 忽略风险：风险控制靠**仓位**（单笔 20%、最多 5 笔）而不是靠砍在坑里。
#   若将来要恢复，必须先让回测也带上同一止损重跑并重新过门，不许只改账本一侧。
#
# 🔴 打板家族（P1/P2/P6）为什么卖在**次日开盘**而不是收盘（11-23 实测，700 交易日）：
#   严格二板 B 格（可成交，4220 格）当日买入后：
#       次日收盘卖 t1_close  +1.439%/笔(t9.02)  胜 63.3%
#       次日开盘卖 t1_open   +2.088%/笔(t19.38) 胜 80.7%   ← 更优且更稳
#   P6 top2：同样 t1_open 更优（+2.395%/t16.45/胜72.6%）。
#   这不是调参选出来的：B 格次日的**日内**收益（开盘买→收盘卖）为负，
#   而 (1+t1_close) = (1+t1_open)×(1+日内) 是恒等式 ⇒ 日内为负时 t1_open 必然更高。
#   经济含义：打板买的是**隔夜情绪溢价**，它在次日集合竞价一次性兑现，开盘后就在回吐。
#   持有到收盘 = 把已经到手的溢价还回去，还白担一天日内波动。
#   可执行性：集合竞价成交价就是开盘价（无滑点）；次日一字涨停开盘照样卖得掉（买单排队），
#   一字跌停开盘则走 sealed 顺延逻辑 —— 这条比 t1_close 更确定，不是更乐观。
#   ⚠️ P3/P4 不改：它们买的是「日内修复/日内延续」，兑现窗口本来就在收盘。
#   ⚠️ P2 必须与 P1 同口径，否则「首板 vs 二板」的对照里混进了不同的卖出时点。
STRATEGIES: dict[str, dict] = {
    "P1": {
        "name": "二板接力",
        "hypothesis": "情绪接力：二板封住=资金认可，次日有溢价（唯一在 3y 回测过门的假设，本账本做前向验证；只记「有卖单」那一 tick 的成交）",
        "phase": "board",
        "scan": None,
        "board": 2,
        # `params` / `evidence` 是**给人看的口径标签**，卡片直接印出来：
        # 老大要的是「合理的参数模式」，那就必须把每个模式的阈值和证据状态写在同一行，
        # 不然推出去的六个模式看起来一样可信，等于把未验证的混进已验证里卖。
        "params": "二板·B格(非一字)·全取·单笔≤20%仓",
        "evidence": "撤回 买点不可成交；可执行口径 −2.30%/t−20.1(摸板即买)",
        "evidence_state": "rejected",
        "exit": "t1_open",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P2": {
        "name": "首板接力",
        # 🔴 原假设「首板参与资金更杂、次日抛压更重」被数据证伪（11-23，700 交易日）：
        #   首板可成交 B 格 t1_open +1.610%/t33.71/胜94.7%（31498 格），
        #   熊/牛/震荡三段 +1.380%/+1.856%/+1.594% 全部显著正（t 19~23），
        #   比二板（+2.088%/t19.38/胜80.7%，仅 4220 格）**样本大 7.5 倍、胜率高 14pp、t 更高**。
        #   它与 P1 不是「对照关系」而是**同一维度的另一端**：首板=高胜率低幅度，
        #   高板=高幅度低胜率。这才是真实的风险收益权衡，不是谁替代谁。
        "hypothesis": "首板 = 资金首次介入，次日开盘溢价最稳（样本最大、胜率最高）；P1 的低幅度高胜率对应物",
        "phase": "board",
        "scan": None,
        "board": 1,
        # 🔴 为什么要 top_n（11-23）：首板每天约 43 个可成交信号，而账本受资金约束
        # 只能买 5 个（单笔 20%、5 笔满仓）。此前是「全取」，实际买哪 5 个由信号
        # 到达顺序决定 ⇒ 等价于从 43 个里随机抽 5 个，期望 = 全取均值 +1.610%。
        # 改成按跳空取前 5 个后，与账本能买的槽位数对齐，期望 +2.084%（+0.47pp/笔）。
        # 这不是挑最优参数：top1/2/3/5 分别为 2.267/2.141/2.160/2.084%，**全部高于**
        # 全取的 1.610% —— 单调平台说明「跳空排序」本身携带信息，不是某个格子的运气。
        # 代价是胜率从 94.7% 降到 85.3%（幅度与胜率的正常权衡），t 仍 25.24。
        "top_n": 5,
        "params": "首板·B格·跳空最大5只·次日开盘卖",
        "evidence": "撤回 买点不可成交；可执行口径 −0.94%/t−16.5(摸板即买)",
        "evidence_state": "rejected",
        "exit": "t1_open",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P3": {
        "name": "超跌反抽",
        "hypothesis": "短期反转：单日暴跌=散户过度反应，次日修复（学术最扎实的短线异象）",
        "phase": "morning",
        "scan": scan_oversold,
        "params": "昨跌≥0.7×跌停·今-1%~+4%·10:00买",
        "evidence": "否证 两条卖点均零边际(次日开 −0.12%/t−1.0)；setup 本身无 alpha",
        "evidence_state": "rejected",
        "exit": "t1_close",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P4": {
        "name": "竞价抢筹",
        "hypothesis": "集合竞价高开+放量=有资金愿在高位真金接货，日内延续",
        "phase": "auction",
        "scan": scan_auction,
        "params": "高开2~7%·竞价额≥昨成交额2%",
        "evidence": "前向验证中·未过门",
        "evidence_state": "unverified",
        "exit": "t1_close",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P5": {
        "name": "尾盘惯性",
        "hypothesis": "隔夜动量：尾盘强势收高，次日开盘有惯性溢价（不持仓过夜到收盘，只吃开盘）",
        "phase": "late",
        "scan": scan_late,
        "params": "涨3~7%·量比≥1.5·换手2~15%·14:45买",
        "evidence": "否证 买的是负隔夜(−0.079%/天)；可执行 −0.72%/t−25.8",
        "evidence_state": "rejected",
        "exit": "t1_open",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        "max_positions": 5,
    },
    "P6": {
        "name": "二板·跳空top2",
        "hypothesis": "情绪接力×隔夜强度：同为二板，隔夜跳空最大的那 2 只溢价更集中（跳空=有资金愿在更高位置真金接货，比封单量更硬）。P1 的选股变体，其余口径完全共用",
        "phase": "board",
        "scan": None,
        "board": 2,
        "top_n": 2,
        "params": "二板·B格·跳空最大2只",
        "evidence": "撤回 同 P1 前视；可执行口径 −2.30%/t−20.1",
        "evidence_state": "rejected",
        "exit": "t1_open",
        "stop_pct": 0.0,
        "position_pct": 0.20,
        # 🔴 max_positions 必须 ≥ 2×top_n，**不能设成 2**：
        # T+1 收盘才结算，所以「昨天买的 2 只」在今天盘中仍然占着仓位。
        # 若上限设 2，今天开盘时 slots_left 恒为 0 ⇒ P6 隔一天就永远「仓位已满」，
        # 组合净值永远不动（信号统计照走，但账本看起来像死了）。
        # 设 5 = 与 P1 完全一致，让唯一变量只剩「选哪两只」。
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
