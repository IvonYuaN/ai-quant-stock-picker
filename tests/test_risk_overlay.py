"""组合风险叠加层（portfolio.risk_overlay）测试。

红线：纯函数只读 / 确定性（重跑逐位一致）/ 权重和 ≤ (1-min_cash) 且非负 /
行业·市值分层上限被尊重 / 低波加分·换手惩罚方向正确 / 缺字段 fail-soft 走中性 /
不塞下单逻辑（输出只有权重+暴露+标注，无买卖指令）。全部合成数据、不碰库、不联网。
"""

from __future__ import annotations

from aqsp.portfolio.risk_overlay import (
    HoldingInput,
    RiskOverlayConfig,
    apply_risk_overlay,
    assign_cap_tier,
    holdings_from_screener,
    summarize_risk_overlay,
)


def _mk(total=0.7, **kw) -> HoldingInput:
    return HoldingInput(symbol=kw.pop("symbol"), total_score=total, **kw)


# ── 市值分层 ─────────────────────────────────────────────────────────


def test_cap_tier_assignment():
    assert assign_cap_tier(150e8) == "large"
    assert assign_cap_tier(100e8) == "large"
    assert assign_cap_tier(20e8) == "mid"
    assert assign_cap_tier(99.9e8) == "mid"
    assert assign_cap_tier(5e8) == "small"
    assert assign_cap_tier(None) == "unknown"
    assert assign_cap_tier(0) == "unknown"


# ── 权重基本性质：非负、和 ≤ (1-min_cash)、现金兜底 ─────────────────


def test_weights_sum_bounded_and_cash():
    cfg = RiskOverlayConfig(min_cash=0.10)
    holdings = [
        _mk(symbol=f"S{i}", total=0.9, market_cap=120e8) for i in range(10)
    ]
    res = apply_risk_overlay(holdings, cfg, as_of="20240131")
    assert all(w >= 0.0 for w in res.weights.values())
    assert sum(res.weights.values()) <= (1.0 - cfg.min_cash) + 1e-6
    assert abs((sum(res.weights.values()) + res.cash) - 1.0) < 1e-3
    assert res.invested <= 0.90 + 1e-6


def test_deterministic_on_rerun():
    cfg = RiskOverlayConfig()
    holdings = [
        _mk(symbol="A", total=0.8, market_cap=200e8, annual_vol=0.20, turnover=0.5, sector="白酒"),
        _mk(symbol="B", total=0.7, market_cap=50e8, annual_vol=0.55, turnover=3.0, sector="医药"),
        _mk(symbol="C", total=0.6, market_cap=30e8, annual_vol=0.35, turnover=1.2, sector="白酒"),
    ]
    a = apply_risk_overlay(holdings, cfg, as_of="20240131")
    b = apply_risk_overlay(holdings, cfg, as_of="20240131")
    assert a.weights == b.weights
    assert a.cash == b.cash
    assert a.sector_exposure == b.sector_exposure


# ── 行业上限被尊重 ───────────────────────────────────────────────────


def test_sector_cap_respected():
    cfg = RiskOverlayConfig(max_single_weight=0.20, max_sector_weight=0.25, min_cash=0.0)
    # 4 只同「白酒」，分数悬殊：不削顶会让白酒逼近 1.0
    holdings = [
        _mk(symbol="A", total=0.95, market_cap=300e8, sector="白酒"),
        _mk(symbol="B", total=0.90, market_cap=250e8, sector="白酒"),
        _mk(symbol="C", total=0.85, market_cap=200e8, sector="白酒"),
        _mk(symbol="D", total=0.80, market_cap=150e8, sector="白酒"),
    ]
    res = apply_risk_overlay(holdings, cfg)
    baijiu = sum(res.weights[s] for s in res.weights if s in ("A", "B", "C", "D"))
    assert baijiu <= 0.25 + 1e-6, f"白酒暴露 {baijiu:.3f} 超 25% 上限"
    # 削顶后必有现金
    assert res.cash > 0.0


