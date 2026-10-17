"""题材（概念板块）日快照历史库 —— 只追加、幂等、本地 CSV。

为什么需要它
------------
``data/concept_board.py`` 只保留**当日一个截面**（``pit_cache/concept_board.csv``
每次抓取覆盖写）。因此「这个题材连涨了几天 / 周线是否刚突破」这类问题在 AQSP 里
**无法回答**——不是算法缺，是数据没有历史。

本模块把每日截面追加成历史表，供 :mod:`aqsp.features.theme_index` 聚合成
日 / 周 / 月 K。历史**从首次积累日开始，不可回补**（东财不提供板块历史接口，
``push2`` 全出口被切，见 #333）。

存储约定
--------
- 落点：``runtime_data_root()/pit_cache/theme_history.csv``（与 ``concept_board.csv``
  同根同目录，遵循 #336 的「写读同源」约定）。**调用时**解析 env，不在 import 期固化。
- 列：``trade_date, board_name, change_pct, up_count, down_count, main_net_inflow``。
- 幂等：同 ``trade_date`` 的旧行**先删再写**。重跑同一天不会产生重复行，
  也能修正当日抓到的错值。
- 排序：``(trade_date, board_name)`` 升序，便于人眼 diff 与逐日切片。
- 原子写：先写 ``.tmp`` 再 ``os.replace``，避免中途崩溃留下半截文件。

两道防线
--------
1. :data:`MIN_BOARDS_PER_SNAPSHOT`：一次快照少于该板块数就**拒绝落盘**并抛
   :class:`~aqsp.core.errors.DataError`。东财当前返回 500+ 个概念板块，若某次只回来
   十几个（分页抖动 / 风控降级），按「覆盖当日」的语义会把一整天数据洗掉。
2. 写入失败不静默：抛 ``DataError``，由调用方（``fetch_concept_board.py``）决定
   降级策略——那里的策略是**只告警、不影响 CSV 缓存产物**，因为历史库是增强项。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.runtime import runtime_data_root

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from aqsp.data.concept_board import ConceptBoardItem

HISTORY_FILENAME = "theme_history.csv"

COLUMNS: tuple[str, ...] = (
    "trade_date",
    "board_name",
    "change_pct",
    "up_count",
    "down_count",
    "main_net_inflow",
)

#: 单次快照的最低板块数。低于此值视为抓取不完整，拒绝覆盖当日数据。
MIN_BOARDS_PER_SNAPSHOT = 100

_DATE_TEXT_LENGTH = 10


def _default_cache_path() -> Path:
    """默认落点 ``runtime_data_root()/pit_cache/theme_history.csv``（调用时解析 env）。"""
    base = runtime_data_root() / "pit_cache"
    base.mkdir(parents=True, exist_ok=True)
    return base / HISTORY_FILENAME


def _resolve_path(path: Optional[os.PathLike[str] | str]) -> Path:
    if path is None:
        return _default_cache_path()
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _normalize_trade_date(trade_date: object) -> str:
    """把交易日收成 ``YYYY-MM-DD``；非法格式直接抛错（不猜）。"""
    text = str(trade_date or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) < _DATE_TEXT_LENGTH:
        raise DataError(f"theme_history: 非法交易日 {trade_date!r}（期望 YYYY-MM-DD）")
    text = text[:_DATE_TEXT_LENGTH]
    try:
        pd.Timestamp(text)
    except (ValueError, TypeError) as exc:
        raise DataError(f"theme_history: 非法交易日 {trade_date!r}（{exc}）") from exc
    return text


def _row_from_item(trade_date: str, item: "ConceptBoardItem") -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "board_name": str(getattr(item, "board_name", "") or "").strip(),
        "change_pct": float(getattr(item, "change_pct", 0.0) or 0.0),
        "up_count": int(getattr(item, "up_count", 0) or 0),
        "down_count": int(getattr(item, "down_count", 0) or 0),
        "main_net_inflow": float(getattr(item, "main_net_inflow", 0.0) or 0.0),
    }


def empty_history() -> pd.DataFrame:
    """空历史表（有列无行），让调用方不必到处判 ``None``。"""
    return pd.DataFrame({column: pd.Series(dtype="object") for column in COLUMNS})


def load_board_history(*, path: Optional[os.PathLike[str] | str] = None) -> pd.DataFrame:
    """读取历史表。

    文件不存在 ⇒ 返回 :func:`empty_history`（**不是** ``None``、不抛错：首次运行
    的正常状态）；文件损坏 ⇒ 抛 :class:`DataError`（损坏要人看，不能静默当空表，
    否则下一次写入会把能救的数据一起洗掉）。
    """
    target = _resolve_path(path)
    if not target.exists():
        return empty_history()
    try:
        frame = pd.read_csv(target, dtype={"trade_date": str, "board_name": str})
    except Exception as exc:  # noqa: BLE001 - 统一转成 DataError
        raise DataError(f"theme_history: 读取失败 {target}（{exc}）") from exc
    missing = [column for column in COLUMNS if column not in frame.columns]
    if missing:
        raise DataError(f"theme_history: {target} 缺列 {missing}")
    return frame.loc[:, list(COLUMNS)]


def append_board_snapshot(
    items: Iterable["ConceptBoardItem"],
    *,
    trade_date: object,
    path: Optional[os.PathLike[str] | str] = None,
    min_boards: int = MIN_BOARDS_PER_SNAPSHOT,
    force: bool = False,
) -> int:
    """把某一天的板块快照写入历史表（同交易日旧行先删再写）。

    :param items: :class:`~aqsp.data.concept_board.ConceptBoardItem` 序列。
    :param trade_date: 交易日（``YYYY-MM-DD`` / ``YYYYMMDD`` / ``date``）。
    :param min_boards: 低于该板块数视为抓取不完整 ⇒ 抛 ``DataError``（``force`` 可绕过）。
    :param force: 明确知道这次数据不全但仍要写（人工补数场景）。
    :return: 本次写入的行数。

    ⚠️ 幂等语义是「**替换当日**」：调用方拿到的必须是一个完整快照。
    不完整的快照会被 :data:`MIN_BOARDS_PER_SNAPSHOT` 拦下，而不是静默覆盖。
    """
    day = _normalize_trade_date(trade_date)
    rows = [_row_from_item(day, item) for item in items]
    rows = [row for row in rows if row["board_name"]]
    if not rows:
        raise DataError(f"theme_history: {day} 快照为空，拒绝写入")
    if len(rows) < min_boards and not force:
        raise DataError(
            f"theme_history: {day} 快照仅 {len(rows)} 个板块（门槛 {min_boards}），"
            "疑似抓取不完整，拒绝覆盖当日数据（确需写入请传 force=True）"
        )

    target = _resolve_path(path)
    existing = load_board_history(path=target)
    if not existing.empty:
        existing = existing[existing["trade_date"].astype(str) != day]
    merged = pd.concat([existing, pd.DataFrame(rows, columns=list(COLUMNS))], ignore_index=True)
    merged = merged.sort_values(["trade_date", "board_name"], kind="stable").reset_index(drop=True)

    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        merged.to_csv(tmp, index=False)
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise DataError(f"theme_history: 写入失败 {target}（{exc}）") from exc
    return len(rows)


def board_change_series(
    history: pd.DataFrame,
    board_name: str,
) -> tuple[tuple[str, float | None], ...]:
    """从历史表切出某板块的 ``(trade_date, change_pct)`` 序列，直接喂给
    :func:`aqsp.features.theme_index.build_vendor_series`。

    板块不存在 ⇒ 空元组。``change_pct`` 无法解析 ⇒ ``None``（由指数层跳过，
    这里**不补 0**）。
    """
    if history.empty or "board_name" not in history.columns:
        return ()
    name = str(board_name or "").strip()
    subset = history[history["board_name"].astype(str).str.strip() == name]
    if subset.empty:
        return ()
    subset = subset.sort_values("trade_date", kind="stable")
    out: list[tuple[str, float | None]] = []
    for trade_date, raw in zip(subset["trade_date"], subset["change_pct"]):
        try:
            pct: float | None = float(raw)
        except (TypeError, ValueError):
            pct = None
        out.append((str(trade_date)[:_DATE_TEXT_LENGTH], pct))
    return tuple(out)


__all__ = [
    "COLUMNS",
    "HISTORY_FILENAME",
    "MIN_BOARDS_PER_SNAPSHOT",
    "append_board_snapshot",
    "board_change_series",
    "empty_history",
    "load_board_history",
]
