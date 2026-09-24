"""分红送转 fetcher 测试（全离线：只喂合成 payload / 假 requests，不触网）。"""

from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from aqsp.data.dividend_plan import (
    _PAGE_SIZE,
    DividendPlanItem,
    DividendPlanSource,
    _default_report_period,
    _is_truncated,
    _norm_date,
    _parse_items,
    _to_float,
)

_VALID_ROW = {
    "SECUCODE": "300789.SZ",
    "SECURITY_NAME_ABBR": "唐源电气",
    "SECURITY_CODE": "300789",
    "BONUS_IT_RATIO": 5.0,
    "BONUS_RATIO": 3.0,
    "IT_RATIO": 2.0,
    "PRETAX_BONUS_RMB": 1.3,
    "PLAN_NOTICE_DATE": "2026-06-30 00:00:00",
    "EQUITY_RECORD_DATE": "2026-07-28 00:00:00",
    "EX_DIVIDEND_DATE": "2026-07-29 00:00:00",
    "REPORT_DATE": "2025-12-31 00:00:00",
    "ASSIGN_PROGRESS": "实施分配",
    "IMPL_PLAN_PROFILE": "10派1.30元(含税,扣税后1.17元)",
    "NOTICE_DATE": "2026-07-21 00:00:00",
}


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _install_fake_requests(monkeypatch, payload, captured: dict) -> None:
    """把假 requests 装进 sys.modules —— _fetch 内的 import 会拿到它（零网络）。"""
    fake = types.ModuleType("requests")

    def _get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(payload)

    fake.get = _get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", fake)


def test_parse_real_shape():
    items = _parse_items({"result": {"data": [dict(_VALID_ROW)]}})
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "300789"
    assert it.name == "唐源电气"
    assert it.report_date == "2025-12-31"
    assert it.plan_notice_date == "2026-06-30"
    assert it.ex_dividend_date == "2026-07-29"
    assert it.bonus_ratio == pytest.approx(5.0)
    assert it.cash_per_10 == pytest.approx(1.3)
    assert it.progress == "实施分配"


def test_parse_keeps_future_ex_dividend_date():
    row = dict(_VALID_ROW, EX_DIVIDEND_DATE="2099-01-01 00:00:00")
    items = _parse_items({"result": {"data": [row]}})
    # 除权除息日可能是未来日期（预案未实施），必须原样保留供日历使用
    assert items[0].ex_dividend_date == "2099-01-01"


def test_parse_null_bonus_ratio_becomes_zero():
    row = dict(_VALID_ROW, BONUS_IT_RATIO=None, BONUS_RATIO=None, IT_RATIO=None)
    items = _parse_items({"result": {"data": [row]}})
    assert len(items) == 1
    assert items[0].bonus_ratio == 0.0
    assert items[0].cash_per_10 == pytest.approx(1.3)


def test_parse_result_as_list():
    items = _parse_items({"result": [dict(_VALID_ROW)]})
    assert len(items) == 1
    assert items[0].cash_per_10 == pytest.approx(1.3)


def test_parse_payload_data_fallback_legacy_keys():
    payload = {
        "data": [
            {
                "symbol": "000001",
                "name": "平安银行",
                "report_date": "2025-12-31",
                "plan_notice_date": "2026-03-20",
                "ex_dividend_date": "2026-05-15",
                "bonus_ratio": 0.0,
                "cash_per_10": 2.5,
                "progress": "股东大会通过",
            }
        ]
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "000001"
    assert it.cash_per_10 == pytest.approx(2.5)
    assert it.progress == "股东大会通过"


def test_parse_skips_dirty_keeps_valid():
    payload = {
        "result": {
            "data": [
                "not-a-dict",
                {"x": 1},
                {"SECURITY_NAME_ABBR": "无代码"},
                dict(_VALID_ROW),
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    assert items[0].symbol == "300789"


@pytest.mark.parametrize("payload", [None, {}, {"result": None}, {"result": {}}, []])
def test_parse_empty_payloads(payload):
    assert _parse_items(payload) == []


def test_parse_missing_optional_fields_degrade_gracefully():
    items = _parse_items({"result": {"data": [{"SECURITY_CODE": "600000"}]}})
    assert len(items) == 1
    it = items[0]
    assert it.report_date == ""
    assert it.plan_notice_date == ""
    assert it.ex_dividend_date == ""
    assert it.bonus_ratio == 0.0
    assert it.cash_per_10 == 0.0
    assert it.progress == ""


def test_to_float_abnormal_inputs():
    assert _to_float(None) == 0.0
    assert _to_float("") == 0.0
    assert _to_float("abc") == 0.0
    assert _to_float(object()) == 0.0
    assert _to_float("1.30") == pytest.approx(1.3)
    assert _to_float(5) == pytest.approx(5.0)


def test_norm_date():
    assert _norm_date(None) == ""
    assert _norm_date("") == ""
    assert _norm_date("2026-07-29 00:00:00") == "2026-07-29"
    assert _norm_date("2026-07-29") == "2026-07-29"


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 1, 15), "2025-12-31"),
        (date(2026, 5, 1), "2026-03-31"),
        (date(2026, 8, 31), "2026-03-31"),
        (date(2026, 9, 1), "2026-06-30"),
        (date(2026, 11, 1), "2026-09-30"),
        (date(2026, 12, 31), "2026-09-30"),
    ],
)
def test_default_report_period(today, expected):
    assert _default_report_period(today) == expected


