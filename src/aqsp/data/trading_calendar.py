from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import (
    _load_basic_trading_calendar,
    _get_basic_next_trading_day,
    _is_basic_trading_day,
)
from aqsp.data.szse_calendar import SzseCalendarSource
from aqsp.data.tushare_pit import (
    TusharePitClient,
    is_open_day_in_calendar,
    next_trade_date_from_calendar,
    previous_trade_date_from_calendar,
)


@dataclass(frozen=True)
class TradingCalendarWindow:
    # 覆盖春节/国庆等长休市窗口，避免只查到周末附近。
    before_days: int = 31
    after_days: int = 31


def load_optional_trade_calendar(
    start: date,
    end: date,
    *,
    exchange: str = "SSE",
    client: TusharePitClient | None = None,
) -> pd.DataFrame | None:
    """Load Tushare trade calendar when available, otherwise return None."""
    try:
        pit_client = client or TusharePitClient()
    except (RuntimeError, ValueError):
        return None
    try:
        return pit_client.fetch_trade_calendar(start, end, exchange=exchange)
    except DataError:
        return None


def load_szse_trade_calendar(
    start: date,
    end: date,
    *,
    anchor: date | None = None,
    source: SzseCalendarSource | None = None,
    allow_network: bool = False,
) -> pd.DataFrame | None:
    """深交所官方日历（issue #314）—— Tushare 缺席时的运行时日历来源。

    生产机没有 ``TUSHARE_TOKEN``，此前 ``_load_runtime_calendar`` 恒返回
    ``None``，交易日判定只能退到「周末 + 手写休市名单」。深交所公开接口
    不需要 token，可把运行时判定升级成交易所口径。

    取数失败 / 锚点月未公布 ⇒ 返回 ``None``，由调用方回退静态日历。
    本源**不抛异常**：日历源故障不得升级成选股链路故障。

    🔴 ``allow_network`` **默认 ``False``**（只读磁盘缓存）。运行时
    ``is_trading_day()`` 路径不得隐式联网 —— 详见 ``szse_calendar`` 模块
    docstring 里 2026-10-16 CI 挂死 17 分钟的事故记录。只有离线批处理
    （``szse_calendar.fetch_months``）才显式传 ``True``。
    """
    try:
        calendar_source = source or SzseCalendarSource()
    except (OSError, ValueError):
        return None
    return calendar_source.as_frame(
        start, end, anchor=anchor, allow_network=allow_network
    )


def resolve_is_trading_day(
    target: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
    window: TradingCalendarWindow | None = None,
) -> bool:
    # 静态休市名单是硬覆盖：交易所公告明写的休市日，即使运行时日历
    # （Tushare）缺席/错误也必须休市。只列「休市日」，不列调休上班日
    # （调休上班日一律是周末，交易所照样休市，issue #312）。
    holidays = _load_basic_trading_calendar()
    if target in holidays:
        return False
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else _load_runtime_calendar(
            target,
            exchange=exchange,
            window=window,
        )
    )
    if runtime_calendar is not None:
        if _calendar_covers(runtime_calendar, target):
            return is_open_day_in_calendar(runtime_calendar, target)
    return _is_basic_trading_day(target)


def resolve_previous_trading_day(
    target: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
    window: TradingCalendarWindow | None = None,
) -> date:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else _load_runtime_calendar(
            target,
            exchange=exchange,
            window=window,
        )
    )
    if runtime_calendar is not None:
        try:
            candidate = previous_trade_date_from_calendar(runtime_calendar, target)
            if resolve_is_trading_day(
                candidate,
                calendar_df=runtime_calendar,
                exchange=exchange,
                window=window,
            ):
                return candidate
        except DataError:
            pass
    cursor = target - timedelta(days=1)
    while not resolve_is_trading_day(
        cursor,
        calendar_df=runtime_calendar,
        exchange=exchange,
        window=window,
    ):
        cursor -= timedelta(days=1)
    return cursor


def resolve_next_trading_day(
    target: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
    window: TradingCalendarWindow | None = None,
) -> date:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else _load_runtime_calendar(
            target,
            exchange=exchange,
            window=window,
        )
    )
    if runtime_calendar is not None:
        try:
            candidate = next_trade_date_from_calendar(runtime_calendar, target)
            if resolve_is_trading_day(
                candidate,
                calendar_df=runtime_calendar,
                exchange=exchange,
                window=window,
            ):
                return candidate
        except DataError:
            pass
    cursor = target + timedelta(days=1)
    while not resolve_is_trading_day(
        cursor,
        calendar_df=runtime_calendar,
        exchange=exchange,
        window=window,
    ):
        cursor += timedelta(days=1)
    return cursor


