"""短线情绪周期模型（``aqsp.regime.emotion_cycle``）测试。

覆盖：指标归一化（含反向）、派生指标、题材分布、配置解析与校验、
四维评分（含缺维降级）、phase 状态机（含 fail-closed）、端到端评估。

🔴 本文件全部是纯逻辑测试，**不联网、不读业务数据**（只读 ``config/emotion_model.yaml``）。
"""

from __future__ import annotations

import textwrap

import pytest

from aqsp.regime.emotion_cycle import (
    PHASE_UNKNOWN,
    EmotionSnapshot,
    MetricBand,
    _CONDITION_KEYS,
    assess,
    classify_phase,
    composite_score,
    derived_metrics,
    load_emotion_model,
    normalize_metric,
    parse_emotion_model,
    score_dimensions,
    theme_shares_from_industries,
)


# ---------------------------------------------------------------------------
# 测试夹具：一个「赚钱效应极低」的冰点快照
# ---------------------------------------------------------------------------


def _ice_snapshot() -> EmotionSnapshot:
    return EmotionSnapshot(
        trade_date="2026-10-16",
        zt_count=12,
        dt_count=18,
        zb_count=25,
        max_boards=2,
        lianban_count=3,
        yzt_count=40,
        yzt_feedback=-4.5,
        yzt_up_ratio=0.10,
        high_board_pct=-6.0,
        seal_rate=0.32,
        break_rate=0.68,
        promotion_rate=0.02,
        up_count=800,
        down_count=4200,
    )


def _hot_snapshot() -> EmotionSnapshot:
    return EmotionSnapshot(
        trade_date="2026-10-16",
        zt_count=110,
        dt_count=1,
        zb_count=20,
        max_boards=7,
        lianban_count=35,
        yzt_count=90,
        yzt_feedback=4.8,
        yzt_up_ratio=0.78,
        high_board_pct=2.0,
        seal_rate=0.85,
        break_rate=0.15,
        promotion_rate=0.38,
        up_count=4000,
        down_count=900,
        theme_shares=theme_shares_from_industries(
            ["算力"] * 12 + ["机器人"] * 5 + ["电力"] * 3
        ),
    )


@pytest.fixture(scope="module")
def model():
    return load_emotion_model()


# ---------------------------------------------------------------------------
# normalize_metric
# ---------------------------------------------------------------------------


def test_normalize_metric_maps_floor_to_zero_and_ceil_to_hundred():
    band = MetricBand(floor=0.0, ceil=1.0, weight=1.0)
    assert normalize_metric(0.0, band) == 0.0
    assert normalize_metric(1.0, band) == 100.0
    assert normalize_metric(0.5, band) == 50.0


def test_normalize_metric_clamps_out_of_range_values():
    band = MetricBand(floor=0.0, ceil=1.0, weight=1.0)
    assert normalize_metric(-5.0, band) == 0.0
    assert normalize_metric(99.0, band) == 100.0


def test_normalize_metric_passes_none_through():
    assert normalize_metric(None, MetricBand(0.0, 1.0, 1.0)) is None


def test_normalize_metric_invert_flips_direction():
    """反向指标：数值越小越严重 ⇒ 分越高。"""
    band = MetricBand(floor=-8.0, ceil=0.0, weight=1.0, invert=True)
    assert normalize_metric(-8.0, band) == 100.0
    assert normalize_metric(0.0, band) == 0.0


def test_normalize_metric_handles_negative_floor():
    band = MetricBand(floor=-3.0, ceil=6.0, weight=1.0)
    assert normalize_metric(-3.0, band) == 0.0
    assert normalize_metric(6.0, band) == 100.0
    assert normalize_metric(1.5, band) == 50.0


# ---------------------------------------------------------------------------
# derived_metrics
# ---------------------------------------------------------------------------


def test_derived_metrics_computes_pressure_and_breadth():
    derived = derived_metrics(_ice_snapshot())
    assert derived["dt_pressure"] == pytest.approx(18 / 12)
    assert derived["up_ratio"] == pytest.approx(800 / 5000)


def test_derived_metrics_returns_none_instead_of_zero_when_denominator_missing():
    """分母为 0 ⇒ ``None``，**不是** 0 分。缺失与「真的 0」必须区分。"""
    snapshot = EmotionSnapshot(zt_count=0, dt_count=5, up_count=0, down_count=0)
    derived = derived_metrics(snapshot)
    assert derived["dt_pressure"] is None
    assert derived["up_ratio"] is None


