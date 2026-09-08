"""申万行业 point-in-time 测试。

核心验证「前视偏差消除」：同一只票在不同时点返回不同历史行业，
上市前的时点返回 None。覆盖 _normalize、IndustryPitSource.from_dataframe、
_industry_as_of 纯函数。
"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.industry_pit import (
    IndustryPitSource,
    _normalize,
    industry_as_of,
)


def _raw_sw() -> pd.DataFrame:
    """模拟申万原始表（中文列名），平安银行三次行业调整。"""
    return pd.DataFrame(
        [
            ["1", "000001", "1991-04-03", "440101", "2099-12-31"],
            ["1", "000001", "2014-02-21", "480101", "2099-12-31"],
            ["1", "000001", "2021-07-30", "480301", "2099-12-31"],
        ],
        columns=["序号", "股票代码", "计入日期", "行业代码", "更新日期"],
    )


def _normalized() -> pd.DataFrame:
    return _normalize(_raw_sw())


def test_normalize_zero_pads_and_builds_l1_l2():
    df = _normalized()
    assert (df["code"] == "000001").all()
    assert (df["industry_code"] == "440101").any()
    # 一级 480000、二级 480300（来自 480301）
    row = df[df["industry_code"] == "480301"].iloc[0]
    assert row["l1_code"] == "480000"
    assert row["l2_code"] == "480300"


def test_normalize_raises_when_schema_changed():
    bad = pd.DataFrame({"foo": [1]})
    with pytest.raises(DataError):
        _normalize(bad)


def test_industry_as_of_point_in_time():
    df = _normalized()
    # 平安银行历史行业变迁（上游实测）
    assert industry_as_of(df, "000001", "2013-01-01").industry_code == "440101"
    assert industry_as_of(df, "000001", "2016-01-01").industry_code == "480101"
    assert industry_as_of(df, "000001", "2026-08-18").industry_code == "480301"


def test_industry_as_of_none_before_listing():
    df = _normalized()
    # 首次行业调整 start_date=1991-04-03，早于该日的时点视为尚未上市
    assert industry_as_of(df, "000001", "1990-01-01") is None


def test_industry_as_of_picks_latest_not_later_than_as_of():
    df = _normalized()
    # 2014-02-21 之后的第一次调整为 480101，2015 年仍应属 480101 而非更早
    assert industry_as_of(df, "000001", "2015-06-01").industry_code == "480101"


def test_source_from_dataframe_query():
    src = IndustryPitSource().from_dataframe(_normalized())
    label = src.industry_as_of("000001", "2026-08-18", autoload=False)
    assert label is not None
    assert label.industry_code == "480301"
    assert label.l1_code == "480000"


def test_source_autoload_raises_if_disabled_and_unloaded():
    src = IndustryPitSource()
    with pytest.raises(DataError):
        src.industry_as_of("000001", "2026-08-18", autoload=False)