def _calendar_covers(calendar_df: pd.DataFrame, target: date) -> bool:
    """Use a supplied calendar only inside its actual coverage window."""

    if calendar_df.empty or "cal_date" not in calendar_df.columns:
        return False
    dates = pd.to_datetime(calendar_df["cal_date"], errors="coerce").dropna()
    if dates.empty:
        return False
    return dates.min().date() <= target <= dates.max().date()


def trading_day_lag(
    latest: date,
    reference_day: date,
    *,
    calendar_df: pd.DataFrame | None = None,
    exchange: str = "SSE",
) -> int:
    runtime_calendar = (
        calendar_df
        if calendar_df is not None
        else load_optional_trade_calendar(
            latest - timedelta(days=31),
            reference_day + timedelta(days=31),
            exchange=exchange,
        )
    )
    anchor = (
        reference_day
        if resolve_is_trading_day(
            reference_day,
            calendar_df=runtime_calendar,
            exchange=exchange,
        )
        else resolve_previous_trading_day(
            reference_day,
            calendar_df=runtime_calendar,
            exchange=exchange,
        )
    )
    if latest >= anchor:
        return 0
    if runtime_calendar is not None:
        normalized = runtime_calendar.copy()
        normalized["cal_date"] = pd.to_datetime(
            normalized["cal_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        normalized["is_open"] = (
            pd.to_numeric(normalized["is_open"], errors="coerce").fillna(0).astype(int)
        )
        open_days = normalized[
            (normalized["is_open"] == 1)
            & (normalized["cal_date"] > latest.isoformat())
            & (normalized["cal_date"] <= anchor.isoformat())
        ]
        return int(open_days.shape[0])

    lag = 0
    cursor = latest
    while cursor < anchor:
        cursor = _get_basic_next_trading_day(cursor)
        lag += 1
    return lag


def _static_calendar_covers_year(target: date) -> bool:
    """静态休市名单是否已覆盖 ``target`` 所在年份。

    ``config/trading_holidays.json`` 按年维护「该年全部休市日」。只要该年出现在
    名单里，就说明我们手上有这一年的权威休市表，``周末 + 名单`` 足以判定 ——
    此时**不需要**再联网问深交所。

    这条判定是**性能红线**，不是可选优化：``is_trading_day()`` 在选股链路里被
    高频调用；若每次都打外部接口，CI（GitHub runner 位于境外，连不上 szse.cn）
    每次调用都要等满重试 × 超时，整个测试套件会被拖死（实测：shard 3 从 154s
    涨到 25min+ 仍未结束）。SZSE 源的职责是**补静态名单没写的年份**（如 2027），
    不是复核已覆盖年份 —— 已覆盖年份的权威性由 #314 的「官方公告照抄」流程保证。
    """
    holidays = _load_basic_trading_calendar()
    return any(holiday.year == target.year for holiday in holidays)


def _load_runtime_calendar(
    target: date,
    *,
    exchange: str,
    window: TradingCalendarWindow | None,
) -> pd.DataFrame | None:
    """运行时日历：Tushare 优先，缺席且静态名单未覆盖该年时回落到深交所。

    静态休市名单仍是**硬覆盖**（见 ``resolve_is_trading_day`` 首段），本函数
    只负责补「静态名单没写的年份」。

    🔴 ``allow_network=False`` 是**红线**，不是默认值的巧合：本函数位于
    ``is_trading_day()`` 的高频路径上，一次隐式联网会被调用几十万次
    （2026-10-16 实测：``DUAL_MAX_LAG_TRADING_DAYS=99999`` 的用例把它调了
    139,737 次，CI ``Tests`` 步挂死 17 分钟被取消）。深交所源在这里**只读
    磁盘缓存**；真正联网只允许走离线批处理 ``szse_calendar.fetch_months``。
    """
    runtime_window = window or TradingCalendarWindow()
    start = target - timedelta(days=runtime_window.before_days)
    end = target + timedelta(days=runtime_window.after_days)
    tushare_calendar = load_optional_trade_calendar(start, end, exchange=exchange)
    if tushare_calendar is not None:
        return tushare_calendar
    if _static_calendar_covers_year(target):
        return None
    return load_szse_trade_calendar(start, end, anchor=target, allow_network=False)
