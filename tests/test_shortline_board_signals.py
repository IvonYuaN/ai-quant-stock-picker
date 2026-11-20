"""「涨停池 → 策略信号」转换（`research/paper_strategies.py::from_board_hits`）契约测试。

为什么值得单独测：这一步决定「哪些触板票真的记进账本」，而它有**三个容易搞错的判断**：

1. **A 格（一字封死）必须丢弃** —— 排不进去，假装买到就是骗自己。
   实测 2026-11-20 的 `000838` 就是 A 格（一字 2 板），它溢价最高（回测 +3.89%），
   但恰恰是买不到的那批。把它记成成交，账本会凭空变好。
2. **板数必须精确匹配** —— P1 只要 2 板、P2 只要 1 板。差一板买的就是另一个策略。
3. **`sealed`（触板即封死）要标记但不能删** —— 删了样本只剩个位数看不见东西；
   留着又会让主统计把「买不到的收益」算成自己赚的。正确做法是分层记录。

最后一条（`sealed=False` 排前面）关系到资金分配：¥1 万 × 20% 只有 5 个仓位，
能买到的先占仓，买不到的只记统计。
"""
from __future__ import annotations

import sys
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from paper_strategies import from_board_hits  # noqa: E402


def _hit(code: str = "600000", board: int = 2, cell: str = "B_放量可成交",
         sealed: bool = False, price: float = 10.0, **over) -> dict:
    """构造一条检测器的 hit（字段名与 shortline_signal.detect_second_board 一致）。"""
    h = {"code": code, "name": "测试票", "price": price, "board": board,
         "cell": cell, "sealed_now": sealed, "seal_tag": "封单10000万",
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
    assert out["blocked_sealed"] == []      # A 格连「上界参考」都不进


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

def test_sealed_is_marked_but_kept():
    """触板即封死：照记，但打 sealed 标记 ± 单独列出，供主统计排除。

    🔴 不能在入口就删掉 —— 删了样本只剩个位数，看不出任何东西。
    """
    out = from_board_hits([_hit("002086", board=2, sealed=True)], 2)

    assert [s["code"] for s in out["signals"]] == ["002086"]
    assert out["signals"][0]["sealed"] is True
    assert out["blocked_sealed"] == ["002086"]


def test_not_sealed_is_not_in_blocked_list():
    out = from_board_hits([_hit("600001", board=2, sealed=False)], 2)

    assert out["signals"][0]["sealed"] is False
    assert out["blocked_sealed"] == []


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
    assert from_board_hits([], 2) == {"signals": [], "blocked_sealed": []}
    assert from_board_hits(None, 2) == {"signals": [], "blocked_sealed": []}


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
