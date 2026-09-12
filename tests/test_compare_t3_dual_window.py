"""T3 双窗口对比判定逻辑的契约测试。

为什么值得测：这几个判定直接决定「T3 方案 A（htf+mr 换 mom+tr）是否成立」。
一旦把「单窗口结论」当成「双窗口一致」，我们就会拿不可信的方向去改策略因子族 ——
比没有结论更危险。原先这段逻辑埋在 `main()` 里（两个闭包 + 一段 if/elif），
无法被任何测试覆盖，故抽成模块级纯函数 `direction` / `decide_scheme` 并在此锁定。

断言用**emoji 前缀**而非「包含/不包含 `方案 A 成立`」：后者会被
`方案 A 不成立`、`不判方案 A 成立` 这类同串文案误判。
"""
from __future__ import annotations

import pytest

from scripts import compare_t3_dual_window as m


# --------------------------------------------------------------------------
# direction：单窗口 → 方向标签
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("beats_sharpe", "beats_return", "expected"),
    [
        (True, True, "改善"),     # 两项都好
        (True, False, "改善"),    # 任一改善即记改善（Sharpe 更好）
        (False, True, "改善"),    # 任一改善即记改善（收益更好）
        (False, False, "退步"),   # 两项都差
        (True, None, "改善"),
        (None, True, "改善"),
        (None, False, "混合"),    # 一项缺、另一项更差 → 不足以判退步
        (False, None, "混合"),
        (None, None, None),       # 数据缺失
    ],
)
def test_direction_labels(beats_sharpe, beats_return, expected):
    assert m.direction({"beats_sharpe": beats_sharpe, "beats_return": beats_return}) == expected


# --------------------------------------------------------------------------
# decide_scheme：双窗口一致性判据
# --------------------------------------------------------------------------
def test_only_consistent_improvement_establishes_scheme_a():
    assert m.decide_scheme("改善", "改善").startswith("✅")


def test_consistent_regression_rejects_scheme_a():
    assert m.decide_scheme("退步", "退步").startswith("❌")


def test_inconsistent_direction_is_not_judged():
    """方向不一致 ⇒ 不判成立（避免把窗口依赖当成结论）。"""
    out = m.decide_scheme("改善", "退步")
    assert out.startswith("🔶")
    assert "3y=改善" in out and "5y=退步" in out


def test_mixed_is_never_consistent():
    """「混合」既不是一致改善也不是一致退步 ⇒ 一律不判成立。"""
    assert m.decide_scheme("混合", "混合").startswith("🔶")


@pytest.mark.parametrize("pair", [("改善", None), (None, "退步"), (None, None)])
def test_missing_window_is_deferred(pair):
    """任一窗口缺数据 ⇒ 暂不可判，绝不出结论。"""
    assert m.decide_scheme(*pair).startswith("⏳")


# --------------------------------------------------------------------------
# verdict_for_window：单窗口「最佳 htf_mr vs 基线」
# --------------------------------------------------------------------------
def test_missing_baseline_is_reported_not_guessed():
    v = m.verdict_for_window({}, {"WF-H01": {"sharpe": 9.9}})
    assert v["beats_sharpe"] is None and v["beats_return"] is None
    assert v["note"] == "基线缺失"


def test_variant_without_sharpe_is_not_usable():
    v = m.verdict_for_window({"sharpe": 1.0, "total_return_pct": 5.0}, {"WF-H01": {"sharpe": None}})
    assert v["best"] is None
    assert v["note"] == "无 htf_mr 变体"


def test_picks_best_sharpe_and_compares_against_baseline():
    base = {"sharpe": 1.0, "total_return_pct": 20.0}
    cand = {
        "WF-H01": {"sharpe": 0.5, "total_return_pct": 30.0},
        "WF-H02": {"sharpe": 1.5, "total_return_pct": 15.0},
    }
    v = m.verdict_for_window(base, cand)
    assert v["best"] == "WF-H02"          # 按 Sharpe 取最佳，而非收益
    assert v["beats_sharpe"] is True
    assert v["beats_return"] is False     # 15% < 20%
    # 该窗口方向：Sharpe 改善 → 改善
    assert m.direction(v) == "改善"


def test_baseline_metric_missing_yields_none_not_false():
    """基线缺 total_return 时不得把「无从比较」说成「没跑赢」。"""
    v = m.verdict_for_window({"sharpe": 1.0}, {"WF-H01": {"sharpe": 0.9, "total_return_pct": 50.0}})
    assert v["beats_sharpe"] is False
    assert v["beats_return"] is None
    assert m.direction(v) == "混合"
