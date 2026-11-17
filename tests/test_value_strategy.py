"""value 维度（低估值）打分测试。

覆盖三件事：
1. **截面分位恢复区分度**（原绝对水平 ``1-pe/max_pe`` 几乎所有票挤在 1 附近、
   零区分度 ⇒ 那才是"7 维里有 3 维是常量"的根因）。
2. **PIT 纪律**：估值只取 ``as_of <= 该票决策日`` 的最近一期快照，绝不取未来期。
3. **fail-soft**：估值源缺失/坏数据 ⇒ 中性 0.5，行为与"无数据"时一致（不崩）。
"""

from __future__ import annotations

import pandas as pd

from aqsp.data.valuation_pit import ValuationPoint
from aqsp.strategies import value as value_mod
from aqsp.strategies.value import ValueStrategy
from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.thresholds import load_thresholds


def _frame(code: str, date: str = "2024-01-24") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(date)],
            "close": [10.0],
            "pe": [None],
            "pb": [None],
        }
    )


def _install_source(monkeypatch, points: list[ValuationPoint]) -> None:
    """注入 PIT 估值单例（测试不联网）。"""
    src = value_mod._VALUATION_SOURCE
    src.from_points(points)
    monkeypatch.setattr(value_mod, "_VALUATION_SOURCE", src)


def _strategy() -> ValueStrategy:
    return ValueStrategy(StrategyConfig(name="value", enabled=True), load_thresholds())


def test_cross_section_rank_distinguishes_cheap_vs_expensive():
    """🔴 核心：截面分位必须给出区分度（便宜 ⇒ 分位高）。"""
    out = ValueStrategy._cross_section_rank(
        {"a": 5.0, "b": 20.0, "c": 10.0, "d": 40.0}, higher_is_cheaper=True
    )
    assert out["a"] > out["c"] > out["b"] > out["d"]
    assert out["a"] == max(out.values())
    assert out["d"] == min(out.values())


def test_cross_section_rank_missing_values_are_neutral():
    out = ValueStrategy._cross_section_rank(
        {"a": 5.0, "b": None, "c": 10.0}, higher_is_cheaper=True
    )
    assert out["b"] == 0.5
    assert out["a"] != out["c"]


def test_cross_section_rank_single_valid_sample_is_neutral():
    """单一有效样本 ⇒ 无区分度 ⇒ 中性（不制造假信号）。"""
    out = ValueStrategy._cross_section_rank({"a": 5.0, "b": None}, higher_is_cheaper=True)
    assert out["a"] == 0.5 and out["b"] == 0.5


def test_cross_section_rank_invert_direction():
    out = ValueStrategy._cross_section_rank(
        {"a": 5.0, "b": 20.0}, higher_is_cheaper=False
    )
    assert out["a"] < out["b"]


def test_calculate_score_uses_pit_valuation_and_distinguishes(monkeypatch):
    """端到端：注入 PIT 快照 ⇒ 更便宜的票得分更高（截面有区分度）。"""
    _install_source(
        monkeypatch,
        [
            ValuationPoint("000001", "20240124", pe_ttm=5.0, pb_mrq=0.5),
            ValuationPoint("000002", "20240124", pe_ttm=30.0, pb_mrq=3.0),
            ValuationPoint("000003", "20240124", pe_ttm=12.0, pb_mrq=1.2),
        ],
    )
    data = {
        "000001.SZ": _frame("000001"),
        "000002.SZ": _frame("000002"),
        "000003.SZ": _frame("000003"),
    }
    scores = _strategy().calculate_score(data)
    assert scores["000001.SZ"] > scores["000003.SZ"] > scores["000002.SZ"]
    # 有区分度：最高分与最低分必须拉开（否则等于常量维度）
    assert max(scores.values()) - min(scores.values()) > 0.1


def test_calculate_score_pit_never_uses_future_snapshot(monkeypatch):
    """🔴 PIT 红线：决策日之后的快照绝不能被取到。

    需 ≥2 只有效票（截面分位在单样本时按规则退化为中性 0.5），
    第二只票用作对照以拉开分位。
    """
    _install_source(
        monkeypatch,
        [
            ValuationPoint("000001", "20240110", pe_ttm=5.0, pb_mrq=0.5),
            # 未来期（决策日 2024-01-24 之后才发布）
            ValuationPoint("000001", "20240207", pe_ttm=99.0, pb_mrq=9.0),
            ValuationPoint("000002", "20240110", pe_ttm=40.0, pb_mrq=4.0),
        ],
    )
    data = {
        "000001.SZ": _frame("000001", "2024-01-24"),
        "000002.SZ": _frame("000002", "2024-01-24"),
    }
    scores = _strategy().calculate_score(data)
    # 取到的是 20240110（便宜 ⇒ 高分），不是 20240207
    # 断言用**相对关系 + 非中性**：最高分因 dividend 分量恒中性 0.5 而非 1.0
    assert scores["000001.SZ"] > 0.7
    assert scores["000001.SZ"] > scores["000002.SZ"]
    assert scores["000001.SZ"] != 0.5  # 非中性 ⇒ 确实取到了估值并参与截面


def test_calculate_score_missing_valuation_is_neutral(monkeypatch):
    """估值源无该票 ⇒ 中性 0.5（fail-soft，不崩、不给假信号）。"""
    _install_source(monkeypatch, [ValuationPoint("999999", "20240124", pe_ttm=5.0)])
    data = {"000001.SZ": _frame("000001")}
    scores = _strategy().calculate_score(data)
    assert scores["000001.SZ"] == 0.5


def test_calculate_score_empty_data_returns_empty(monkeypatch):
    _install_source(monkeypatch, [])
    assert _strategy().calculate_score({}) == {}


def test_calculate_score_skips_empty_frames(monkeypatch):
    _install_source(
        monkeypatch,
        [ValuationPoint("000001", "20240124", pe_ttm=5.0, pb_mrq=0.5)],
    )
    data = {"000001.SZ": pd.DataFrame(), "000002.SZ": _frame("000002")}
    scores = _strategy().calculate_score(data)
    assert scores["000001.SZ"] == 0.5
    assert "000002.SZ" in scores


def test_calculate_score_scores_stay_in_unit_interval(monkeypatch):
    _install_source(
        monkeypatch,
        [
            ValuationPoint("000001", "20240124", pe_ttm=0.01, pb_mrq=0.01),
            ValuationPoint("000002", "20240124", pe_ttm=5000.0, pb_mrq=500.0),
        ],
    )
    data = {"000001.SZ": _frame("000001"), "000002.SZ": _frame("000002")}
    for value in _strategy().calculate_score(data).values():
        assert 0.0 <= value <= 1.0
