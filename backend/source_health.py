"""数据源健康台账（进程内、只读观测）。

## 为什么需要

源被封或挂掉时，各接口会**静默返回空载荷**：`/api/industry` 给
`{"top":[],"bottom":[],"total":0}`、`/api/global/indices` 给 `{"data":[]}`。
页面显示「未返回」，但用户和运维看到的是同一句话——**分不清「源不可达」和
「今天确实没数据」**。

2026-10-19 的真实场景：prod 出口被东财按主机封禁（`push2`/`push2delay`/`push2his`
全断，`push2ex` 正常），排查时只能靠一条条手打探测才定位到范围。本模块把这件事
变成可查询的状态。

## 设计约束

- **进程内内存**，不落盘、不进 DB：观测数据重启即清空，不需要持久化。
- 接线在两处，都是「取数失败会静默返回空」的地方：
  1. `astock.em_get`（东财全部请求，覆盖 push2 / push2his / push2delay / push2ex /
     datacenter-web 等主机）；
  2. `market._sentiment` / `market._sectors` 的两个 akshare 源
     （`legulegu.com`、`data.10jqka.com.cn`）。
- **键是主机名**，键空间天然有限（不会无界增长）。凡是不打源的路径（缓存命中、
  本地计算）不记账——没有请求就没有可记的状态，硬记只会造出假的新鲜度。
- 只记成功/失败与错误摘要，**不记请求参数**；错误摘要里的查询串一律打码
  （`push2ex` 的 `ut` 属凭据类参数，不能进台账）。
- 记账失败**绝不能影响取数**：所有函数内部吞异常，调用方拿不到异常。
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone

_CST = timezone(timedelta(hours=8))

# 键是主机名，正常不会多；留一个上限纯粹是防御（防止有人把 URL 当 source 传进来）。
_MAX_SOURCES = 64
_MAX_ERROR_LEN = 200

_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}


def _iso(ts: float | None) -> str | None:
    """epoch 秒 → 上海时区 ISO；None 原样返回。

    刻意用 `fromtimestamp` 而不是 `now()`：这里只做**换算**，不产生新时刻，
    天然不违反「禁裸 datetime.now()」。
    """
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, _CST).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def clean_error(exc: object) -> str:
    """异常 → 可入库的一行摘要。

    两个必要的清洗：
    1. **去掉查询串**——`requests` 的异常消息里常带完整 URL，而 `push2ex` 的 `ut`
       是凭据类参数，不该进台账；
    2. 截断——避免把整段堆栈/HTML 塞进内存。
    """
    if isinstance(exc, BaseException):
        text = f"{type(exc).__name__}: {exc}"
    else:
        text = str(exc)
    text = text.split("\n", 1)[0]
    text = re.sub(r"\?[^\s'\")]*", "?<redacted>", text)
    return text[:_MAX_ERROR_LEN]


def record_ok(source: str) -> None:
    """记一次成功。会清零连续失败计数与「故障起点」。"""
    if not source:
        return
    try:
        now = time.time()
        with _LOCK:
            entry = _STATE.get(source)
            if entry is None:
                if len(_STATE) >= _MAX_SOURCES:
                    return
                entry = {
                    "ok_count": 0,
                    "error_count": 0,
                    "consecutive_errors": 0,
                    "last_ok_at": None,
                    "last_error_at": None,
                    "last_error": "",
                    "down_since": None,
                }
                _STATE[source] = entry
            entry["ok_count"] += 1
            entry["consecutive_errors"] = 0
            entry["down_since"] = None
            entry["last_ok_at"] = now
    except Exception:  # noqa: BLE001 —— 记账绝不能拖垮取数
        return


def record_error(source: str, exc: object = "") -> None:
    """记一次失败。首次失败时记下「故障起点」，连续失败累加。"""
    if not source:
        return
    try:
        now = time.time()
        with _LOCK:
            entry = _STATE.get(source)
            if entry is None:
                if len(_STATE) >= _MAX_SOURCES:
                    return
                entry = {
                    "ok_count": 0,
                    "error_count": 0,
                    "consecutive_errors": 0,
                    "last_ok_at": None,
                    "last_error_at": None,
                    "last_error": "",
                    "down_since": None,
                }
                _STATE[source] = entry
            entry["error_count"] += 1
            entry["consecutive_errors"] += 1
            if entry["down_since"] is None:
                entry["down_since"] = now
            entry["last_error_at"] = now
            entry["last_error"] = clean_error(exc)
    except Exception:  # noqa: BLE001
        return


def snapshot() -> list[dict]:
    """当前各源状态，**不健康的排前面**（运维第一眼要看到坏的那个）。

    每次返回新对象，调用方改不到内部状态。
    """
    try:
        with _LOCK:
            items = list(_STATE.items())
    except Exception:  # noqa: BLE001
        return []

    rows = []
    for source, e in items:
        rows.append(
            {
                "source": source,
                "healthy": e["consecutive_errors"] == 0,
                "consecutive_errors": e["consecutive_errors"],
                "ok_count": e["ok_count"],
                "error_count": e["error_count"],
                "last_ok_at": _iso(e["last_ok_at"]),
                "last_error_at": _iso(e["last_error_at"]),
                "last_error": e["last_error"],
                "down_since": _iso(e["down_since"]),
            }
        )
    # 不健康优先；同组内连续失败多的在前；再按名字稳定排序
    rows.sort(key=lambda r: (r["healthy"], -r["consecutive_errors"], r["source"]))
    return rows


def reset() -> None:
    """清空台账。仅供测试与手动复位。"""
    with _LOCK:
        _STATE.clear()