def test_derived_metrics_computes_theme_concentration():
    snapshot = EmotionSnapshot(
        zt_count=10,
        theme_shares=(("算力", 0.6), ("机器人", 0.3), ("电力", 0.1)),
    )
    derived = derived_metrics(snapshot)
    assert derived["top_theme_share"] == pytest.approx(0.6)
    assert derived["hhi"] == pytest.approx(0.36 + 0.09 + 0.01)


def test_derived_metrics_theme_metrics_are_none_without_themes():
    derived = derived_metrics(EmotionSnapshot(zt_count=10))
    assert derived["top_theme_share"] is None
    assert derived["hhi"] is None


# ---------------------------------------------------------------------------
# theme_shares_from_industries
# ---------------------------------------------------------------------------


def test_theme_shares_ranks_by_count_desc():
    shares = theme_shares_from_industries(["A", "A", "A", "B", "B", "C"])
    assert shares == (("A", 0.5), ("B", 0.3333), ("C", 0.1667))


def test_theme_shares_breaks_ties_by_name_for_determinism():
    assert theme_shares_from_industries(["B", "A"]) == (("A", 0.5), ("B", 0.5))


def test_theme_shares_skips_blank_labels():
    shares = theme_shares_from_industries(["A", "", "  ", None, "A"])
    assert shares == (("A", 1.0),)


def test_theme_shares_returns_empty_for_no_usable_labels():
    assert theme_shares_from_industries([]) == ()
    assert theme_shares_from_industries(["", "   "]) == ()


# ---------------------------------------------------------------------------
# 配置解析与校验
# ---------------------------------------------------------------------------

_MINIMAL_YAML = {
    "version": "9.9.9",
    "effective_from": "2026-01-01",
    "dimensions": {
        "profit": {
            "label": "赚钱效应",
            "weight": 1.0,
            "metrics": {"seal_rate": {"floor": 0.0, "ceil": 1.0, "weight": 1.0}},
        }
    },
    "phase_rules": [
        {"name": "freezing", "label": "冰点", "when": {"profit_at_most": 25}},
        {"name": "diverging", "label": "分歧", "when": {}},
    ],
}


def test_parse_emotion_model_accepts_minimal_payload():
    config = parse_emotion_model(_MINIMAL_YAML)
    assert config.version == "9.9.9"
    assert list(config.dimensions) == ["profit"]
    assert config.phase_rules[0].when == {"profit_at_most": 25.0}
    assert config.phase_rules[-1].when == {}


def test_parse_emotion_model_rejects_missing_dimensions():
    payload = {k: v for k, v in _MINIMAL_YAML.items() if k != "dimensions"}
    with pytest.raises(ValueError, match="dimensions"):
        parse_emotion_model(payload)


def test_parse_emotion_model_rejects_missing_phase_rules():
    payload = {k: v for k, v in _MINIMAL_YAML.items() if k != "phase_rules"}
    with pytest.raises(ValueError, match="phase_rules"):
        parse_emotion_model(payload)


def test_parse_emotion_model_rejects_inverted_band():
    """``ceil <= floor`` 是配置错误 —— 反向指标必须用 ``invert``。"""
    payload = {
        **_MINIMAL_YAML,
        "dimensions": {
            "profit": {
                "metrics": {"seal_rate": {"floor": 1.0, "ceil": 0.0, "weight": 1.0}}
            }
        },
    }
    with pytest.raises(ValueError, match="invert"):
        parse_emotion_model(payload)


def test_parse_emotion_model_rejects_non_positive_weight():
    payload = {
        **_MINIMAL_YAML,
        "dimensions": {
            "profit": {
                "metrics": {"seal_rate": {"floor": 0.0, "ceil": 1.0, "weight": 0}}
            }
        },
    }
    with pytest.raises(ValueError, match="weight"):
        parse_emotion_model(payload)


def test_parse_emotion_model_rejects_unknown_condition_key():
    """🔴 拼错条件键（如 profit_at_leat）会让规则永不命中且毫无报错 ⇒ 启动就炸。"""
    payload = {
        **_MINIMAL_YAML,
        "phase_rules": [
            {"name": "freezing", "when": {"profit_at_leat": 25}},
            {"name": "diverging", "when": {}},
        ],
    }
    with pytest.raises(ValueError, match="未知条件"):
        parse_emotion_model(payload)


