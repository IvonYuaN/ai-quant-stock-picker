#!/usr/bin/env python3
"""多模式推荐卡片渲染 —— 纯函数，无 I/O、无网络、无账本读写。

为什么要单独一个模块：
推送和推荐**必须是同一份口径**。如果打板卡片在一个文件里拼字符串、
其他模式在另一个文件里拼，迟早出现「账本买了 A、卡片推的是 B」。
这里只做「给定信号 → markdown」，谁调用都长一样；本机 `shortline_push.py`
和 runner 上的 `shortline_cloud.py` 共用它。

🔴 纪律：每个模式必须把**参数**和**证据状态**印在同一行。
六个模式里只有打板（P1/P6）真过门，其余是前向验证中；
卡片不标状态 = 把未验证的混进已验证里卖，这是红线（不冒充实有 alpha）。

用法：
  from mode_card import render_phase_card, render_daily_footer
  md = render_phase_card("morning", res["sleeves"])
"""
from __future__ import annotations

from paper_strategies import STRATEGIES

# 证据状态 → 卡片标签。措辞刻意保守：`passed` 只给真过门的，
# 其余一律写「未过门」，不写「观察中可能有效」这种含糊话。
EVIDENCE_TAG = {
    "passed": "✅过门",
    "unverified": "⏳未过门",
    "control": "⚪对照",
    # 🔴 11-23 新增：不是「还没验证」，是**已验证为不成立**，必须与未过门分开，
    # 否则推出去的卡片会把已被证伪的模式当成「待观察」，等于继续误导。
    "rejected": "❌已否证",
}

PHASE_CLOCK = {"auction": "09:27", "morning": "10:00", "late": "14:45",
               "board": "盘中", "close": "15:05"}
PHASE_NAME = {"auction": "竞价抢筹", "morning": "超跌反抽", "late": "尾盘惯性",
              "board": "打板", "close": "收盘"}

# 卡片行数上限：企业微信 markdown 一屏大概 20 行，超了老大要滑屏才看得完，
# 等于没推。超出的用「还有 N 只」收口，详情去报告看。
MAX_ROWS = 4


def tag_of(spec: dict) -> str:
    return EVIDENCE_TAG.get(spec.get("evidence_state", ""), "⏳未过门")


# ---------------- 推送闸门：只推「有用」的 ----------------
#
# 🔴 2026-11-25 老大原话（附截图）：
#   「这种通知全都都是失效的完全没必要出现 还影响视觉 我只需要有用的」
# 截图里那条「今日推荐」四个模式全是 `⏳未过门` / `❌已否证`，每个后面还跟着
# 两到三只票。对老大来说这不是信息，是**负资产**：他得先读一行判决、再决定
# 忽略后面那几只票，净收益为负；更糟的是扫一眼只记住「今天推了 4 个模式」。
#
# 所以推荐类出口（盘中卡片 / 今日推荐 / 结算 / 尾数统计）**硬闸在 passed**：
# 没证据的东西一律不出现。这不是「少推」，是「不拿未验证的当推荐卖」——
# 与模块头那句红线同一条：不冒充实有 alpha。
PUSHABLE_STATES = ("passed",)


def is_pushable(sid: str) -> bool:
    """该模式是否允许出现在**推荐类**推送里（`passed` 才允许）。

    🔴 注意这是**出口闸门**，不是判决逻辑：`STRATEGIES[sid]["evidence_state"]`
    仍由 `paper_strategies` 定义，这里只决定「要不要让老大看到它的选股」。
    未过门 / 已否证 / 对照 三种状态一律不推 —— 包括「未过门」：
    没测过的东西推出去，等于让老大替我承担验证成本。
    """
    return (STRATEGIES.get(sid) or {}).get("evidence_state") in PUSHABLE_STATES


