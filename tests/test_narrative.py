"""概念标签 → 交易叙事归并 + 噪声过滤测试。"""

from __future__ import annotations

import pytest

from aqsp.features.narrative import (
    NARRATIVE_RULES,
    NOISE_LABELS,
    ROLE_NARRATIVE,
    ROLE_NOISE,
    ROLE_STRUCTURAL,
    ROLE_UNMAPPED,
    STRUCTURAL_LABELS,
    filter_labels,
    is_noise_label,
    is_structural_label,
    label_role,
    merge_narratives,
    narrative_for,
    normalize_label,
)


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def test_normalize_label_strips_and_uppercases():
    assert normalize_label("  cpo ") == "CPO"
    assert normalize_label("Ai语料") == "AI语料"


def test_normalize_label_strips_full_width_and_nbsp():
    assert normalize_label("光模块\u3000") == "光模块"
    assert normalize_label("\u00a0CPO\u00a0") == "CPO"


def test_normalize_label_handles_none_and_empty():
    assert normalize_label(None) == ""
    assert normalize_label("") == ""
    assert normalize_label("   ") == ""


# ---------------------------------------------------------------------------
# 角色判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label", ["融资融券", "深股通", "昨日涨停", "机构重仓", "沪深300_", "破净股"]
)
def test_noise_labels_are_classified_as_noise(label):
    assert label_role(label) == ROLE_NOISE
    assert is_noise_label(label) is True
    assert narrative_for(label) is None


@pytest.mark.parametrize("label", ["长江三角", "深圳特区", "上海自贸"])
def test_structural_labels_are_not_narratives(label):
    assert label_role(label) == ROLE_STRUCTURAL
    assert is_structural_label(label) is True
    assert narrative_for(label) is None


def test_narrative_label_is_classified_as_narrative():
    assert label_role("光模块") == ROLE_NARRATIVE
    assert narrative_for("光模块") == "光通信/CPO"


def test_unknown_label_is_unmapped_but_not_noise():
    assert label_role("TOP贴片") == ROLE_UNMAPPED
    assert is_noise_label("TOP贴片") is False
    assert narrative_for("TOP贴片") is None


def test_empty_label_is_unmapped():
    assert label_role("") == ROLE_UNMAPPED
    assert label_role(None) == ROLE_UNMAPPED


# ---------------------------------------------------------------------------
# 同义归并
# ---------------------------------------------------------------------------


def test_synonym_labels_map_to_single_narrative():
    for label in ("CPO", "光通信模块", "光模块", "cpo"):
        assert narrative_for(label) == "光通信/CPO"


def test_ai_labels_map_to_ai_application_narrative():
    for label in ("AI应用", "AIGC", "DeepSeek", "AI语料", "豆包"):
        assert narrative_for(label) == "AI应用"


def test_matching_is_case_insensitive_for_latin_keywords():
    assert narrative_for("miniled") == "消费电子"
    assert narrative_for("MiniLED") == "消费电子"


def test_more_specific_rule_wins_over_broader_one():
    """AI芯片 应归 AI算力，而不是被后面的半导体规则抢走。"""
    assert narrative_for("AI芯片") == "AI算力"
    assert narrative_for("汽车芯片") == "半导体"


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------


def test_filter_labels_drops_noise_but_keeps_order_and_original_text():
    labels = ["光模块", "融资融券", "  AI语料 ", "昨日涨停", "长江三角"]
    assert filter_labels(labels) == ("光模块", "AI语料", "长江三角")


def test_filter_labels_ignores_blank_entries():
    assert filter_labels(["", "   ", None, "光模块"]) == ("光模块",)


def test_filter_labels_returns_empty_for_all_noise():
    assert filter_labels(["融资融券", "深股通"]) == ()


# ---------------------------------------------------------------------------
# 归并
# ---------------------------------------------------------------------------


def test_merge_narratives_groups_synonyms_and_drops_noise():
    buckets = merge_narratives(
        [
            ("CPO", 6.0),
            ("光模块", 4.0),
            ("融资融券", 99.0),  # 噪声必须被丢弃
        ]
    )
    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket.name == "光通信/CPO"
    assert bucket.label_count == 2
    assert bucket.labels == ("CPO", "光模块")
    assert bucket.mean_change_pct == pytest.approx(5.0)
    assert bucket.max_change_pct == pytest.approx(6.0)


def test_merge_narratives_sorted_by_mean_change_desc():
    buckets = merge_narratives([("CPO", 1.0), ("光模块", 2.0), ("种业", 9.0)])
    assert [b.name for b in buckets] == ["农业", "光通信/CPO"]


def test_merge_narratives_keeps_unmapped_labels_in_own_bucket_by_default():
    buckets = merge_narratives([("TOP贴片", 7.0)])
    assert [b.name for b in buckets] == ["TOP贴片"]


def test_merge_narratives_can_drop_unmapped_labels():
    buckets = merge_narratives([("TOP贴片", 7.0)], include_unmapped=False)
    assert buckets == ()


def test_merge_narratives_returns_empty_tuple_for_empty_input():
    assert merge_narratives([]) == ()


def test_merge_narratives_returns_empty_when_only_noise():
    assert merge_narratives([("融资融券", 5.0), ("深股通", 3.0)]) == ()


def test_merge_narratives_skips_blank_labels():
    assert merge_narratives([("", 5.0), ("   ", 3.0)]) == ()


def test_merge_narratives_treats_structural_labels_as_unmapped_bucket():
    buckets = merge_narratives([("长江三角", 2.0)])
    assert [b.name for b in buckets] == ["长江三角"]


# ---------------------------------------------------------------------------
# 表自身的一致性守卫
# ---------------------------------------------------------------------------


def test_narrative_rule_names_are_unique():
    names = [rule.name for rule in NARRATIVE_RULES]
    assert len(names) == len(set(names))


def test_every_narrative_rule_has_keywords():
    for rule in NARRATIVE_RULES:
        assert rule.keywords, f"{rule.name} 没有关键词"


def test_no_rule_name_collides_with_noise_or_structural_label():
    """叙事名不得与噪声 / 结构标签同名，否则过滤与归并会互相打架。"""
    overlap = {rule.name for rule in NARRATIVE_RULES} & (NOISE_LABELS | STRUCTURAL_LABELS)
    assert overlap == set()


def test_no_duplicate_keyword_across_rules():
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for rule in NARRATIVE_RULES:
        for keyword in rule.keywords:
            key = keyword.upper()
            if key in seen and seen[key] != rule.name:
                clashes.append(f"{keyword}: {seen[key]} vs {rule.name}")
            seen[key] = rule.name
    assert clashes == [], f"关键词跨叙事重复（首匹配顺序会决定结果）: {clashes}"


def test_noise_labels_are_not_matched_by_any_rule():
    """噪声标签不得同时命中叙事规则，否则过滤规则形同虚设。"""
    leaked = [label for label in NOISE_LABELS if narrative_for(label) is not None]
    assert leaked == []
