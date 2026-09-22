"""事件日历（`aqsp.features.event_calendar`）单元测试。

测试原则（宪法红线）：
- **全部离线**：只喂内存记录 / 合成 payload，测试里绝不触网。
- 覆盖「正常 + 边界 + 错误」三类场景，尤其 **no-look-ahead** 边界。
"""

from __future__ import annotations

import os
from datetime import date

import pytest

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


# ============================================================
# _norm_symbol —— float 形态（2026-09-22 复核 F1 回归）
# ============================================================


def test_norm_symbol_when_value_is_integral_float():
    """`pd.read_csv` 列里**任意一个空值**就会把整列升格成 float64 ⇒ 得到 1.0 / 600519.0。

    修复前 `str(1.0)` = "1.0" → 数字位 "10" → 键 "000010"：
    查询 "000001" 永远为空，而查询 "000010" 会拿到 000001 的解禁 —— 证据挂错股票。
    """
    assert _norm_symbol(1.0) == "000001"
    assert _norm_symbol(600519.0) == "600519"
    assert _norm_symbol("1.0") == "000001"
    assert _norm_symbol("600519.0") == "600519"
    assert _norm_symbol("600519.00") == "600519"


def test_norm_symbol_rejects_when_more_than_six_digits():
    """7 位数字绝不能拿去当键（`str(600519.0)` 误当月字符串会得到 "6005190"）。"""
    assert _norm_symbol(6005190) == ""
    assert _norm_symbol("6005190") == ""
    assert _norm_symbol("12345678") == ""


def test_norm_symbol_rejects_fractional_decimal():
    """非整数小数无法可靠还原成 6 位代码 ⇒ 宁可丢弃也不误配。"""
    assert _norm_symbol(1.5) == ""
    assert _norm_symbol("000001.5") == ""


def test_upcoming_unlocks_matches_when_cached_symbol_read_as_float():
    """端到端 F1 回归：缓存整列升格成 float 后，日历仍按 "000001" 查得到。"""
    cal = EventCalendar(unlocks=[_unlock(symbol=1.0, plan_date="2026-09-30")])  # type: ignore[arg-type]
    events = cal.upcoming_unlocks("000001", "2026-09-22")
    assert len(events) == 1
    assert events[0].symbol == "000001"
    # 反向：修复前的错误键 "000010" 必须查不到（否则就是「证据挂错股票」）
    assert cal.upcoming_unlocks("000010", "2026-09-22") == []


# ============================================================
# 去重
# ============================================================


def test_upcoming_unlocks_dedupes_identical_records():
    """同一条记录被重复喂入（分页重叠 / 缓存叠加）不得报两遍。"""
    item = _unlock(plan_date="2026-09-30")
    cal = EventCalendar(unlocks=[item, item, _unlock(plan_date="2026-09-30")])
    assert len(cal.upcoming_unlocks("600000", "2026-09-22")) == 1


def test_recent_longhubang_dedupes_identical_records():
    item = _lhb(trade_date="2026-09-21")
    cal = EventCalendar(longhubang=[item, item])
    assert len(cal.recent_longhubang("600000", "2026-09-22")) == 1


def test_dedupe_keeps_distinct_records_on_same_date():
    """同日但不同解禁类型/股数属两条不同事实，不能被去重合并。"""
    cal = EventCalendar(
        unlocks=[
            _unlock(plan_date="2026-09-30", kind="首发原股东限售股份", shares=100.0),
            _unlock(plan_date="2026-09-30", kind="定向增发机构配售股份", shares=200.0),
        ]
    )
    assert len(cal.upcoming_unlocks("600000", "2026-09-22")) == 2


# ============================================================
# 覆盖区间（F3 / F4 的可观测性）
# ============================================================


def test_spans_are_none_when_face_not_loaded():
    cal = EventCalendar()
    assert cal.unlock_span() is None
    assert cal.longhubang_span() is None


def test_spans_report_actual_min_max():
    cal = EventCalendar(
        unlocks=[_unlock(plan_date="2026-10-20"), _unlock(plan_date="2026-09-25")],
        longhubang=[_lhb(trade_date="2026-09-21"), _lhb(trade_date="2026-09-18")],
    )
    assert cal.unlock_span() == (date(2026, 9, 25), date(2026, 10, 20))
    assert cal.longhubang_span() == (date(2026, 9, 18), date(2026, 9, 21))


def test_longhubang_covers_false_when_cache_holds_a_single_day():
    """原始缺陷形态：只抓 1 个交易日 ⇒ 不能用 1 天的数据断言 5 天。"""
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-22")])
    assert cal.longhubang_covers("2026-09-22") is False


def test_longhubang_covers_true_when_span_reaches_window_start():
    """修复后形态：fetch 抓 [anchor-5d, anchor]，最早交易日就在窗口起点附近。"""
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-17")])
    assert cal.longhubang_covers("2026-09-22") is True


def test_longhubang_covers_tolerates_weekend_at_window_start():
    """窗口起点若是周末（无任何数据行），不应误判为覆盖不足。"""
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-19")])
    assert (
        cal.longhubang_covers("2026-09-22") is True
    )  # 09-17 是窗口起点，09-19 起有数据


def test_longhubang_covers_false_when_as_of_unparsable():
    cal = EventCalendar(longhubang=[_lhb(trade_date="2026-09-17")])
    assert cal.longhubang_covers("garbage") is False


