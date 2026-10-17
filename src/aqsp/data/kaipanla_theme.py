"""开盘啦题材轮动 / 龙头梯队源 —— 取数 + 缓存 + 独立表（#404，对账依据 #398）。

端点
----
- ``POST https://duanxianxia.com/api/getPlateRotatData``（``from=kaipan&days=20``）
  题材轮动矩阵：行=排名槽、列=日期（最新在前），单元格带 ``code``/``name`` 属性
  与热度值。热度**带符号**（实测负值渲染绿色=跌，如 -25/-46/-92；正值红色），
  负值远小于正值 ⇒ 疑为「热度变化量」而非绝对水平（口径未定，存原值）。
- ``POST https://duanxianxia.com/api/getLongByPlate``（``platecode=<code>&days=20``）
  龙一~龙五梯队：响应无日期列，列 i 与轮动日期列 i 同窗口同序对齐
  （days=1 vs days=2 对齐验证过）。

为什么独立于 ``theme_history.csv``
----------------------------------
1. 热度口径独立：#398 对账排除「成交额/主力净流入/涨跌幅/振幅」四个假设
   （heat 与成交额仅中等排名相关 0.59）⇒ **存原值、不做跨板块比较**，
   不能塞进东财口径的 ``change_pct`` 列；
2. 轮动只覆盖 top-11（每日 7~11 个板块），与 theme_history 的完整快照语义
   （``MIN_BOARDS_PER_SNAPSHOT=100``）不同，混写会污染「覆盖当日」语义；
3. schema 不同：本模块的 ``rank``/``heat`` 在东财表里没有对应列。

解析契约（外部 HTML，fail-closed）
----------------------------------
- 响应是 JSON ``{first, html}``；html 是**畸形**表格：
  - ``code``/``name`` 在 ``<td>`` 标签**属性**上，不在单元格内容里
    （只取内容 ⇒ 静默 0 条记录，本仓库踩过同类「静默假阴性」坑）；
  - 龙榜的「当日无领涨」占位单元格**缺 ``</td>``**（实际是 ``</div>``）⇒
    **按 ``<td`` 边界切列**，按 ``</td>`` 会把占位列与次日列合并；
  - 龙一只数在 ``<div class='kline' code='...'>`` 的属性上。
- 单条记录缺 code/name 属性、热度非数字 ⇒ 跳过并计数（不猜列位置）；
- 整批无日期列 / 无记录 / 全是占位 ⇒ 抛 :class:`~aqsp.core.errors.DataError`；
- 解析出的日期若与交易日历对不上 ⇒ 计数告警（日历覆盖缺口不阻断）。

存储约定
--------
- 落点：``runtime_data_root()/pit_cache/kaipanla_theme_rotation.csv`` 与
  ``kaipanla_theme_leaders.csv``（调用时解析 env，不在 import 期固化）。
- 幂等：同 ``trade_date`` 旧行先删再写；原子写（.tmp + os.replace）。
- 排序：rotation 按 ``(trade_date, rank)``，leaders 按 ``(trade_date, board_code, leader_rank)``。
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.runtime import runtime_data_root

# ---------------------------------------------------------------------------
# 端点常量（#398 复验：runner 直连 200；prod/Mac 出口不可靠，只在 runner 抓）
# ---------------------------------------------------------------------------

KPL_ROTATION_URL = "https://duanxianxia.com/api/getPlateRotatData"
KPL_LEADERS_URL = "https://duanxianxia.com/api/getLongByPlate"
_KPL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://duanxianxia.com/",
}
#: 轮动矩阵默认窗口（交易日数，一次请求的历史深度）
DEFAULT_DAYS = 20

#: 龙榜请求间隔（秒）：开盘啦低频率未见限流，但 20 天窗口可含 60+ 板块码，
#: 连发有频控风险（#398 东财 push2delay 连发即 502 的教训）⇒ 保守 1.5s。
LEADER_REQUEST_INTERVAL = 1.5

#: 单请求重试次数与退避基数（秒）：开盘啦端点偶发读超时（实测 30s 超时过一次），
#: 请求级 2 次重试 + 指数退避（与 preload 中继的 4 次退避互补）。
_POST_RETRIES = 2
_POST_BACKOFF_BASE = 2.0

#: 无领涨占位文案（畸形单元格：缺 </td>）
NO_LEADER_PLACEHOLDER = "当日无领涨"

ROTATION_FILENAME = "kaipanla_theme_rotation.csv"
LEADERS_FILENAME = "kaipanla_theme_leaders.csv"

ROTATION_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "board_code",
    "board_name",
    "rank",
    "heat",
)
LEADERS_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "board_code",
    "board_name",
    "leader_rank",
    "stock_code",
    "stock_name",
)

_DATE_TEXT_LENGTH = 10
_RANK_CELL_PATTERN = re.compile(r"<span class='rank'[^>]*>(\d+)</span>")
_TD_TAG_PATTERN = re.compile(r"<td([^>]*)>", re.I)
#: 热度值 span：**带符号**（实测负值渲染绿色=跌，如 -25/-46/-92；正值红色）。
#: 负值远小于正值 ⇒ 疑为「热度变化量」而非绝对水平（口径未定，存原值）。
_HEAT_SPAN_PATTERN = re.compile(r"<span[^>]*>(-?\d+)</span>")
_CODE_ATTR_PATTERN = re.compile(r"code='([^']+)'")
_NAME_ATTR_PATTERN = re.compile(r"name='([^']+)'")
_LEADER_DIV_PATTERN = re.compile(r"<div class='kline' code='([^']+)'>\s*<span>([^<]+)</span>([^<]*)</div>")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RotationRecord:
    """轮动矩阵一行：某交易日某排名槽的板块 + 热度（原值，禁跨板块比较）。"""

    trade_date: str
    board_code: str
    board_name: str
    rank: int
    heat: int


@dataclass(frozen=True)
class LeaderRecord:
    """龙榜一行：某交易日某板块的龙 n 梯队成员。"""

    trade_date: str
    board_code: str
    board_name: str
    leader_rank: int
    stock_code: str
    stock_name: str


@dataclass(frozen=True)
class ParseStats:
    """解析统计：anomalies 计数用于告警（schema 漂移检测），不阻断写入。"""

    total: int
    anomalies: int
    detail: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 日期工具
# ---------------------------------------------------------------------------


def _normalize_trade_date(trade_date: object) -> str:
    """交易日收成 ``YYYY-MM-DD``；非法格式直接抛错（不猜）。"""
    text = str(trade_date or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) < _DATE_TEXT_LENGTH:
        raise DataError(f"kaipanla: 非法交易日 {trade_date!r}（期望 YYYY-MM-DD）")
    text = text[:_DATE_TEXT_LENGTH]
    try:
        pd.Timestamp(text)
    except (ValueError, TypeError) as exc:
        raise DataError(f"kaipanla: 非法交易日 {trade_date!r}（{exc}）") from exc
    return text


def _is_well_formed_date(text: str) -> bool:
    try:
        pd.Timestamp(text)
    except (ValueError, TypeError):
        return False
    return True


def _warn_non_trading_days(dates: Iterable[str]) -> list[str]:
    """日期与交易日历对不上 ⇒ 计数告警（日历覆盖缺口不阻断，#314 起 2027 仅告警）。"""
    from aqsp.core.time import is_trading_day

    bad: list[str] = []
    for text in dates:
        try:
            day = pd.Timestamp(text).date()
        except (ValueError, TypeError):
            continue
        try:
            if not is_trading_day(day):
                bad.append(text)
        except Exception:  # noqa: BLE001 - 日历不可用时跳过校验，不阻断
            continue
    return bad


