"""数据密度探测的测试。

核心回归：**"边界上有数据"不等于"窗口可用"**。
2026-09 事故就是被 MIN/MAX 骗过去的 —— 几百行零散残留让 5 年窗口看起来有覆盖，
gate 跑到第 1/36 期才发现训练窗口是空的。
"""
from __future__ import annotations

import pytest

from aqsp.data.coverage_density import (
    build_density_report,
    find_dense_start,
    find_sparse_spans,
    resolve_effective_window,
    resolve_threshold,
)


def _days(n: int, start: str = "2021-09-13") -> list[str]:
    from datetime import date, timedelta

    base = date.fromisoformat(start)
    return [(base + timedelta(days=i)).isoformat() for i in range(n)]


# --------------------------------------------------------------------------
# 阈值
# --------------------------------------------------------------------------


def test_threshold_uses_absolute_floor_when_pool_is_small():
    assert resolve_threshold([100, 120, 90]) == 50


def test_threshold_uses_median_ratio_when_pool_is_large():
    # 近端中位数 10000，比例 0.5 → 5000 > 3000
    counts = [10000] * 70
    assert resolve_threshold(counts) == 5000


def test_threshold_ignores_peak_outlier():
    # 有一天异常高（如全市场补数），不应把阈值抬上去
    counts = [5000] * 59 + [50000]
    assert resolve_threshold(counts) == 2500


def test_threshold_empty_counts_falls_back_to_floor():
    assert resolve_threshold([]) == 1


# --------------------------------------------------------------------------
# 密集起点
# --------------------------------------------------------------------------


def test_dense_start_is_none_when_all_sparse():
    assert find_dense_start([10, 20, 30], threshold=3000) is None


def test_dense_start_is_zero_when_all_dense():
    assert find_dense_start([5000, 5000, 5000], threshold=3000) == 0


def test_dense_start_skips_leading_gap():
    """前段空、后段密集 → 起点应落在密集处。"""
    counts = [10, 20, 30, 5000, 5000, 5000, 5000]
    assert find_dense_start(counts, threshold=3000) == 3


def test_dense_start_tolerates_a_few_holes():
    """密集区里偶尔缺一天（半日市/停牌）不该把起点推后。"""
    counts = [5000] * 100
    counts[50] = 0  # 1% 的洞，容差 5%
    assert find_dense_start(counts, threshold=3000) == 0


def test_dense_start_rejects_when_holes_exceed_tolerance():
    """洞太多（>5%）说明这段本来就不可用。"""
    counts = [5000] * 100
    for index in range(0, 20):
        counts[index] = 0  # 前 20% 是空的
    assert find_dense_start(counts, threshold=3000) == 20


def test_dense_start_never_lands_on_a_sparse_day():
    """起点当日必须自身达标 —— 容忍度只用于放过密集区**内部**的缺日。

    否则 gate 的第一期训练窗口仍会落在空段上（2026-09 事故的死因）。
    """
    # 后 60 天密集；若只看"后缀 95% 达标"，索引 37 也能通过（60/63=95.2%），
    # 但那会把 37~39 三个空日圈进窗口。
    counts = [50] * 40 + [5000] * 60
    start = find_dense_start(counts, threshold=3000)
    assert start == 40
    assert counts[start] >= 3000


def test_dense_start_on_empty_returns_none():
    assert find_dense_start([], threshold=3000) is None


# --------------------------------------------------------------------------
# 缺口区间
# --------------------------------------------------------------------------


def test_sparse_spans_merges_contiguous_runs():
    days = _days(6)
    counts = [10, 20, 5000, 30, 40, 5000]
    spans = find_sparse_spans(days, counts, threshold=3000)
    assert len(spans) == 2
    assert spans[0].start == days[0] and spans[0].end == days[1]
    assert spans[0].trade_days == 2
    assert spans[1].start == days[3] and spans[1].end == days[4]


def test_sparse_spans_empty_when_all_dense():
    assert find_sparse_spans(_days(3), [5000, 5000, 5000], threshold=3000) == []


def test_sparse_spans_handles_trailing_run():
    days = _days(3)
    spans = find_sparse_spans(days, [5000, 10, 20], threshold=3000)
    assert len(spans) == 1
    assert spans[0].end == days[2]


# --------------------------------------------------------------------------
# 报告与收敛（真实事故形态）
# --------------------------------------------------------------------------


def test_report_detects_the_real_incident_shape():
    """复现 2026-09 事故：5 年窗口 + 前 2 年空。"""
    days = _days(1215, start="2021-09-13")
    # 前 490 个交易日只有零星数据（对应 2021-09 ~ 2023-08）
    counts = [50] * 490 + [5000] * (1215 - 490)
    report = build_density_report(
        days, counts, start="2021-09-12", end="2026-09-11"
    )

    assert report.usable is True
    assert report.has_leading_gap is True
    assert report.dense_start == days[490]
    assert report.describe().startswith("窗口起点 2021-09-12 早于数据覆盖")
    assert "实际可用起点" in report.describe()