def test_source_from_items_and_items_without_autoload():
    src = DividendPlanSource().from_items(
        [
            DividendPlanItem(
                symbol="300789",
                name="x",
                report_date="2025-12-31",
                plan_notice_date="2026-06-30",
                ex_dividend_date="2026-07-29",
                bonus_ratio=5.0,
                cash_per_10=1.3,
                progress="实施分配",
            )
        ]
    )
    got = src.items(autoload=False)
    assert len(got) == 1
    assert got[0].symbol == "300789"
    got.clear()
    assert len(src.items(autoload=False)) == 1


def test_items_without_autoload_on_empty_source():
    assert DividendPlanSource().items(autoload=False) == []


def test_default_cache_path_follows_runtime_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    path = DividendPlanSource()._default_cache_path()
    assert path == str(tmp_path / "pit_cache" / "dividend_plan.csv")


def test_explicit_cache_path_wins(tmp_path):
    explicit = tmp_path / "custom.csv"
    assert DividendPlanSource(cache_path=str(explicit))._default_cache_path() == str(
        explicit
    )


def test_load_reads_existing_cache_without_network(tmp_path):
    cache = tmp_path / "dividend_plan.csv"
    pd.DataFrame(
        [
            {
                "symbol": "300789",
                "name": "唐源电气",
                "report_date": "2025-12-31",
                "plan_notice_date": "2026-06-30",
                "ex_dividend_date": "2026-07-29",
                "bonus_ratio": 5.0,
                "cash_per_10": 1.3,
                "progress": "实施分配",
            }
        ]
    ).to_csv(cache, index=False)
    src = DividendPlanSource(cache_path=str(cache))
    items = src.load()
    assert len(items) == 1
    assert items[0].symbol == "300789"
    assert items[0].cash_per_10 == pytest.approx(1.3)


def test_load_with_corrupt_cache_falls_back_to_fetch(tmp_path, monkeypatch):
    cache = tmp_path / "dividend_plan.csv"
    cache.write_text("", encoding="utf-8")  # 空文件 → read_csv 抛错
    src = DividendPlanSource(cache_path=str(cache))
    monkeypatch.setattr(src, "_fetch", lambda report_date="", ex_dividend_from="": [])
    assert src.load() == []


@pytest.mark.parametrize(
    "count,expected", [(0, False), (1, False), (499, False), (500, True), (501, True)]
)
def test_is_truncated_boundaries(count, expected):
    assert _is_truncated(count, _PAGE_SIZE) is expected


def test_fetch_appends_ex_dividend_from_to_filter(monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, {"result": {"data": [dict(_VALID_ROW)]}}, captured
    )
    src = DividendPlanSource()
    items = src._fetch(report_date="2025-12-31", ex_dividend_from="2026-09-22")
    assert len(items) == 1
    assert captured["params"]["filter"] == (
        "(REPORT_DATE='2025-12-31')(EX_DIVIDEND_DATE>='2026-09-22')"
    )
    assert captured["params"]["pageSize"] == str(_PAGE_SIZE)
    assert src.truncated is False


def test_fetch_without_ex_dividend_from_keeps_single_clause_filter(monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, {"result": {"data": [dict(_VALID_ROW)]}}, captured
    )
    DividendPlanSource()._fetch(report_date="2025-12-31")
    assert captured["params"]["filter"] == "(REPORT_DATE='2025-12-31')"


def test_fetch_sets_truncated_and_warns_on_full_page(monkeypatch, caplog):
    full_page = [dict(_VALID_ROW, SECURITY_CODE=f"{i:06d}") for i in range(_PAGE_SIZE)]
    captured: dict = {}
    _install_fake_requests(monkeypatch, {"result": {"data": full_page}}, captured)
    src = DividendPlanSource()
    with caplog.at_level("WARNING", logger="aqsp.data.dividend_plan"):
        items = src._fetch(report_date="2025-12-31")
    assert len(items) == _PAGE_SIZE
    assert src.truncated is True
    assert any("截断" in r.message for r in caplog.records)


def test_fetch_not_truncated_below_page_size(monkeypatch, caplog):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch,
        {"result": {"data": [_VALID_ROW] * (_PAGE_SIZE - 1)}},
        captured,
    )
    src = DividendPlanSource()
    with caplog.at_level("WARNING", logger="aqsp.data.dividend_plan"):
        src._fetch(report_date="2025-12-31")
    assert src.truncated is False
    assert not [r for r in caplog.records if "截断" in r.message]


def test_truncated_resets_on_next_fetch(monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch,
        {"result": {"data": [_VALID_ROW] * _PAGE_SIZE}},
        captured,
    )
    src = DividendPlanSource()
    src._fetch(report_date="2025-12-31")
    assert src.truncated is True
    _install_fake_requests(
        monkeypatch, {"result": {"data": [dict(_VALID_ROW)]}}, captured
    )
    src._fetch(report_date="2025-12-31", ex_dividend_from="2026-09-22")
    assert src.truncated is False