def test_sector_unknown_not_capped():
    """缺行业（unknown）的票不受行业上限约束（不误杀，沿用既有语义）。"""
    cfg = RiskOverlayConfig(max_single_weight=0.30, max_sector_weight=0.25, min_cash=0.0)
    holdings = [
        _mk(symbol="A", total=0.9),
        _mk(symbol="B", total=0.85),
    ]
    res = apply_risk_overlay(holdings, cfg)
    # 两票都 unknown 行业：单票 30% 上限内即可合计到 60%
    assert sum(res.weights.values()) > 0.5


# ── 市值分层上限被尊重 ───────────────────────────────────────────────


def test_cap_tier_cap_respected():
    cfg = RiskOverlayConfig(
        max_single_weight=0.40,
        max_sector_weight=0.90,  # 行业放宽，专注看市值分层
        cap_tier_limits={"large": 0.30, "mid": 0.30, "small": 0.30},
        min_cash=0.0,
    )
    holdings = [
        _mk(symbol=f"L{i}", total=0.9, market_cap=300e8) for i in range(4)  # large
    ]
    res = apply_risk_overlay(holdings, cfg)
    large = res.cap_tier_exposure.get("large", 0.0)
    # 相对总投资的 large 占比应 ≤ 100%（全 large），但绝对权重和 ≤ 0.30
    assert sum(res.weights.values()) <= 0.30 + 1e-6, f"large 合计 {sum(res.weights.values()):.3f}"
    assert large <= 1.0 + 1e-6


# ── 低波加分 / 换手惩罚 方向 ─────────────────────────────────────────


def test_low_vol_favors_lower_volatility():
    """同分、同市值同行业，仅波动不同：波动低的最终权重应 ≥ 波动高的。"""
    cfg = RiskOverlayConfig(low_vol_tilt=0.5, turnover_penalty=0.0, min_cash=0.0)
    holdings = [
        _mk(symbol="calm", total=0.8, market_cap=300e8, annual_vol=0.18, sector="X"),
        _mk(symbol="wild", total=0.8, market_cap=300e8, annual_vol=0.60, sector="Y"),
    ]
    res = apply_risk_overlay(holdings, cfg)
    assert res.weights.get("calm", 0.0) >= res.weights.get("wild", 0.0)


def test_turnover_penalizes_high_turnover():
    """同分、同波动，仅换手不同：换手高的最终权重应 ≤ 换手低的。"""
    cfg = RiskOverlayConfig(low_vol_tilt=0.0, turnover_penalty=0.6, min_cash=0.0)
    holdings = [
        _mk(symbol="illiq", total=0.8, market_cap=300e8, annual_vol=0.30, turnover=0.4, sector="X"),
        _mk(symbol="hot", total=0.8, market_cap=300e8, annual_vol=0.30, turnover=5.0, sector="Y"),
    ]
    res = apply_risk_overlay(holdings, cfg)
    assert res.weights.get("hot", 0.0) <= res.weights.get("illiq", 0.0)


# ── 缺字段 fail-soft 走中性（不崩、不加不罚）────────────────────────


def test_missing_fields_are_neutral_failsoft():
    cfg = RiskOverlayConfig(low_vol_tilt=0.5, turnover_penalty=0.6, min_cash=0.0)
    holdings = [
        _mk(symbol="A", total=0.8, sector="白酒"),   # 全缺市值/波动/换手
        _mk(symbol="B", total=0.7, sector="医药"),
    ]
    res = apply_risk_overlay(holdings, cfg)
    assert "A" in res.weights and "B" in res.weights
    # notes 应如实标注缺失
    assert any("缺市值" in n for n in res.notes)
    assert any("缺波动" in n for n in res.notes)
    assert any("缺换手" in n for n in res.notes)


def test_empty_holdings_failsoft():
    res = apply_risk_overlay([], None, as_of="20240131")
    assert res.weights == {}
    assert res.cash == 1.0
    assert res.n_holdings == 0


# ── 不塞下单逻辑（输出只读、无指令词）──────────────────────────────


def test_output_has_no_trade_instructions():
    cfg = RiskOverlayConfig()
    holdings = [
        _mk(symbol="A", total=0.9, market_cap=300e8, annual_vol=0.2, turnover=0.5, sector="白酒"),
        _mk(symbol="B", total=0.8, market_cap=30e8, annual_vol=0.4, turnover=1.0, sector="医药"),
    ]
    text = summarize_risk_overlay(apply_risk_overlay(holdings, cfg, as_of="20240131"))
    for word in ("买入", "卖出", "加仓", "清仓", "止损", "全仓"):
        assert word not in text