# ---------------------------------------------------------------------------
# 轮动解析
# ---------------------------------------------------------------------------


def parse_rotation_payload(payload: object) -> tuple[list[str], list[RotationRecord], ParseStats]:
    """解析轮动响应（JSON 字符串或已解析 dict）。

    :return: (dates, records, stats)；dates 最新在前，与龙榜列序同窗口。
    :raises DataError: 无日期列 / 无任何有效记录（整批失败，fail-closed）。

    🔴 契约陷阱（#398 实测）：``code``/``name`` 在 ``<td>`` 标签**属性**上，
    热度在单元格内容的嵌套 span 里。只取 td 内容会静默拿到 0 条。
    """
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DataError(f"kaipanla: 轮动响应不是合法 JSON（{exc}）") from exc
    if not isinstance(payload, dict):
        raise DataError(f"kaipanla: 轮动响应不是对象（{type(payload).__name__}）")
    table = html_mod.unescape(str(payload.get("html") or ""))
    if not table.strip():
        raise DataError("kaipanla: 轮动响应 html 为空")

    dates: list[str] = []
    records: list[RotationRecord] = []
    anomalies: list[str] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    for tr in rows:
        # 同时捕获 td 标签属性（code/name 在标签上）与内容（热度在内容里）
        tds = _TD_TAG_PATTERN.findall(tr)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if not tds and not cells:
            continue  # 空行（表格尾部的布局 tr，非数据）
        if len(tds) != len(cells):
            anomalies.append("tr 内 td 标签/内容数不匹配，跳过整行")
            continue
        # 表头行：排名 + 日期序列
        if "排名" in cells[0]:
            dates = [c.strip() for c in cells[1:]]
            continue
        rank_m = _RANK_CELL_PATTERN.search(cells[0])
        if not rank_m:
            continue
        rank = int(rank_m.group(1))
        for i, (tag, content) in enumerate(zip(tds[1:], cells[1:])):
            if i >= len(dates):
                break
            code_m = _CODE_ATTR_PATTERN.search(tag)
            name_m = _NAME_ATTR_PATTERN.search(tag)
            heat_m = _HEAT_SPAN_PATTERN.findall(content)
            if not (code_m and name_m and heat_m):
                anomalies.append(f"第 {rank} 名第 {dates[i]} 列缺 code/name/热度，跳过")
                continue
            date_text = dates[i].strip()
            if not _is_well_formed_date(date_text):
                anomalies.append(f"非法日期列 {date_text!r}，跳过")
                continue
            records.append(
                RotationRecord(
                    trade_date=_normalize_trade_date(date_text),
                    board_code=code_m.group(1).strip(),
                    board_name=html_mod.unescape(name_m.group(1).strip()),
                    rank=rank,
                    heat=int(heat_m[-1]),
                )
            )

    if not dates:
        raise DataError("kaipanla: 轮动响应无日期列表头，疑似 schema 漂移")
    if not records:
        raise DataError("kaipanla: 轮动响应无任何有效板块记录，疑似 schema 漂移")
    stats = ParseStats(total=len(records), anomalies=len(anomalies), detail=tuple(anomalies[:10]))
    return dates, records, stats


