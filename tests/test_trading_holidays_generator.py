"""由深交所源生成 ``config/trading_holidays.json``（issue #314）的测试。

覆盖两层：

- **纯逻辑层** ``aqsp.data.trading_holidays``：口径（只写工作日休市日）、
  schema 往返、按年合并、报告 diff；
- **CLI** ``scripts/data/generate_trading_holidays.py``：dry-run / 落盘 /
  缺月拒绝落盘 / 退出码 / 默认路径锚点。

🔴 最关键的是 :func:`test_regenerated_calendar_preserves_trading_day_verdicts`：
它把「交易所给出的休市日全集」（含周末）喂给生成器，再让**真实消费方**
``resolve_is_trading_day`` 对 2026 全年逐日判定，断言与手写文件**零差异**。
这条守卫保证「换掉生成口径」不会悄悄改变选股链路的交易日判定。

本文件全部使用测试替身，**不联网**（``build_holiday_report`` 的取数入口被替换）。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from aqsp.data import trading_holidays as th
from aqsp.data.szse_calendar import SzseCalendarDay
from scripts.data.generate_trading_holidays import (
    EXIT_ERROR,
    EXIT_INCOMPLETE,
    EXIT_OK,
    _resolve_config_path,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = REPO_ROOT / "config" / "trading_holidays.json"

# 真实文件在 2026-10-16 核对深交所后确认混进的 4 个「节假日区间内的周末」条目。
# 生成口径会丢掉它们；它们对判定结果毫无影响（周末本来就休市）。
LEGACY_WEEKEND_ENTRIES = (
    date(2026, 1, 3),
    date(2026, 2, 15),
    date(2026, 2, 21),
    date(2026, 2, 22),
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


def _iter_year(year: int) -> list[date]:
    days: list[date] = []
    cursor = date(year, 1, 1)
    while cursor.year == year:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _exchange_days(year: int, *, closed: set[date] | None = None) -> list[SzseCalendarDay]:
    """该年逐日标志：默认只有周末休市；``closed`` 追加工作日休市日。"""
    closed = closed or set()
    return [
        SzseCalendarDay(
            trade_date=day,
            is_open=day.weekday() < 5 and day not in closed,
        )
        for day in _iter_year(year)
    ]


def _month_mapping(
    year: int, *, closed: set[date] | None = None
) -> dict[str, tuple[SzseCalendarDay, ...]]:
    """按 ``YYYY-MM`` 分桶，模拟 ``fetch_months`` 的返回结构。"""
    buckets: dict[str, list[SzseCalendarDay]] = {}
    for day in _exchange_days(year, closed=closed):
        key = f"{day.trade_date.year:04d}-{day.trade_date.month:02d}"
        buckets.setdefault(key, []).append(day)
    return {key: tuple(value) for key, value in buckets.items()}


class _FakeSource:
    """按 ``month`` 返回预置数据，并记录 ``allow_network`` 取值。"""

    def __init__(self, mapping: dict[str, tuple[SzseCalendarDay, ...] | None]) -> None:
        self.mapping = mapping
        self.calls: list[tuple[str, bool]] = []

    def load_month(
        self, month: str, *, allow_network: bool = False
    ) -> tuple[SzseCalendarDay, ...] | None:
        self.calls.append((month, allow_network))
        return self.mapping.get(month)


def _patch_fetch_months(
    monkeypatch: pytest.MonkeyPatch,
    mapping: dict[str, tuple[SzseCalendarDay, ...] | None],
) -> None:
    """替换 ``trading_holidays.fetch_months`` —— 生成侧唯一联网入口。

    签名必须与真实 ``fetch_months(months, *, source=None, cache_dir=None)`` 一致，
    否则 CLI 的调用点会静默走偏（本函数替换后无法再从返回值察觉）。
    """

    def _fake(months, *, source=None, cache_dir=None):
        return {month: mapping.get(month) for month in months}

    monkeypatch.setattr(th, "fetch_months", _fake)


def _real_config_2026() -> set[date]:
    real = th.parse_holiday_config(REAL_CONFIG.read_text(encoding="utf-8"))
    return {day for day in real if day.year == 2026}


# ---------------------------------------------------------------------------
# 纯逻辑：holidays_from_days / open_days_from_days
# ---------------------------------------------------------------------------


def test_holidays_from_days_drops_weekends_and_keeps_weekday_closures():
    days = _exchange_days(
        2026,
        closed={date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)},
    )
    holidays = th.holidays_from_days(days, year=2026)
    # 10-03 是周六 ⇒ 按口径丢掉；10-01/10-02 是周四/周五 ⇒ 保留。
    assert holidays == (date(2026, 10, 1), date(2026, 10, 2))


def test_holidays_from_days_filters_to_target_year():
    days = [
        SzseCalendarDay(trade_date=date(2026, 12, 31), is_open=False),
        SzseCalendarDay(trade_date=date(2027, 1, 4), is_open=False),  # 周一
    ]
    assert th.holidays_from_days(days, year=2027) == (date(2027, 1, 4),)
    assert th.holidays_from_days(days, year=2026) == (date(2026, 12, 31),)


def test_holidays_from_days_is_sorted_and_deduplicated():
    days = [
        SzseCalendarDay(trade_date=date(2026, 5, 5), is_open=False),  # 周二
        SzseCalendarDay(trade_date=date(2026, 5, 1), is_open=False),  # 周五
        SzseCalendarDay(trade_date=date(2026, 5, 5), is_open=False),
    ]
    assert th.holidays_from_days(days, year=2026) == (
        date(2026, 5, 1),
        date(2026, 5, 5),
    )


def test_holidays_from_days_ignores_open_days():
    days = _exchange_days(2026)  # 无工作日休市日
    assert th.holidays_from_days(days, year=2026) == ()


def test_open_days_from_days_counts_only_open_days_in_year():
    days = _exchange_days(2026, closed={date(2026, 1, 1), date(2026, 1, 2)})
    open_days = th.open_days_from_days(days, year=2026)
    assert date(2026, 1, 1) not in open_days
    assert date(2026, 1, 5) in open_days  # 周一
    # 2026 全年 365 天，去掉 104 个周末日与 2 个工作日休市日
    assert len(open_days) == 365 - 104 - 2


# ---------------------------------------------------------------------------
# 纯逻辑：parse / render
# ---------------------------------------------------------------------------


def test_render_then_parse_round_trips():
    holidays = [date(2026, 10, 1), date(2026, 2, 16), date(2026, 10, 1)]
    text = th.render_holiday_config(holidays)
    assert th.parse_holiday_config(text) == (date(2026, 2, 16), date(2026, 10, 1))


def test_render_holiday_config_keeps_legacy_schema_and_has_no_timestamp():
    """schema 必须与旧文件一致，且**不得**写入生成时间（否则每次重生成都产生噪声 diff）。"""
    text = th.render_holiday_config([date(2026, 10, 1)])
    assert json.loads(text) == {"holidays": ["2026-10-01"]}
    assert text.endswith("\n")
    assert "generated_at" not in text


def test_render_holiday_config_is_deterministic():
    holidays = [date(2026, 10, 1), date(2026, 5, 1)]
    assert th.render_holiday_config(holidays) == th.render_holiday_config(reversed(holidays))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{",
        "[]",
        '"holidays"',
        '{"holidays": "2026-10-01"}',
        '{"holidays": null}',
        '{"other": ["2026-10-01"]}',
    ],
)
def test_parse_holiday_config_tolerates_garbage(text):
    assert th.parse_holiday_config(text) == ()


def test_parse_holiday_config_skips_unparsable_entries():
    text = json.dumps({"holidays": ["2026-10-01", "", "not-a-date", "2026-10-05"]})
    assert th.parse_holiday_config(text) == (date(2026, 10, 1), date(2026, 10, 5))


def test_parse_holiday_config_dedups_and_sorts():
    text = json.dumps({"holidays": ["2026-10-05", "2026-10-01", "2026-10-05"]})
    assert th.parse_holiday_config(text) == (date(2026, 10, 1), date(2026, 10, 5))


def test_real_config_is_parsable_and_covers_2026():
    """真实文件必须能被生成侧解析（前置断言，防空转）。"""
    real = th.parse_holiday_config(REAL_CONFIG.read_text(encoding="utf-8"))
    assert real
    assert 2026 in {day.year for day in real}


# ---------------------------------------------------------------------------
# 纯逻辑：merge_year_holidays / next_year
# ---------------------------------------------------------------------------


def test_merge_year_holidays_replaces_only_target_year():
    existing = [date(2025, 10, 1), date(2026, 10, 1), date(2026, 10, 2)]
    merged = th.merge_year_holidays(
        existing, year=2026, holidays=[date(2026, 1, 1)]
    )
    assert merged == (date(2025, 10, 1), date(2026, 1, 1))


def test_merge_year_holidays_is_idempotent():
    holidays = [date(2026, 1, 1), date(2026, 10, 1)]
    once = th.merge_year_holidays([date(2025, 5, 1)], year=2026, holidays=holidays)
    twice = th.merge_year_holidays(once, year=2026, holidays=holidays)
    assert once == twice


def test_merge_year_holidays_ignores_out_of_year_input():
    merged = th.merge_year_holidays([], year=2026, holidays=[date(2027, 1, 1)])
    assert merged == ()


def test_next_year_defaults_to_following_year():
    assert th.next_year(date(2026, 12, 31)) == 2027
    assert th.next_year(date(2027, 1, 1)) == 2028


# ---------------------------------------------------------------------------
# 报告：build_holiday_report
# ---------------------------------------------------------------------------


def test_build_report_marks_complete_when_all_months_present(tmp_path, monkeypatch):
    mapping = _month_mapping(2026, closed={date(2026, 10, 1)})
    _patch_fetch_months(monkeypatch, mapping)
    report = th.build_holiday_report(2026, config_path=tmp_path / "nope.json")
    assert report.complete is True
    assert report.missing_months == ()
    assert len(report.available_months) == 12
    assert report.holidays == (date(2026, 10, 1),)
    assert report.existing == ()


def test_build_report_marks_incomplete_when_fetch_failed(tmp_path, monkeypatch):
    mapping = _month_mapping(2026)
    mapping["2026-03"] = None  # 取数失败
    _patch_fetch_months(monkeypatch, mapping)
    report = th.build_holiday_report(2026, config_path=tmp_path / "nope.json")
    assert report.complete is False
    assert report.missing_months == ("2026-03",)
    assert "2026-03" not in report.available_months


def test_build_report_marks_incomplete_when_month_not_yet_published(tmp_path, monkeypatch):
    """``()``（成功但无数据）与 ``None``（失败）**都必须**判为不可信。"""
    mapping = _month_mapping(2026)
    mapping["2026-12"] = ()  # 已公布到 11 月，12 月暂无
    _patch_fetch_months(monkeypatch, mapping)
    report = th.build_holiday_report(2026, config_path=tmp_path / "nope.json")
    assert report.complete is False
    assert report.missing_months == ("2026-12",)


def test_build_report_uses_explicit_network_entry_point(tmp_path):
    """生成侧取数必须走 ``allow_network=True``（否则离线源永远返回 None）。"""
    source = _FakeSource(_month_mapping(2026))
    th.build_holiday_report(2026, source=source, config_path=tmp_path / "nope.json")
    assert len(source.calls) == 12
    assert {flag for _, flag in source.calls} == {True}


def test_build_report_diffs_against_existing_file(tmp_path, monkeypatch):
    config = tmp_path / "trading_holidays.json"
    config.write_text(
        th.render_holiday_config(
            [date(2025, 10, 1), date(2026, 10, 1), date(2026, 5, 4)]
        ),
        encoding="utf-8",
    )
    _patch_fetch_months(
        monkeypatch, _month_mapping(2026, closed={date(2026, 10, 1), date(2026, 10, 2)})
    )
    report = th.build_holiday_report(2026, config_path=config)
    assert report.existing_for_year == (date(2026, 5, 4), date(2026, 10, 1))
    assert report.added == (date(2026, 10, 2),)
    assert report.removed == (date(2026, 5, 4),)
    assert report.legacy_weekend_entries == ()


def test_build_report_flags_legacy_weekend_entries(tmp_path, monkeypatch):
    """被删掉的条目若落在周末，单列为「旧噪声」而不是普通「删除」。"""
    config = tmp_path / "trading_holidays.json"
    config.write_text(
        th.render_holiday_config([date(2026, 10, 1), date(2026, 10, 3)]),
        encoding="utf-8",
    )
    _patch_fetch_months(monkeypatch, _month_mapping(2026, closed={date(2026, 10, 1)}))
    report = th.build_holiday_report(2026, config_path=config)
    assert report.removed == (date(2026, 10, 3),)
    assert report.legacy_weekend_entries == (date(2026, 10, 3),)


def test_build_report_tolerates_missing_config_file(tmp_path, monkeypatch):
    _patch_fetch_months(monkeypatch, _month_mapping(2026))
    report = th.build_holiday_report(2026, config_path=tmp_path / "absent.json")
    assert report.existing == ()
    assert report.added == ()


def test_report_as_dict_is_json_serialisable(tmp_path, monkeypatch):
    _patch_fetch_months(monkeypatch, _month_mapping(2026, closed={date(2026, 1, 1)}))
    report = th.build_holiday_report(2026, config_path=tmp_path / "nope.json")
    payload = json.loads(json.dumps(report.as_dict(), ensure_ascii=False))
    assert payload["year"] == 2026
    assert payload["complete"] is True
    assert payload["holidays"] == ["2026-01-01"]
    assert payload["holidays_count"] == 1
    assert payload["missing_months"] == []


def test_render_report_mentions_refusal_when_incomplete(tmp_path, monkeypatch):
    mapping = _month_mapping(2026)
    mapping["2026-07"] = None
    _patch_fetch_months(monkeypatch, mapping)
    report = th.build_holiday_report(2026, config_path=tmp_path / "nope.json")
    text = th.render_report(report, generated_at=datetime(2026, 12, 1, 9, 0, 0))
    assert "覆盖月份: 11/12" in text
    assert "2026-07" in text
    assert "拒绝落盘" in text


def test_render_report_lists_legacy_weekend_entries(tmp_path, monkeypatch):
    config = tmp_path / "trading_holidays.json"
    config.write_text(
        th.render_holiday_config([date(2026, 1, 3)]), encoding="utf-8"
    )
    _patch_fetch_months(monkeypatch, _month_mapping(2026))
    report = th.build_holiday_report(2026, config_path=config)
    text = th.render_report(report, generated_at=datetime(2026, 12, 1, 9, 0, 0))
    assert "2026-01-03" in text
    assert "旧条目" in text


# ---------------------------------------------------------------------------
# 🔴 等价性守卫：换生成口径不得改变交易日判定
# ---------------------------------------------------------------------------


def _verdicts(monkeypatch, holidays: set[date], year: int) -> dict[date, bool]:
    """用**真实消费方** ``resolve_is_trading_day`` 对整年逐日判定。

    ``_load_basic_trading_calendar`` 在 ``aqsp.core.time``（供 ``_is_basic_trading_day``）
    与 ``aqsp.data.trading_calendar``（供 ``resolve_is_trading_day`` 的硬覆盖与
    ``_static_calendar_covers_year``）**两处**各自解析，必须同时替换，否则
    回退路径会读到真实文件、让对比失真。
    """
    from aqsp.core import time as time_mod
    from aqsp.data import trading_calendar as tc

    monkeypatch.setattr(tc, "load_optional_trade_calendar", lambda *a, **k: None)
    monkeypatch.setattr(
        time_mod, "_load_basic_trading_calendar", lambda: frozenset(holidays)
    )
    monkeypatch.setattr(
        tc, "_load_basic_trading_calendar", lambda: frozenset(holidays)
    )
    return {day: tc.resolve_is_trading_day(day) for day in _iter_year(year)}


def test_regenerated_calendar_preserves_trading_day_verdicts(monkeypatch):
    """🔴 生成口径与手写文件对 2026 全年**判定逐日一致**。

    做法：把真实文件里的 2026 条目当作「交易所给出的休市日全集」，再并上
    全部周末（交易所周末一律休市），喂给 :func:`holidays_from_days`；
    然后让真实消费方对全年 365 天判定，与手写文件的结果逐日比对。

    这条守卫只在「生成器改变了判定」时失败 —— 例如它误删了某个工作日休市日
    （该日会被判成交易日）或误留了周末条目。丢周末本身不改变判定。
    """
    year = 2026
    real_year = _real_config_2026()
    assert real_year, "前置断言：真实文件必须覆盖 2026（否则本守卫空转）"

    exchange_truth = real_year | {
        day for day in _iter_year(year) if day.weekday() >= 5
    }
    generated = set(
        th.holidays_from_days(
            [SzseCalendarDay(trade_date=day, is_open=False) for day in exchange_truth],
            year=year,
        )
    )

    dropped = exchange_truth - generated
    assert dropped, "前置断言：生成口径应当丢掉周末条目（否则本守卫空转）"
    weekday_dropped = sorted(day for day in dropped if day.weekday() < 5)
    assert weekday_dropped == [], (
        f"生成口径只允许丢周末条目，实际丢了 {len(weekday_dropped)} 个工作日休市日："
        f"{[day.isoformat() for day in weekday_dropped]}"
    )

    baseline = _verdicts(monkeypatch, real_year, year)
    regenerated = _verdicts(monkeypatch, generated, year)

    # 防「两侧都坏掉」的空转：基线必须真的区分交易日与休市日。
    assert baseline[date(2026, 10, 1)] is False  # 国庆，工作日休市
    assert baseline[date(2026, 10, 3)] is False  # 周六
    assert baseline[date(2026, 10, 8)] is True  # 节后首个交易日（周四）
    assert 0 < sum(baseline.values()) < len(baseline)

    mismatches = sorted(
        day.isoformat() for day in baseline if baseline[day] != regenerated[day]
    )
    assert mismatches == [], f"生成口径改变了以下日期的交易日判定：{mismatches}"


def test_real_config_legacy_entries_are_exactly_the_weekend_noise():
    """真实文件的 2026 条目里，落周末的**不会超出**那 4 个已知噪声条目。

    用 ``<=`` 而不是 ``==``：文件若按新口径重新生成，周末条目会归零，
    本断言仍成立（手写时代的 4 个噪声条目见 ``LEGACY_WEEKEND_ENTRIES``）。
    真正要拦的是「有人又手写进一个周末休市日」。
    """
    real_year = _real_config_2026()
    weekend_entries = {day for day in real_year if day.weekday() >= 5}
    assert all(day.weekday() >= 5 for day in LEGACY_WEEKEND_ENTRIES)
    assert weekend_entries <= set(LEGACY_WEEKEND_ENTRIES)


def test_real_config_weekday_entries_are_all_preserved_by_generator():
    """手写文件里的**工作日**休市日必须一条不落地被生成口径保留。"""
    real_year = _real_config_2026()
    weekday_entries = {day for day in real_year if day.weekday() < 5}
    generated = set(
        th.holidays_from_days(
            [SzseCalendarDay(trade_date=day, is_open=False) for day in real_year],
            year=2026,
        )
    )
    assert weekday_entries == generated


def test_real_config_is_readable_by_runtime_loader():
    from aqsp.core.time import _load_basic_trading_calendar

    holidays = _load_basic_trading_calendar()
    assert holidays
    assert all(isinstance(day, date) for day in holidays)
    assert _real_config_2026() <= set(holidays)


# ---------------------------------------------------------------------------
# 与 check_before_live 覆盖门的口径一致性（issue #392）
# ---------------------------------------------------------------------------


def test_real_config_satisfies_check_before_live_coverage_gate():
    """真实 config 必须满足 `check_before_live` 的交易日历覆盖门。

    🔴 本守卫钉住 issue #392 的口径一致性：生成侧口径是「只写工作日休市日」，
    而覆盖门的 `critical_holidays` 仍硬编码要求 3 个**周末**日期（2026-02-15 /
    02-21 / 02-22）在名单里。两者目前靠「不对已覆盖年份落盘」共存 ——
    若有人对 2026 执行 `--write`，本用例会立刻变红（见下一个用例的证据），
    而不是让覆盖门在生产里静默打红。
    """
    from scripts.monitor.check_before_live import _check_trading_calendar_coverage

    finding = _check_trading_calendar_coverage(REPO_ROOT, date(2026, 10, 16))
    assert finding.ok is True, finding.detail


def test_coverage_gate_would_reject_the_pruned_calendar(tmp_path):
    """反向验证（issue #392 的证据）：按生成口径剪掉周末条目的日历**会**被覆盖门拒绝。

    ⚠️ #392 若按「方案 A」（覆盖门只校验工作日）收口，本用例会失败 ——
    那时请连同 `_check_trading_calendar_coverage.critical_holidays` 一起改掉。
    """
    from scripts.monitor.check_before_live import _check_trading_calendar_coverage

    pruned = [day for day in _real_config_2026() if day.weekday() < 5]
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "trading_holidays.json").write_text(
        th.render_holiday_config(pruned), encoding="utf-8"
    )
    finding = _check_trading_calendar_coverage(tmp_path, date(2026, 10, 16))
    assert finding.ok is False
    assert "2026-02-15" in finding.detail


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_resolve_config_path_anchors_to_repo_root():
    """默认路径必须落在仓库根的 ``config/``（守卫 ``parents[N]`` 落点漂移）。"""
    assert _resolve_config_path(None) == REPO_ROOT / "config" / "trading_holidays.json"


def test_resolve_config_path_honours_explicit_override(tmp_path):
    target = tmp_path / "custom.json"
    assert _resolve_config_path(target) == target


def _run_cli(tmp_path, monkeypatch, *, closed=None, argv_extra=()):
    config = tmp_path / "trading_holidays.json"
    _patch_fetch_months(monkeypatch, _month_mapping(2026, closed=closed))
    code = main(["--year", "2026", "--config", str(config), *argv_extra])
    return code, config


def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    code, config = _run_cli(tmp_path, monkeypatch, closed={date(2026, 10, 1)})
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert config.exists() is False
    assert "dry-run" in out
    assert "2026-10-01" in out


def test_cli_write_merges_target_year_and_is_idempotent(tmp_path, monkeypatch, capsys):
    config = tmp_path / "trading_holidays.json"
    config.write_text(
        th.render_holiday_config([date(2025, 10, 1), date(2026, 10, 3)]),
        encoding="utf-8",
    )
    _patch_fetch_months(
        monkeypatch, _month_mapping(2026, closed={date(2026, 10, 1)})
    )

    assert main(["--year", "2026", "--config", str(config), "--write"]) == EXIT_OK
    first = config.read_text(encoding="utf-8")
    capsys.readouterr()
    # 2025 保留、2026 替换（10-03 这个周末噪声被清掉）
    assert th.parse_holiday_config(first) == (date(2025, 10, 1), date(2026, 10, 1))

    assert main(["--year", "2026", "--config", str(config), "--write"]) == EXIT_OK
    out = capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == first
    assert "no-op" in out


def test_cli_refuses_to_write_when_a_month_is_unavailable(tmp_path, monkeypatch, capsys):
    config = tmp_path / "trading_holidays.json"
    config.write_text(
        th.render_holiday_config([date(2025, 10, 1)]), encoding="utf-8"
    )
    before = config.read_text(encoding="utf-8")
    mapping = _month_mapping(2026)
    mapping["2026-11"] = None
    _patch_fetch_months(monkeypatch, mapping)

    code = main(["--year", "2026", "--config", str(config), "--write"])
    captured = capsys.readouterr()
    assert code == EXIT_INCOMPLETE
    assert config.read_text(encoding="utf-8") == before
    assert "2026-11" in captured.err
    assert "不落盘" in captured.err


def test_cli_json_reports_shape(tmp_path, monkeypatch, capsys):
    code, _ = _run_cli(
        tmp_path, monkeypatch, closed={date(2026, 10, 1)}, argv_extra=("--json",)
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["year"] == 2026
    assert payload["complete"] is True
    assert payload["written"] is False
    assert payload["holidays"] == ["2026-10-01"]
    assert payload["config_path"].endswith("trading_holidays.json")


def test_cli_json_write_emits_exactly_one_document(tmp_path, monkeypatch, capsys):
    """``--json --write`` 只能输出**一个** JSON 文档，否则管道解析会炸。"""
    config = tmp_path / "trading_holidays.json"
    _patch_fetch_months(monkeypatch, _month_mapping(2026, closed={date(2026, 10, 1)}))
    code = main(["--year", "2026", "--config", str(config), "--json", "--write"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    payload = json.loads(out)  # 多余文档会在这里失败
    assert payload["written"] is True
    assert config.exists() is True


def test_cli_defaults_to_next_year(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(th, "next_year", lambda today=None: 2027)
    _patch_fetch_months(monkeypatch, _month_mapping(2027, closed={date(2027, 1, 1)}))
    config = tmp_path / "trading_holidays.json"
    assert main(["--config", str(config), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["year"] == 2027


def test_cli_returns_error_code_on_io_failure(tmp_path, monkeypatch, capsys):
    _patch_fetch_months(monkeypatch, _month_mapping(2026))

    def _boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(th, "build_holiday_report", _boom)
    code = main(["--year", "2026", "--config", str(tmp_path / "x.json")])
    assert code == EXIT_ERROR
    assert "IO 错误" in capsys.readouterr().err
