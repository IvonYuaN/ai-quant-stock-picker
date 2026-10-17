"""`aqsp.features.theme_index` 单测：题材指数两种口径 + 日/周/月聚合。

守卫重点（都是"错了但看起来很正常"的那类）：
- 缺数据必须**跳过**，不能按 0% 补齐（补 0 = 把"没数据"伪装成"平盘"）；
- 周 / 月 K 的极值字段是**收盘序列**极值，不是盘中高低；
- 口径（vendor / reference）不可互换，每个输出都必须带 ``kind``。
"""

from __future__ import annotations

import math

import pytest

from aqsp.features.theme_index import (
    BASE_LEVEL,
    FREQ_DAY,
    FREQ_MONTH,
    FREQ_WEEK,
    KIND_REFERENCE,
    KIND_VENDOR,
    MIN_REFERENCE_COVERAGE,
    ThemeSeries,
    build_reference_series,
    build_vendor_series,
    describe_kind,
)


def _vendor(pairs, **kwargs) -> ThemeSeries:
    kwargs.setdefault("board_name", "AI应用")
    return build_vendor_series(pairs, **kwargs)


# ---------------------------------------------------------------------------
# 数据源口径：复利链与缺口处理
# ---------------------------------------------------------------------------


def test_daily_chaining_compounds_returns() -> None:
    """+10% 再 -10% ≠ 回到原点：1000 → 1100 → 990。"""
    series = _vendor([("2026-10-12", 10.0), ("2026-10-13", -10.0)])

    assert series.kind == KIND_VENDOR
    assert series.freq == FREQ_DAY
    assert [bar.close for bar in series.bars] == pytest.approx([1100.0, 990.0])
    assert series.level == pytest.approx(990.0)
    assert series.total_change_pct == pytest.approx(-1.0)


def test_daily_bar_open_is_previous_close() -> None:
    series = _vendor([("2026-10-12", 5.0), ("2026-10-13", 5.0)])

    first, second = series.bars
    assert first.open == pytest.approx(BASE_LEVEL)
    assert second.open == pytest.approx(first.close)
    # 单日 K 的极值只能是开收两端（数据源没有盘中价）
    assert first.close_high == pytest.approx(first.close)
    assert first.close_low == pytest.approx(first.open)


def test_absent_day_is_simply_absent_not_flat() -> None:
    """输入里**根本没有**某一天 ⇒ 序列里就没有那根 bar，也不计入 skipped。

    零 I/O 纯函数拿不到交易日历，无法区分「这天休市」与「这天数据没抓到」，
    因此不谎报 skipped；真正的缺口检测属于调用方（有日历、有 freshness 的那层）。
    关键是**不能补一根 0% 的平盘**——那会让周 / 月 K 多出一根假 K。
    """
    series = _vendor([("2026-10-12", 10.0), ("2026-10-14", 10.0)])

    assert [bar.period for bar in series.bars] == ["2026-10-12", "2026-10-14"]
    assert series.skipped_dates == ()
    assert series.level == pytest.approx(1210.0)


def test_explicit_none_value_is_skipped_and_reported() -> None:
    series = _vendor([("2026-10-12", 10.0), ("2026-10-13", None), ("2026-10-14", 10.0)])

    assert [bar.period for bar in series.bars] == ["2026-10-12", "2026-10-14"]
    assert series.skipped_dates == ("2026-10-13",)
    assert series.degraded is True
    assert series.coverage == pytest.approx(2 / 3)


@pytest.mark.parametrize("dirty", ["abc", "", None, float("nan"), float("inf"), -100.0, -150.0])
def test_dirty_change_pct_is_skipped(dirty: object) -> None:
    series = _vendor([("2026-10-12", 1.0), ("2026-10-13", dirty)])

    assert len(series.bars) == 1
    assert series.skipped_dates == ("2026-10-13",)


def test_same_date_keeps_last_value() -> None:
    series = _vendor([("2026-10-12", 10.0), ("2026-10-12", 2.0)])

    assert len(series.bars) == 1
    assert series.bars[0].change_pct == pytest.approx(2.0)


def test_input_order_does_not_matter() -> None:
    forward = _vendor([("2026-10-12", 3.0), ("2026-10-13", 4.0)])
    reverse = _vendor([("2026-10-13", 4.0), ("2026-10-12", 3.0)])

    assert [bar.close for bar in forward.bars] == pytest.approx([bar.close for bar in reverse.bars])


