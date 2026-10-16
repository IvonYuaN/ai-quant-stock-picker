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

口径：只写「**工作日**休市日」
------------------------------
写入文件的集合 = 「交易所判定为休市、且当天**不是**周六/周日」的自然日。

- 普通周末**不写**：``_is_basic_trading_day`` 已按 ``weekday() >= 5`` 判休市；
- 调休上班日**不写**：它们一律落在周末，交易所照样休市（#312 语义）；
- 落在节假日区间内的周末**也不写**。旧手写文件混进了 4 个这类条目
  （2026-01-03 / 02-15 / 02-21 / 02-22）—— 它们对判定结果毫无影响
  （周末本来就休市），只是手写维护时的噪声。统一成「工作日休市日」后，
  文件可以**机器复现、可逐行 diff**。

⚠️ 因此本模块生成的集合**不等于**旧手写文件的字面内容（会少那几个周末条目），
但 ``is_trading_day()`` 的判定结果**逐日一致** —— 这条等价性由
``tests/test_trading_holidays_generator.py`` 的守卫钉住。

为什么输出 schema 保持 ``{"holidays": [...]}`` 不变
----------------------------------------------------
``_load_basic_trading_calendar`` 只读 ``payload["holidays"]``，任何新增字段都会被忽略。
更关键的是：**不要把 ``generated_at`` 写进被提交的文件** —— 那会让每次重生成都产生
一条无意义 diff，掩盖真正的内容变更。出处信息写在 CLI 输出与 commit message 里。

🔴 临时约束：**只对新年份执行落盘**（issue #392）
------------------------------------------------
``scripts/monitor/check_before_live.py::_check_trading_calendar_coverage`` 的
``critical_holidays`` 目前**硬编码**要求 ``2026-02-15``（周日）/ ``2026-02-21``（周六）
/ ``2026-02-22``（周日）出现在名单里 —— 这三个都是周末，而本模块的口径**不写**周末。

⇒ 对**已覆盖的年份**（如 2026）执行 ``--write`` 会把它们删掉，把覆盖门打红。
   ``merge_year_holidays`` 只替换目标年份，所以 ``--year <次年> --write`` 不会碰到
   既有年份，是安全的用法；口径统一见 issue #392。

⚠️ 旧手写文件本身是自相矛盾的：它有 ``2026-01-03`` / ``02-15`` / ``02-21`` / ``02-22``
（节假日窗口内的周末），却没有 ``2026-10-03`` / ``10-04``（国庆窗口内的周六周日）
⇒ 找不到能复现它的规则。「工作日休市日」是唯一可机器复现的口径。
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
    - 丢掉周六/周日（周末本来就是休市，写进去是噪声，见模块 docstring）；
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
    """用新集合**替换**目标年份的条目，其他年份原样保留。"""
    kept = {day for day in existing if day.year != year}
    kept.update(day for day in holidays if day.year == year)
    return tuple(sorted(kept))


@dataclass(frozen=True)
class HolidayRefreshReport:
    """一次「由交易所口径生成/复核休市名单」的结果。"""

    year: int
    holidays: tuple[date, ...]
    existing: tuple[date, ...]
    added: tuple[date, ...]
    removed: tuple[date, ...]
    legacy_weekend_entries: tuple[date, ...]
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

    def as_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
            "complete": self.complete,
            "holidays": [d.isoformat() for d in self.holidays],
            "holidays_count": len(self.holidays),
            "open_days": self.open_days,
            "added": [d.isoformat() for d in self.added],
            "removed": [d.isoformat() for d in self.removed],
            "legacy_weekend_entries": [
                d.isoformat() for d in self.legacy_weekend_entries
            ],
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

    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    try:
        existing = parse_holiday_config(path.read_text(encoding="utf-8"))
    except OSError:
        existing = ()

    existing_year = {day for day in existing if day.year == year}
    new_set = set(holidays)
    added = tuple(sorted(new_set - existing_year))
    removed = tuple(sorted(existing_year - new_set))
    legacy_weekend = tuple(sorted(day for day in removed if day.weekday() >= 5))

    return HolidayRefreshReport(
        year=year,
        holidays=holidays,
        existing=existing,
        added=added,
        removed=removed,
        legacy_weekend_entries=legacy_weekend,
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
    lines.append(f"  新增 {len(report.added)}: {[d.isoformat() for d in report.added]}")
    lines.append(f"  删除 {len(report.removed)}: {[d.isoformat() for d in report.removed]}")
    if report.legacy_weekend_entries:
        lines.append(
            "  ⚠️ 其中落在周末的旧条目（生成口径不含，判定结果不受影响）: "
            f"{[d.isoformat() for d in report.legacy_weekend_entries]}"
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
