"""宏观 PIT（社融/PMI）测试。

核心：未发布月份整行丢弃（不返回 NaN/0），PIT 回退到最近已发布月；
未知 PMI 分项抛 DataError；实时抓取在未配置 URL 时抛 DataError（沙箱/单测走注入）。
"""

from __future__ import annotations

import pytest

from aqsp.core.errors import DataError
from aqsp.data.macro_pit import (
    MacroPitSource,
    MacroPoint,
    _fetch_pmi,
    _fetch_social_financing,
    pmi_as_of,
    social_financing_as_of,
)


def _points() -> list[MacroPoint]:
    return [
        MacroPoint("2026-01", social_financing_increment=5.0, pmi_manufacturing=50.1),
        # 2026-02 社融未发布（None），但 PMI 已发布
        MacroPoint("2026-02", social_financing_increment=None, pmi_manufacturing=50.3),
        # 2026-03 社融发布，PMI 未发布
        MacroPoint("2026-03", social_financing_increment=4.8, pmi_manufacturing=None),
    ]


def test_social_financing_pit_falls_back_to_last_published():
    pts = _points()
    # 3 月已发布 -> 取 4.8
    assert social_financing_as_of(pts, "2026-03-15") == 4.8
    # 2 月社融未发布 -> 回退到 1 月 5.0，绝不为 0 或 NaN
    assert social_financing_as_of(pts, "2026-02-10") == 5.0
    # 早于所有数据 -> None
    assert social_financing_as_of(pts, "2025-12-31") is None


def test_pmi_pit_falls_back_to_last_published():
    pts = _points()
    # 3 月 PMI 未发布 -> 回退到 2 月 50.3
    assert pmi_as_of(pts, "2026-03-20") == 50.3
    assert pmi_as_of(pts, "2026-01-05") == 50.1
    assert pmi_as_of(pts, "2025-12-31") is None
    # 从未设置的分项 -> None
    assert pmi_as_of(pts, "2026-03-20", which="pmi_non_manufacturing") is None


def test_pmi_as_of_rejects_unknown_field():
    with pytest.raises(DataError):
        pmi_as_of(_points(), "2026-03", which="not_a_field")


def test_source_from_points_accessors():
    src = MacroPitSource().from_points(_points())
    assert src.social_financing("2026-03-15", autoload=False) == 4.8
    assert src.pmi("2026-03-20", autoload=False) == 50.3


def test_fetch_raises_without_url():
    with pytest.raises(DataError):
        _fetch_social_financing()
    with pytest.raises(DataError):
        _fetch_pmi()