def test_report_usable_when_dense_from_the_start():
    days = _days(300)
    report = build_density_report(
        days, [5000] * 300, start=days[0], end=days[-1]
    )
    assert report.has_leading_gap is False
    assert report.dense_start == days[0]
    assert "数据密集" in report.describe()


def test_report_not_usable_when_no_stretch_is_dense():
    """没有任何一段"连续达标"的区间 → 不可用。

    注意：密度阈值是**相对**的（近端中位数的一半），所以"整窗都很小但均匀"
    会判为可用 —— 绝对大小由标的数闸门负责。真正不可用是**忽多忽少**：
    稀疏日散布其间，任何后缀都达不到 95%。
    """
    days = _days(30)
    counts = [5000 if index % 2 == 0 else 1 for index in range(30)]
    report = build_density_report(days, counts, start=days[0], end=days[-1])
    assert report.usable is False
    assert "没有任何一段密集区间" in report.describe()


def test_uniform_small_pool_is_still_usable():
    """均匀的小标的池判为可用 —— 绝对不足交给标的数闸门，不在这里重复判。"""
    days = _days(30)
    report = build_density_report(days, [10] * 30, start=days[0], end=days[-1])
    assert report.usable is True


def test_report_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="长度不一致"):
        build_density_report(
            _days(3), [1, 2], start="a", end="b"
        )


def test_effective_window_shifts_on_internal_gap():
    """数据内部有空段（密集起点晚于首个数据日）→ 收敛。"""
    days = _days(100)
    counts = [50] * 40 + [5000] * 60
    report = build_density_report(days, counts, start=days[0], end=days[-1])
    effective, clamped = resolve_effective_window(
        report, requested_start=days[0], first_data_day=days[0]
    )
    assert clamped is True
    assert effective == days[40]


def test_effective_window_keeps_requested_when_already_dense():
    days = _days(50)
    report = build_density_report(days, [5000] * 50, start=days[0], end=days[-1])
    effective, clamped = resolve_effective_window(
        report, requested_start=days[0], first_data_day=days[0]
    )
    assert clamped is False
    assert effective == days[0]


def test_effective_window_does_not_move_when_unusable():
    """整窗不可用时不要"修"成某个更晚的起点 —— 那会静默缩短窗口。"""
    days = _days(10)
    report = build_density_report(days, [1] * 10, start=days[0], end=days[-1])
    effective, clamped = resolve_effective_window(
        report, requested_start=days[0], first_data_day=days[0]
    )
    assert clamped is False
    assert effective == days[0]


def test_effective_window_compares_days_not_strings():
    """紧凑格式（库）与 ISO 格式（请求）混比会误判 —— 这是原事故的同款错误。

    `"20210913" > "2021-09-13"` 为真（`0` > `-`），若不归一就会把
    "本已对齐"的窗口误判成需要收敛。
    """
    from aqsp.data.coverage_density import canonical_day

    days = _days(50, start="2021-09-13")
    report = build_density_report(
        days, [5000] * 50, start="2021-09-13", end=days[-1]
    )
    assert report.dense_start == "2021-09-13"
    assert canonical_day("2021-09-13") == "20210913"

    effective, clamped = resolve_effective_window(
        report, requested_start="20210913", first_data_day="20210913"
    )
    assert clamped is False
    assert effective == "20210913"


def test_effective_window_shifts_across_formats():
    days = _days(100, start="2021-09-13")
    counts = [50] * 40 + [5000] * 60
    report = build_density_report(days, counts, start="2021-09-13", end=days[-1])
    effective, clamped = resolve_effective_window(
        report, requested_start="20210913", first_data_day="20210913"
    )
    assert clamped is True
    assert effective == days[40]


def test_short_leading_gap_is_not_a_gap():
    """请求起点落在假期（元旦/周末）→ 不是缺口，不能改窗口。

    基础交易日历不认元旦，所以这里必须用容差判断，而不是查日历。
    """
    days = _days(20, start="2024-01-02")
    report = build_density_report(
        days, [5000] * 20, start="2024-01-02", end=days[-1]
    )
    effective, clamped = resolve_effective_window(
        report, requested_start="2024-01-01", first_data_day="20240102"
    )
    assert clamped is False
    assert effective == "2024-01-01"


def test_long_leading_gap_is_a_real_gap():
    """起点之后长期无数据 → 窗口超出覆盖，必须收敛（原事故形态）。"""
    days = _days(200, start="2023-09-05")
    report = build_density_report(
        days, [5000] * 200, start="2021-09-12", end=days[-1]
    )
    effective, clamped = resolve_effective_window(
        report, requested_start="2021-09-12", first_data_day="20230905"
    )
    assert clamped is True
    assert effective == "2023-09-05"
