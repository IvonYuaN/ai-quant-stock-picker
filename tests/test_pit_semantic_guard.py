"""前视语义守卫（行为级截断探测）测试。

🔴 核心契约（§24 病根复现）：
- **全期池化**行业内分位（复刻 ``industry_neutral_ic._within_rank`` 的 ravel+只按行业码
  漏日期维）⇒ 必被 ``check_pit_safe`` 判**前视**（pit_safe=False）。
- **逐日横截面**行业内分位（生产可实现、无前视）⇒ 必判 **PIT 安全**（pit_safe=True）。
- 恒等变换 / 逐日全市场分位 ⇒ PIT 安全。
- 全期均值 / 全期分位数 ⇒ 前视。
"""

from __future__ import annotations

import numpy as np

from aqsp.research.pit_semantic_guard import (
    PitAuditReport,
    check_pit_safe,
    daily_within_rank,
    pooled_within_rank,
)


def _panel(T: int = 40, N: int = 30, seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    values = rng.uniform(0.5, 5.0, size=(T, N))
    # 行业码：每票一个固定行业（真实场景里行业随时间变迁，这里固定更贴近最严情形）
    ind_per_stock = rng.integers(0, 5, size=N)
    aux = np.repeat(ind_per_stock[None, :], T, axis=0).astype(float)
    return values, aux


def test_pooled_within_rank_is_lookahead():
    """🔴 §24 病根：全期池化行业内分位用了未来数据 ⇒ 必须判前视。"""
    values, aux = _panel()
    rep = check_pit_safe(pooled_within_rank, values, aux)
    assert isinstance(rep, PitAuditReport)
    assert rep.pit_safe is False, "全期池化分位含未来，必须被抓出前视"
    assert rep.violated_at, "前视变换必须记录出问题的时点"
    assert "前视" in rep.summary()


def test_daily_within_rank_is_pit_safe():
    """逐日横截面行业内分位（生产可实现）⇒ 必判 PIT 安全。"""
    values, aux = _panel()
    rep = check_pit_safe(daily_within_rank, values, aux)
    assert rep.pit_safe is True, f"逐日横截面无前视，却报前视：{rep.summary()}"
    assert rep.violated_at == []


def test_identity_transform_is_pit_safe():
    """恒等变换（out = in）⇒ 显然 PIT 安全。"""
    values, aux = _panel()

    def identity(v, a):
        return np.array(v, dtype=float)

    rep = check_pit_safe(identity, values, aux)
    assert rep.pit_safe is True


def test_daily_whole_market_rank_is_pit_safe():
    """逐日全市场横截面分位（按当天全 N 排名，不用未来行）⇒ PIT 安全。"""
    values, aux = _panel()

    def daily_market_rank(v, a):
        T, N = v.shape
        out = np.full((T, N), np.nan)
        for t in range(T):
            row = v[t]
            ok = np.isfinite(row)
            if ok.sum() < 2:
                continue
            order = np.argsort(row[ok], kind="stable")
            rank = np.zeros_like(order, dtype=float)
            rank[order] = np.arange(1, ok.sum() + 1) / ok.sum()
            out[t, np.flatnonzero(ok)] = rank
        return out

    rep = check_pit_safe(daily_market_rank, values, aux)
    assert rep.pit_safe is True


def test_full_period_mean_is_lookahead():
    """全期均值（out 每行 = 整段 T×N 的均值）⇒ 用了未来 ⇒ 前视。"""
    values, aux = _panel()

    def full_mean(v, a):
        m = float(np.nanmean(v))
        return np.full(v.shape, m, dtype=float)

    rep = check_pit_safe(full_mean, values, aux)
    assert rep.pit_safe is False, "全期均值含未来，必须判前视"


def test_rolling_causal_window_is_pit_safe():
    """因果滚动（每行只用 0..t，含当前时点）⇒ PIT 安全。

    这是「正确写法」的对照：3 日因果均值在时点 t 只用 t-2..t，屏蔽未来不影响 0..t。
    """
    values, aux = _panel()

    def causal_rolling_mean(v, a):
        T, N = v.shape
        out = np.full((T, N), np.nan)
        for t in range(T):
            lo = max(0, t - 2)
            seg = v[lo : t + 1]
            if np.isfinite(seg).any():
                out[t] = np.nanmean(seg)
        return out

    rep = check_pit_safe(causal_rolling_mean, values, aux)
    assert rep.pit_safe is True, f"因果滚动无前视，却报前视：{rep.summary()}"


def test_centered_rolling_is_lookahead():
    """居中滚动（center=True 等价：用到 t+1）⇒ 前视。"""
    values, aux = _panel()

    def centered_rolling(v, a):
        T, N = v.shape
        out = np.full((T, N), np.nan)
        for t in range(T):
            lo, hi = t - 1, t + 2  # 窗口跨到 t+1/t+2（未来）
            lo = max(0, lo)
            if hi <= T and np.isfinite(v[lo:hi]).any():
                out[t] = np.nanmean(v[lo:hi])
        return out

    rep = check_pit_safe(centered_rolling, values, aux)
    assert rep.pit_safe is False, "居中/跨未来滚动必须判前视"


def test_report_summary_wording():
    """summary() 文案：安全=「PIT 安全」、前视=「前视」。"""
    values, aux = _panel()
    ok = check_pit_safe(lambda v, a: np.array(v, dtype=float), values, aux)
    bad = check_pit_safe(pooled_within_rank, values, aux)
    assert "PIT 安全" in ok.summary()
    assert "前视" in bad.summary()
    assert ok.violated_at == [] and bad.violated_at
