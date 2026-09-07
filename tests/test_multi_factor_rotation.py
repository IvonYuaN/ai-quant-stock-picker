from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from aqsp.strategies.multi_factor_rotation import MultiFactorRotationStrategy


def test_factor_weight_update_is_proposal_only_when_evidence_passes() -> None:
    strategy = MultiFactorRotationStrategy()
    before = dict(strategy._factor_weights)

    proposal = strategy.update_factor_weights(
        {"rsi_14": 1.0},
        independent_signal_days=30,
        cooldown_active=False,
        walkforward_evidence={"status": "pass"},
    )

    assert proposal is not None
    assert proposal.proposal_only is True
    assert proposal.walkforward_status == "pass"
    assert dict(strategy._factor_weights) == before


def test_factor_weight_update_is_blocked_without_samples_or_gate() -> None:
    strategy = MultiFactorRotationStrategy()

    assert (
        strategy.update_factor_weights(
            {"rsi_14": 1.0},
            independent_signal_days=29,
            cooldown_active=False,
            walkforward_evidence={"status": "pass"},
        )
        is None
    )
    assert (
        strategy.update_factor_weights(
            {"rsi_14": 1.0},
            independent_signal_days=30,
            cooldown_active=False,
            walkforward_evidence=None,
        )
        is None
    )
    assert (
        strategy.update_factor_weights(
            {"rsi_14": 1.0},
            independent_signal_days=30,
            cooldown_active=True,
            walkforward_evidence={"status": "pass"},
        )
        is None
    )


# ---------------------------------------------------------------------------
# beta_60 修复：必须由调用方提供 benchmark_returns；无 benchmark 时跳过该因子
# （旧版假造 market_returns = returns 导致 beta 恒 1、factors["beta_60"] 恒 1）。
# 宪法 §3.7：evaluate 纯函数，无 IO/网络。
# ---------------------------------------------------------------------------


def _make_close_df(n: int, seed: int = 0) -> pd.DataFrame:
    """构造 n 行 close 数据。"""
    rng = np.random.default_rng(seed)
    base = 10.0 + np.cumsum(rng.normal(0, 0.01, n))
    return pd.DataFrame(
        {
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": base,
            "high": base * 1.005,
            "low": base * 0.995,
            "close": base,
            "volume": np.full(n, 1000.0),
        }
    )


def test_volatility_factors_omits_beta_60_without_benchmark() -> None:
    """无 benchmark_returns → 不返回 beta_60（绝不假造 market_returns）。"""
    strategy = MultiFactorRotationStrategy()
    df = _make_close_df(120, seed=1)
    factors = strategy.factor_calculator.calculate_volatility_factors(df)
    assert "beta_60" not in factors
    # 其他波动率因子应仍在
    assert "atr_14" in factors


def test_volatility_factors_beta_60_uses_external_benchmark() -> None:
    """有 benchmark_returns → beta_60 必须依赖外部市场收益率（非恒 1）。"""
    strategy = MultiFactorRotationStrategy()
    df = _make_close_df(120, seed=2)
    # 以个股收益为基准骨架构造市场序列：market = 1.2*stock + 微噪（β≈1.2），
    # market2 = 0.5*stock + 微噪（β≈0.5）——确定的 β 响应，避免独立随机序列
    # 因 cov 符号随机被 clip(1-|β-1|) 双双压到 0.0 而失去区分度。
    close = df["close"].values
    stock_ret = np.diff(close[-61:]) / close[-61:-1]
    noise1 = np.random.default_rng(99).normal(0, 1e-4, 60)
    noise2 = np.random.default_rng(7).normal(0, 1e-4, 60)
    market = stock_ret * 1.2 + noise1
    market2 = stock_ret * 0.5 + noise2
    factors = strategy.factor_calculator.calculate_volatility_factors(
        df, benchmark_returns=market
    )
    assert "beta_60" in factors
    assert 0.0 <= factors["beta_60"] <= 1.0
    factors2 = strategy.factor_calculator.calculate_volatility_factors(
        df, benchmark_returns=market2
    )
    assert factors["beta_60"] != factors2["beta_60"]


def test_volatility_factors_beta_60_skipped_when_benchmark_too_short() -> None:
    """benchmark 长度不足 60 → 跳过 beta_60。"""
    strategy = MultiFactorRotationStrategy()
    df = _make_close_df(120, seed=3)
    short_market = np.zeros(30)  # 长度 < 60
    factors = strategy.factor_calculator.calculate_volatility_factors(
        df, benchmark_returns=short_market
    )
    assert "beta_60" not in factors


def test_volatility_factors_beta_60_skipped_when_benchmark_zero_variance() -> None:
    """benchmark 零方差（常平）→ 跳过 beta_60，避免除零。"""
    strategy = MultiFactorRotationStrategy()
    df = _make_close_df(120, seed=4)
    flat_market = np.zeros(120)  # var = 0
    factors = strategy.factor_calculator.calculate_volatility_factors(
        df, benchmark_returns=flat_market
    )
    assert "beta_60" not in factors


def test_calculate_score_accepts_benchmark_passthrough() -> None:
    """calculate_score 必须接受 benchmark_returns 参数并透传。"""
    strategy = MultiFactorRotationStrategy()
    df = _make_close_df(120, seed=5)
    market = np.random.default_rng(11).normal(0, 0.01, 120)
    scores = strategy.calculate_score(
        {"000001.SZ": df}, regime="trend", benchmark_returns=market
    )
    assert "000001.SZ" in scores
    assert isinstance(scores["000001.SZ"], float)


def test_liquidity_factors_amihud_computes_without_shape_error() -> None:
    """回归：np.diff(close[-20:]) / close[-21:-1] 形状不匹配(19 vs 20)曾使
    amihud 分支一进 len>=20 必炸 ValueError；修复后应正常产出因子。"""
    strategy = MultiFactorRotationStrategy()
    df = _make_close_df(120, seed=3)
    factors = strategy.factor_calculator.calculate_liquidity_factors(df)
    assert "amihud_illiquidity" in factors
    assert 0.0 <= factors["amihud_illiquidity"] <= 1.0
