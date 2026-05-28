from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from aqsp.filters_lethal import (
    AnnouncementKeywordFilter,
    FilterResult,
    HolderCountFilter,
    LethalFilter,
    LethalFilterPipeline,
    LockupReleaseFilter,
)

random.seed(42)
np.random.seed(42)


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "close", "volume"])


def test_filter_result_is_frozen():
    r = FilterResult(symbol="600000", passed=True, reason="ok", filter_name="test")
    with pytest.raises(AttributeError):
        r.symbol = "other"  # type: ignore[misc]


def test_hypothesis_non_empty_for_all_filters():
    filters: list[LethalFilter] = [
        LockupReleaseFilter(),
        HolderCountFilter(),
        AnnouncementKeywordFilter(),
    ]
    for flt in filters:
        assert flt.hypothesis, f"{flt.name} hypothesis must not be empty"


class TestLockupReleaseFilter:
    def test_pass_when_no_data_file(self):
        flt = LockupReleaseFilter(data_path="/nonexistent/lockup.csv")
        result = flt.check("600000", _empty_df())
        assert result.passed is True
        assert result.filter_name == "lockup_release"

    def test_pass_when_symbol_not_in_data(self):
        lockup_data = pd.DataFrame(
            {
                "symbol": ["000001"],
                "release_date": ["2026-06-15"],
            }
        )
        flt = LockupReleaseFilter()
        result = flt.check("600000", _empty_df(), lockup_data=lockup_data)
        assert result.passed is True

    def test_filter_when_near_lockup(self):
        from aqsp.core.time import today_shanghai

        today = today_shanghai()
        near_date = (today + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        lockup_data = pd.DataFrame(
            {
                "symbol": ["600000"],
                "release_date": [near_date],
            }
        )
        flt = LockupReleaseFilter(lookback_days=30)
        result = flt.check("600000", _empty_df(), lockup_data=lockup_data)
        assert result.passed is False
        assert "解禁" in result.reason

    def test_pass_when_far_lockup(self):
        from aqsp.core.time import today_shanghai

        today = today_shanghai()
        far_date = (today + pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        lockup_data = pd.DataFrame(
            {
                "symbol": ["600000"],
                "release_date": [far_date],
            }
        )
        flt = LockupReleaseFilter(lookback_days=30)
        result = flt.check("600000", _empty_df(), lockup_data=lockup_data)
        assert result.passed is True

    def test_pass_when_past_lockup(self):
        lockup_data = pd.DataFrame(
            {
                "symbol": ["600000"],
                "release_date": ["2020-01-01"],
            }
        )
        flt = LockupReleaseFilter()
        result = flt.check("600000", _empty_df(), lockup_data=lockup_data)
        assert result.passed is True


class TestHolderCountFilter:
    def test_pass_when_no_data_file(self):
        flt = HolderCountFilter(data_path="/nonexistent/holder.csv")
        result = flt.check("600000", _empty_df())
        assert result.passed is True

    def test_pass_when_symbol_not_in_data(self):
        holder_data = pd.DataFrame(
            {
                "symbol": ["000001"],
                "quarter": ["2025Q4"],
                "holder_count": [50000],
            }
        )
        flt = HolderCountFilter()
        result = flt.check("600000", _empty_df(), holder_data=holder_data)
        assert result.passed is True

    def test_pass_when_insufficient_data(self):
        holder_data = pd.DataFrame(
            {
                "symbol": ["600000"],
                "quarter": ["2025Q4"],
                "holder_count": [50000],
            }
        )
        flt = HolderCountFilter(min_quarters=2)
        result = flt.check("600000", _empty_df(), holder_data=holder_data)
        assert result.passed is True

    def test_filter_when_consecutive_decline(self):
        holder_data = pd.DataFrame(
            {
                "symbol": ["600000", "600000", "600000"],
                "quarter": ["2025Q2", "2025Q3", "2025Q4"],
                "holder_count": [100000, 80000, 64000],
            }
        )
        flt = HolderCountFilter(decline_threshold=0.15, min_quarters=2)
        result = flt.check("600000", _empty_df(), holder_data=holder_data)
        assert result.passed is False
        assert "股东户数" in result.reason

    def test_pass_when_stable(self):
        holder_data = pd.DataFrame(
            {
                "symbol": ["600000", "600000", "600000"],
                "quarter": ["2025Q2", "2025Q3", "2025Q4"],
                "holder_count": [100000, 98000, 99000],
            }
        )
        flt = HolderCountFilter(decline_threshold=0.15, min_quarters=2)
        result = flt.check("600000", _empty_df(), holder_data=holder_data)
        assert result.passed is True

    def test_pass_when_only_one_decline(self):
        holder_data = pd.DataFrame(
            {
                "symbol": ["600000", "600000", "600000"],
                "quarter": ["2025Q2", "2025Q3", "2025Q4"],
                "holder_count": [100000, 80000, 82000],
            }
        )
        flt = HolderCountFilter(decline_threshold=0.15, min_quarters=2)
        result = flt.check("600000", _empty_df(), holder_data=holder_data)
        assert result.passed is True


class TestAnnouncementKeywordFilter:
    def test_pass_when_no_data_file(self):
        flt = AnnouncementKeywordFilter(data_path="/nonexistent/ann.csv")
        result = flt.check("600000", _empty_df())
        assert result.passed is True

    def test_filter_when_text_has_blacklisted_keyword(self):
        flt = AnnouncementKeywordFilter()
        result = flt.check(
            "600000", _empty_df(), announcement_text="公司因涉嫌财务造假被立案调查"
        )
        assert result.passed is False
        assert "立案调查" in result.reason

    def test_pass_when_text_clean(self):
        flt = AnnouncementKeywordFilter()
        result = flt.check(
            "600000", _empty_df(), announcement_text="公司2025年度利润分配方案公告"
        )
        assert result.passed is True

    def test_filter_from_data_file(self):
        ann_data = pd.DataFrame(
            {
                "symbol": ["600000", "600000", "000001"],
                "text": ["正常公告", "公司收到行政处罚决定", "年度报告"],
            }
        )
        flt = AnnouncementKeywordFilter()
        result = flt.check("600000", _empty_df(), announcement_data=ann_data)
        assert result.passed is False
        assert "行政处罚" in result.reason

    def test_pass_when_symbol_not_in_data(self):
        ann_data = pd.DataFrame(
            {
                "symbol": ["000001"],
                "text": ["公司因违规被处罚"],
            }
        )
        flt = AnnouncementKeywordFilter()
        result = flt.check("600000", _empty_df(), announcement_data=ann_data)
        assert result.passed is True

    def test_custom_keywords(self):
        flt = AnnouncementKeywordFilter(keywords=["暴雷", "跑路"])
        result = flt.check("600000", _empty_df(), announcement_text="公司业绩暴雷")
        assert result.passed is False

    def test_custom_keywords_no_match(self):
        flt = AnnouncementKeywordFilter(keywords=["暴雷", "跑路"])
        result = flt.check("600000", _empty_df(), announcement_text="公司收到行政处罚")
        assert result.passed is True


class TestLethalFilterPipeline:
    def test_default_pipeline_runs_all_filters(self):
        pipeline = LethalFilterPipeline()
        assert len(pipeline.filters) == 3
        assert isinstance(pipeline.filters[0], LockupReleaseFilter)
        assert isinstance(pipeline.filters[1], HolderCountFilter)
        assert isinstance(pipeline.filters[2], AnnouncementKeywordFilter)

    def test_pipeline_pass_when_no_data(self):
        pipeline = LethalFilterPipeline()
        passed, rejected = pipeline.run("600000", _empty_df())
        assert passed is True
        assert rejected == []

    def test_pipeline_rejects_on_announcement(self):
        pipeline = LethalFilterPipeline()
        passed, rejected = pipeline.run(
            "600000",
            _empty_df(),
            announcement_text="公司因重大违法被退市风险警示",
        )
        assert passed is False
        assert "announcement_keyword" in rejected

    def test_pipeline_rejects_on_lockup(self):
        from aqsp.core.time import today_shanghai

        today = today_shanghai()
        near_date = (today + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        lockup_data = pd.DataFrame(
            {
                "symbol": ["600000"],
                "release_date": [near_date],
            }
        )
        pipeline = LethalFilterPipeline()
        passed, rejected = pipeline.run("600000", _empty_df(), lockup_data=lockup_data)
        assert passed is False
        assert "lockup_release" in rejected

    def test_pipeline_multiple_rejections(self):
        from aqsp.core.time import today_shanghai

        today = today_shanghai()
        near_date = (today + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        lockup_data = pd.DataFrame(
            {
                "symbol": ["600000"],
                "release_date": [near_date],
            }
        )
        pipeline = LethalFilterPipeline()
        passed, rejected = pipeline.run(
            "600000",
            _empty_df(),
            lockup_data=lockup_data,
            announcement_text="公司涉嫌财务造假",
        )
        assert passed is False
        assert len(rejected) == 2
        assert "lockup_release" in rejected
        assert "announcement_keyword" in rejected

    def test_pipeline_custom_filters(self):
        flt = AnnouncementKeywordFilter()
        pipeline = LethalFilterPipeline(filters=[flt])
        assert len(pipeline.filters) == 1
        passed, rejected = pipeline.run("600000", _empty_df())
        assert passed is True

    def test_graceful_degradation_no_files(self):
        pipeline = LethalFilterPipeline(
            filters=[
                LockupReleaseFilter(data_path="/no/such/file.csv"),
                HolderCountFilter(data_path="/no/such/file.csv"),
                AnnouncementKeywordFilter(data_path="/no/such/file.csv"),
            ]
        )
        passed, rejected = pipeline.run("600000", _empty_df())
        assert passed is True
        assert rejected == []