def test_unparseable_date_is_reported_not_silently_dropped() -> None:
    series = _vendor([("2026-10-12", 1.0), ("not-a-date", 2.0)])

    assert len(series.bars) == 1
    assert "not-a-date" in series.skipped_dates


def test_compact_date_format_is_accepted() -> None:
    series = _vendor([("20261012", 1.0), ("20261013", 1.0)])

    assert [bar.period for bar in series.bars] == ["2026-10-12", "2026-10-13"]


def test_empty_input_yields_empty_series_without_fake_level() -> None:
    series = _vendor([])

    assert series.bars == ()
    assert series.level is None
    assert series.total_change_pct is None
    assert series.coverage == 0.0
    assert series.degraded is False


# ---------------------------------------------------------------------------
# 周 / 月聚合
# ---------------------------------------------------------------------------


def test_week_bars_group_by_iso_week() -> None:
    pairs = [
        ("2026-10-12", 1.0),  # 周一
        ("2026-10-13", 1.0),  # 周二
        ("2026-10-16", 1.0),  # 周五
        ("2026-10-19", 1.0),  # 下周一
    ]
    series = _vendor(pairs, freq=FREQ_WEEK)

    assert [bar.period for bar in series.bars] == ["2026-W42", "2026-W43"]
    assert series.bars[0].points == 3
    assert series.bars[0].start == "2026-10-12"
    assert series.bars[0].end == "2026-10-16"
    assert series.bars[1].points == 1


def test_only_last_week_bar_is_marked_partial() -> None:
    """「本周走完没有」需要交易日历才能判；本模块保守地只把最后一根标成未完成。"""
    pairs = [("2026-10-12", 1.0), ("2026-10-19", 1.0), ("2026-10-20", 1.0)]
    series = _vendor(pairs, freq=FREQ_WEEK)

    assert [bar.partial for bar in series.bars] == [False, True]


def test_month_bars_group_by_calendar_month() -> None:
    pairs = [("2026-09-30", 1.0), ("2026-10-08", 2.0), ("2026-10-09", -1.0)]
    series = _vendor(pairs, freq=FREQ_MONTH)

    assert [bar.period for bar in series.bars] == ["2026-09", "2026-10"]
    assert series.bars[1].points == 2
    assert series.bars[1].start == "2026-10-08"
    assert series.bars[1].end == "2026-10-09"


def test_week_bar_change_pct_is_period_open_to_close() -> None:
    pairs = [("2026-10-12", 10.0), ("2026-10-13", 10.0)]
    series = _vendor(pairs, freq=FREQ_WEEK)

    bar = series.bars[0]
    assert bar.open == pytest.approx(BASE_LEVEL)
    assert bar.close == pytest.approx(BASE_LEVEL * 1.1 * 1.1)
    assert bar.change_pct == pytest.approx(21.0)


def test_close_high_low_are_close_series_extremes() -> None:
    """字段语义守卫：极值来自**收盘序列**（含周期起始开盘价），不是盘中高低。

    +10% → -5% → +3% 的收盘序列：1100 / 1045 / 1076.35，周期起点 1000。
    所以 close_high=1100（周二收盘）、close_low=1000（周期起点），而真实盘中
    最高/最低一定不小于/不大于这两个数——数据源没给盘中价，就不该叫 high/low。
    """
    series = _vendor([("2026-10-12", 10.0), ("2026-10-13", -5.0), ("2026-10-14", 3.0)])
    daily_closes = [bar.close for bar in series.bars]
    assert daily_closes == pytest.approx([1100.0, 1045.0, 1076.35])

    bar = _vendor(
        [("2026-10-12", 10.0), ("2026-10-13", -5.0), ("2026-10-14", 3.0)], freq=FREQ_WEEK
    ).bars[0]
    assert bar.open == pytest.approx(BASE_LEVEL)
    assert bar.close == pytest.approx(1076.35)
    assert bar.close_high == pytest.approx(1100.0)
    assert bar.close_low == pytest.approx(BASE_LEVEL)
    assert bar.change_pct == pytest.approx(7.635)


def test_unknown_freq_raises_value_error() -> None:
    with pytest.raises(ValueError, match="未知频率"):
        _vendor([("2026-10-12", 1.0)], freq="quarter")


# ---------------------------------------------------------------------------
# 等权参考口径
# ---------------------------------------------------------------------------


def test_reference_uses_equal_weight_mean() -> None:
    series = build_reference_series(
        {
            "600000": [("2026-10-12", 10.0)],
            "000001": [("2026-10-12", 0.0)],
            "300750": [("2026-10-12", -2.0)],
        },
        board_name="固态电池",
    )

    assert series.kind == KIND_REFERENCE
    assert series.bars[0].change_pct == pytest.approx((10.0 + 0.0 - 2.0) / 3)


