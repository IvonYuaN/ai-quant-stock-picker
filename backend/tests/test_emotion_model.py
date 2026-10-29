"""``backend/market.py`` 的情绪周期接入测试（纯逻辑 + 替身，不联网）。

守两件事：

1. 四池 → ``EmotionSnapshot`` 的折算正确（昨日涨停反馈、高标表现、题材分布）；
2. **fail-soft**：情绪模型出任何问题都只能让 ``emotion_model`` 字段消失，
   涨跌停/连板/炸板率这些核心字段必须照常返回（``/api/market/emotion`` 不能 500）。
"""

from __future__ import annotations

import pytest

import market
from breadth_fixtures import complete_breadth


def _zt(code: str, boards: int, pct: float, industry: str) -> dict:
    return {"c": code, "n": code, "lbc": boards, "zdp": pct, "hybk": industry}


def _pools(
    *,
    zt: list[dict],
    zb: list[dict] | None = None,
    dt: list[dict] | None = None,
    yzt: list[dict] | None = None,
):
    mapping = {
        "getTopicZTPool": zt,
        "getTopicZBPool": zb or [],
        "getTopicDTPool": dt or [],
        "getYesterdayZTPool": yzt or [],
    }

    def _fake(endpoint: str, date: str, sort: str = "fbt:asc"):
        return mapping[endpoint]

    return _fake


_HOT_ZT = [
    _zt("A", 7, 10.0, "算力"),
    _zt("B", 5, 9.98, "算力"),
    _zt("C", 3, 10.01, "机器人"),
    _zt("D", 2, 10.03, "电力"),
    _zt("E", 1, 9.99, "算力"),
]
_HOT_YZT = [{"zdp": 6.2}, {"zdp": 4.1}, {"zdp": -1.0}, {"zdp": 3.3}]
_HOT_BOARDS = [7, 5, 3, 2, 1]


def _assess_hot(**overrides):
    kwargs = dict(
        resolved="20261016",
        zt=_HOT_ZT,
        zb_count=2,
        dt_count=0,
        yzt=_HOT_YZT,
        boards=_HOT_BOARDS,
        lianban_count=4,
        yzt_count=4,
        seal_rate=0.714,
        break_rate=0.286,
        promotion_rate=0.25,
    )
    kwargs.update(overrides)
    return market._emotion_assessment(**kwargs)


# ---------------------------------------------------------------------------
# 折算
# ---------------------------------------------------------------------------


def test_emotion_assessment_builds_snapshot_from_pools():
    assessment = _assess_hot()
    assert assessment["trade_date"] == "2026-10-16"
    assert assessment["model_version"]
    assert set(assessment["dimensions"]) == {"profit", "loss", "breadth", "focus"}
    # 题材分布来自 hybk：算力 3/5 ⇒ focus 有分
    assert assessment["dimensions"]["focus"]["score"] is not None


def test_emotion_assessment_computes_yesterday_feedback_and_up_ratio():
    """昨日涨停反馈 = zdp 均值 3.15 ⇒ (3.15+3)/9 = 68.3 分；上涨占比 3/4 ⇒ 91.7 分。"""
    metrics = _assess_hot()["dimensions"]["profit"]["metrics"]
    assert metrics["yzt_feedback"] == pytest.approx(68.3, abs=0.1)
    assert metrics["yzt_up_ratio"] == pytest.approx(91.7, abs=0.1)


def test_emotion_assessment_high_board_pct_uses_only_top_tier():
    """高标 = 最高板那一档（只有 A，7 板，+10%）⇒ 反向归一化后亏钱贡献 0。"""
    metrics = _assess_hot()["dimensions"]["loss"]["metrics"]
    assert metrics["high_board_pct"] == pytest.approx(0.0)


def test_emotion_assessment_high_board_pct_penalises_when_top_tier_drops():
    """最高板集体杀跌（-9%）⇒ 亏钱效应维度拿满分贡献。"""
    zt = [_zt("A", 7, -9.0, "算力"), _zt("B", 2, 3.0, "电力")]
    metrics = market._emotion_assessment(
        resolved="20261016",
        zt=zt,
        zb_count=1,
        dt_count=2,
        yzt=_HOT_YZT,
        boards=[7, 2],
        lianban_count=2,
        yzt_count=4,
        seal_rate=0.667,
        break_rate=0.333,
        promotion_rate=0.5,
    )["dimensions"]["loss"]["metrics"]
    assert metrics["high_board_pct"] == pytest.approx(100.0)


def test_emotion_assessment_reaches_climax_on_large_pool():
    """涨停 ≥ 80 家 + 最高板 ≥ 5 + 赚钱效应 ≥ 80 ⇒ 高潮。"""
    zt = [_zt("A", 7, 10.0, "算力")] + [
        _zt(f"S{i}", 1, 10.0, "算力" if i % 2 else "机器人") for i in range(99)
    ]
    assessment = market._emotion_assessment(
        resolved="20261016",
        zt=zt,
        zb_count=2,
        dt_count=0,
        yzt=_HOT_YZT,
        boards=[7] + [1] * 99,
        lianban_count=40,
        yzt_count=4,
        seal_rate=0.98,
        break_rate=0.02,
        promotion_rate=0.38,
    )
    assert assessment["phase"]["name"] == "climax"