def render_gate_line() -> str:
    """「今天为什么没有推荐」—— 一行，只在**没有任何模式过门**时出现。

    🔴 2026-11-25（#662 的轻量落点）：`is_pushable` 闸门让盘中静默成为常态
    （当前 0/6 过门 ⇒ 盘中「今日推荐」永远为空），但**静默本身不带正证据** ——
    老大分不清「今天没有可信推荐」和「推送链路坏了」。健康文件里写了
    `skip_reasons`，可那是给我排查用的，不是给他看的。

    口径：
    - 有模式过门 ⇒ 返回空串（有推荐时不需要解释「为什么没有」）。
    - 全部不过门 ⇒ 说清总数与过门数，并点明「不发推荐」是**闸门行为**，不是故障。
    """
    total = len(STRATEGIES)
    passed = sum(1 for sid in STRATEGIES if is_pushable(sid))
    if passed:
        return ""
    return "> 模式过门 %d/%d：其余未过门/已否证，盘中不发推荐" % (passed, total)


def mode_head(sid: str, spec: dict) -> str:
    """一个模式的标题行：名称 + 参数 + 证据状态，三件事缺一不可。"""
    return "**%s %s** %s ｜ %s ｜ %s" % (
        sid, spec.get("name", ""), tag_of(spec),
        spec.get("params", "（未声明参数）"), spec.get("evidence", ""))


def _skip_reason_bucket(why: str) -> str:
    """把逐票未成交原因归并成人类能一眼扫过的一类。

    🔴 不合并的话，8 只票会打 8 行「单笔¥2000买不起1手(价33.5)」，
    把真正的信息（有信号、但小资金买不起）埋掉。
    """
    if why.startswith("单笔"):
        return "买不起1手"
    if why == "仓位已满":
        return "超仓"
    if why == "现金不足":
        return "现金不足"
    return "其他"


def skip_summary(skipped: list[dict]) -> str:
    """`3 买不起1手 · 2 超仓` 这样的归并摘要（顺序固定，便于测试与对照）。"""
    if not skipped:
        return ""
    order = ("买不起1手", "超仓", "现金不足", "其他")
    cnt: dict[str, int] = {}
    for s in skipped:
        b = _skip_reason_bucket(str(s.get("why") or ""))
        cnt[b] = cnt.get(b, 0) + 1
    return " · ".join("%d %s" % (cnt[b], b) for b in order if cnt.get(b))


def _price(v) -> str:
    try:
        return "%7.2f" % float(v)
    except (TypeError, ValueError):
        return "      -"


def pick_row(sid: str, spec: dict, item: dict) -> str:
    """一行推荐：代码 名称 价格 说明。

    `note` 由各模式自己写（打板是封单/跳空，超跌是昨日跌幅，竞价是高开+竞价额），
    这里不做解释、不改写 —— 上层改了 note 就等于改了推荐理由。
    """
    code = item.get("code", "")
    name = str(item.get("name") or "")[:4]
    note = item.get("note") or ""
    shares = item.get("shares")
    lot = " %d股" % shares if shares else ""
    return "✅ %s %-4s %s%s  %s" % (code, name, _price(item.get("price")), lot, note)


def render_mode_block(sid: str, spec: dict, rec: dict, max_rows: int = MAX_ROWS) -> list[str]:
    """单个模式一块：标题 + 最多 max_rows 行成交 + 一行未成交归并。"""
    bought = list(rec.get("bought") or [])
    skipped = list(rec.get("skipped") or [])
    n_sig = int(rec.get("signals") or 0)
    L = [mode_head(sid, spec)]
    for it in bought[:max_rows]:
        L.append(pick_row(sid, spec, it))
    if len(bought) > max_rows:
        L.append("　 …还有 %d 只成交" % (len(bought) - max_rows))
    if not bought:
        if n_sig > 0:
            L.append("⚪ 有 %d 个信号，全部未成交%s" % (
                n_sig, ("（%s）" % skip_summary(skipped)) if skipped else ""))
        else:
            L.append("⚪ 无信号")
    elif skipped:
        L.append("⚪ 另 %d 只未成交（%s）" % (len(skipped), skip_summary(skipped)))
    return L


