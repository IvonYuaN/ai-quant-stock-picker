#!/usr/bin/env python3
"""预加载并缓存东财「事件」数据源到本地 CSV。生产机联网执行。

覆盖：停复牌 / 业绩预告 / 分红送转 / 股东户数 / 近期公告标题。单个源失败不阻断其余源。

业绩预告 / 分红送转带时间窗（notice_from / ex_dividend_from）收窄到「最近发布 /
尚未除权除息」，既规避东财 pageSize=500 截断，也让覆盖范围可见（#193）。
股东户数取最近 4 个已完成披露季的季度末（#161 排雷层数据面）；公告取最近
10 天全市场标题（#161 排雷层数据面，与 AnnouncementKeywordFilter 写读同源）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai
from aqsp.data.announcement import AnnouncementSource
from aqsp.data.dividend_plan import DividendPlanSource
from aqsp.data.earnings_forecast import EarningsForecastSource
from aqsp.data.holder_num import (
    HolderNumSource,
    _default_quarter_count,
    _recent_quarter_ends,
)
from aqsp.data.suspend_resume import SuspendResumeSource

# 事件日历只需要「最近发布 / 尚未除权除息」这一片；lookback 默认 90 天（约一个季度），
# 可用 AQSP_EVENT_DATA_LOOKBACK_DAYS 覆盖。窗口外数据由 event_calendar 另行处理。
EVENT_DATA_LOOKBACK_DAYS = int(os.environ.get("AQSP_EVENT_DATA_LOOKBACK_DAYS", "90"))


def _window_from() -> str:
    """时间窗下界（含）：今天往前推 lookback 天，ISO 日期（YYYY-MM-DD）。"""
    return (today_shanghai() - timedelta(days=EVENT_DATA_LOOKBACK_DAYS)).isoformat()


def _coverage_label(load_kwargs: dict) -> str:
    for key in ("notice_from", "ex_dividend_from"):
        if key in load_kwargs:
            return f"覆盖 {load_kwargs[key]} ~ 至今 | lookback {EVENT_DATA_LOOKBACK_DAYS}d"
    return "全量（无时间窗）"


def _write_meta(name: str, csv_path: str, coverage: str, truncated: bool) -> None:
    """截断 / 覆盖范围落产物侧标记（#193 选项 B）：下游无需翻日志即可判断完整性。"""
    meta = {
        "source": name,
        "coverage": coverage,
        "truncated": bool(truncated),
        "generated_at": today_shanghai().isoformat(),
    }
    meta_path = os.path.splitext(csv_path)[0] + ".meta.json"
    try:
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _preload(name: str, source: object, coverage: str = "", **load_kwargs: str) -> bool:
    try:
        items = source.load(force=True, **load_kwargs)  # type: ignore[attr-defined]
    except DataError as exc:
        print(f"[{name}] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return False
    path = source._default_cache_path()  # type: ignore[attr-defined]
    truncated = bool(getattr(source, "truncated", False))  # type: ignore[attr-defined]
    # 页满会被 pageSize 截断：必须显式打出来，避免「静默少拿数据」
    cov = coverage or _coverage_label(load_kwargs)
    print(f"[{name}] 已缓存 {len(items)} 条 -> {path} (truncated={truncated}) | {cov}")
    # 覆盖范围 / 截断状态落产物侧标记（#193 选项 B）：下游无需翻日志即可判完整性。
    _write_meta(name, path, cov, truncated)
    return True


def main() -> int:
    window = _window_from()
    today = today_shanghai()
    holder_quarters = _recent_quarter_ends(today, _default_quarter_count())
    ann_days = int(os.environ.get("AQSP_ANN_WINDOW_DAYS", "10"))
    ann_begin = (today - timedelta(days=ann_days)).isoformat()
    results = [
        _preload("suspend_resume", SuspendResumeSource()),
        _preload("earnings_forecast", EarningsForecastSource(), notice_from=window),
        _preload("dividend_plan", DividendPlanSource(), ex_dividend_from=window),
        _preload(
            "holder_num",
            HolderNumSource(),
            coverage=f"最近 {len(holder_quarters)} 季（{' ~ '.join(holder_quarters)}）",
            quarters=holder_quarters,
        ),
        _preload(
            "announcement",
            AnnouncementSource(),
            coverage=f"近 {ann_days} 天（{ann_begin} ~ {today.isoformat()}）",
            begin_time=ann_begin,
            end_time=today.isoformat(),
        ),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