# ---------------------------------------------------------------------------
# 龙榜解析
# ---------------------------------------------------------------------------


def parse_leaders_payload(
    payload: object,
    *,
    board_code: str,
    dates: list[str],
) -> tuple[list[LeaderRecord], ParseStats]:
    """解析龙榜响应并与轮动日期列按位置对齐（列 i ↔ dates[i]，最新在前）。

    :raises DataError: 响应畸形 / 列数与日期数不一致（对齐假设被破坏）。
    🔴 契约陷阱（#398 实测）：占位单元格缺 ``</td>`` ⇒ 按 ``<td`` 边界切列；
    全是「当日无领涨」占位 = 无数据（不抛错，返回空记录 + 0 异常）。
    """
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DataError(f"kaipanla: 龙榜响应不是合法 JSON（{exc}）") from exc
    if not isinstance(payload, dict):
        raise DataError(f"kaipanla: 龙榜响应不是对象（{type(payload).__name__}）")
    table = html_mod.unescape(str(payload.get("html") or ""))
    if not table.strip():
        raise DataError("kaipanla: 龙榜响应 html 为空")

    # 按 <td 边界切列：占位单元格缺 </td>（实为 </div>），按 </td> 切会串行
    columns = _split_columns_by_tag(table)
    if columns and "领涨" in columns[0]:
        columns = columns[1:]
    if not columns:
        raise DataError("kaipanla: 龙榜响应无日列")
    if len(columns) != len(dates):
        raise DataError(
            f"kaipanla: 龙榜列数 {len(columns)} 与轮动日期数 {len(dates)} 不一致，对齐假设被破坏"
        )

    records: list[LeaderRecord] = []
    anomalies: list[str] = []
    for col, date_text in zip(columns, dates):
        date_text = _normalize_trade_date(date_text)
        if NO_LEADER_PLACEHOLDER in col:
            continue  # 当日无领涨 = 无数据（不是「今天没龙头」）
        found = _LEADER_DIV_PATTERN.findall(col)
        if not found:
            anomalies.append(f"{date_text} 列无有效龙一只数（非占位），跳过")
            continue
        for code, rank_text, name_text in found:
            code = code.strip()
            name = name_text.strip()
            if not code or not name:
                anomalies.append(f"{date_text} 列龙只缺 code/name，跳过")
                continue
            leader_rank = _parse_leader_rank(rank_text)
            if leader_rank is None:
                anomalies.append(f"{date_text} 列龙只排名 {rank_text!r} 不可解析，跳过")
                continue
            records.append(
                LeaderRecord(
                    trade_date=date_text,
                    board_code=board_code,
                    board_name="",
                    leader_rank=leader_rank,
                    stock_code=code,
                    stock_name=name,
                )
            )
    stats = ParseStats(total=len(records), anomalies=len(anomalies), detail=tuple(anomalies[:10]))
    return records, stats


