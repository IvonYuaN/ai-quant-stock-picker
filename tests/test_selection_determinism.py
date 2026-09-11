"""选股取序的确定性回归测试。

背景（2026-09-11 定位）：``CompositeStrategy.calculate_score`` 用 ``set`` 聚合
universe（``all_symbols``），而 CPython 字符串 ``set`` 的迭代顺序受
``PYTHONHASHSEED`` 影响；``BaseStrategy.rank`` 原先是 ``sorted(keys, key=score)``
的**稳定排序** → 并列分的先后顺序继承 set 顺序 → ``select_stocks`` 末尾的
``[:n]`` 截断会在并列边界上「换人」。

后果是同一份数据、同一套参数，跨进程跑出**不同的选股**与不同的回测收益
（实测 3 年 −13.90% vs −15.30%、5 年 44.90% vs 47.65%，分歧只落在个别期）。
因子打分会大量落在边界值上（momentum 的 RSI 越界直接返回 0.0/1.0、triple_rise
是离散档位加权），并列并非小概率事件。

本文件锁定的契约：**并列分一律以 symbol 升序做 tie-break**，取序不得依赖
dict / set 的插入或迭代顺序。
"""

from __future__ import annotations

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.composite import CompositeStrategy


class _RankProbe(BaseStrategy):
    """仅用于测试 rank 的最小可实例化子类。"""

    def calculate_score(self, data):
        return {}


def _rank_probe() -> _RankProbe:
    return _RankProbe(
        StrategyConfig(name="rank_probe"),
        id="rank_probe",
        version="1.0",
        hypothesis="rank 确定性 tie-break 测试",
    )


def test_rank_ties_break_by_symbol_ascending():
    """并列分必须按 symbol 升序取序，而非依赖 dict/set 插入顺序。"""
    probe = _rank_probe()
    scores = {sym: 0.7 for sym in set(["B", "A", "D", "C"])}

    assert probe.rank(scores) == ["A", "B", "C", "D"]


def test_rank_ties_ignore_insertion_order():
    """同样内容、不同插入顺序的 scores 必须给出同一个排序结果。"""
    probe = _rank_probe()
    symbols = [f"sh60{i:04d}" for i in range(20)]

    forward = {sym: 0.9 for sym in symbols}
    backward = {sym: 0.9 for sym in reversed(symbols)}
    from_set = {sym: 0.9 for sym in set(symbols)}

    assert probe.rank(forward) == probe.rank(backward) == probe.rank(from_set)
    assert probe.rank(forward) == sorted(symbols)


def test_rank_ascending_ties_break_by_symbol_ascending():
    """升序方向下并列同样按 symbol 升序，不随排序方向翻转。"""
    probe = _rank_probe()
    scores = {sym: 0.3 for sym in ["C", "A", "B"]}

    assert probe.rank(scores, ascending=True) == ["A", "B", "C"]


def test_rank_score_takes_priority_over_symbol():
    """主序仍是分值；symbol 只在并列时生效。"""
    probe = _rank_probe()

    assert probe.rank({"B": 0.2, "A": 0.9, "C": 0.2}) == ["A", "B", "C"]


def test_composite_select_stocks_tie_break_is_deterministic(monkeypatch):
    """回归：CompositeStrategy.calculate_score 内部用 set 聚合 universe，
    旧实现下 top-n 的取人会随 set 迭代顺序（受 PYTHONHASHSEED 影响）变化，
    导致同配置跨进程选出不同票 —— gate 的全部比较被污染。
    """
    strategy = CompositeStrategy(StrategyConfig(name="composite"))
    symbols = [f"sh60{i:04d}" for i in range(30)]

    # 直接用 set 派生 scores，精确复刻 calculate_score 里 all_symbols 的行为。
    monkeypatch.setattr(
        strategy,
        "calculate_score",
        lambda data, regime="unknown": {sym: 0.9 for sym in set(symbols)},
    )

    data = {sym: pd.DataFrame() for sym in symbols}
    selected = strategy.select_stocks(data, n=10)

    assert selected == symbols[:10]
