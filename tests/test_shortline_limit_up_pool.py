"""「今日涨停池」判定（`research/shortline_signal.py::_em_rows_to_pool`）契约测试。

## 为什么这条路径必须有

打板 seed 池原来只从本地库取「最近 N 个截面涨停并集」。但库经常缺最新一个
交易日（BaoStock 更新连续失败），而「二板接力」恰恰要知道**昨天谁涨停**。
库缺 11-19 那天，11-19 首板的票就全进不了种子 —— 而它们正是今天要二板的票。

实测 2026-11-20：
- 全市场主板涨停 28 只，库种子池只覆盖 2 只；
- 当天真正的主板二板是 `000838` / `002579`，**两只都不在种子里**，漏失 100%。

所以加了「东财涨幅榜」这条不依赖库的路径（1 请求）。

## 最关键的口径

**涨幅 ≥9.8% 不等于涨停**。20cm 的票涨 10% 只是半路。必须按「该票自己的
涨停价」判 —— 否则会把半路的票当涨停买进去，凭空多出一批假信号。
下面的 `test_20cm_stock_up_10pct_is_not_limit_up` 就是锁这一条。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import shortline_signal as ss  # noqa: E402


def _row(code: str, cur: float, pre: float, pct: float = 0.0, name: str = "") -> dict:
    """构造一条东财涨幅榜原始行（只填判定用到的字段）。"""
    return {"f12": code, "f14": name, "f2": cur, "f18": pre, "f3": pct}


@pytest.mark.parametrize("code,expect", [
    ("600000", 0.10), ("000001", 0.10), ("002579", 0.10),
    ("300812", 0.20), ("301313", 0.20), ("688981", 0.20),
])
def test_board_limit_by_prefix(code, expect):
    """涨停幅度按板块：创业板/科创 20%，其余 10%。"""
    assert ss._board_limit_for(code) == expect


def test_main_board_limit_up_is_picked():
    """主板昨收 10.00 → 涨停 11.00，现价 11.00 命中。"""
    out = ss._em_rows_to_pool([_row("600000", 11.0, 10.0, 10.0)])
    assert [w["code"] for w in out] == ["600000"]
    assert out[0]["tencent_id"] == "sh600000"
    assert out[0]["board_lim"] == 0.10


def test_shenzhen_prefix_maps_to_sz():
    """0/3 开头映射到 sz 前缀（腾讯报价的写法）。"""
    out = ss._em_rows_to_pool([_row("002579", 13.39, 12.17, 10.02)])
    assert out[0]["tencent_id"] == "sz002579"


def test_20cm_stock_up_10pct_is_not_limit_up():
    """🔴 最容易错的一条：20cm 的票涨 10% 不是涨停。

    若按「涨幅 ≥9.8%」一刀切，这类半路票会被当成涨停买进去 —— 凭空造假信号。
    """
    # 创业板昨收 10.00 → 涨停 12.00；现价 11.00（+10%）远未到板
    out = ss._em_rows_to_pool([_row("300001", 11.0, 10.0, 10.0)])
    assert out == []


def test_20cm_stock_at_its_own_limit_up_is_picked():
    """创业板涨到自己的 20cm 涨停价才算。"""
    out = ss._em_rows_to_pool([_row("300812", 18.0, 15.0, 20.0)])
    assert [w["code"] for w in out] == ["300812"]
    assert out[0]["board_lim"] == 0.20


@pytest.mark.parametrize("code,cur,pre", [
    ("688981", 120.0, 100.0),   # 科创 20cm，确实封在涨停价上
    ("689009", 120.0, 100.0),
    ("830799", 130.0, 100.0),   # 北交 30cm，也是真涨停
    ("430047", 130.0, 100.0),
    ("920001", 130.0, 100.0),
])
def test_kcb_and_bse_are_excluded(code, cur, pre):
    """科创（688/689）与北交（8xx/4xx/920）不进池 —— 与库种子口径一致。

    ⚠️ 用例必须让它们**真的封在涨停价上**：若用「只涨 11%」当样本，
    这两种板块（20cm/30cm）本来就不会命中，测试会变成恒真的假断言
    —— 变异测试当场抓出过这个坑。
    """
    assert ss._em_rows_to_pool([_row(code, cur, pre, 20.0)]) == []


@pytest.mark.parametrize("bad", [
    {"f12": "600000", "f2": "-", "f18": 10.0},      # 停牌：现价是 "-"
    {"f12": "600000", "f2": 11.0, "f18": None},     # 缺昨收
    {"f12": "600000", "f18": 10.0},                 # 整个 f2 键都没有
    {"f12": "600000", "f2": 11.0},                  # 整个 f18 键都没有
    {"f12": "600000", "f2": 0, "f18": 10.0},        # 现价为 0
    {"f12": "60000", "f2": 11.0, "f18": 10.0},      # 代码短了
    {"f12": "60000A", "f2": 11.0, "f18": 10.0},     # 代码非数字
    {"f2": 11.0, "f18": 10.0},                      # 没代码
])
def test_malformed_rows_are_skipped_not_crash(bad):
    """残缺行必须跳过而不是抛异常（涨幅榜里停牌行、退市行常年出现）。

    🔴 「键不存在」与「值为 None」是两种情形：前者要求 `.get()` 而不是 `[]`。
    变异测试验证过 —— 只测值为 None 的话，`it["f2"]` 的改动不会被抓住。
    """
    assert ss._em_rows_to_pool([bad]) == []


def test_empty_input():
    assert ss._em_rows_to_pool([]) == []
    assert ss._em_rows_to_pool(None) == []


# ---------------- 真实数据回归（2026-11-20 盘中实测快照） ----------------

def test_real_snapshot_20261120():
    """用当天真实的东财返回片段锁口径。

    这段数据里既有 20cm 封住的、也有 20cm 只涨十几%的半路票，
    还有一只主板 ST —— 正好把「按涨停价判」和「按涨幅判」区分开。
    """
    rows = [
        _row("301313", 59.16, 49.30, 20.00),     # 20cm 封住 → 命中
        _row("300812", 18.00, 15.00, 20.00),     # 20cm 封住 → 命中
        _row("301132", 51.07, 42.56, 20.00),     # 20cm 封住 → 命中
        _row("301052", 38.41, 32.88, 16.82),     # 20cm 半路 → 不命中
        _row("300426", 9.19, 7.95, 15.60),       # 20cm 半路 → 不命中
        _row("301225", 56.16, 49.40, 13.68),     # 20cm 半路 → 不命中
        _row("301282", 32.29, 28.51, 13.26),     # 20cm 半路 → 不命中
        _row("301030", 3.21, 2.88, 11.46),       # 20cm 半路 → 不命中
    ]

    got = [w["code"] for w in ss._em_rows_to_pool(rows)]

    assert got == ["301313", "300812", "301132"]
    assert "301052" not in got          # 涨 16.82% 但没到 20cm 板
    assert "301030" not in got          # 涨 11.46% 看着像涨停，其实是 20cm 板块
