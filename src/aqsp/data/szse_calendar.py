"""深交所官方交易日历源（SZSE ``monthList``）—— A 股日历的权威运行时来源。

背景（issue #314）
------------------
``config/trading_holidays.json`` 是**手写**文件，2026 年已发生两起事实错误
（#310 国庆多列、#312 春节缺列），且每年 12 月需要人工照抄交易所公告。
深交所公开接口 ``onepersistenthour/monthList`` 直接给出逐日 ``jybz``（交易标志），
可作为**不依赖 Tushare token** 的运行时日历权威源 —— 生产机没有
``TUSHARE_TOKEN``，此前 ``_load_runtime_calendar`` 恒返回 ``None``，
只能退到「周末 + 静态休市名单」的近似判定。

移植说明（许可证边界）
----------------------
端点与字段语义参考 ``jundizhou/easy-stock`` 的 ``review/trading_calendar.go``
（PolyForm Noncommercial 1.0.0，**非 OSI 开源**）。本模块为 AQSP **自研实现**，
未复制其代码；仅使用「公开端点 + 响应字段语义」这类事实信息。若本项目未来
商业化，此边界仍然成立（事实信息不受著作权保护）。

设计约束
--------
- :func:`parse_month_payload` 是**纯函数**，不碰网络，可单测；
- :class:`SzseCalendarSource` 负责取数 + 磁盘缓存（新鲜 24h / 断网 7 天内可用旧值）；
- **取数失败一律不抛异常**：返回 ``None``，让调用方回退静态日历。日历源故障
  不得升级成选股链路故障（与 ``cls_news`` 的容错方向一致）；
- 显式 ``User-Agent`` + ``Referer``：深交所接口无 UA 会拒绝；
- 字段语义漂移（未知 ``jybz``）按 **fail-closed** 处理（视为休市），并记 warning；
- **锚点月缺失即整体降级**：接口按整月返回（28–31 行）或整月为空，因此只有
  锚点月（目标日所在月）真的拿到数据，才认为覆盖成立。避免「邻月有数据 ⇒
  目标月缺失的日子被当成休市」这类静默错误。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from aqsp.core.http import HttpClientConfig, build_http_session
from aqsp.core.runtime import runtime_data_root
from aqsp.core.time import now_shanghai

logger = logging.getLogger(__name__)

SZSE_MONTH_LIST_URL = (
    "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
)

# 深交所接口对无 UA / 无 Referer 的请求直接拒绝（实测 2026-10-16）。
_SZSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://www.szse.cn/",
}

# 🔴 本源的 HTTP 配置刻意**不走全仓默认**（connect 5s / read 10s / retry 2）：
# ``is_trading_day()`` 是高频路径，境外出口（CI runner）连不上 szse.cn 时，
# 默认配置下每次调用要等满 3 次重试 × 超时。这里压到「一次、短超时」，
# 失败就快速降级回静态日历，把等待成本从数十秒降到个位数秒。
_SZSE_HTTP_CONFIG = HttpClientConfig(
    connect_timeout=3.0,
    read_timeout=6.0,
    max_retries=0,
    backoff_factor=0.0,
    backoff_max=0.0,
)

# 新鲜 TTL：命中则直接用磁盘缓存，不再打网络。
_FRESH_TTL = timedelta(hours=24)
# 陈旧 TTL：网络失败时，此前校验过的完整月份仍可短暂使用。
_STALE_TTL = timedelta(days=7)
# 进程内 memo：避免 ``is_trading_day()`` 热路径反复读盘。
_MEMO_TTL_SECONDS = 300.0
# 失败 memo：网络不可达时不要在同一个进程里反复重试（境外出口连不上 szse.cn）。
# 取 10 分钟——日历本来就有 24h TTL，短暂失败不值得让整个进程一直空转。
_MEMO_FAILURE_TTL_SECONDS = 600.0

# ``jybz`` 取值语义：1 = 交易日，0 = 休市。未知值按 fail-closed 视为休市。
_OPEN_FLAG = "1"
_CLOSED_FLAG = "0"

# key = f"{cache_dir}|{month}"，value = (monotonic 时间戳, 该月天数或 None)
_MEMO: dict[str, tuple[float, Optional[tuple["SzseCalendarDay", ...]]]] = {}


@dataclass(frozen=True)
class SzseCalendarDay:
    """深交所单日交易标志。"""

    trade_date: date
    is_open: bool


def month_key(value: date | datetime) -> str:
    """把日期归一成 ``YYYY-MM`` 的月份键。"""
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.year:04d}-{value.month:02d}"


def months_between(start: date, end: date) -> tuple[str, ...]:
    """返回 ``[start, end]`` 覆盖到的月份键（升序、去重）。"""
    if end < start:
        return ()
    keys: list[str] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        key = month_key(cursor)
        if key not in keys:
            keys.append(key)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return tuple(keys)


def clear_memo() -> None:
    """清空进程内 memo（测试与强制刷新用）。"""
    _MEMO.clear()


def parse_month_payload(payload: object, *, month: str) -> tuple[SzseCalendarDay, ...]:
    """从 ``monthList`` 响应解析整月日历；单条漂移跳过，**不抛异常**。

    真实结构::

        {"data": [{"zrxh": 5, "jybz": "0", "jyrq": "2026-10-01"}, ...]}

    ``zrxh`` 是自然序号（未使用）；``jybz`` 是交易标志；``jyrq`` 是交易日期。

    防御策略：
    - 顶层不是 dict、``data`` 不是 list ⇒ 返回空元组；
    - 单条缺 ``jyrq`` / 日期不可解析 ⇒ 跳过该条；
    - 日期**不属于请求月份** ⇒ 跳过（防上游串月导致的静默错配）；
    - ``jybz`` 非 ``"0"``/``"1"`` ⇒ fail-closed 视为休市并记 warning。
    """
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("data")
    if not isinstance(rows, list):
        return ()

    out: list[SzseCalendarDay] = []
    unknown_flags = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        raw_date = str(raw.get("jyrq") or "").strip()
        if not raw_date:
            continue
        try:
            trade_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if month_key(trade_date) != month:
            # 上游串月：宁可丢一条，也不要把别的月份的日子混进本月。
            continue
        flag = str(raw.get("jybz") or "").strip()
        if flag == _OPEN_FLAG:
            out.append(SzseCalendarDay(trade_date=trade_date, is_open=True))
        elif flag == _CLOSED_FLAG:
            out.append(SzseCalendarDay(trade_date=trade_date, is_open=False))
        else:
            unknown_flags += 1
            out.append(SzseCalendarDay(trade_date=trade_date, is_open=False))

    if unknown_flags:
        logger.warning(
            "szse_calendar: %s 有 %d 条未知 jybz 标志，已按休市处理（fail-closed）",
            month,
            unknown_flags,
        )
    return tuple(sorted(out, key=lambda item: item.trade_date))


def _default_cache_dir() -> Path:
    return runtime_data_root() / "pit_cache" / "szse_calendar"


class SzseCalendarSource:
    """深交所日历源：取数 + 磁盘缓存 + 容错回退。

    语义约定（关键）：

    - ``load_month`` 返回 ``None`` ⇒ **取数失败**（网络 / HTTP / 解析异常）；
    - ``load_month`` 返回 ``()`` ⇒ **取数成功但该月无数据**（例如尚未公布的下月）。

    二者必须区分：前者不可信，后者是可信的「暂无」。
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        session: object | None = None,
        timeout: float = 12.0,
        enabled: bool | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self._now = now or now_shanghai
        self._session = session
        self._timeout = timeout
        self._enabled = _enabled_from_env() if enabled is None else bool(enabled)

    # -- 内部 --------------------------------------------------------------

    def _cache_path(self, month: str) -> Path:
        return self._cache_dir / f"{month}.json"

    def _read_cache(self, month: str) -> tuple[datetime, tuple[SzseCalendarDay, ...]] | None:
        """读磁盘缓存。返回 ``None`` = 无缓存或缓存损坏。"""
        try:
            payload = json.loads(self._cache_path(month).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        fetched_raw = str(payload.get("fetched_at") or "").strip()
        days_raw = payload.get("days")
        if not fetched_raw or not isinstance(days_raw, dict):
            return None
        try:
            fetched_at = datetime.fromisoformat(fetched_raw)
        except ValueError:
            return None
        days: list[SzseCalendarDay] = []
        for raw_date, is_open in days_raw.items():
            try:
                days.append(
                    SzseCalendarDay(
                        trade_date=date.fromisoformat(str(raw_date)),
                        is_open=bool(is_open),
                    )
                )
            except ValueError:
                continue
        return fetched_at, tuple(sorted(days, key=lambda item: item.trade_date))

    def _write_cache(self, month: str, days: tuple[SzseCalendarDay, ...]) -> None:
        payload = {
            "month": month,
            "fetched_at": self._now().isoformat(timespec="seconds"),
            "source": SZSE_MONTH_LIST_URL,
            "days": {day.trade_date.isoformat(): day.is_open for day in days},
        }
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            target = self._cache_path(month)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(target)
        except OSError as exc:  # 缓存写失败不致命，下次重取即可
            logger.warning("szse_calendar: 写缓存失败 %s（%s）", month, exc)

    def _fetch(self, month: str) -> tuple[SzseCalendarDay, ...] | None:
        """取一个月。``None`` = 失败；``()`` = 成功但该月无数据。"""
        session = self._session
        try:
            if session is None:
                session = build_http_session(
                    config=_SZSE_HTTP_CONFIG, headers=_SZSE_HEADERS
                )
            response = session.get(  # type: ignore[attr-defined]
                SZSE_MONTH_LIST_URL,
                params={"month": month},
                timeout=self._timeout,
            )
            status = getattr(response, "status_code", 0)
            if status != 200:
                logger.warning("szse_calendar: %s HTTP %s", month, status)
                return None
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - 网络/SSL/JSON 失败一律降级
            logger.warning("szse_calendar: %s 取数失败（%s）", month, exc)
            return None
        return parse_month_payload(payload, month=month)

    # -- 对外 --------------------------------------------------------------

    def load_month(self, month: str) -> tuple[SzseCalendarDay, ...] | None:
        """取整月日历。

        - 返回 ``None`` ⇒ 取数失败（调用方应回退）；
        - 返回 ``()`` ⇒ 成功但该月无数据（例如尚未公布的下月）。
        """
        if not self._enabled:
            return None

        memo_key = f"{self._cache_dir}|{month}"
        stamp = time.monotonic()
        memo = _MEMO.get(memo_key)
        if memo is not None:
            memo_stamp, cached_value = memo
            ttl = (
                _MEMO_TTL_SECONDS
                if cached_value is not None
                else _MEMO_FAILURE_TTL_SECONDS
            )
            if stamp - memo_stamp < ttl:
                return cached_value

        now = self._now()
        cached = self._read_cache(month)
        if cached is not None:
            fetched_at, days = cached
            if now - fetched_at < _FRESH_TTL:
                _MEMO[memo_key] = (stamp, days)
                return days

        fresh = self._fetch(month)
        if fresh is not None:
            self._write_cache(month, fresh)
            _MEMO[memo_key] = (stamp, fresh)
            return fresh

        if cached is not None:
            fetched_at, days = cached
            if now - fetched_at < _STALE_TTL:
                logger.warning(
                    "szse_calendar: %s 取数失败，使用 %s 的陈旧缓存（7 天窗口内）",
                    month,
                    fetched_at.isoformat(timespec="seconds"),
                )
                _MEMO[memo_key] = (stamp, days)
                return days

        _MEMO[memo_key] = (stamp, None)
        return None

    def as_frame(
        self,
        start: date,
        end: date,
        *,
        anchor: date | None = None,
    ) -> pd.DataFrame | None:
        """返回与 Tushare 日历同 schema 的 DataFrame（``cal_date`` / ``is_open``）。

        ``anchor`` 是**必须拿到数据的锚点日**（通常是目标日）。只有锚点日所在月
        取数成功且非空，才返回 DataFrame；否则返回 ``None`` 触发上层回退。

        这条规则防的是一类静默错误：窗口跨月时，邻月有数据、目标月尚未公布，
        若照单返回，目标月缺失的日子会被 ``is_open_day_in_calendar`` 判成休市。
        """
        if end < start:
            return None
        anchor_month = month_key(anchor) if anchor is not None else None
        rows: list[SzseCalendarDay] = []
        anchor_ok = anchor_month is None
        for month in months_between(start, end):
            days = self.load_month(month)
            if days is None:
                # 取数失败：该月整体不可信。若它就是锚点月 ⇒ 放弃本次覆盖。
                if month == anchor_month:
                    return None
                continue
            if month == anchor_month:
                if not days:
                    return None
                anchor_ok = True
            rows.extend(day for day in days if start <= day.trade_date <= end)
        if not anchor_ok or not rows:
            return None
        return pd.DataFrame(
            {
                "cal_date": [day.trade_date.isoformat() for day in rows],
                "is_open": [1 if day.is_open else 0 for day in rows],
            }
        )


def _enabled_from_env() -> bool:
    """``AQSP_SZSE_CALENDAR=0`` 可关闭本源（离线环境 / 排障用）。默认开启。"""
    raw = str(os.getenv("AQSP_SZSE_CALENDAR", "") or "").strip().lower()
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off"}


__all__ = [
    "SZSE_MONTH_LIST_URL",
    "SzseCalendarDay",
    "SzseCalendarSource",
    "clear_memo",
    "month_key",
    "months_between",
    "parse_month_payload",
]
