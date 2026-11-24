"""影子账户「真实敞口构成」的守卫。

🔴 背景：2026-11-24 老大点名「我看到主流还是打板」。

名义上六个 sleeve 里只有三个是打板（P1 二板 / P2 首板 / P6 二板跳空）＝ 50%，
但实测持仓市值是 **27300 / 41172 = 66.3%**。两件事让名义权重彻底失真：

  1. 打板 sleeve 只要有信号就把现金打光（P1 收盘时现金只剩 ¥76），
     而 P3/P4/P5 有合格票的日子远少于每天；
  2. `max_positions` 只管「今天买几个」，**不管总敞口**。

外加 P1 与 P6 同日常常买**完全相同的票**（000530/002199/002749/605577）——
那不是两个独立假设，是一笔押两次。

这些数字只有把持仓摊开才算得出来，而摊开是每天变的。所以必须变成一张
自动印在卡片上的行，而不是每次由人肉复核。

| 自欺方式                                   | 对应测试                                          |
|-------------------------------------------|--------------------------------------------------|
| 用 sleeve 个数代替风险权重                     | test_board_share_uses_held_value_not_sleeve_count |
| 空仓时除零炸掉整张卡片                         | test_empty_book_reports_zero_without_dividing      |
| 老账本（迁移前）没有 cost 字段 ⇒ 整行崩            | test_missing_cost_field_is_treated_as_zero         |
| 重复持仓只报「有」不报「谁跟谁重」                  | test_duplicates_are_attributed_to_sleeves          |
| 把「单 sleeve 多只票」误报成重复                  | test_single_holder_is_not_a_duplicate              |
"""
from __future__ import annotations

import sys
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import paper_ledger as pl  # noqa: E402


def _row(exposure, codes):
    return {"exposure": exposure, "codes": list(codes)}


# 2026-11-24 收盘时的真实账本形状（六个 sleeve，¥60000 名义资金）
BOOK_TODAY = {
    "P1": _row(10000.0, ["000530", "002199", "002749", "605577"]),
    "P2": _row(7300.0, ["601599", "603103"]),
    "P3": _row(6000.0, ["002872"]),
    "P4": _row(0.0, []),
    "P5": _row(7872.0, ["300594"]),
    "P6": _row(10000.0, ["000530", "002199", "002749", "605577"]),
}


def test_board_share_uses_held_value_not_sleeve_count():
    """打板占 66.3%，不是「3/6 = 50%」。

    这条是整组测试的核心：如果哪天有人把分子改回「sleeve 个数 × 名义资金」，
    卡片会印出 50%，而真实风险是 66% —— 一个看起来更让人安心、但错的数字。
    """
    mix = pl.exposure_mix(BOOK_TODAY)
    assert mix["total"] == 41172.0
    assert mix["board"] == 27300.0            # P1 10000 + P2 7300 + P6 10000
    assert mix["board_pct"] == 66.3
    assert mix["board_pct"] != 50.0, "又用 sleeve 个数代替风险权重了"


def test_empty_book_reports_zero_without_dividing():
    """空仓（或全部未成交）时不能除零 —— 否则整张卡片变成「摘要失败」。"""
    mix = pl.exposure_mix({sid: _row(0, []) for sid in BOOK_TODAY})
    assert mix["total"] == 0.0
    assert mix["board_pct"] == 0.0
    assert mix["dups"] == {}
    assert mix["n_names"] == 0


def test_missing_cost_field_is_treated_as_zero():
    """老账本（11-23 迁移前）的持仓没有 cost 字段，不能让摘要整行崩掉。

    打在 `sleeve_exposure` 这一层（`overview()` 真正调的就是它）：
    直接测 `exposure_mix` 测不到这段降级逻辑 —— 第一版就是这么写错的，
    把 `p.get("cost")` 改成 `p["cost"]` 之后测试照样全绿。
    """
    v = pl.sleeve_exposure([{"code": "000530"}, {"code": "601599", "cost": 2000.0}])
    assert v["exposure"] == 2000.0            # 缺失按 0 算，不抛 KeyError
    assert v["positions"] == 2
    assert v["codes"] == ["000530", "601599"]


def test_sleeve_exposure_ignores_unfunded():
    """未成交的持仓不进敞口 —— 否则「想买但没买上」会被算成风险。"""
    held = [{"code": "000530", "cost": 2000.0, "funded": True}]
    assert pl.sleeve_exposure(held)["exposure"] == 2000.0
    # funded 过滤在 overview() 里做，sleeve_exposure 只接收已过滤的列表
    assert pl.sleeve_exposure([])["exposure"] == 0.0


def test_duplicates_are_attributed_to_sleeves():
    """重复持仓必须报「哪只票、哪几个 sleeve 撞了」。"""
    mix = pl.exposure_mix(BOOK_TODAY)
    assert set(mix["dups"]) == {"000530", "002199", "002749", "605577"}
    assert mix["dups"]["000530"] == ["P1", "P6"]
    assert mix["n_names"] == 8                # 12 个持仓格子去重后 8 只票


def test_single_holder_is_not_a_duplicate():
    """同一 sleeve 内部持有多只票不是「重复」，不能刷出一堆噪声。"""
    mix = pl.exposure_mix({"P1": _row(4000.0, ["000530", "002199", "002749", "605577"])})
    assert mix["dups"] == {}
    assert mix["n_names"] == 4


def test_same_code_twice_in_one_sleeve_is_reported_separately():
    """同一 sleeve 里同一只票持两笔：单票风险加倍，但不是「跨 sleeve 撞票」。

    真实账本就长这样（P4 曾同时持有两笔 002685）。如果按原样拼标签，
    卡片会印出 `002685:P2/P4/P4` —— 看的人只会以为程序坏了。
    """
    rows = {"P2": _row(2000.0, ["002685"]), "P4": _row(4000.0, ["002685", "002685"])}
    mix = pl.exposure_mix(rows)
    assert mix["dups"]["002685"] == ["P2", "P4"]
    assert mix["doubled"] == {"002685": ["P4"]}


def test_exposure_line_only_counts_board_sleeves():
    """渲染层：卡片那行必须写 66%（而不是 50%），并把撞票的 sleeve 点出来。"""
    import shortline_cloud as sc

    mix = pl.exposure_mix(BOOK_TODAY)
    line = sc._exposure_line(mix)
    assert "打板占 66%" in line
    assert "重复 4 只" in line
    assert "000530:P1/P6" in line
    # 空仓时整行不出现，避免在无持仓的日子里印一行「敞口 ¥0」
    assert sc._exposure_line({"total": 0, "board_pct": 0, "dups": {}}) == ""
