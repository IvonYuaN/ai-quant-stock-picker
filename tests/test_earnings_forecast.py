"""业绩预告 fetcher 测试（全离线：只喂合成 payload，不触网）。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aqsp.data.earnings_forecast import (
    EarningsForecastItem,
    EarningsForecastSource,
    _default_report_period,
    _norm_date,
    _parse_items,
    _to_float,
)

_VALID_ROW = {
    "SECUCODE": "600187.SH",
    "SECURITY_CODE": "600187",
    "SECURITY_NAME_ABBR": "*ST国中",
    "NOTICE_DATE": "2026-08-19 00:00:00",
    "REPORT_DATE": "2026-06-30 00:00:00",
    "PREDICT_FINANCE": "归属于上市公司股东的净利润",
    "PREDICT_AMT_LOWER": 2650000,
    "PREDICT_AMT_UPPER": 3150000,
    "ADD_AMP_LOWER": 114.47,
    "ADD_AMP_UPPER": 117.19,
    "PREDICT_CONTENT": "预计2026年1-6月归属于上市公司股东的净利润盈利:265万元至315万元。",
    "CHANGE_REASON_EXPLAIN": "投资收益影响。",
    "PREDICT_TYPE": "扭亏",
    "PREYEAR_SAME_PERIOD": -18320000,
    "IS_LATEST": "T",
}


def test_parse_real_shape_with_unit_conversion():
    items = _parse_items({"result": {"data": [dict(_VALID_ROW)]}})
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "600187"
    assert it.name == "*ST国中"
    assert it.notice_date == "2026-08-19"
    assert it.report_date == "2026-06-30"
    assert it.forecast_type == "扭亏"
    # 东财原始单位为「元」，模块统一换算为「万元」
    assert it.forecast_amt_lower == pytest.approx(265.0)
    assert it.forecast_amt_upper == pytest.approx(315.0)
    assert it.change_pct_lower == pytest.approx(114.47)
    assert it.change_pct_upper == pytest.approx(117.19)
    assert "投资收益" in it.reason


def test_parse_result_as_list():
    items = _parse_items({"result": [dict(_VALID_ROW)]})
    assert len(items) == 1
    assert items[0].forecast_amt_lower == pytest.approx(265.0)


def test_parse_payload_data_fallback_legacy_keys():
    payload = {
        "data": [
            {
                "symbol": "000001",
                "name": "平安银行",
                "notice_date": "2026-01-10",
                "report_date": "2025-12-31",
                "forecast_type": "预增",
                "forecast_amt_lower": 1000000.0,
                "forecast_amt_upper": 2000000.0,
                "change_pct_lower": 10.0,
                "change_pct_upper": 20.0,
                "reason": "主营增长",
            }
        ]
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "000001"
    # 兜底键走同一条换算路径（「元」→「万元」），不因键名不同而改写单位
    assert it.forecast_amt_lower == pytest.approx(100.0)
    assert it.forecast_amt_upper == pytest.approx(200.0)
    assert it.change_pct_upper == pytest.approx(20.0)
    assert it.reason == "主营增长"


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
    assert items[0].symbol == "600187"


@pytest.mark.parametrize("payload", [None, {}, {"result": None}, {"result": {}}, []])
def test_parse_empty_payloads(payload):
    assert _parse_items(payload) == []


def test_parse_missing_optional_fields_degrade_gracefully():
    items = _parse_items({"result": {"data": [{"SECURITY_CODE": "600000"}]}})
    assert len(items) == 1
    it = items[0]
    assert it.notice_date == ""
    assert it.forecast_type == ""
    assert it.forecast_amt_lower == 0.0
    assert it.reason == ""


def test_parse_null_amounts_become_zero():
    items = _parse_items(
        {
            "result": {
                "data": [
                    {
                        "SECURITY_CODE": "600000",
                        "PREDICT_AMT_LOWER": None,
                        "PREDICT_AMT_UPPER": None,
                        "ADD_AMP_LOWER": None,
                        "ADD_AMP_UPPER": None,
                    }
                ]
            }
        }
    )
    assert items[0].forecast_amt_upper == 0.0
    assert items[0].change_pct_lower == 0.0


def test_to_float_abnormal_inputs():
    assert _to_float(None) == 0.0
    assert _to_float("") == 0.0
    assert _to_float("abc") == 0.0
    assert _to_float(object()) == 0.0
    assert _to_float("12.5") == pytest.approx(12.5)
    assert _to_float(3) == pytest.approx(3.0)


def test_norm_date():
    assert _norm_date(None) == ""
    assert _norm_date("") == ""
    assert _norm_date("2026-08-19 00:00:00") == "2026-08-19"
    assert _norm_date("2026-06-30") == "2026-06-30"


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
    src = EarningsForecastSource().from_items(
        [
            EarningsForecastItem(
                symbol="600187",
                name="x",
                notice_date="2026-08-19",
                report_date="2026-06-30",
                forecast_type="扭亏",
                forecast_amt_lower=265.0,
                forecast_amt_upper=315.0,
                change_pct_lower=114.47,
                change_pct_upper=117.19,
                reason="r",
            )
        ]
    )
    got = src.items(autoload=False)
    assert len(got) == 1
    assert got[0].symbol == "600187"
    got.clear()
    assert len(src.items(autoload=False)) == 1


def test_items_without_autoload_on_empty_source():
    assert EarningsForecastSource().items(autoload=False) == []


def test_default_cache_path_follows_runtime_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    path = EarningsForecastSource()._default_cache_path()
    assert path == str(tmp_path / "pit_cache" / "earnings_forecast.csv")


def test_explicit_cache_path_wins(tmp_path):
    explicit = tmp_path / "custom.csv"
    assert EarningsForecastSource(
        cache_path=str(explicit)
    )._default_cache_path() == str(explicit)


def test_load_reads_existing_cache_without_network(tmp_path):
    cache = tmp_path / "earnings_forecast.csv"
    pd.DataFrame(
        [
            {
                "symbol": "600187",
                "name": "*ST国中",
                "notice_date": "2026-08-19",
                "report_date": "2026-06-30",
                "forecast_type": "扭亏",
                "forecast_amt_lower": 265.0,
                "forecast_amt_upper": 315.0,
                "change_pct_lower": 114.47,
                "change_pct_upper": 117.19,
                "reason": "投资收益影响。",
            }
        ]
    ).to_csv(cache, index=False)
    src = EarningsForecastSource(cache_path=str(cache))
    items = src.load()
    assert len(items) == 1
    assert items[0].symbol == "600187"
    assert items[0].forecast_amt_lower == pytest.approx(265.0)


def test_load_with_corrupt_cache_falls_back_to_fetch(tmp_path, monkeypatch):
    cache = tmp_path / "earnings_forecast.csv"
    cache.write_text("", encoding="utf-8")  # 空文件 → read_csv 抛错
    src = EarningsForecastSource(cache_path=str(cache))
    monkeypatch.setattr(src, "_fetch", lambda report_date="": [])
    assert src.load() == []