def test_parse_emotion_model_requires_fallback_rule_last():
    """最后一条必须是兜底（``when`` 为空），否则会有观测组合无 phase 可归。"""
    payload = {
        **_MINIMAL_YAML,
        "phase_rules": [
            {"name": "freezing", "when": {"profit_at_most": 25}},
        ],
    }
    with pytest.raises(ValueError, match="兜底"):
        parse_emotion_model(payload)


def test_parse_emotion_model_falls_back_to_known_label():
    payload = {
        **_MINIMAL_YAML,
        "phase_rules": [
            {"name": "climax", "when": {"profit_at_most": 25}},
            {"name": "diverging", "when": {}},
        ],
    }
    config = parse_emotion_model(payload)
    assert config.phase_rules[0].label == "高潮"


# ---------------------------------------------------------------------------
# 真实配置文件
# ---------------------------------------------------------------------------


def test_real_config_loads_and_covers_all_four_dimensions(model):
    assert set(model.dimensions) == {"profit", "loss", "breadth", "focus"}
    assert model.version
    assert model.effective_from


def test_real_config_last_rule_is_fallback(model):
    assert model.phase_rules[-1].when == {}


def test_real_config_only_uses_known_conditions(model):
    """真实 YAML 的每个条件键都必须在代码里有实现（防「YAML 写了代码不认」）。"""
    for rule in model.phase_rules:
        assert set(rule.when) <= set(_CONDITION_KEYS), rule.name


def test_real_config_phase_rules_are_unique_and_labelled(model):
    names = [rule.name for rule in model.phase_rules]
    assert len(names) == len(set(names))
    assert all(rule.label for rule in model.phase_rules)
    assert all(rule.description for rule in model.phase_rules)


def test_real_config_dimension_weights_are_positive(model):
    for dim in model.dimensions.values():
        assert dim.weight > 0
        assert dim.metrics
        assert all(band.weight > 0 for band in dim.metrics.values())


def test_load_emotion_model_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError, match="不可读"):
        load_emotion_model(tmp_path / "absent.yaml")