def _split_columns_by_tag(table: str) -> list[str]:
    """按 ``<td`` 边界切列（占位单元格缺 ``</td>``，不能按 ``</td>`` 切）。"""
    parts = re.split(r"<td[^>]*>", table, flags=re.I)
    # 首段是 <table 前的残余（通常为空），丢弃
    return [p for p in parts[1:]]


def _parse_leader_rank(text: str) -> Optional[int]:
    """「龙一」~「龙五」-> 1~5；其他返回 None（不猜）。"""
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    text = text.strip()
    if text.startswith("龙") and len(text) == 2 and text[1] in digits:
        return digits[text[1]]
    return None


# ---------------------------------------------------------------------------
# 存储（独立表：幂等同交易日 + 原子写）
# ---------------------------------------------------------------------------


def _rotation_cache_path(path: Optional[os.PathLike[str] | str] = None) -> Path:
    if path is not None:
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
    base = runtime_data_root() / "pit_cache"
    base.mkdir(parents=True, exist_ok=True)
    return base / ROTATION_FILENAME


def _leaders_cache_path(path: Optional[os.PathLike[str] | str] = None) -> Path:
    if path is not None:
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
    base = runtime_data_root() / "pit_cache"
    base.mkdir(parents=True, exist_ok=True)
    return base / LEADERS_FILENAME


def empty_rotation() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in ROTATION_COLUMNS})


def empty_leaders() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in LEADERS_COLUMNS})


