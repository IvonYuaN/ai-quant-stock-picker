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
    """
    rows: list[str] = []
    for sid in sorted(sleeves):
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
    """
    parts = []
    for sid in sorted(picks):
        rec = picks[sid] or {}
        n = len(rec.get("bought") or [])
        parts.append("%s %d笔" % (sid, n))
    for sid, clock in (pending or []):
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
        return "## 💰 六模式结算 %s\n今日无结算（首笔 T+1 收盘才平仓）" % date
    return "\n".join(["## 💰 六模式结算 %s" % date] + blocks)