def test_load_emotion_model_rejects_non_mapping(tmp_path):
    target = tmp_path / "bad.yaml"
    target.write_text(textwrap.dedent("- just\n- a list\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="映射"):
        load_emotion_model(target)


# ---------------------------------------------------------------------------
# 四维评分
# ---------------------------------------------------------------------------


def test_score_dimensions_produces_all_four_dimensions(model):
    scores = score_dimensions(_hot_snapshot(), model)
    assert set(scores) == {"profit", "loss", "breadth", "focus"}
    for dim in scores.values():
        assert dim.score is not None
        assert 0.0 <= dim.score <= 100.0


def test_score_dimensions_ice_is_low_profit_and_high_loss(model):
    scores = score_dimensions(_ice_snapshot(), model)
    assert scores["profit"].score == pytest.approx(0.0, abs=1e-6)
    assert scores["loss"].score > 80


def test_score_dimensions_returns_none_when_all_metrics_missing(model):
    """完全没有观测 ⇒ 维度分为 ``None``，**不**按 0 分处理。"""
    scores = score_dimensions(EmotionSnapshot(), model)
    for dim in scores.values():
        assert dim.score is None


def test_score_dimensions_degrades_per_dimension(model):
    """只有涨跌家数、没有涨停池 ⇒ ``breadth`` 有分，``profit`` 为 None。

    ``up_ratio = 3000/5000 = 0.6``，带宽 ``[0.25, 0.75]`` ⇒ ``(0.6-0.25)/0.5 = 70``。
    """
    snapshot = EmotionSnapshot(zt_count=30, up_count=3000, down_count=2000)
    scores = score_dimensions(snapshot, model)
    assert scores["breadth"].score == pytest.approx(70.0)
    assert scores["profit"].score is None
    assert scores["focus"].score is None


def test_dimension_score_as_dict_rounds_and_keeps_none():
    scores = score_dimensions(_hot_snapshot(), load_emotion_model())
    payload = scores["profit"].as_dict()
    assert payload["name"] == "profit"
    assert isinstance(payload["score"], float)
    assert payload["score"] == round(payload["score"], 1)


# ---------------------------------------------------------------------------
# 综合分
# ---------------------------------------------------------------------------


def test_composite_score_renormalises_over_available_dimensions(model):
    """缺维度时在**剩余维度**上重新归一化权重，而不是把它当 0 分。

    这个快照只有涨跌家数（``zt_count=0`` ⇒ ``dt_pressure`` 也是 ``None``），
    因此只有 ``breadth`` 有分 ⇒ 综合分必须**等于** ``breadth`` 的 70 分，
    而不是被另外三个缺失维度稀释成 ``70 × 0.20 = 14``。
    """
    only_breadth = score_dimensions(
        EmotionSnapshot(up_count=3000, down_count=2000), model
    )
    assert only_breadth["loss"].score is None
    assert composite_score(only_breadth, model) == pytest.approx(70.0)


def test_composite_score_is_none_without_any_dimension(model):
    assert composite_score(score_dimensions(EmotionSnapshot(), model), model) is None


def test_composite_score_weights_match_config(model):
    scores = score_dimensions(_hot_snapshot(), model)
    expected = sum(
        scores[name].score * dim.weight for name, dim in model.dimensions.items()
    ) / sum(dim.weight for dim in model.dimensions.values())
    assert composite_score(scores, model) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# phase 状态机
# ---------------------------------------------------------------------------


def test_classify_phase_returns_unknown_without_any_observation(model):
    verdict = classify_phase(EmotionSnapshot(), model)
    assert verdict.name == PHASE_UNKNOWN
    assert verdict.rule_index is None


def test_classify_phase_detects_freezing(model):
    verdict = classify_phase(_ice_snapshot(), model)
    assert verdict.name == "freezing"
    assert verdict.label == "冰点"
    assert verdict.reasons


def test_classify_phase_detects_climax(model):
    verdict = classify_phase(_hot_snapshot(), model)
    assert verdict.name == "climax"
    assert verdict.label == "高潮"
    # 三条条件都必须出现在解释里
    assert len(verdict.reasons) == 3


def test_classify_phase_falls_back_to_diverging(model):
    """赚钱效应中等、晋级率也一般 ⇒ 落到兜底规则。"""
    snapshot = EmotionSnapshot(
        zt_count=45,
        dt_count=6,
        zb_count=25,
        max_boards=3,
        lianban_count=8,
        yzt_feedback=0.5,
        yzt_up_ratio=0.45,
        high_board_pct=-1.0,
        seal_rate=0.60,
        break_rate=0.40,
        promotion_rate=0.15,
    )
    verdict = classify_phase(snapshot, model)
    assert verdict.name == "diverging"
    assert verdict.rule_index == len(model.phase_rules) - 1


def test_classify_phase_is_fail_closed_when_profit_score_missing(model):
    """拿不到赚钱效应 ⇒ 依赖 profit 的规则全部不成立 ⇒ 落到兜底，绝不猜。"""
    snapshot = EmotionSnapshot(zt_count=30, max_boards=3, lianban_count=5)
    scores = score_dimensions(snapshot, model)
    assert scores["profit"].score is None
    verdict = classify_phase(snapshot, model, scores=scores)
    assert verdict.name == "diverging"


def test_classify_phase_is_deterministic(model):
    snapshot = _hot_snapshot()
    assert classify_phase(snapshot, model) == classify_phase(snapshot, model)


def test_phase_verdict_as_dict_is_json_ready(model):
    payload = classify_phase(_ice_snapshot(), model).as_dict()
    assert payload["name"] == "freezing"
    assert isinstance(payload["reasons"], list)
    assert payload["description"]


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


def test_assess_bundles_phase_scores_and_version(model):
    assessment = assess(_hot_snapshot(), model)
    assert assessment.phase.name == "climax"
    assert assessment.composite is not None
    assert assessment.config_version == model.version
    assert assessment.trade_date == "2026-10-16"


def test_assess_as_dict_shape(model):
    payload = assess(_hot_snapshot(), model).as_dict()
    assert set(payload) == {
        "trade_date",
        "phase",
        "composite",
        "dimensions",
        "model_version",
    }
    assert set(payload["dimensions"]) == {"profit", "loss", "breadth", "focus"}
    assert payload["composite"] == round(payload["composite"], 1)


def test_assess_orders_ice_below_climax(model):
    ice = assess(_ice_snapshot(), model)
    hot = assess(_hot_snapshot(), model)
    assert ice.composite < hot.composite
    assert ice.phase.name != hot.phase.name