def test_result_dataclass_serializable_fields():
    cfg = RiskOverlayConfig()
    holdings = [_mk(symbol="A", total=0.9, market_cap=300e8, sector="白酒")]
    res = apply_risk_overlay(holdings, cfg, as_of="20240131")
    # frozen dataclass：逐字段存在（不用 set(res)）
    required = {
        "as_of", "n_holdings", "weights", "cash",
        "sector_exposure", "cap_tier_exposure", "risk_flags", "notes",
    }
    for name in required:
        assert hasattr(res, name), f"缺少字段 {name}"
    assert res.as_of == "20240131"
    assert res.n_holdings == 1
    assert "A" in res.weights


# ── 路线① → 路线② 接线 ─────────────────────────────────────────────


def test_holdings_from_screener_fills_and_missing_neutral():
    """接线：把 screener 快照的 selected 转成 HoldingInput，缺映射走 None。"""
    snap = {
        "selected": [
            {"symbol": "000001.SZ", "total_score": 0.82, "rank": 1},
            {"symbol": "600519.SH", "total_score": 0.77, "rank": 2},
        ]
    }
    holdings = holdings_from_screener(
        snap,
        market_caps={"000001.SZ": 25e8},          # 只有第一只有市值
        sectors={"600519.SH": "白酒"},            # 只有第二只有行业
    )
    assert len(holdings) == 2
    by = {h.symbol: h for h in holdings}
    assert by["000001.SZ"].market_cap == 25e8
    assert by["000001.SZ"].sector == ""            # 缺行业 ⇒ 空（中性）
    assert by["600519.SH"].market_cap is None      # 缺市值 ⇒ None（中性）
    assert by["600519.SH"].sector == "白酒"
    # total_score 透传（来自快照，生产口径）
    assert abs(by["000001.SZ"].total_score - 0.82) < 1e-9


def test_holdings_from_screener_respects_top_n_and_deterministic():
    snap = {
        "selected": [
            {"symbol": f"S{i}", "total_score": round(0.9 - i * 0.05, 4), "rank": i + 1}
            for i in range(6)
        ]
    }
    a = holdings_from_screener(snap, top_n=3)
    b = holdings_from_screener(snap, top_n=3)
    assert [h.symbol for h in a] == ["S0", "S1", "S2"]
    assert a == b  # 确定性（HoldingInput 可 ==）


def test_route1_to_route2_end_to_end():
    """端到端闭环：路线①真实快照 → holdings_from_screener → apply_risk_overlay。

    验证「筛选器清单 + 逐票映射」能直接产出风险切片，权重性质成立、缺字段 fail-soft。
    """
    from aqsp.strategies.screener import build_screener_snapshot
    from aqsp.strategies.thresholds import load_thresholds
    from tests.test_screener_snapshot import _frames

    th = load_thresholds()
    data = _frames(n=12)
    snap = build_screener_snapshot(data, regime="unknown", thresholds=th, top_n=6, as_of="20240131")
    assert snap["selected"], "路线① 应至少选出几只（合成数据）"

    # 路线② 吃路线① 的输出（市值/波动/换手全缺 ⇒ 全走中性，验证不崩）
    holdings = holdings_from_screener(snap)
    res = apply_risk_overlay(holdings, RiskOverlayConfig(min_cash=0.10), as_of="20240131")
    # 权重和 ≤ (1-min_cash)、非负、现金兜底
    assert all(w >= 0.0 for w in res.weights.values())
    assert sum(res.weights.values()) <= 0.90 + 1e-3
    # 入选票 ⊆ 路线① 清单
    assert set(res.weights) <= {h.symbol for h in holdings}
    # 缺字段的 notes 如实标注（市值/波动/换手 全缺）
    assert any("缺市值" in n for n in res.notes)
    assert any("缺波动" in n for n in res.notes)
    assert any("缺换手" in n for n in res.notes)
