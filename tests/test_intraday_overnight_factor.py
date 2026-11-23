"""日内/隔夜拆分因子与筛选器的守卫。

🔴 这组测试的重点不是「函数能跑」，而是**前视**：
拆收益这件事本身就是在价格上做文章，最容易在 rolling / shift 上错一格。
所以第一条测试用「扰动未来行、断言历史行不变」把前视钉死。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    p = ROOT / "research" / "intraday_overnight_factor.py"
    spec = importlib.util.spec_from_file_location("iof", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["iof"] = m
    spec.loader.exec_module(m)
    return m


iof = _load()


def _panel(T=120, N=40, seed=0):
    rng = np.random.default_rng(seed)
    cq = 10.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, size=(T, N)), axis=0))
    op = cq * np.exp(rng.normal(0, 0.01, size=(T, N)))
    return op, cq


def test_split_returns_recovers_intraday_and_overnight_exactly():
    """两段之积必须还原收盘到收盘的总收益（恒等式，不能被任何近似破坏）。"""
    op, cq = _panel()
    r_intra, r_over = iof.split_returns(op, cq)
    with np.errstate(invalid="ignore", divide="ignore"):
        total = np.log(cq[1:] / cq[:-1])
    got = r_intra[1:] + r_over[1:]
    ok = np.isfinite(total) & np.isfinite(got)
    assert ok.sum() > 1000
    assert np.allclose(got[ok], total[ok], atol=1e-10)


def test_impossible_move_is_nan_not_counted_as_loss():
    """主板 ±10% 制度下，单日 −50% 只可能是除权/脏数据 ⇒ 必须是 nan。

    这条守的是「未复权」这个已知数据约定（库里 `*_qfq` 与原始列逐行相同）。
    如果不挡，一次 10 送 10 会被当成 −50% 的真实亏损，污染因子与截面均值。
    """
    op = np.array([[10.0, 10.0], [10.0, 10.0], [5.0, 10.0]])
    cq = np.array([[10.0, 10.0], [10.0, 10.0], [5.0, 10.0]])
    r_intra, r_over = iof.split_returns(op, cq)
    # 第 0 列：第 2 行 open=5、close=5 ⇒ 日内 0%，但隔夜 log(5/10) = −69% 仍应被挡
    assert np.isnan(r_over[2, 0]), "隔夜 −50% 应被挡掉"
    # 第 1 列全程平价 ⇒ 必须保留，不能误杀正常的 0% 行
    assert np.isfinite(r_intra[2, 1]) and np.isfinite(r_over[2, 1])


def test_factor_uses_only_past_data():
    """把未来行改掉，历史行的因子值必须一字不变（前视守卫）。

    🔴 变异方式守前视，而不是只断言「跑得通」：只要 rolling 窗口多吃了
    一行未来数据，下面第 t 行就会变红。
    """
    op, cq = _panel()
    base = iof.build_factors(op, cq, 20)
    t = 80
    op2, cq2 = op.copy(), cq.copy()
    op2[t + 1:] *= 1.5
    cq2[t + 1:] *= 0.7
    pert = iof.build_factors(op2, cq2, 20)
    for key in base:
        np.testing.assert_allclose(
            base[key][: t + 1], pert[key][: t + 1], rtol=0, atol=0,
            err_msg="%s 在第 <=%d 行用到了未来数据" % (key, t))
        assert not np.allclose(base[key][t + 1:], pert[key][t + 1:]), \
            "%s 的未来行竟然没被扰动影响，说明扰动没生效、测试本身恒真" % key


def test_screen_mask_drops_bottom_overnight_and_top_intraday():
    """筛选器的语义：留隔夜累计靠前、日内累计靠后的，各切 cut 分位。"""
    n = 100
    cum_over = np.arange(n, dtype=float)          # 0..99，越大越好
    cum_intra = np.arange(n, dtype=float)         # 0..99，越大越该剔
    valid = np.ones(n, dtype=bool)
    keep = iof.screen_mask(cum_intra, cum_over, 0.10, valid)
    assert keep.sum() == 80, "cut=10%% 两条腿各剔 10 个、互不重叠 ⇒ 应留 80"
    assert not keep[0], "隔夜累计最差的一档必须被剔"
    assert not keep[n - 1], "日内累计最强的一档必须被剔"
    assert keep[10] and keep[n - 11], "边界内侧应保留"


def test_screen_mask_cut_zero_keeps_everything():
    n = 50
    keep = iof.screen_mask(np.arange(n, dtype=float), np.arange(n, dtype=float),
                          0.0, np.ones(n, dtype=bool))
    assert keep.all()


def test_screen_mask_respects_valid_and_small_universe():
    """无效标的不能被「保留」，样本太少也不能硬筛（否则等于随机抽几只）。"""
    n = 30
    valid = np.ones(n, dtype=bool)
    valid[:10] = False
    keep = iof.screen_mask(np.arange(n, dtype=float), np.arange(n, dtype=float),
                          0.10, valid)
    assert not keep[:10].any(), "无效标的不能出现在结果里"
    tiny = np.zeros(n, dtype=bool)
    tiny[:5] = True
    assert not iof.screen_mask(np.arange(n, dtype=float), np.arange(n, dtype=float),
                               0.10, tiny).any(), "有效样本 <20 应返回全 False 而非随机留几只"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
