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

    def test_reads_producer_pit_cache_with_plan_date(self, monkeypatch, tmp_path):
        """缺省应读生产者落盘的 pit_cache/lockup.csv（plan_date 列）。

        旧默认 data/lockup_schedule.csv 全仓不存在 ⇒ 解禁排雷在生产空转。
        """

        from aqsp.core.time import today_shanghai

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        near = (today_shanghai() + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        cache = tmp_path / "pit_cache" / "lockup.csv"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            "symbol,name,plan_date,lockup_shares,ratio,lockup_type\n"
            f"600000,浦发银行,{near},10000,1.2,定增\n",
            encoding="utf-8",
        )
        flt = LockupReleaseFilter()
        result = flt.check("600000", _empty_df())
        assert result.passed is False
        assert "解禁" in result.reason


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

    def test_pipeline_pass_when_no_data(self, monkeypatch, tmp_path):
        # 隔离：默认数据路径指向空 runtime root，确保真「无数据」
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        pipeline = LethalFilterPipeline()
        passed, rejected = pipeline.run("600000", _empty_df())
        assert passed is True
        assert rejected == []

    def test_pipeline_rejects_on_announcement(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
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

    def test_pipeline_custom_filters(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
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


class TestDataMissingObservability:
    """#161 静默失效链根治：数据缺失必须可观测，不可当健康放行。"""

    def test_lockup_data_missing_flag(self, tmp_path):
        flt = LockupReleaseFilter(data_path=str(tmp_path / "nope.csv"))
        result = flt.check("600000", _empty_df())
        assert result.passed is True
        assert result.data_missing is True

    def test_holder_data_missing_flag(self, tmp_path):
        flt = HolderCountFilter(data_path=str(tmp_path / "nope.csv"))
        result = flt.check("600000", _empty_df())
        assert result.passed is True
        assert result.data_missing is True

    def test_holder_symbol_absent_is_not_data_missing(self):
        """数据面存在但该股无记录 = 正常缺失（非数据缺失），不打标记。"""
        holder_data = pd.DataFrame(
            {"symbol": ["000001"], "quarter": ["2025Q4"], "holder_count": [5]}
        )
        flt = HolderCountFilter()
        result = flt.check("600000", _empty_df(), holder_data=holder_data)
        assert result.passed is True
        assert result.data_missing is False

    def test_announcement_data_missing_flag(self, tmp_path):
        flt = AnnouncementKeywordFilter(data_path=str(tmp_path / "nope.csv"))
        result = flt.check("600000", _empty_df())
        assert result.passed is True
        assert result.data_missing is True

    def test_announcement_symbol_absent_is_not_data_missing(self):
        ann_data = pd.DataFrame(
            {"symbol": ["000001"], "text": ["公司因违规被处罚"]}
        )
        flt = AnnouncementKeywordFilter()
        result = flt.check("600000", _empty_df(), announcement_data=ann_data)
        assert result.passed is True
        assert result.data_missing is False

    def test_pipeline_run_collects_missing_in_place(self, tmp_path):
        pipeline = LethalFilterPipeline(
            filters=[
                LockupReleaseFilter(data_path=str(tmp_path / "a.csv")),
                HolderCountFilter(data_path=str(tmp_path / "b.csv")),
                AnnouncementKeywordFilter(data_path=str(tmp_path / "c.csv")),
            ]
        )
        missing: list[str] = []
        passed, rejected = pipeline.run(
            "600000", _empty_df(), missing_filters=missing
        )
        assert passed is True
        assert rejected == []
        assert sorted(missing) == [
            "announcement_keyword",
            "holder_count",
            "lockup_release",
        ]

    def test_run_with_observability_returns_triple(self, tmp_path):
        pipeline = LethalFilterPipeline(
            filters=[
                LockupReleaseFilter(data_path=str(tmp_path / "a.csv")),
                HolderCountFilter(data_path=str(tmp_path / "b.csv")),
                AnnouncementKeywordFilter(data_path=str(tmp_path / "c.csv")),
            ]
        )
        passed, rejected, missing = pipeline.run_with_observability(
            "600000", _empty_df()
        )
        assert passed is True
        assert rejected == []
        assert sorted(missing) == [
            "announcement_keyword",
            "holder_count",
            "lockup_release",
        ]

    def test_defaults_read_pit_cache_runtime_root(
        self, monkeypatch, tmp_path
    ):
        """缺省数据路径 = $AQSP_RUNTIME_DATA_ROOT/pit_cache（写读同源）。"""
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        near = (
            __import__("aqsp.core.time", fromlist=["today_shanghai"])
            .today_shanghai()
            + pd.Timedelta(days=3)
        ).strftime("%Y-%m-%d")
        cache = tmp_path / "pit_cache"
        cache.mkdir(parents=True)
        (cache / "lockup.csv").write_text(
            "symbol,plan_date\n600000," + near + "\n", encoding="utf-8"
        )
        flt = LockupReleaseFilter()
        assert flt.data_path == str(cache / "lockup.csv")
        result = flt.check("600000", _empty_df())
        assert result.passed is False  # 从 pit_cache 读到数据并真生效


class TestDefaultsReadPitCache:
    def test_holder_default_path(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        assert HolderCountFilter().data_path == str(
            tmp_path / "pit_cache" / "holder_count.csv"
        )

    def test_announcement_default_path(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        assert AnnouncementKeywordFilter().data_path == str(
            tmp_path / "pit_cache" / "announcements.csv"
        )

    def test_holder_default_loads_pit_cache_and_rejects(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        cache = tmp_path / "pit_cache" / "holder_count.csv"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            "symbol,quarter,holder_count\n"
            "600000,2026-03-31,100000\n"
            "600000,2026-06-30,80000\n",
            encoding="utf-8",
        )
        result = HolderCountFilter().check("600000", _empty_df())
        assert result.passed is False
        assert "股东户数" in result.reason
        assert result.data_missing is False