def render_phase_card(phase: str, sleeves: dict, when: str = "") -> str:
    """一个时点的多模式卡片。

    `sleeves` = paper_scan.run_phase 返回的 res["sleeves"]：
        {sid: {"name", "signals", "bought": [...], "skipped": [...]}}
    只渲染**真的产出了信号或成交**的模式：全部为空时返回空串，
    让调用方按 fail-closed 静默，而不是推一张「今天啥也没有」的空卡。

    🔴 另加一道 `is_pushable` 闸：未过门/已否证的模式即使买了票也不推（见 PUSHABLE_STATES）。
    """
    rows: list[str] = []
    for sid in sorted(sleeves):
        if not is_pushable(sid):
            continue
        spec = STRATEGIES.get(sid) or {}
        rec = sleeves[sid] or {}
        if not rec.get("bought") and not rec.get("signals"):
            continue
        rows.extend(render_mode_block(sid, spec, rec))
    if not rows:
        return ""
    head = "## 🎯 %s %s" % (PHASE_NAME.get(phase, phase), when or PHASE_CLOCK.get(phase, ""))
    return "\n".join([head.rstrip()] + rows)


def render_daily_footer(picks: dict, pending: list[tuple[str, str]] | None = None) -> str:
    """打板卡片尾部的一行「今日其他模式」——让老大每张卡都能看到全貌。

    `picks` = {sid: {"bought": [...], "signals": n}}（当天累计，取自 picks_<date>.json）
    `pending` = [(sid, "14:45")]，还没到时点的模式，写「未到点」而不是装作没有。

    🔴 同样只统计可推模式：一行「P1 0笔 / P4 2笔」里混着已否证模式，
    等于把判决结果又当成绩汇报了一遍。全不可推时返回空串。
    """
    parts = []
    for sid in sorted(picks):
        if not is_pushable(sid):
            continue
        rec = picks[sid] or {}
        n = len(rec.get("bought") or [])
        parts.append("%s %d笔" % (sid, n))
    for sid, clock in (pending or []):
        if not is_pushable(sid):
            continue
        parts.append("%s 未到%s" % (sid, clock))
    if not parts:
        return ""
    return "> 今日各模式: " + " / ".join(parts)


def render_legend(with_board: bool = True) -> str:
    """口径说明一行。

    `炸板-5%` 是老大点名问过的字段：它对主板票**永远是 5%**，因为它是
    「半个跌停幅度」这个规则值，不是行情数据。不写清楚就会被当成坏数据。

    只在卡片里确实有打板段时才解释炸板止损 —— 一张纯竞价/超跌的卡上写「炸板-5%」
    是噪音，还会让人以为这些模式也按打板口径止损。
    """
    tail = "> 单笔≤20%仓·严格兑现·不恋战"
    if not with_board:
        return tail
    return ("> 炸板-5% = 从买入价回落即止损（主板=半个跌停5%，双创10%）"
            "·20cm票回测未验过·单笔≤20%仓·T+1收盘兑现")


def render_card(board_section: str, phase_card: str, footer: str,
                arena_line: str = "") -> str:
    """把各段拼成最终一条消息：打板段 → 多模式段 → 尾部 → 口径 → 账本摘要。"""
    segs = [s for s in (board_section, phase_card) if s]
    if not segs:
        return ""
    segs.append(render_legend(with_board=bool(board_section)))
    if footer:
        segs.append(footer)
    if arena_line:
        segs.append(arena_line.strip())
    return "\n\n".join(segs)