def test_reference_skips_days_below_coverage_gate() -> None:
    """只有 4/10 成分有数据（0.4 < 0.5）⇒ 该日不出 bar。"""
    returns = {
        "600000": [("2026-10-12", 1.0)],
        "000001": [("2026-10-12", 2.0)],
        "300750": [("2026-10-12", 3.0)],
        "600519": [("2026-10-12", 4.0)],
    }
    series = build_reference_series(returns, board_name="固态电池", total_members=10)

    assert series.bars == ()
    assert series.skipped_dates == ("2026-10-12",)
    assert series.reference_coverage is None


def test_reference_keeps_days_at_or_above_coverage_gate() -> None:
    returns = {f"60000{i}": [("2026-10-12", float(i))] for i in range(5)}
    series = build_reference_series(returns, board_name="固态电池", total_members=10)

    assert len(series.bars) == 1
    assert series.reference_coverage == pytest.approx(MIN_REFERENCE_COVERAGE)


def test_reference_skips_day_with_no_members_at_all() -> None:
    returns = {"600000": [("2026-10-12", 1.0), ("2026-10-14", 1.0)]}
    series = build_reference_series(returns, board_name="固态电池")

    assert [bar.period for bar in series.bars] == ["2026-10-12", "2026-10-14"]


def test_reference_without_total_members_uses_symbol_count() -> None:
    returns = {"600000": [("2026-10-12", 4.0)], "000001": [("2026-10-12", 0.0)]}
    series = build_reference_series(returns, board_name="固态电池")

    assert series.reference_coverage == pytest.approx(1.0)
    assert series.bars[0].change_pct == pytest.approx(2.0)


def test_reference_empty_mapping_yields_empty_series() -> None:
    series = build_reference_series({}, board_name="固态电池")

    assert series.bars == ()
    assert series.level is None
    assert series.reference_coverage is None
    assert series.skipped_dates == ()


def test_reference_total_members_zero_never_produces_bars() -> None:
    """``total_members=0`` 是调用方的口径错误；宁可不出 bar，也不要除零或全放行。"""
    series = build_reference_series(
        {"600000": [("2026-10-12", 1.0)]}, board_name="固态电池", total_members=0
    )

    assert series.bars == ()
    assert series.skipped_dates == ("2026-10-12",)


def test_reference_weekly_aggregation_works() -> None:
    returns = {"600000": [("2026-10-12", 1.0), ("2026-10-13", 1.0)], "000001": [("2026-10-12", 1.0)]}
    series = build_reference_series(returns, board_name="固态电池", freq=FREQ_WEEK)

    assert [bar.period for bar in series.bars] == ["2026-W42"]
    assert series.bars[0].points == 2


# ---------------------------------------------------------------------------
# 口径不可互换
# ---------------------------------------------------------------------------


def test_kinds_are_distinct_and_labeled() -> None:
    """展示层必须能看出两种口径的差别——否则会把市值加权链当成等权指数。"""
    vendor = _vendor([("2026-10-12", 1.0)])
    reference = build_reference_series({"600000": [("2026-10-12", 1.0)]}, board_name="AI应用")

    assert vendor.kind != reference.kind
    assert describe_kind(vendor.kind) != describe_kind(reference.kind)
    assert "不可交易" in describe_kind(KIND_VENDOR)
    assert "等权" in describe_kind(KIND_REFERENCE)
    assert describe_kind("bogus").startswith("未知口径")


def test_as_dict_exposes_kind_label_and_no_daily_noise() -> None:
    payload = _vendor([("2026-10-12", 1.0)]).as_dict()

    assert payload["kind"] == KIND_VENDOR
    assert payload["kind_label"] == describe_kind(KIND_VENDOR)
    assert payload["level"] == pytest.approx(BASE_LEVEL * 1.01)
    assert len(payload["bars"]) == 1  # type: ignore[arg-type]
    assert "daily" not in payload
    assert set(payload["bars"][0]) == {  # type: ignore[index]
        "period",
        "start",
        "end",
        "open",
        "close",
        "close_high",
        "close_low",
        "change_pct",
        "points",
        "partial",
    }


def test_nan_check_does_not_leak_nan_level() -> None:
    series = _vendor([("2026-10-12", float("nan")), ("2026-10-13", 1.0)])

    assert math.isfinite(series.level or 0.0)
    assert len(series.bars) == 1