# ============================================================
# from_cache —— 打分链路唯一合法入口（F11 回归）
# ============================================================


def _write_cache(root, filename: str, rows: list[dict]) -> str:
    import pandas as pd

    directory = os.path.join(str(root), "pit_cache")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_from_cache_reads_csv_and_preserves_leading_zeros(tmp_path):
    _write_cache(
        tmp_path,
        "lockup.csv",
        [
            {
                "symbol": "000001",
                "name": "平安银行",
                "plan_date": "2026-10-15",
                "lockup_shares": 1000.0,
                "ratio": 0.05,
                "lockup_type": "首发原股东限售股份",
            }
        ],
    )
    cal = EventCalendar.from_cache(runtime_data_root=str(tmp_path))
    events = cal.upcoming_unlocks("000001", "2026-09-22")
    assert len(events) == 1
    assert events[0].symbol == "000001"  # dtype=str 从源头保住前导零


def test_from_cache_reads_longhubang_csv(tmp_path):
    _write_cache(
        tmp_path,
        "longhubang.csv",
        [
            {
                "trade_date": "2026-09-21",
                "symbol": "000001",
                "name": "平安银行",
                "close_price": 11.0,
                "change_rate": 9.9,
                "buy_amount": 100.0,
                "sell_amount": 20.0,
                "net_amount": 80.0,
                "interpretation": "机构买入",
            }
        ],
    )
    cal = EventCalendar.from_cache(runtime_data_root=str(tmp_path))
    records = cal.recent_longhubang("000001", "2026-09-22")
    assert len(records) == 1
    assert records[0].net_amount == pytest.approx(80.0)


def test_from_cache_honours_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    _write_cache(
        tmp_path,
        "lockup.csv",
        [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "plan_date": "2026-10-15",
                "lockup_shares": 1.0,
                "ratio": 0.02,
                "lockup_type": "首发",
            }
        ],
    )
    assert len(EventCalendar.from_cache().upcoming_unlocks("600000", "2026-09-22")) == 1


def test_from_cache_returns_empty_calendar_when_cache_missing(tmp_path):
    assert EventCalendar.from_cache(runtime_data_root=str(tmp_path)).is_empty() is True


def test_from_cache_degrades_to_empty_when_cache_corrupted(tmp_path):
    directory = os.path.join(str(tmp_path), "pit_cache")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "lockup.csv"), "wb") as handle:
        handle.write(b"\x00\x01 not,a,valid\xff csv\x00")
    cal = EventCalendar.from_cache(runtime_data_root=str(tmp_path))
    assert isinstance(cal, EventCalendar)  # 不抛异常


def test_from_cache_tolerates_nan_cells(tmp_path):
    """NaN 单元格不能被原样传下去（曾导致「净卖出 nan 万元」这类错误文案）。"""
    _write_cache(
        tmp_path,
        "longhubang.csv",
        [
            {
                "trade_date": "2026-09-21",
                "symbol": "600000",
                "name": "浦发银行",
                "close_price": 11.0,
                "change_rate": 9.9,
                "buy_amount": 100.0,
                "sell_amount": 20.0,
                "net_amount": None,  # → NaN
                "interpretation": "机构买入",
            }
        ],
    )
    cal = EventCalendar.from_cache(runtime_data_root=str(tmp_path))
    records = cal.recent_longhubang("600000", "2026-09-22")
    assert len(records) == 1
    assert "净卖出" not in records[0].detail
    assert "净额缺失" in records[0].detail


# ============================================================
# 边界
# ============================================================


def test_upcoming_unlocks_includes_event_when_days_until_is_zero():
    """解禁日 = as_of 当天：当天无法再提前操作，算预警（docstring 承诺 `>= as_of`）。"""
    cal = EventCalendar(unlocks=[_unlock(plan_date="2026-09-22")])
    events = cal.upcoming_unlocks("600000", "2026-09-22")
    assert len(events) == 1
    assert events[0].days_until == 0


def test_to_float_tolerates_non_numeric_and_blank():
    from aqsp.features.event_calendar import _to_float

    assert _to_float(None) == 0.0
    assert _to_float("") == 0.0
    assert _to_float("abc") == 0.0
    assert _to_float("1.5") == pytest.approx(1.5)
    assert _to_float(2) == pytest.approx(2.0)


def test_default_windows_stay_within_data_layer_bounds():
    """钉住本层两个默认窗口的合理上界。

    ⚠️ 为什么不直接 import 数据层的 `DEFAULT_HORIZON_DAYS` / `DEFAULT_LOOKBACK_DAYS`：
    那两个常量由**另一条未合并分支**（`feat/event-data-fetchers`）引入，直接 import
    会让本 PR 依赖未合并代码、甚至需要 `skip`（而 `skipped≠健康`，不该留假绿）。
    跨模块一致性断言待两条 PR 都合并后再补 —— 已登记为 follow-up。

    这里退一步守住**本层自己**的不变量：默认窗口必须远小于数据面窗口，
    否则预警/佐证会因为「窗口大于数据覆盖」而恒空（这正是 2026-09-22 踩到的坑：
    解禁数据面覆盖 90 天，若本层默认窗口大于它，预警将永远为空）。
    """
    cal = EventCalendar()
    assert 0 < cal.unlock_horizon_days <= 60, "预警窗口不得超出数据面覆盖范围的数量级"
    assert 0 < cal.longhubang_lookback_days <= 10, "佐证窗口不得超过数据面默认 lookback"