def render_settlement_card(led: dict, date: str, max_rows: int = 4) -> str:
    """收盘复盘的六模式结算段：每个模式今天真的赚/亏了多少。

    🔴 纪律：
    - **只显示当天 `sell_date == date` 的真实成交**，不拿持仓浮盈冒充结算；
    - 0 结算样本写「未结算」，**不显示 0% 超额** —— 首日超额恰是基准反数，
      显示 0 就是假的（这条前端已经守了，推送卡片不能漏）；
    - 一行一个模式，标参数/证据状态，跟盘中卡片同一套口径，不另造文案。

    `led` = paper_ledger.load() 的账本 dict。
    """
    try:
        from paper_strategies import STRATEGIES
    except ImportError:  # pragma: no cover - 同目录模块，正常不会走到
        return ""
    blocks: list[str] = []
    for sid in sorted(STRATEGIES):
        if not is_pushable(sid):
            continue
        trades = [t for t in (led.get("sleeves", {}).get(sid, {}).get("trades") or [])
                  if t.get("sell_date") == date]
        spec = STRATEGIES[sid]
        funded = [t for t in trades if t.get("funded")]
        # 🔴 有 trade 记录但全是未成交信号 ⇒ 实质是 0 结算。
        # 显示「0 笔 盈亏¥+0」会让人以为真平过仓、收益恰好为 0，
        # 而首日 0% 超额恰是基准反数 —— 直接跳过这个模式。
        if not funded:
            continue
        pnl = sum(float(t.get("pnl") or 0) for t in funded)
        rows = []
        for t in funded[:max_rows]:
            exc = t.get("excess_pct")
            exc_s = "超额—" if exc is None else "超额%+.2f%%" % float(exc)
            ns = t.get("no_stop_ret_pct")
            ns_s = "" if ns is None else " 若不止损%+.2f%%" % float(ns)
            rows.append("  %s %s %+.2f%% %s%s" % (
                t.get("code", ""), str(t.get("name") or "")[:4],
                float(t.get("ret_pct") or 0), exc_s, ns_s))
        head = "**%s %s** %s 今日结算 %d 笔 盈亏¥%+.0f" % (
            sid, spec.get("name", ""), tag_of(spec), len(funded), pnl)
        stop_n = sum(1 for t in trades if t.get("no_stop_ret_pct") is not None)
        if stop_n:
            diffs = [float(t["ret_pct"]) - float(t["no_stop_ret_pct"])
                     for t in trades if t.get("no_stop_ret_pct") is not None]
            head += " 止损%d笔净影响%+.2fpp" % (stop_n, sum(diffs) / len(diffs))
        blocks.extend([head] + rows)
    if not blocks:
        # 🔴 全不可推时返回空串：结算卡的价值是「过门策略今天真赚了多少」，
        # 一个过门模式都没有时，「今日无结算」这行字对老大毫无信息量 ——
        # 它跟「系统没跑」「没成交」三种情况长得一模一样。
        return ""
    return "\n".join(["## 💰 六模式结算 %s" % date] + blocks)


def render_picks_only(picks: dict, max_per_mode: int = 3) -> str:
    """极简推荐卡：**只列过门模式**今天买了什么，一行一个模式。

    老大 2026-11-23 原话「通知太多废话，还是只通知打板其他策略完全没有」。
    之前卡片只有打板票 + 一行笔数 + 两行口径 + 账本摘要 —— 其他模式的推荐
    全被压成「P3 2笔」，点开也看不到买了谁。这里只留事实：代码 名称 价 理由。

    不印口径长句、不印账本摘要、不印「炸板-5%是什么」——那些是文档不是推荐。

    11-24 加的「每行带模式名 + 判决词」保留（`P3 超跌反抽 ✅过门 ｜ …`）：
    老大要能一眼看出这是哪个模式。

    🔴🔴 11-25 反转（老大原话「这种通知全都都是失效的完全没必要出现
    还影响视觉 我只需要有用的」，附了一张四个模式全标 `⏳未过门`/`❌已否证` 的截图）：
    11-24 的结论「判决做了就要印出来」只解决了**可读性**，没解决**该不该推**。
    一张卡片上四条推荐、三条已否证，读起来的净效果是「今天有四个模式在选股」——
    与事实（一个过门的都没有）相反。所以这里从「标注状态」升级为**按状态拦截**：
    只有 `passed` 的模式才出现在卡片上；一个都没有时返回空串，调用方静默不推。
    """
    try:
        from paper_strategies import STRATEGIES
    except ImportError:  # pragma: no cover
        return ""
    rows: list[str] = []
    for sid in sorted(STRATEGIES):
        if not is_pushable(sid):
            continue
        rec = picks.get(sid) or {}
        bought = list(rec.get("bought") or [])
        if not bought:
            continue
        spec = STRATEGIES[sid]
        items = []
        for it in bought[:max_per_mode]:
            note = str(it.get("note") or "")
            items.append("%s %s %.2f %s" % (
                it.get("code", ""), str(it.get("name") or "")[:4],
                float(it.get("price") or 0), note))
        more = " …+%d" % (len(bought) - max_per_mode) if len(bought) > max_per_mode else ""
        head = "%s %s %s" % (sid, spec.get("name", ""), tag_of(spec))
        rows.append("%s ｜ %s" % (head, " / ".join(items) + more))
    if not rows:
        return ""
    return "\n".join(rows)
