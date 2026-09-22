"""事件日历（`aqsp.features.event_calendar`）单元测试。

测试原则（宪法红线）：
- **全部离线**：只喂内存记录 / 合成 payload，测试里绝不触网。
- 覆盖「正常 + 边界 + 错误」三类场景，尤其 **no-look-ahead** 边界。
"""

from __future__ import annotations

from aqsp.data.lockup import LockupItem, LockupSource
from aqsp.data.longhubang import LongHubangItem, LongHubangSource
from aqsp.features.event_calendar import (
    UNLOCK_RATIO_HIGH,
    UNLOCK_RATIO_LOW,
    UNLOCK_RATIO_MEDIUM,
    EventCalendar,
    _norm_symbol,
    _parse_iso,
    unlock_severity,
)


def _unlock(
    symbol: str = "600000",
    plan_date: str = "2026-09-30",
    ratio: float = 0.05,
    shares: float = 1000.0,
    kind: str = "首发原股东限售股份",
    name: str = "测试股",
) -> LockupItem:
    return LockupItem(
        symbol=symbol,
        name=name,
        plan_date=plan_date,
        lockup_shares=shares,
        ratio=ratio,
        lockup_type=kind,
    )


def _lhb(
    symbol: str = "600000",
    trade_date: str = "2026-09-28",
    net: float = 8000.0,
    interpretation: str = "机构专用席位买入",
    name: str = "测试股",
) -> LongHubangItem:
    return LongHubangItem(
        trade_date=trade_date,
        symbol=symbol,
        name=name,
        close_price=10.0,
        change_rate=9.9,
        buy_amount=10000.0,
        sell_amount=2000.0,
        net_amount=net,
        interpretation=interpretation,
    )


# ============================================================
# _parse_iso
# ============================================================


def test_parse_iso_when_input_carries_time_component():
    assert _parse_iso("2026-09-15 00:00:00").isoformat() == "2026-09-15"


def test_parse_iso_when_input_is_plain_date():
    assert _parse_iso("2026-09-15").isoformat() == "2026-09-15"


def test_parse_iso_when_separator_is_slash():
    assert _parse_iso("2026/09/15").isoformat() == "2026-09-15"


def test_parse_iso_returns_none_when_blank_or_garbage():
    assert _parse_iso("") is None
    assert _parse_iso("   ") is None
    assert _parse_iso(None) is None
    assert _parse_iso("not-a-date") is None
    assert _parse_iso("2026-13-45") is None


# ============================================================
# unlock_severity
# ============================================================


def test_unlock_severity_when_ratio_at_declared_boundaries():
    assert unlock_severity(UNLOCK_RATIO_HIGH) == "high"
    assert unlock_severity(UNLOCK_RATIO_MEDIUM) == "medium"
    assert unlock_severity(UNLOCK_RATIO_LOW) == "low"


def test_unlock_severity_when_ratio_below_lowest_boundary():
    assert unlock_severity(UNLOCK_RATIO_LOW / 2) == "negligible"
    assert unlock_severity(0.0) == "negligible"


def test_unlock_severity_returns_negligible_when_ratio_not_numeric():
    assert unlock_severity(None) == "negligible"  # type: ignore[arg-type]
    assert unlock_severity("abc") == "negligible"  # type: ignore[arg-type]


# ============================================================
# upcoming_unlocks（前瞻预警 + 点内安全）
# ============================================================


def test_upcoming_unlocks_includes_event_when_within_horizon():
    cal = EventCalendar(unlocks=[_unlock(plan_date="2026-09-30", ratio=0.05)])
    events = cal.upcoming_unlocks("600000", "2026-09-22")
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "lockup_expiry"
    assert ev.event_date == "2026-09-30"
    assert ev.days_until == 8
    assert ev.severity == "medium"
    assert ev.detail  # 证据文案非空


def test_upcoming_unlocks_excludes_past_event_when_event_before_as_of():
    """as_of 之前的解禁是已发生事实，不是预警 —— 必须排除。"""
    cal = EventCalendar(unlocks=[_unlock(plan_date="2026-09-01")])
    assert cal.upcoming_unlocks("600000", "2026-09-22") == []


def test_upcoming_unlocks_excludes_event_when_beyond_horizon():
    cal = EventCalendar(
        unlocks=[_unlock(plan_date="2026-12-31")], unlock_horizon_days=30
    )
    assert cal.upcoming_unlocks("600000", "2026-09-22") == []


def test_upcoming_unlocks_respects_explicit_horizon_override():
    cal = EventCalendar(
        unlocks=[_unlock(plan_date="2026-12-31")], unlock_horizon_days=30
    )
    assert len(cal.upcoming_unlocks("600000", "2026-09-22", horizon_days=200)) == 1


def test_upcoming_unlocks_returns_empty_when_symbol_untracked():
    cal = EventCalendar(unlocks=[_unlock(symbol="600000")])
    assert cal.upcoming_unlocks("000001", "2026-09-22") == []


def test_upcoming_unlocks_returns_empty_when_as_of_unparsable():
    cal = EventCalendar(unlocks=[_unlock(plan_date="2026-09-30")])
    assert cal.upcoming_unlocks("600000", "garbage") == []
    assert cal.upcoming_unlocks("600000", "") == []


def test_upcoming_unlocks_sorted_ascending_when_multiple_events():
    cal = EventCalendar(
        unlocks=[
            _unlock(plan_date="2026-10-20"),
            _unlock(plan_date="2026-09-25"),
            _unlock(plan_date="2026-10-05"),
        ]
    )
    days = [ev.days_until for ev in cal.upcoming_unlocks("600000", "2026-09-22")]
    assert days == sorted(days)
    assert len(days) == 3


# ============================================================
# recent_longhubang（回溯佐证 + 点内安全）
# ============================================================


