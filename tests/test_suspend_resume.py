"""停复牌 fetcher 测试（全离线：只喂合成 payload，不触网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.data.suspend_resume import (
    SuspendResumeItem,
    SuspendResumeSource,
    _days_between,
    _norm_date,
    _parse_items,
    _to_float,
)

_VALID_ROW = {
    "SECURITY_CODE": "002860",
    "SECURITY_NAME_ABBR": "星帅尔",
    "SUSPEND_START_TIME": "2026-09-22 09:30:00",
    "SUSPEND_END_TIME": "2026-10-13 15:00:00",
    "SUSPEND_EXPIRE": "连续停牌",
    "SUSPEND_REASON": "刊登重要公告",
    "TRADE_MARKET": "深交所主板",
    "SUSPEND_START_DATE": "2026-09-22 00:00:00",
    "PREDICT_RESUME_DATE": "2026-10-14 00:00:00",
    "SECUCODE": "002860.SZ",
}


def test_parse_real_shape():
    items = _parse_items({"result": {"data": [dict(_VALID_ROW)]}})
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "002860"
    assert it.name == "星帅尔"
    assert it.suspend_date == "2026-09-22"
    assert it.resume_date == "2026-10-14"
    assert it.suspend_days == pytest.approx(22.0)
    assert it.suspend_type == "连续停牌"
    assert it.reason == "刊登重要公告"


def test_parse_result_as_list():
    items = _parse_items({"result": [dict(_VALID_ROW)]})
    assert len(items) == 1
    assert items[0].symbol == "002860"


def test_parse_payload_data_fallback_legacy_keys():
    payload = {
        "data": [
            {
                "symbol": "000001",
                "name": "平安银行",
                "suspend_date": "2026-01-05",
                "resume_date": "2026-01-09",
                "suspend_type": "停牌1天",
                "reason": "重大资产重组",
            }
        ]
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "000001"
    assert it.suspend_date == "2026-01-05"
    assert it.suspend_days == pytest.approx(4.0)
    assert it.suspend_type == "停牌1天"


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
    assert items[0].symbol == "002860"


@pytest.mark.parametrize("payload", [None, {}, {"result": None}, {"result": {}}, []])
def test_parse_empty_payloads(payload):
    assert _parse_items(payload) == []


def test_parse_missing_dates_degrade_gracefully():
    items = _parse_items({"result": {"data": [{"SECURITY_CODE": "600000"}]}})
    assert len(items) == 1
    it = items[0]
    assert it.suspend_date == ""
    assert it.resume_date == ""
    assert it.suspend_days == 0.0


def test_to_float_abnormal_inputs():
    assert _to_float(None) == 0.0
    assert _to_float("") == 0.0
    assert _to_float("abc") == 0.0
    assert _to_float(object()) == 0.0
    assert _to_float("3.5") == pytest.approx(3.5)
    assert _to_float(7) == pytest.approx(7.0)


def test_norm_date_and_days_between():
    assert _norm_date(None) == ""
    assert _norm_date("") == ""
    assert _norm_date("2026-09-22 00:00:00") == "2026-09-22"
    assert _norm_date("2026-09-22") == "2026-09-22"
    assert _days_between("2026-09-22", "2026-10-14") == pytest.approx(22.0)
    assert _days_between("2026-10-14", "2026-09-22") == 0.0
    assert _days_between("", "2026-10-14") == 0.0
    assert _days_between("bad", "worse") == 0.0


def test_source_from_items_and_items_without_autoload():
    src = SuspendResumeSource().from_items(
        [
            SuspendResumeItem(
                symbol="000001",
                name="x",
                suspend_date="2026-01-01",
                resume_date="2026-01-05",
                suspend_days=4.0,
                suspend_type="连续停牌",
                reason="r",
            )
        ]
    )
    got = src.items(autoload=False)
    assert len(got) == 1
    assert got[0].symbol == "000001"
    got.clear()
    assert len(src.items(autoload=False)) == 1


def test_items_without_autoload_on_empty_source():
    assert SuspendResumeSource().items(autoload=False) == []


def test_default_cache_path_follows_runtime_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    path = SuspendResumeSource()._default_cache_path()
    assert path == str(tmp_path / "pit_cache" / "suspend_resume.csv")


def test_explicit_cache_path_wins(tmp_path):
    explicit = tmp_path / "custom.csv"
    assert SuspendResumeSource(cache_path=str(explicit))._default_cache_path() == str(
        explicit
    )


def test_load_reads_existing_cache_without_network(tmp_path):
    cache = tmp_path / "suspend_resume.csv"
    pd.DataFrame(
        [
            {
                "symbol": "002860",
                "name": "星帅尔",
                "suspend_date": "2026-09-22",
                "resume_date": "2026-10-14",
                "suspend_days": 22.0,
                "suspend_type": "连续停牌",
                "reason": "刊登重要公告",
            }
        ]
    ).to_csv(cache, index=False)
    src = SuspendResumeSource(cache_path=str(cache))
    items = src.load()
    assert len(items) == 1
    assert items[0].symbol == "002860"  # 前导零必须保留（否则会退化成 286）
    assert items[0].suspend_days == pytest.approx(22.0)


def test_load_with_corrupt_cache_falls_back_to_fetch(tmp_path, monkeypatch):
    cache = tmp_path / "suspend_resume.csv"
    cache.write_text("", encoding="utf-8")  # 空文件 → read_csv 抛错
    src = SuspendResumeSource(cache_path=str(cache))
    monkeypatch.setattr(src, "_fetch", lambda query_date="": [])
    assert src.load() == []
