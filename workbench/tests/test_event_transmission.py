from workbench.variants.event_transmission import (
    MarketEvent,
    infer_sector_hypotheses,
)


def test_event_transmission_maps_spacex_to_commercial_aerospace():
    event = MarketEvent(
        event_id="e-spacex",
        headline="SpaceX considers a public listing",
        source="verified-source",
        observed_at="2026-07-15T09:00:00+08:00",
        kind="overseas_theme",
        confidence=0.9,
        keywords=("SpaceX",),
    )

    result = infer_sector_hypotheses(event)

    assert result[0].sector == "商业航天"
    assert result[0].direction == "positive"
    assert result[0].horizon == "days"
    assert "成交量" in result[0].invalidation


def test_event_transmission_keeps_nvidia_mapping_as_two_hypotheses():
    event = MarketEvent(
        event_id="e-physical-ai",
        headline="NVIDIA announces physical AI direction",
        source="verified-source",
        observed_at="2026-07-15T09:00:00+08:00",
        kind="overseas_theme",
        confidence=0.8,
        keywords=("NVIDIA", "physical_ai"),
    )

    result = infer_sector_hypotheses(event)

    assert [item.sector for item in result] == ["机器人与物理AI", "半导体设备与零部件"]
    assert result[1].confidence < result[0].confidence


def test_event_transmission_separates_geopolitical_beneficiaries_and_risk():
    event = MarketEvent(
        event_id="e-conflict",
        headline="Geopolitical conflict escalates",
        source="verified-source",
        observed_at="2026-07-15T09:00:00+08:00",
        kind="geopolitical_risk",
        confidence=1.2,
    )

    result = infer_sector_hypotheses(event)

    assert [item.sector for item in result] == ["黄金", "军工", "风险资产"]
    assert result[0].direction == result[1].direction == "positive"
    assert result[2].direction == "negative"
    assert all(0.0 <= item.confidence <= 1.0 for item in result)


def test_event_transmission_does_not_infer_from_unknown_theme():
    event = MarketEvent(
        event_id="e-unknown",
        headline="Unverified theme",
        source="unknown",
        observed_at="2026-07-15T09:00:00+08:00",
        kind="overseas_theme",
        confidence=0.1,
        keywords=("unknown",),
    )

    assert infer_sector_hypotheses(event) == ()
