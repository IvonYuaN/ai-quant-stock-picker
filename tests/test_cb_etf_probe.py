"""转债 ETF 隔夜探针（`research/cb_etf_probe.py`）的守卫。

ETF 是这条路上**唯一能真的下单**的东西，所以它的口径错不得：
一旦「隔夜」写成了「收→收」，整份结论就从「微观结构」变成「市场涨了」。

| 自欺方式                                   | 对应测试                                    |
|-------------------------------------------|--------------------------------------------|
| 隔夜腿写成 收→收，把日内涨幅混进来            | test_overnight_leg_is_close_to_next_open      |
| 脏数据只挡一个腿，另两个腿照样算              | test_bad_move_drops_all_three_legs            |
| 扣成本时只减一次（把往返当单边）              | test_net_after_cost_is_round_trip             |
| 只有 30 天的年份也报出来充样本                | test_short_year_is_skipped                    |
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    p = ROOT / "research" / "cb_etf_probe.py"
    spec = importlib.util.spec_from_file_location("cb_etf_probe", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["cb_etf_probe"] = m
    spec.loader.exec_module(m)
    return m


ep = _load()


def test_overnight_leg_is_close_to_next_open():
    """隔夜 = 次日开盘 / 当日收盘 − 1，日期标签跟着「次日」走。

    这个测试守的是整份结论的定义。若写成 收→收，2026 的 +0.017% 会变成
    「全段 +0.007%」，看起来差不多但性质完全不同 —— 一个是可赚的隔夜，
    一个是只能拿到收盘的残差。
    """
    rows = [("2026-01-05", 10.0, 10.0), ("2026-01-06", 11.0, 10.5)]
    dates, over, intra, full = ep.split_legs(rows)
    assert dates == ["2026-01-06"]           # 标签是卖出那天
    assert over[0] == pytest.approx(11.0 / 10.0 - 1.0)      # 隔夜：昨收 → 今开
    assert intra[0] == pytest.approx(10.5 / 11.0 - 1.0)     # 日内：今开 → 今收
    assert full[0] == pytest.approx(10.5 / 10.0 - 1.0)      # 全段：昨收 → 今收


def test_bad_move_drops_all_three_legs():
    """一根 >12% 的脏数据必须同时把三个腿置 nan。

    只挡隔夜腿会让「全段」偷偷保留那根 —— 而全段恰恰是用来对账的。
    """
    rows = [("2026-01-05", 10.0, 10.0), ("2026-01-06", 20.0, 20.0)]
    _d, over, intra, full = ep.split_legs(rows)
    assert np.isnan(over[0]) and np.isnan(intra[0]) and np.isnan(full[0])


def test_net_after_cost_is_round_trip():
    """扣的是**往返**佣金：0.02% 档 = 一天减 0.0002，年化再按 240 天折。"""
    rng = np.random.default_rng(2)
    x = rng.normal(0.0006, 0.002, 500)
    net = ep.net_after_cost(x)
    lab = "往返0.02%(万1)"
    assert lab in net
    assert net[lab]["mean_pct"] == pytest.approx((x.mean() - 0.0002) * 100, abs=1e-3)
    assert net[lab]["annual_pct"] == pytest.approx(((1 + x.mean() - 0.0002) ** 240 - 1) * 100,
                                                   abs=0.2)
    # 扣更多成本 ⇒ 年化必须更低（方向不能反）
    assert net["往返0.05%(万2.5)"]["annual_pct"] < net[lab]["annual_pct"]


def test_short_year_is_skipped():
    """样本不足 40 天的年份不进分年表 —— 否则一个 5 天的年份也能报 t=3。"""
    dates = ["2025-12-%02d" % d for d in range(1, 21)] + \
            ["2026-01-%02d" % d for d in range(1, 61)]
    x = np.full(len(dates), 0.001)
    out = ep.by_year(dates, x, min_days=40)
    assert "2025" not in out
    assert "2026" in out


def test_probe_output_is_json_safe():
    """整份结果必须能被 allow_nan=False 序列化 —— NaN 不是合法 JSON。"""
    rng = np.random.default_rng(9)
    n = 120
    cl = 10 * np.cumprod(1 + rng.normal(0.0003, 0.004, n))
    op = cl * (1 + rng.normal(0, 0.002, n))
    rows = [("2026-01-%02d" % (i + 1), float(op[i]), float(cl[i])) for i in range(n)]
    res = ep.probe_etf(rows)
    json.dumps(res, allow_nan=False)
    assert res["n_days"] == n - 1
    assert res["span"].startswith("2026-01-02")
