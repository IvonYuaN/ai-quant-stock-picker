"""由深交所官方日历派生 ``config/trading_holidays.json``（issue #314）。

背景
----
``config/trading_holidays.json`` 是**手写**文件：2026 年出过两起事实错误
（#310 国庆多列、#312 春节缺列），而且每年 12 月都要人肉照抄交易所公告。
本模块把「照抄」换成「生成 + 人工复核」：

    离线取数（``szse_calendar.fetch_months``，唯一联网入口）
        → :func:`holidays_from_days`（纯函数，可单测）
        → :func:`build_holiday_report`（与现有文件逐条 diff）
        → ``scripts/data/generate_trading_holidays.py``（CLI，默认 dry-run）

🔴 本模块**不进入运行时热路径**。``is_trading_day()`` 只读
``config/trading_holidays.json``（``aqsp.core.time._load_basic_trading_calendar``）；
这里只负责**生成**那个文件。取数走 ``allow_network=True`` 的显式入口，
只能在离线批处理里调用。

🔴 分工：机器管工作日，人管「窗口内周末」
------------------------------------------
本模块自动产出的集合 = 「交易所判定为休市、且当天**不是**周六/周日」的自然日。

**为什么不能连周末一起生成**（尽管 #314 要求 ``holidays`` 覆盖完整放假窗口）：
深交所 ``monthList`` 只返回 ``zrxh`` / ``jybz`` / ``jyrq`` 三个字段，**没有节假日
名称**，因此无法区分

- 「节假日窗口内的周末」——例如 ``2026-02-15``（周日，属春节 9 天窗口）；
- 「普通周末」——例如 ``2026-02-14``（周六，紧邻窗口但不属窗口）。

两者 ``jybz`` 都是 ``"0"``。实测（2026-10-16，365 天全覆盖）：2026 年 123 个
休市日里只有 19 个是工作日休市日，其余 104 个都是普通周末 —— 而「哪几个周末
属于窗口」只写在交易所公告原文里（#314 待办第 2 条的「逐字照抄」正指这个）。

⇒ 因此 :func:`merge_year_holidays` 是**只增不删**：人工补的窗口内周末
（以及任何既有条目）**永不**被自动生成覆盖掉。机器负责「不漏掉任何工作日
休市日」，人负责「把窗口内周末补上」。

⚠️ 若文件里有条目被交易所判为**开市**，那是真错误（如 #310 把 ``2026-10-08``
误列进名单），报告会单列为 ``conflicting_open_days`` 并在 CLI 的 stderr 响亮
提示 —— 但本模块**不自动删**：删哪一条属于人工复核的判断。

为什么输出 schema 保持 ``{"holidays": [...]}`` 不变
----------------------------------------------------
``_load_basic_trading_calendar`` 只读 ``payload["holidays"]``，任何新增字段都会被忽略。
更关键的是：**不要把 ``generated_at`` 写进被提交的文件** —— 那会让每次重生成都产生
一条无意义 diff，掩盖真正的内容变更。出处信息写在 CLI 输出与 commit message 里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from aqsp.core.time import now_shanghai
from aqsp.data.szse_calendar import SzseCalendarDay, SzseCalendarSource, fetch_months

DEFAULT_CONFIG_PATH = Path("config/trading_holidays.json")

MONTHS_PER_YEAR = 12


def holidays_from_days(
    days: Iterable[SzseCalendarDay], *, year: int
) -> tuple[date, ...]:
    """把交易所逐日标志折算成「工作日休市日」集合（纯函数）。

    - 只取 ``is_open=False``；
    - 丢掉周六/周日：机器无法区分「窗口内周末」与「普通周末」（见模块 docstring），
      窗口内周末由人工补，:func:`merge_year_holidays` 只增不删；
    - 只保留 ``year`` 年（防上游串月/串年）；
    - 升序、去重。
    """
    picked = {
        day.trade_date
        for day in days
        if not day.is_open
        and day.trade_date.year == year
        and day.trade_date.weekday() < 5
    }
    return tuple(sorted(picked))


def open_days_from_days(days: Iterable[SzseCalendarDay], *, year: int) -> tuple[date, ...]:
    """该年交易所判定为交易日的自然日（升序、去重）。供报告与断言用。"""
    picked = {
        day.trade_date
        for day in days
        if day.is_open and day.trade_date.year == year
    }
    return tuple(sorted(picked))


def parse_holiday_config(text: str) -> tuple[date, ...]:
    """解析 ``config/trading_holidays.json`` 文本 → 升序去重的日期元组。

    容错方向与运行时一致：结构坏掉就返回空元组（调用方据此判断「文件不可用」），
    不抛异常 —— 但**生成侧**拿到空集会立刻暴露（diff 里满屏 added），不会静默。
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("holidays")
    if not isinstance(raw, list):
        return ()
    out: set[date] = set()
    for item in raw:
        try:
            out.add(date.fromisoformat(str(item).strip()))
        except ValueError:
            continue
    return tuple(sorted(out))


def render_holiday_config(holidays: Iterable[date]) -> str:
    """渲染成 ``{"holidays": [...]}``（schema 与旧文件一致，末尾换行）。"""
    payload = {"holidays": [day.isoformat() for day in sorted(set(holidays))]}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def merge_year_holidays(
    existing: Iterable[date], *, year: int, holidays: Iterable[date]
) -> tuple[date, ...]:
    """把交易所口径的「工作日休市日」并入既有条目 —— **只增不删**。

    为什么不是「替换目标年份」（#314）：``holidays`` 要求覆盖完整放假窗口
    （**周末日也列上**），而窗口内周末无法从交易所接口推导、只能人工按公告补。
    若做替换，这些人工条目会被当成噪声删掉。

    因此本函数**只补缺失**：``existing ∪ holidays``（``holidays`` 只取 ``year``）。
    要删条目必须人工改文件 —— 删哪一条属于复核判断，不是机器的活。
    """
    merged = set(existing)
    merged.update(day for day in holidays if day.year == year)
    return tuple(sorted(merged))