def test_recent_longhubang_includes_record_when_within_lookback():
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-19")])
    records = cal.recent_longhubang("600000", "2026-09-22")
    assert len(records) == 1
    rec = records[0]
    assert rec.event_type == "longhubang"
    assert rec.days_ago == 3
    assert rec.interpretation == "机构专用席位买入"
    assert "龙虎榜" in rec.detail


def test_recent_longhubang_excludes_future_record_when_no_look_ahead():
    """as_of 之后才发生的上榜记录一律排除（最关键的红线用例）。"""
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-25")])
    assert cal.recent_longhubang("600000", "2026-09-22") == []


def test_recent_longhubang_excludes_record_older_than_lookback():
    cal = EventCalendar(
        longhubang=[_lhb(trade_date="2026-01-05")], longhubang_lookback_days=5
    )
    assert cal.recent_longhubang("600000", "2026-09-22") == []


def test_recent_longhubang_sorts_newest_first_when_multiple_records():
    cal = EventCalendar(
        longhubang=[
            _lhb(trade_date="2026-09-18"),
            _lhb(trade_date="2026-09-21"),
            _lhb(trade_date="2026-09-20"),
        ]
    )
    records = cal.recent_longhubang("600000", "2026-09-22")
    assert [r.days_ago for r in records] == [1, 2, 4]


def test_recent_longhubang_detail_marks_net_sell_when_amount_negative():
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-21", net=-3200.0)])
    detail = cal.recent_longhubang("600000", "2026-09-22")[0].detail
    assert "净卖出" in detail
    assert "3200" in detail


# ============================================================
# 构造健壮性 / 数据面覆盖标记
# ============================================================


def test_calendar_skips_row_when_date_unparsable_or_symbol_blank():
    cal = EventCalendar(
        unlocks=[
            _unlock(plan_date=""),
            _unlock(symbol=" "),
            _unlock(plan_date="2026-09-30"),
        ],
        longhubang=[_lhb(trade_date="bad"), _lhb(trade_date="2026-09-21")],
    )
    assert cal.tracked_symbols() == 1
    assert cal.has_unlock_data() is True
    assert cal.has_longhubang_data() is True


def test_is_empty_true_when_no_data_loaded():
    cal = EventCalendar()
    assert cal.is_empty() is True
    assert cal.has_unlock_data() is False
    assert cal.has_longhubang_data() is False


def test_coverage_flags_report_per_face_when_only_one_loaded():
    """只有解禁面时，龙虎榜面必须如实报 False —— 否则会把「没数据」当「没上榜」。"""
    cal = EventCalendar(unlocks=[_unlock()])
    assert cal.has_unlock_data() is True
    assert cal.has_longhubang_data() is False
    assert cal.is_empty() is False


def test_tracked_symbols_counts_union_when_both_faces_loaded():
    cal = EventCalendar(
        unlocks=[_unlock(symbol="600000"), _unlock(symbol="000001")],
        longhubang=[_lhb(symbol="600000"), _lhb(symbol="300750")],
    )
    assert cal.tracked_symbols() == 3


# ============================================================
# _norm_symbol（前导零防御）
# ============================================================


def test_norm_symbol_pads_int_when_cache_lost_leading_zeros():
    """`pd.read_csv` 会把 `"000001"` 读成 int `1`；归一化必须补回前导零。"""
    assert _norm_symbol(1) == "000001"
    assert _norm_symbol("1") == "000001"
    assert _norm_symbol(600000) == "600000"


def test_norm_symbol_strips_suffix_and_whitespace():
    assert _norm_symbol("000001.SZ") == "000001"
    assert _norm_symbol(" 600000 ") == "600000"
    assert _norm_symbol("600000.SH") == "600000"


def test_norm_symbol_returns_empty_when_not_numeric():
    assert _norm_symbol("") == ""
    assert _norm_symbol(None) == ""
    assert _norm_symbol("abc") == ""
    assert _norm_symbol("   ") == ""


def test_upcoming_unlocks_matches_when_cached_symbol_lost_leading_zeros():
    """端到端：缓存里 symbol 被读成 int，日历仍必须按 '000001' 查得到。"""
    cal = EventCalendar(unlocks=[_unlock(symbol=1, plan_date="2026-09-30")])  # type: ignore[arg-type]
    events = cal.upcoming_unlocks("000001", "2026-09-22")
    assert len(events) == 1
    assert events[0].symbol == "000001"


def test_recent_longhubang_matches_when_query_symbol_carries_exchange_suffix():
    cal = EventCalendar(longhubang=[_lhb(symbol="1", trade_date="2026-09-21")])
    records = cal.recent_longhubang("000001.SZ", "2026-09-22")
    assert len(records) == 1
    assert records[0].symbol == "000001"


# ============================================================
# from_sources（预加载路径，autoload=False 不触网）
# ============================================================


def test_from_sources_reads_preloaded_items_when_autoload_false():
    lockup_src = LockupSource().from_items([_unlock(plan_date="2026-09-30")])
    lhb_src = LongHubangSource().from_items([_lhb(trade_date="2026-09-21")])
    cal = EventCalendar.from_sources(
        lockup_source=lockup_src, longhubang_source=lhb_src
    )
    assert len(cal.upcoming_unlocks("600000", "2026-09-22")) == 1
    assert len(cal.recent_longhubang("600000", "2026-09-22")) == 1


def test_from_sources_degrades_to_empty_when_source_raises():
    class _Boom:
        def items(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("network down")

    cal = EventCalendar.from_sources(lockup_source=_Boom(), longhubang_source=_Boom())  # type: ignore[arg-type]
    assert cal.is_empty() is True


def test_from_sources_without_sources_yields_empty_calendar():
    assert EventCalendar.from_sources().is_empty() is True