def _load_table(path: Path, columns: tuple[str, ...], label: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    try:
        frame = pd.read_csv(path, dtype={"trade_date": str, "board_code": str, "stock_code": str})
    except Exception as exc:  # noqa: BLE001 - 统一转成 DataError
        raise DataError(f"kaipanla: {label} 读取失败 {path}（{exc}）") from exc
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DataError(f"kaipanla: {label} {path} 缺列 {missing}")
    return frame.loc[:, list(columns)]


def _atomic_write(path: Path, frame: pd.DataFrame, label: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        frame.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise DataError(f"kaipanla: {label} 写入失败 {path}（{exc}）") from exc


def _merge_by_date(
    frame: pd.DataFrame,
    rows: list[dict[str, object]],
    columns: tuple[str, ...],
    day: str,
    sort_keys: list[str],
) -> pd.DataFrame:
    """同交易日旧行先删再写（幂等替换），整体重排序后原子写。"""
    if not frame.empty:
        frame = frame[frame["trade_date"].astype(str) != day]
    merged = pd.concat([frame, pd.DataFrame(rows, columns=list(columns))], ignore_index=True)
    merged = merged.sort_values(sort_keys, kind="stable").reset_index(drop=True)
    return merged


def append_rotation_records(
    records: Iterable[RotationRecord],
    *,
    path: Optional[os.PathLike[str] | str] = None,
) -> int:
    """把（可跨多个交易日的）轮动记录写入独立表；同交易日旧行先删再写。"""
    rows = [
        {
            "trade_date": r.trade_date,
            "board_code": r.board_code,
            "board_name": r.board_name,
            "rank": int(r.rank),
            "heat": int(r.heat),
        }
        for r in records
    ]
    if not rows:
        raise DataError("kaipanla: 轮动记录为空，拒绝写入")
    frame = pd.DataFrame(rows, columns=list(ROTATION_COLUMNS))
    target = _rotation_cache_path(path)
    existing = _load_table(target, ROTATION_COLUMNS, "轮动表")
    merged = existing
    for day in sorted(frame["trade_date"].unique()):
        day_rows = frame[frame["trade_date"] == day].to_dict("records")
        merged = _merge_by_date(merged, day_rows, ROTATION_COLUMNS, day, ["trade_date", "rank"])
    _atomic_write(target, merged, "轮动表")
    return len(rows)


def append_leaders_records(
    records: Iterable[LeaderRecord],
    *,
    board_name: str = "",
    path: Optional[os.PathLike[str] | str] = None,
) -> int:
    """把龙榜记录写入独立表；同（交易日, 板块）旧行先删再写。

    :param board_name: 板块名（龙榜响应不含板块名，由调用方从轮动表补）。
    """
    rows = [
        {
            "trade_date": r.trade_date,
            "board_code": r.board_code,
            "board_name": board_name or r.board_name,
            "leader_rank": int(r.leader_rank),
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
        }
        for r in records
    ]
    if not rows:
        return 0
    frame = pd.DataFrame(rows, columns=list(LEADERS_COLUMNS))
    target = _leaders_cache_path(path)
    for (day, code), group in frame.groupby(["trade_date", "board_code"], sort=True):
        day_rows = group.to_dict("records")
        existing = _load_table(target, LEADERS_COLUMNS, "龙榜表")
        if not existing.empty:
            mask = ~(
                (existing["trade_date"].astype(str) == day)
                & (existing["board_code"].astype(str) == code)
            )
            existing = existing[mask]
        merged = pd.concat([existing, pd.DataFrame(day_rows)], ignore_index=True)
        merged = merged.sort_values(
            ["trade_date", "board_code", "leader_rank"], kind="stable"
        ).reset_index(drop=True)
        _atomic_write(target, merged, "龙榜表")
    return len(rows)


def load_rotation(*, path: Optional[os.PathLike[str] | str] = None) -> pd.DataFrame:
    return _load_table(_rotation_cache_path(path), ROTATION_COLUMNS, "轮动表")


def load_leaders(*, path: Optional[os.PathLike[str] | str] = None) -> pd.DataFrame:
    return _load_table(_leaders_cache_path(path), LEADERS_COLUMNS, "龙榜表")


# ---------------------------------------------------------------------------
# 取数源
# ---------------------------------------------------------------------------


class KaipanlaThemeSource:
    """开盘啦源：取数 + 落两张独立表。失败抛 :class:`DataError`（不静默）。"""

    def __init__(
        self,
        *,
        days: int = DEFAULT_DAYS,
        timeout: int = 30,
        leader_interval: float = LEADER_REQUEST_INTERVAL,
        rotation_path: Optional[os.PathLike[str] | str] = None,
        leaders_path: Optional[os.PathLike[str] | str] = None,
    ) -> None:
        self.days = days
        self.timeout = timeout
        self._leader_interval = leader_interval
        self._rotation_path = rotation_path
        self._leaders_path = leaders_path
        self.stats: dict[str, ParseStats] = {}

    # -- HTTP ---------------------------------------------------------------

    def _post(self, url: str, data: dict[str, str]) -> object:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise DataError(f"kaipanla: 缺少依赖 requests（{exc}）") from exc
        last_exc: Exception | None = None
        for attempt in range(_POST_RETRIES + 1):
            try:
                # 开盘啦是外部 HTML 契约：Session 级 trust_env=False 禁环境代理，
                # 避免出口画像受代理干扰（Mac 走代理 403 / runner 直连 200，见 #398）。
                # ⚠️ trust_env 是 Session 参数，不是 request 参数（曾因此报
                # unexpected keyword argument）。
                session = requests.Session()
                session.trust_env = False
                response = session.post(
                    url, data=data, headers=_KPL_HEADERS, timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - fail-closed：统一抛 DataError
                last_exc = exc
                if attempt < _POST_RETRIES:
                    time.sleep(_POST_BACKOFF_BASE * (2**attempt))
        raise DataError(f"kaipanla: 请求失败 {url}（{last_exc}）")

    def fetch_rotation(self) -> tuple[list[str], list[RotationRecord]]:
        payload = self._post(KPL_ROTATION_URL, {"from": "kaipan", "days": str(self.days), "dates": ""})
        dates, records, stats = parse_rotation_payload(payload)
        bad_days = _warn_non_trading_days(dates)
        if bad_days:
            self.stats["rotation"] = ParseStats(
                total=stats.total,
                anomalies=stats.anomalies + len(bad_days),
                detail=stats.detail + (f"非交易日日期 {bad_days[:3]}…",),
            )
        else:
            self.stats["rotation"] = stats
        return dates, records

    def fetch_leaders(self, board_code: str, dates: list[str]) -> list[LeaderRecord]:
        payload = self._post(
            KPL_LEADERS_URL, {"platecode": board_code, "days": str(self.days), "dates": ""}
        )
        records, stats = parse_leaders_payload(payload, board_code=board_code, dates=dates)
        self.stats[f"leaders:{board_code}"] = stats
        return records

    # -- 一站式 ---------------------------------------------------------------

    def load(self, *, with_leaders: bool = True) -> dict[str, int]:
        """抓轮动 + 可选各板块龙榜，落两张独立表，返回行数统计。"""
        dates, rotation = self.fetch_rotation()
        written_rotation = append_rotation_records(rotation, path=self._rotation_path)
        written_leaders = 0
        if with_leaders:
            board_names = {r.board_code: r.board_name for r in rotation}
            for index, code in enumerate(sorted(board_names)):
                if index and self._leader_interval:
                    time.sleep(self._leader_interval)
                records = self.fetch_leaders(code, dates)
                if records:
                    written_leaders += append_leaders_records(
                        records, board_name=board_names[code], path=self._leaders_path
                    )
            # 首日若所有板块都无龙头（全占位），表文件不会被创建 ⇒ 主动落空表，
            # 否则 relay 的 rsync 会因源文件不存在而失败。
            leaders_target = _leaders_cache_path(self._leaders_path)
            if not leaders_target.exists():
                _atomic_write(leaders_target, empty_leaders(), "龙榜表")
        return {"rotation": written_rotation, "leaders": written_leaders}


__all__ = [
    "DEFAULT_DAYS",
    "LEADERS_COLUMNS",
    "LEADERS_FILENAME",
    "LEADER_REQUEST_INTERVAL",
    "NO_LEADER_PLACEHOLDER",
    "ROTATION_COLUMNS",
    "ROTATION_FILENAME",
    "KaipanlaThemeSource",
    "LeaderRecord",
    "ParseStats",
    "RotationRecord",
    "append_leaders_records",
    "append_rotation_records",
    "empty_leaders",
    "empty_rotation",
    "load_leaders",
    "load_rotation",
    "parse_leaders_payload",
    "parse_rotation_payload",
]