@dataclass(frozen=True)
class HolidayRefreshReport:
    """一次「由交易所口径生成/复核休市名单」的结果。"""

    year: int
    holidays: tuple[date, ...]
    existing: tuple[date, ...]
    added: tuple[date, ...]
    conflicting_open_days: tuple[date, ...]
    weekend_entries: tuple[date, ...]
    open_days: int
    available_months: tuple[str, ...]
    missing_months: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """12 个月是否全部拿到交易所数据（缺月 ⇒ 生成结果不可信）。"""
        return not self.missing_months

    @property
    def existing_for_year(self) -> tuple[date, ...]:
        return tuple(day for day in self.existing if day.year == self.year)

    @property
    def has_conflicts(self) -> bool:
        """文件里有条目被交易所判为开市 ⇒ 必须人工复核。"""
        return bool(self.conflicting_open_days)

    def as_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
            "complete": self.complete,
            "has_conflicts": self.has_conflicts,
            "holidays": [d.isoformat() for d in self.holidays],
            "holidays_count": len(self.holidays),
            "open_days": self.open_days,
            "added": [d.isoformat() for d in self.added],
            "conflicting_open_days": [
                d.isoformat() for d in self.conflicting_open_days
            ],
            "weekend_entries": [d.isoformat() for d in self.weekend_entries],
            "available_months": list(self.available_months),
            "missing_months": list(self.missing_months),
        }


def build_holiday_report(
    year: int,
    *,
    source: SzseCalendarSource | None = None,
    cache_dir: Path | str | None = None,
    config_path: Path | str | None = None,
) -> HolidayRefreshReport:
    """取交易所数据 → 派生休市名单 → 与现有文件 diff。

    🔴 **会联网**（经由 ``szse_calendar.fetch_months``，即唯一被允许的入口）。
    只在离线批处理 / 人工复核时调用，不得放进 ``is_trading_day()`` 路径。
    """
    months = [f"{year:04d}-{m:02d}" for m in range(1, MONTHS_PER_YEAR + 1)]
    fetched = fetch_months(months, source=source, cache_dir=cache_dir)

    available: list[str] = []
    missing: list[str] = []
    days: list[SzseCalendarDay] = []
    for month, month_days in fetched.items():
        if month_days is None:
            missing.append(month)  # 取数失败：不可信
            continue
        if not month_days:
            missing.append(month)  # 成功但尚未公布：同样不可信
            continue
        available.append(month)
        days.extend(month_days)

    holidays = holidays_from_days(days, year=year)
    open_days = open_days_from_days(days, year=year)
    open_set = set(open_days)

    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    try:
        existing = parse_holiday_config(path.read_text(encoding="utf-8"))
    except OSError:
        existing = ()

    existing_year = {day for day in existing if day.year == year}
    added = tuple(sorted(set(holidays) - existing_year))
    # 文件说休市、交易所说开市 ⇒ 真错误（#310 那类）。交易所周末一律休市，
    # 所以 open_set 里不会有周末 ⇒ 这里天然只列出工作日。
    conflicting = tuple(sorted(day for day in existing_year if day in open_set))
    weekend_entries = tuple(sorted(day for day in existing_year if day.weekday() >= 5))

    return HolidayRefreshReport(
        year=year,
        holidays=holidays,
        existing=existing,
        added=added,
        conflicting_open_days=conflicting,
        weekend_entries=weekend_entries,
        open_days=len(open_days),
        available_months=tuple(available),
        missing_months=tuple(missing),
    )


def next_year(today: date | None = None) -> int:
    """默认目标年份 = 次年（交易所通常 12 月公布下一年日历）。"""
    return (today or now_shanghai().date()).year + 1


def render_report(report: HolidayRefreshReport, *, generated_at: datetime) -> str:
    """人类可读的复核报告（stdout 用）。"""
    lines = [
        f"目标年份: {report.year}",
        f"取数时间: {generated_at.isoformat(timespec='seconds')}",
        f"覆盖月份: {len(report.available_months)}/12"
        + (f"，缺失 {list(report.missing_months)}" if report.missing_months else ""),
        f"交易日: {report.open_days} 天",
        f"工作日休市日: {len(report.holidays)} 天",
    ]
    if report.existing_for_year:
        lines.append(f"现有文件该年条目: {len(report.existing_for_year)} 天")
    lines.append(f"  待补 {len(report.added)}: {[d.isoformat() for d in report.added]}")
    lines.append(
        f"  冲突 {len(report.conflicting_open_days)}"
        "（文件说休市、交易所说开市，必须人工核）: "
        f"{[d.isoformat() for d in report.conflicting_open_days]}"
    )
    if report.weekend_entries:
        lines.append(
            f"  文件中的周末条目 {len(report.weekend_entries)}"
            "（节假日窗口内的周末，自动生成不覆盖，见 #314）: "
            f"{[d.isoformat() for d in report.weekend_entries]}"
        )
    if not report.complete:
        lines.append("🔴 有月份未取到交易所数据 ⇒ **结果不可信，拒绝落盘**")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "HolidayRefreshReport",
    "build_holiday_report",
    "holidays_from_days",
    "merge_year_holidays",
    "next_year",
    "open_days_from_days",
    "parse_holiday_config",
    "render_holiday_config",
    "render_report",
]
