"""打板候选池「停牌」判定（`research/shortline_signal.py::_alive_mask`）契约测试。

为什么值得单独测：这个判定错了**不会报错**，只会让候选池悄悄变小。
池子小 ⇒ 盘中触板命中少 ⇒ 影子账户攒不到样本，而且从日志上看不出来
（`pool: 65` 看着很正常，实际该是 111）。

实测 2026-11-20：runner 库最新截面（11-18）只有 1385/5567 只票有价，
旧口径「只看最新截面有价」把 46 只前一天还在正常交易的票当成停牌杀掉，
候选池从 111 缩到 65 —— 二板信号漏掉四成，当天 P1 一条信号都没出。

锁三件事：
1. 最新截面缺失、但近 N 日任一天有价 ⇒ **在市**（就是被误杀的那 46 只）；
2. 近 N 日全无价 ⇒ 不在市（真停牌，该排除）；
3. 回归守卫：旧写法在同一个 fixture 上会少认 1/3 —— 防有人改回只看最新截面。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import shortline_signal as ss  # noqa: E402


def _frame(*cols: list[float | None]) -> np.ndarray:
    """按「每列一只票、每行一个截面」拼矩阵，None → NaN。"""
    return np.array([[np.nan if v is None else v for v in c] for c in cols], dtype=float).T


def test_latest_gap_still_alive():
    """最新截面缺失、前一天有价 ⇒ 在市。库里单日数据残缺时这就是主流情形。"""
    cv = _frame(
        [10.0, 11.0, None],      # 第 3 个截面缺失（被误杀的那类）
        [20.0, 21.0, None],
        [None, None, None],      # 三天全无价 = 真停牌
    )

    assert ss._alive_mask(cv, [0, 1, 2]).tolist() == [True, True, False]


def test_all_window_missing_is_dropped():
    """整个窗口都没有价 ⇒ 排除（长期停牌/退市，不该进候选池）。"""
    cv = _frame([None, None, None], [5.0, None, None])

    assert ss._alive_mask(cv, [0, 1, 2]).tolist() == [False, True]


def test_single_day_window_equals_old_rule():
    """窗口只有一天时，与旧写法 `isfinite(cv[-1])` 完全等价。

    `_alive_mask` 是那条例子的推广，不是换了套语义。
    """
    cv = _frame([1.0, 2.0, 3.0], [1.0, None, None], [None, None, None])

    assert ss._alive_mask(cv, [2]).tolist() == np.isfinite(cv[2]).tolist()


def test_regression_guard_old_rule_would_drop_a_third():
    """回归守卫：旧写法只看最新截面，在同一批数据上会误杀 1/3 的票。

    ⚠️ 这条断言本身就是「为什么改」的证据 —— 如果以后有人把 `_alive_mask`
    换回 `np.isfinite(cv[-1])`，前两条会红；如果有人把它改成「永远 True」
    （干脆不排除停牌），第二条会红。
    """
    cv = _frame(
        [1.0, 2.0, 3.0], [1.0, 2.0, 3.0], [1.0, 2.0, 3.0],
        [1.0, 2.0, None], [1.0, 2.0, None], [1.0, 2.0, None],
    )
    recent = [0, 1, 2]

    new = ss._alive_mask(cv, recent)
    old = np.isfinite(cv[recent[-1]])

    assert int(new.sum()) == 6
    assert int(old.sum()) == 3
    assert int((new & ~old).sum()) == 3          # 旧口径误杀的正是这批


def test_shape_and_dtype():
    """返回逐票的布尔掩码（长度 == 票数），不是单值、也不是 float。"""
    cv = _frame([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])

    m = ss._alive_mask(cv, [0, 1, 2])

    assert m.shape == (2,)
    assert m.dtype == np.bool_


@pytest.mark.parametrize("recent", [[0], [1, 2], [0, 1, 2]])
def test_window_subset_never_adds_codes(recent):
    """窗口是小集合时，结果只能是全窗口结果的子集 —— 多看几天只可能多认，不会少认。"""
    cv = _frame([1.0, None, None], [None, 2.0, None], [None, None, 3.0])

    assert set(np.where(ss._alive_mask(cv, recent))[0]) <= set(
        np.where(ss._alive_mask(cv, [0, 1, 2]))[0]
    )
