"""估值历史采集（东财 RPT_VALUEANALYSIS_DET）—— 行为级单测。

覆盖三条红线：
1. **代码前导零**（"000001" 不能变 1，否则消费方静默匹配失败）；
2. **分页必须取完**（页满即停 ⇒ 截面残缺 ⇒ 行业分位排序失真）；
3. **时点滞后**（当日收盘数据不得泄漏进当日盘中决策）。
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data import valuation_history as vh


def _row(code: str, day: str, **extra: object) -> dict:
    row = {
        "SECURITY_CODE": code,
        "SECURITY_NAME_ABBR": "测试",
        "TRADE_DATE": day,
        "PE_TTM": 10.0,
        "PB_MRQ": 1.5,
        "PS_TTM": 2.0,
        "TOTAL_MARKET_CAP": 1234.0,
    }
    row.update(extra)
    return row


def _payload(rows: list[dict], pages: int = 1) -> dict:
    return {"result": {"pages": pages, "count": len(rows), "data": rows}}


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def test_parse_rows_zero_pads_code_and_normalizes_date():
    payload = _payload(
        [
            _row("1", "2023-11-01 00:00:00"),
            _row("600000", "2023-11-01"),
        ]
    )
    rows = vh._parse_rows(payload)
    assert [r["SECURITY_CODE"] for r in rows] == ["000001", "600000"]
    assert rows[0]["TRADE_DATE"] == "2023-11-01"


def test_parse_rows_drops_rows_without_code_or_date():
    payload = _payload(
        [
            {"SECURITY_CODE": "", "TRADE_DATE": "2023-11-01"},
            {"SECURITY_CODE": "600000", "TRADE_DATE": "不是日期"},
            _row("600001", "2023-11-01"),
        ]
    )
    rows = vh._parse_rows(payload)
    assert [r["SECURITY_CODE"] for r in rows] == ["600001"]


def test_parse_rows_keeps_only_known_columns():
    rows = vh._parse_rows(_payload([_row("600000", "2023-11-01", EXTRA_JUNK="x")]))
    assert set(rows[0]) == set(vh.VALUATION_COLUMNS)
    assert "EXTRA_JUNK" not in rows[0]


# ---------------------------------------------------------------------------
# 分页
# ---------------------------------------------------------------------------
def test_fetch_date_rows_pages_until_exhausted(monkeypatch):
    """页满就停 ⇒ 截面残缺 ⇒ 行业内分位排序失真（必须取完全部页）。"""
    pages_seen: list[int] = []

    def _fake_page(params: dict[str, str], timeout: int):
        page = int(params["pageNumber"])
        pages_seen.append(page)
        if page == 1:
            return [_row(f"60{page:04d}", "2023-11-01")], 3
        if page == 2:
            return [_row(f"61{page:04d}", "2023-11-01")], 3
        return [_row(f"62{page:04d}", "2023-11-01")], 3

    monkeypatch.setattr(vh, "_fetch_page", _fake_page)
    rows = vh.fetch_date_rows("2023-11-01")
    assert pages_seen == [1, 2, 3]
    assert len(rows) == 3


def test_iter_date_batches_groups_by_trade_date(monkeypatch):
    def _fake_page(params: dict[str, str], timeout: int):
        page = int(params["pageNumber"])
        if page == 1:
            return (
                [_row("600000", "2023-11-01"), _row("600001", "2023-11-01")],
                2,
            )
        return [_row("600002", "2023-11-02")], 2

    monkeypatch.setattr(vh, "_fetch_page", _fake_page)
    batches = list(vh.iter_date_batches("2023-11-01", "2023-11-02"))
    assert [day for day, _ in batches] == ["2023-11-01", "2023-11-02"]
    assert len(batches[0][1]) == 2
    assert len(batches[1][1]) == 1


def test_iter_date_batches_rejects_bad_range():
    with pytest.raises(DataError):
        list(vh.iter_date_batches("2023-11-05", "2023-11-01"))
    with pytest.raises(DataError):
        list(vh.iter_date_batches("不是日期", "2023-11-01"))


# ---------------------------------------------------------------------------
# 缓存落盘（写读同源）
# ---------------------------------------------------------------------------
def test_sync_dates_appends_and_skips_cached(tmp_path, monkeypatch):
    cache = str(tmp_path / "valuation_history.csv")

    def _fake_fetch(day: str, **kwargs: object) -> list[dict]:
        return [_row("600000", day)]

    monkeypatch.setattr(vh, "fetch_date_rows", _fake_fetch)
    src = vh.ValuationHistorySource(cache_path=cache)

    assert src.sync_dates(["2023-11-01", "2023-11-02"]) == 2
    # 已缓存的日期不重复采、不重复写
    assert src.sync_dates(["2023-11-01"]) == 0
    assert src.cached_dates() == {"2023-11-01", "2023-11-02"}

    df = pd.read_csv(cache, dtype={"SECURITY_CODE": str})
    assert len(df) == 2
    assert list(df.columns) == list(vh.VALUATION_COLUMNS)
    assert df["SECURITY_CODE"].tolist() == ["600000", "600000"]


def test_sync_dates_skips_invalid_dates(tmp_path, monkeypatch):
    cache = str(tmp_path / "v.csv")
    monkeypatch.setattr(
        vh, "fetch_date_rows", lambda day, **kw: pytest.fail("不该被调用")
    )
    src = vh.ValuationHistorySource(cache_path=cache)
    assert src.sync_dates(["", "不是日期"]) == 0


def test_default_cache_path_under_runtime_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(vh, "runtime_data_root", lambda: str(tmp_path))
    src = vh.ValuationHistorySource()
    expected = os.path.join(str(tmp_path), "pit_cache", "valuation_history.csv")
    assert src._default_cache_path() == expected


# ---------------------------------------------------------------------------
# 时点查询（滞后红线）
# ---------------------------------------------------------------------------
def _write_cache(path: str, days: list[str]) -> None:
    rows = []
    for day in days:
        for code in ("600000", "000001"):
            rows.append(_row(code, day))
    pd.DataFrame(rows).to_csv(path, index=False)


def test_snapshot_asof_respects_trading_day_lag(tmp_path):
    cache = str(tmp_path / "v.csv")
    _write_cache(cache, ["2023-11-01", "2023-11-02", "2023-11-03"])
    src = vh.ValuationHistorySource(cache_path=cache)

    # 滞后 0 ⇒ 当日截面
    same_day = src.snapshot_asof("2023-11-03", lag_trading_days=0)
    assert set(same_day["TRADE_DATE"]) == {"2023-11-03"}

    # 滞后 1 ⇒ 退到上一个已采集交易日（当日收盘数据不得泄漏进当日盘中决策）
    lagged = src.snapshot_asof("2023-11-03", lag_trading_days=1)
    assert set(lagged["TRADE_DATE"]) == {"2023-11-02"}

    # as_of 早于所有数据 ⇒ 空
    assert src.snapshot_asof("2023-10-01").empty


def test_snapshot_asof_never_leaks_future_dates(tmp_path):
    cache = str(tmp_path / "v.csv")
    _write_cache(cache, ["2023-11-01", "2023-11-02"])
    src = vh.ValuationHistorySource(cache_path=cache)
    snap = src.snapshot_asof("2023-11-01", lag_trading_days=0)
    assert set(snap["TRADE_DATE"]) == {"2023-11-01"}
