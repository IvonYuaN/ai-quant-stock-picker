"""「涨停池 → 策略信号」转换（`research/paper_strategies.py::from_board_hits`）契约测试。

为什么值得单独测：这一步决定「哪些触板票真的记进账本」，而它有**三个容易搞错的判断**：

1. **A 格（一字封死）必须丢弃** —— 排不进去，假装买到就是骗自己。
   实测 2026-11-20 的 `000838` 就是 A 格（一字 2 板），它溢价最高（回测 +3.89%），
   但恰恰是买不到的那批。把它记成成交，账本会凭空变好。
2. **板数必须精确匹配** —— P1 只要 2 板、P2 只要 1 板。差一板买的就是另一个策略。
3. **`sealed`（检测时已封死）只标记、不删、更不能拦买入** ——
   回测 B 格的定义是「open 没顶死 + 摸板 + **收停板**」，「收停板」在盘中就表现为
   `sealed=True`。也就是说回测唯一过门的那批（+1.37%/t19.2/n23571）**全部是 sealed**。
   所以在入口或 ingest 层把它判死 = 把作业集整个抹掉，P1/P2 永远 0 成交（11-20 实况）。
   正确做法：照买，账本打 `sealed_entry` 标记，统计时单独计数以便拆保守口径。

最后一条（`sealed=False` 排前面）只影响**资金分配顺序**：¥1 万 × 20% 只有 5 个仓位，
离「买到」更近的先占仓，但封死的照样要买。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from paper_strategies import (  # noqa: E402
    STRATEGIES,
    board_signals,
    from_board_hits,
    from_board_hits_top_gap,
)


def _hit(code: str = "600000", board: int = 2, cell: str = "B_放量可成交",
         sealed: bool = False, price: float = 10.0, pre: float | None = None,
         open_: float | None = None, **over) -> dict:
    """构造一条检测器的 hit（字段名与 shortline_signal.detect_second_board 一致）。

    `pre`/`open_`（开盘价）默认由 price 反推成一个 2% 跳空，保证**每只票都有 gap**；
    要测「字段缺失」时显式传 `pre=0` / `open_=0`（或事后 pop 掉键）。
    """
    base = price if isinstance(price, (int, float)) and price else 10.0
    h = {"code": code, "name": "测试票", "price": price, "board": board,
         "cell": cell, "sealed_now": sealed, "seal_tag": "封单10000万",
         "pre": round(base / 1.02, 3) if pre is None else pre,
         "open": round(base / 1.001, 3) if open_ is None else open_,
         "limit_up": price, "tencent_id": "sh" + code}
    h.update(over)
    return h


# ---------------- A 格必须丢弃 ----------------

def test_a_cell_single_word_is_dropped():
    """A 格（开盘一字）排不进 —— 假装买到就是骗自己。

    2026-11-20 的 000838（一字 2 板）就是这类：它溢价最高，但恰恰买不到。
    """
    out = from_board_hits([_hit("000838", board=2, cell="A_一字排不进", sealed=True)], 2)

    assert out["signals"] == []
    assert out["sealed_codes"] == []        # A 格连上界标记都不进（cell 已排除）


def test_only_b_cell_survives():
    """混合输入里只有 B 格留下。"""
    hits = [
        _hit("600001", board=2, cell="A_一字排不进"),
        _hit("600002", board=2, cell="B_放量可成交"),
        _hit("600003", board=2, cell="C_炸板"),
        _hit("600004", board=2, cell="D_高开未封"),
    ]

    out = from_board_hits(hits, 2)

    assert [s["code"] for s in out["signals"]] == ["600002"]


# ---------------- 板数必须精确匹配 ----------------

def test_board_must_match_exactly():
    """P1 只要 2 板、P2 只要 1 板；差一板就是另一个策略。"""
    hits = [_hit("600001", board=1), _hit("600002", board=2),
            _hit("600003", board=3), _hit("600004", board=4)]

    assert [s["code"] for s in from_board_hits(hits, 2)["signals"]] == ["600002"]
    assert [s["code"] for s in from_board_hits(hits, 1)["signals"]] == ["600001"]


def test_missing_board_field_is_treated_as_zero():
    """缺 board 字段的 hit 不能当成任意板数（否则会混进 P1/P2 任一边）。"""
    h = _hit("600001")
    h.pop("board")

    assert from_board_hits([h], 2)["signals"] == []
    assert from_board_hits([h], 1)["signals"] == []


# ---------------- sealed 分层 ----------------

def test_b_cell_sealed_still_produces_signal():
    """B 格「检测时已封死」照样产出信号 —— 这是回测唯一过门的作业集。

    🔴 回测 B 格 =「open 没顶死 + 摸板 + **收停板**」，收停板在盘中即 sealed=True。
    在入口把它删掉 = 把 +1.37%/t19.2/n23571 那批全杀光，P1/P2 永远 0 成交。
    sealed 只作分组标记（下游 ingest 照买，账本记 sealed_entry）。
    """
    out = from_board_hits([_hit("002086", board=2, sealed=True)], 2)

    assert [s["code"] for s in out["signals"]] == ["002086"]
    assert out["signals"][0]["sealed"] is True
    assert out["sealed_codes"] == ["002086"]


def test_not_sealed_is_not_in_sealed_list():
    out = from_board_hits([_hit("600001", board=2, sealed=False)], 2)

    assert out["signals"][0]["sealed"] is False
    assert out["sealed_codes"] == []


def test_sealed_sorts_after_buyable():
    """能买到的排前面 —— ¥1 万只有 5 个仓位，得先让可成交的占仓。"""
    hits = [
        _hit("600001", board=2, sealed=True, price=30.0),
        _hit("600002", board=2, sealed=False, price=5.0),
        _hit("600003", board=2, sealed=True, price=50.0),
        _hit("600004", board=2, sealed=False, price=20.0),
    ]

    codes = [s["code"] for s in from_board_hits(hits, 2)["signals"]]

    assert codes[:2] == ["600004", "600002"]        # 可成交的两个在前，高价优先
    assert set(codes[2:]) == {"600001", "600003"}


# ---------------- 容错 ----------------

def test_empty_and_none_input():
    assert from_board_hits([], 2) == {"signals": [], "sealed_codes": []}
    assert from_board_hits(None, 2) == {"signals": [], "sealed_codes": []}


def test_price_falls_back_to_zero_not_crash():
    """price 缺失/非法时给 0（下游 ingest 会跳过），不要让整轮检测崩掉。"""
    out = from_board_hits([_hit("600001", board=2, price=None)], 2)

    assert out["signals"][0]["price"] == 0.0


def test_note_carries_board_and_seal_tag():
    """note 里带板数与封单简签，报告/推送直接引用，不二次格式化。"""
    out = from_board_hits([_hit("600001", board=2, seal_tag="封单12345万")], 2)

    assert out["signals"][0]["note"].startswith("2板·")
    assert "封单12345万" in out["signals"][0]["note"]


# ---------------- 真实快照回归（2026-11-20 盘中） ----------------

def test_real_snapshot_20261120():
    """当天真实的 7 只触板里，P1（2 板）应只留下 1 只。

    真实分布：4 板 1 只（002771）、2 板 2 只（002579 B 格 / 000838 A 格一字）、
    1 板 4 只。P1 要 2 板且必须 B 格 ⇒ 只剩 002579。
    """
    hits = [
        _hit("002771", board=4, cell="B_放量可成交", sealed=True, price=15.82),
        _hit("002579", board=2, cell="B_放量可成交", sealed=True, price=13.39),
        _hit("000838", board=2, cell="A_一字排不进", sealed=True, price=1.32),
        _hit("002741", board=1, cell="B_放量可成交", sealed=True, price=18.76),
        _hit("002749", board=1, cell="B_放量可成交", sealed=True, price=8.69),
        _hit("002642", board=1, cell="B_放量可成交", sealed=True, price=7.26),
        _hit("002086", board=1, cell="B_放量可成交", sealed=True, price=3.75),
    ]

    p1 = from_board_hits(hits, 2)
    p2 = from_board_hits(hits, 1)

    assert [s["code"] for s in p1["signals"]] == ["002579"]
    assert "000838" not in [s["code"] for s in p1["signals"]]   # 一字排不进
    assert len(p2["signals"]) == 4
    assert [s["code"] for s in p2["signals"]] == ["002741", "002749", "002642", "002086"]


# ---------------- P6 二板·跳空top2 ----------------
#
# P6 与 P1 的唯一差别是「选哪两只」：成交格、板数、成本、退出、仓位全部共用。
# 所以这一组的每个用例都在验「排序 + 截断」这一个动作，而不是重测一遍口径。
#
# 为什么值得单独测：P6 存在的理由是**判读效率**（二板全取要 185 个交易日才 t≥2，
# 跳空 top2 只要 15 个）。一旦排序写错（比如把 gap 写成升序、或 tie-break 用输入顺序），
# 它就从「top2」悄悄变成「top-1 / 随机 2 只」，而账本上完全看不出来 ——
# 只会表现为「这个策略怎么不灵」，然后把一个其实没错的假设判死。

def test_p6_takes_top2_by_gap_desc():
    """4 只 B 格二板 ⇒ 只买跳空最大的 2 只，且按跳空从大到小。"""
    hits = [
        _hit("600001", pre=10.0, open_=10.2),      # +2%
        _hit("600002", pre=10.0, open_=10.5),      # +5%
        _hit("600003", pre=10.0, open_=10.1),      # +1%
        _hit("600004", pre=10.0, open_=10.9),      # +9%
    ]

    out = from_board_hits_top_gap(hits, board=2, top_n=2)

    assert [s["code"] for s in out["signals"]] == ["600004", "600002"]


def test_p6_gap_ordering_overrides_sealed_and_price():
    """跳空排在 `sealed` / 价格之前 —— P6 的排序键只有 gap。

    若沿用 P1 的 (sealed, -price) 排序，封死的、高价的会先占仓，
    P6 就退化成「P1 随便取两只」，跳空这个变量根本没进去。
    """
    hits = [
        _hit("600001", sealed=True, pre=10.0, open_=11.0),    # +10%，检测时已封死
        _hit("600002", sealed=False, pre=10.0, open_=10.3),   # +3%
        _hit("600003", sealed=False, pre=10.0, open_=10.8),   # +8%
    ]

    out = from_board_hits_top_gap(hits, board=2, top_n=2)

    assert [s["code"] for s in out["signals"]] == ["600001", "600003"]
    assert out["signals"][0]["sealed"] is True      # 封死照进，只是排位由 gap 决定


def test_p6_tie_break_is_deterministic():
    """跳空幅度相同 ⇒ 按代码排，不看输入顺序。

    同一天多只票都是一字涨停价开盘（跳空幅度完全相等）是常事。
    若按输入顺序取前 N 只，回放就不可复现 —— 输入顺序取决于行情接口返回顺序，
    而账本一旦记进去就改不了。
    """
    hits = [_hit(c, pre=10.0, open_=10.5) for c in ("600003", "600001", "600002")]

    a = [s["code"] for s in from_board_hits_top_gap(hits, board=2, top_n=2)["signals"]]
    b = [s["code"] for s in from_board_hits_top_gap(list(reversed(hits)), board=2, top_n=2)["signals"]]

    assert a == b == ["600001", "600002"]


def test_p6_missing_gap_fields_rank_last_not_first():
    """缺 `pre`/`open` ⇒ gap 记 0，排到最后；绝不能当成「跳空无穷大」挤进 top2。"""
    holes = _hit("600009", pre=0, open_=0)                 # 字段在但值非法
    missing = _hit("600008")
    missing.pop("pre")
    missing.pop("open")                                    # 字段直接不存在
    hits = [holes, missing,
            _hit("600001", pre=10.0, open_=10.05),         # +0.5%
            _hit("600002", pre=10.0, open_=10.02)]         # +0.2%

    out = from_board_hits_top_gap(hits, board=2, top_n=2)

    assert [s["code"] for s in out["signals"]] == ["600001", "600002"]


def test_p6_still_drops_a_cell_and_wrong_board():
    """P6 复用同一套成交口径：A 格一字、非二板一律不进候选。

    这两条如果漏了，P6 会开始买「一字板」（根本排不进）和「首板」，
    那就不是一个策略了 —— 而它和 P1 的对照关系也就断了。
    """
    hits = [
        _hit("600001", cell="A_一字排不进", pre=10.0, open_=11.0),   # 跳空最大但排不进
        _hit("600002", board=1, pre=10.0, open_=10.9),               # 首板
        _hit("600003", board=3, pre=10.0, open_=10.8),               # 三板
        _hit("600004", pre=10.0, open_=10.1),                        # 唯一合格
    ]

    out = from_board_hits_top_gap(hits, board=2, top_n=2)

    assert [s["code"] for s in out["signals"]] == ["600004"]


def test_p6_sealed_codes_only_counts_picked():
    """`sealed_codes` 只统计**真的进了 top N** 的那几只。

    统计口径要跟作业集一致：一只封死票没被选中，就不该出现在 P6 的 sealed 计数里，
    否则「封死占比」这个分母是虚的。
    """
    hits = [
        _hit("600001", sealed=False, pre=10.0, open_=10.9),   # +9% 选中
        _hit("600002", sealed=True, pre=10.0, open_=10.5),    # +5% 选中且封死
        _hit("600003", sealed=True, pre=10.0, open_=10.1),    # +1% 落选且封死
    ]

    out = from_board_hits_top_gap(hits, board=2, top_n=2)

    assert [s["code"] for s in out["signals"]] == ["600001", "600002"]
    assert out["sealed_codes"] == ["600002"]                  # 600003 不该出现


def test_p6_note_carries_gap():
    """note 里带跳空幅度：报告/推送直接引用，事后能一眼看出为什么选了它。"""
    out = from_board_hits_top_gap([_hit("600001", pre=10.0, open_=10.72)], top_n=1)

    assert "跳空+7.2%" in out["signals"][0]["note"]


def test_p6_empty_and_none_input():
    assert from_board_hits_top_gap([], board=2, top_n=2) == {"signals": [], "sealed_codes": []}
    assert from_board_hits_top_gap(None, board=2, top_n=2) == {"signals": [], "sealed_codes": []}


# ---------------- 注册表：口径由数据声明，不由代码里的 if 决定 ----------------

def test_board_signals_dispatch_comes_from_registry():
    """P1=二板全取、P2=首板全取、P6=二板跳空 top2 —— 全部由 STRATEGIES 声明。

    以前这条分发写在 `paper_scan` 里（`2 if sid == "P1" else 1`），
    加一个 sleeve 就得改 scan，很容易漏；现在只认注册表字段。
    """
    hits = [
        _hit("600001", board=2, pre=10.0, open_=10.9),   # 二板 +9%
        _hit("600002", board=2, pre=10.0, open_=10.3),   # 二板 +3%
        _hit("600003", board=1, pre=10.0, open_=10.5),   # 首板
        _hit("600004", board=2, pre=10.0, open_=10.1),   # 二板 +1%
    ]

    p1 = board_signals(STRATEGIES["P1"], hits)
    p2 = board_signals(STRATEGIES["P2"], hits)
    p6 = board_signals(STRATEGIES["P6"], hits)

    assert [s["code"] for s in p1["signals"]] == ["600001", "600002", "600004"]   # 全取二板
    assert [s["code"] for s in p2["signals"]] == ["600003"]                      # 只取首板
    assert [s["code"] for s in p6["signals"]] == ["600001", "600002"]            # 跳空 top2


def test_board_signals_rejects_spec_without_board():
    """没声明板数 ⇒ 报错，不许静默返回空。

    「静默空」会被上游记成「今天没有信号」，而账本分不清「真的没有」
    和「口径没配上」，于是一个配错的策略可以安静地空跑几个月。

    🔴 断言必须带**策略名**，不能只 `pytest.raises(KeyError)`：
    删掉这个守卫后，下一行 `spec["board"]` 自己也会抛 KeyError，
    只判异常类型的话用例照样绿 —— 变异验证实测过，这就是「因为错误的原因通过」。
    """
    with pytest.raises(KeyError) as ei:
        board_signals({"name": "忘了写板数的策略", "phase": "board"}, [])

    assert "忘了写板数的策略" in str(ei.value)      # 证明是守卫报的，不是顺手 KeyError


def test_top_n_sleeves_hold_at_least_two_days_of_positions():
    """`max_positions` 必须 ≥ 2×top_n。

    T+1 收盘才结算 ⇒ 昨天买的 top_n 只，今天盘中仍然占着仓位。
    上限若只等于 top_n，新信号每天都会撞「仓位已满」，组合净值永远不动
    （信号统计照走，所以从报表上几乎看不出来 —— 只有「组合收益」那一列是死的）。
    """
    for sid, spec in STRATEGIES.items():
        top_n = spec.get("top_n")
        if not top_n:
            continue
        assert spec["max_positions"] >= 2 * top_n, \
            "%s 的 max_positions=%d 容不下「昨天的 %d 只 + 今天的 %d 只」" % (
                sid, spec["max_positions"], top_n, top_n)


# ---------------- 20cm 不进打板成交集 ----------------
#
# 长线口径决定（2026-11-23）：回测过门的 B 格 +1.37%/t19.2 来自纯主板池
# （shortline_probe.load_main_board 只取 60/00）。创业板 20cm 的涨停幅度、
# 止损幅度、跳空分布都不同，进账本就不是在验同一个假设。

def test_20cm_hits_are_excluded_from_all_board_sleeves():
    """主板和创业板同时触二板，只有主板进 P1/P2/P6 的成交集。"""
    main = _hit(code="002749", board=2, price=9.56, board_lim=0.10)
    growth = _hit(code="300829", board=2, price=31.40, board_lim=0.20)
    hits = [main, growth]
    for sid, got in (("P1", board_signals(STRATEGIES["P1"], hits)),
                     ("P2", board_signals(STRATEGIES["P2"], [dict(growth, board=1)])),
                     ("P6", board_signals(STRATEGIES["P6"], hits))):
        codes = [s["code"] for s in got["signals"]]
        assert "300829" not in codes, "%s 把 20cm 票记进了成交集" % sid
    assert [s["code"] for s in board_signals(STRATEGIES["P1"], hits)["signals"]] == ["002749"]


def test_missing_board_lim_defaults_to_main_board():
    """历史检测快照可能没有 board_lim 字段 —— 缺省按主板 10cm，不能误杀。"""
    h = _hit(code="600000", board=2)
    h.pop("board_lim", None)
    assert [s["code"] for s in board_signals(STRATEGIES["P1"], [h])["signals"]] == ["600000"]
