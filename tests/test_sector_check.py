from __future__ import annotations

from aqsp.portfolio.sector_check import check_sector_concentration, get_sector


def test_check_sector_concentration_ignores_unknown_sectors_when_all_unknown() -> None:
    result = check_sector_concentration(["000021", "000338", "000400"])

    assert result.total_candidates == 3
    assert result.sector_count == 1
    assert result.max_concentration == 0.0
    assert result.warnings == ()


def test_check_sector_concentration_excludes_unknown_sector_from_ratio() -> None:
    result = check_sector_concentration(["600036", "000001", "000021"])

    assert result.total_candidates == 3
    assert result.sector_count == 2
    assert result.max_concentration == 2 / 3
    assert any("银行占比" in warning for warning in result.warnings)


def test_get_sector_prefers_runtime_hint_over_static_cache() -> None:
    assert get_sector("600036", industry_hint="高股息红利") == "高股息红利"


def test_check_sector_concentration_uses_runtime_sector_map() -> None:
    result = check_sector_concentration(
        ["600036", "000001", "000021"],
        sector_map={"000021": "银行"},
    )

    assert result.max_concentration == 1.0
    assert any("银行占比100%" in warning for warning in result.warnings)