def test_emotion_assessment_is_empty_when_model_raises(monkeypatch):
    """模型炸了只能让字段消失，不能抛异常。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("aqsp.regime.emotion_cycle.assess", _boom)
    assert _assess_hot() == {}


# ---------------------------------------------------------------------------
# _emotion() 接入与 fail-soft
# ---------------------------------------------------------------------------


def test_emotion_attaches_model_field(monkeypatch):
    monkeypatch.setattr(
        market.astock,
        "em_zt_topic_pool",
        _pools(zt=_HOT_ZT, zb=[{"c": "X"}, {"c": "Y"}], yzt=_HOT_YZT),
    )
    monkeypatch.setattr(
        market, "_breadth_counts", lambda: complete_breadth(market.astock._now_shanghai(), up=4000, down=900)
    )

    payload = market._emotion()
    assert payload["zt_count"] == 5
    assert payload["max_boards"] == 7
    assert "emotion_model" in payload
    model = payload["emotion_model"]
    assert model["phase"]["name"] != "unknown"
    assert model["phase"]["label"]
    assert model["phase"]["reasons"]
    assert set(model["dimensions"]) == {"profit", "loss", "breadth", "focus"}


def test_emotion_keeps_core_fields_when_model_fails(monkeypatch):
    """fail-soft 守卫：模型炸掉时核心字段必须完好，只是没有 emotion_model。"""
    monkeypatch.setattr(
        market.astock,
        "em_zt_topic_pool",
        _pools(zt=_HOT_ZT, zb=[{"c": "X"}], yzt=_HOT_YZT),
    )
    monkeypatch.setattr(market, "_breadth_counts", lambda: None)

    def _boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("aqsp.regime.emotion_cycle.assess", _boom)

    payload = market._emotion()
    assert "emotion_model" not in payload
    assert payload["zt_count"] == 5
    assert payload["zb_count"] == 1
    assert payload["seal_rate"] == pytest.approx(5 / 6, abs=1e-3)
    assert payload["ladder"]
    assert len(payload["date"]) == 10  # YYYY-MM-DD


def test_emotion_returns_empty_without_any_pool(monkeypatch):
    monkeypatch.setattr(market.astock, "em_zt_topic_pool", _pools(zt=[]))
    assert market._emotion() == {}


@pytest.mark.parametrize("breadth", [
    {"up": 100, "down": 0, "flat": 0, "total": 100},
    {"up": 100, "down": 0, "flat": 0, "total": 100, "coverage_complete": True},
])
def test_emotion_excludes_unverified_breadth_from_dimension_and_aggregate(monkeypatch, breadth):
    monkeypatch.setattr(market, "_breadth_counts", lambda: breadth)
    assessment = _assess_hot()
    assert assessment["dimensions"]["breadth"]["score"] is None
    assert assessment["breadth_evidence"] is None
    assert assessment["breadth_contract_version"] == 1


def test_emotion_does_not_mix_current_width_with_another_pool_day(monkeypatch):
    from test_position_action_plan import NOW
    monkeypatch.setattr(market, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(market, "_breadth_counts", lambda: complete_breadth(NOW))
    assessment = _assess_hot(resolved="20261016")
    assert assessment["dimensions"]["breadth"]["score"] is None
    assert assessment["breadth_evidence"] is None


@pytest.mark.parametrize("mode", ["legacy", "current", "no_breadth", "stale", "other_pool_day"])
def test_emotion_cache_drops_unproven_aggregate_without_refetching_pool(monkeypatch, tmp_path, mode):
    from test_position_action_plan import NOW
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(market.time, "time", lambda: NOW.timestamp())
    monkeypatch.setattr(market, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(market, "_breadth_counts", lambda: complete_breadth(NOW))
    model = _assess_hot(resolved="20261023")
    if mode == "legacy":
        model.pop("breadth_contract_version")
        model.pop("breadth_evidence")
    elif mode == "no_breadth":
        model["breadth_evidence"] = None
        model["dimensions"]["breadth"]["score"] = None
    elif mode == "stale":
        model["breadth_evidence"]["as_of"] = "2026-10-22T10:00:00+08:00"
    elif mode == "other_pool_day":
        model["trade_date"] = "2026-10-22"
    market._write_cache("emotion", {"date": "2026-10-23", "zt_count": 12, "dt_count": 3, "emotion_model": model})
    monkeypatch.setattr(market, "_emotion", lambda: pytest.fail("fresh cached read must not refetch"))
    result = market.get_short_term_emotion()
    assert result["zt_count"] == 12 and result["dt_count"] == 3
    assert ("emotion_model" in result) is (mode in ("current", "no_breadth"))
