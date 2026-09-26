"""pit_cache 产出方↔消费方「写读同源」连通性闸（§11.3 静默失效防回归项）。

背景：09-22 审计发现「静默失效」——
- (A) filters_lethal 三下跌保护过滤器因 pit_cache/*.csv 从无产出方而恒 passed=True；
- (B) event_calendar 只读 pit_cache/lockup.csv + longhubang.csv，同名产出方未被调度。

PR #234 把 4 个生产者接入每日管线后，本闸钉死两条不变量，防止「写读路径将来再次
分叉」：

1. **路径对齐**：同一 ``AQSP_RUNTIME_DATA_ROOT`` 下，生产者 ``_default_cache_path()``
   写出的路径必须与消费者读取路径**逐字相等**（文件名 + ``pit_cache/`` 子目录 +
   data-root 规则全对齐）。任何一端将来改了 CSV 名 / 目录 / env 约定，CI 立刻红。
2. **列 schema 往返**：按 item dataclass 写 CSV（与生产者 ``load()`` 落盘形态同款
   ``pd.DataFrame([i.__dict__ ...]).to_csv``），消费者**默认路径**读回必须「非
   data_missing / 非空日历」，并锁死 ``symbol`` 前导零（CSV 以字符串写、消费者以
   ``dtype={"symbol": str}`` 读）。

本测试全程**不联网**：不调 ``load(force=True)`` / ``from_sources(autoload=True)``，
只读已写好的 CSV。生产者是「写读同源」的唯一约定来源，consumer 侧路径函数若与
生产者分叉，测试 1 即失败。
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from aqsp.data.announcement import AnnouncementItem, AnnouncementSource
from aqsp.data.holder_num import HolderNumItem, HolderNumSource
from aqsp.data.lockup import LockupItem, LockupSource
from aqsp.data.longhubang import LongHubangItem, LongHubangSource
from aqsp.features import event_calendar
from aqsp.features.event_calendar import EventCalendar
from aqsp.filters_lethal.announcement_keyword import (
    AnnouncementKeywordFilter,
    _default_announcement_cache_path,
)
from aqsp.filters_lethal.holder_count import (
    HolderCountFilter,
    _default_holder_cache_path,
)
from aqsp.filters_lethal.lockup_release import (
    LockupReleaseFilter,
    _default_lockup_cache_path,
)

_SYMBOL = "000001"  # 前导零：用来锁死 CSV 字符串写读链路


def _write_producer_csv(path: str, items: list[object]) -> None:
    """与生产者 ``load()`` 落盘完全同款：``pd.DataFrame([i.__dict__ ...]).to_csv``。

    用 ``__dict__`` 而非 ``dataclasses.asdict``——这正是 ``holder_num.py:233`` 等
    生产者写盘用的形态，往返才真正复现「生产者写、消费者读」。
    """
    pd.DataFrame([item.__dict__ for item in items]).to_csv(path, index=False)


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch) -> str:
    """把生产者与消费者共同钉死到同一个 tmp data root（写读同源前提）。"""
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    return str(tmp_path)


# ---------------------------------------------------------------------------
# 1) 路径对齐：4 源生产者写路径 == 消费者读路径
# ---------------------------------------------------------------------------
def test_holder_count_path_alignment(data_root: str) -> None:
    expected = os.path.join(data_root, "pit_cache", "holder_count.csv")
    assert HolderNumSource()._default_cache_path() == expected
    assert _default_holder_cache_path() == expected


def test_announcements_path_alignment(data_root: str) -> None:
    expected = os.path.join(data_root, "pit_cache", "announcements.csv")
    assert AnnouncementSource()._default_cache_path() == expected
    assert _default_announcement_cache_path() == expected


def test_lockup_path_alignment(data_root: str) -> None:
    expected = os.path.join(data_root, "pit_cache", "lockup.csv")
    assert LockupSource()._default_cache_path() == expected
    assert _default_lockup_cache_path() == expected
    # event_calendar 读同一份 lockup.csv（文件名 + pit_cache 目录约定对齐）
    assert (
        event_calendar._pit_cache_path(
            event_calendar.LOCKUP_CACHE_FILENAME, data_root
        )
        == expected
    )


def test_longhubang_path_alignment(data_root: str) -> None:
    expected = os.path.join(data_root, "pit_cache", "longhubang.csv")
    assert LongHubangSource()._default_cache_path() == expected
    assert (
        event_calendar._pit_cache_path(
            event_calendar.LONGHUBANG_CACHE_FILENAME, data_root
        )
        == expected
    )


# ---------------------------------------------------------------------------
# 2) 写→读往返：生产者同款落盘 → 消费者默认路径读回（非降级 + 前导零）
# ---------------------------------------------------------------------------
def test_holder_count_roundtrip_reaches_consumer(
    data_root: str, tmp_path: Path
) -> None:
    """生产形态写盘 → HolderCountFilter 默认路径读得到（非 data_missing）。"""
    items = [
        HolderNumItem(
            symbol=_SYMBOL,
            name="测试股份",
            quarter="2026-03-31",
            holder_count=100_000.0,
            notice_date="2026-04-20",
        ),
        HolderNumItem(
            symbol=_SYMBOL,
            name="测试股份",
            quarter="2026-06-30",
            holder_count=120_000.0,  # 上升 ⇒ 不触发「连续减少」
            notice_date="2026-07-15",
        ),
    ]
    _write_producer_csv(HolderNumSource()._default_cache_path(), items)

    flt = HolderCountFilter()  # 默认 data_path = 生产者落盘路径
    result = flt.check(_SYMBOL, pd.DataFrame())
    assert result.data_missing is False, "消费者须读得到生产者写的数据（非降级）"
    assert result.passed is True  # 股东户数上升 ⇒ 正常

    # 前导零保留：以 dtype=str 读回仍为 "000001" 而非 1 / 1.0
    frame = pd.read_csv(flt.data_path, dtype={"symbol": str})
    assert set(frame["symbol"]) == {_SYMBOL}


def test_announcements_roundtrip_reaches_consumer(
    data_root: str, tmp_path: Path
) -> None:
    items = [
        AnnouncementItem(
            symbol=_SYMBOL,
            name="测试股份",
            notice_date="2026-09-25",
            text="年度报告已披露",  # 无负面关键词
        ),
    ]
    _write_producer_csv(AnnouncementSource()._default_cache_path(), items)

    flt = AnnouncementKeywordFilter()
    result = flt.check(_SYMBOL, pd.DataFrame())
    assert result.data_missing is False, "消费者须读得到生产者写的数据（非降级）"
    assert result.passed is True


def test_lockup_roundtrip_reaches_consumer(data_root: str, tmp_path: Path) -> None:
    items = [
        LockupItem(
            symbol=_SYMBOL,
            name="测试股份",
            plan_date="2026-09-25",
            lockup_shares=1_000.0,
            ratio=0.0001,
            lockup_type="首发",
        ),
    ]
    _write_producer_csv(LockupSource()._default_cache_path(), items)

    flt = LockupReleaseFilter()
    result = flt.check(_SYMBOL, pd.DataFrame())
    assert result.data_missing is False, "消费者须读得到生产者写的数据（非降级）"


def test_event_calendar_reads_lockup_and_longhubang(
    data_root: str, tmp_path: Path
) -> None:
    """event_calendar.from_cache 读生产形态落盘的 lockup + longhubang（非空日历）。"""
    _write_producer_csv(
        LockupSource()._default_cache_path(),
        [
            LockupItem(
                symbol=_SYMBOL,
                name="测试股份",
                plan_date="2026-09-25",
                lockup_shares=1_000.0,
                ratio=0.0001,
                lockup_type="首发",
            ),
        ],
    )
    _write_producer_csv(
        LongHubangSource()._default_cache_path(),
        [
            LongHubangItem(
                trade_date="2026-09-24",
                symbol=_SYMBOL,
                name="测试股份",
                close_price=10.0,
                change_rate=3.2,
                buy_amount=1_000.0,
                sell_amount=500.0,
                net_amount=500.0,
                interpretation="机构净买入",
            ),
        ],
    )

    cal = EventCalendar.from_cache(runtime_data_root=data_root)
    assert cal.has_unlock_data() is True, "从生产者落盘读得到解禁面"
    assert cal.has_longhubang_data() is True, "从生产者落盘读得到龙虎榜面"
    assert cal.is_empty() is False


def test_event_calendar_cache_missing_still_degrades_gracefully(
    data_root: str, tmp_path: Path
) -> None:
    """对照：无 CSV 时 from_cache 不抛错、不联网，降级为空日历。"""
    cal = EventCalendar.from_cache(runtime_data_root=data_root)
    assert cal.is_empty() is True
    assert cal.has_unlock_data() is False
    assert cal.has_longhubang_data() is False
