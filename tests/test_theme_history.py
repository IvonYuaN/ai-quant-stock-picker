"""`aqsp.data.theme_history` 单测：题材历史库的幂等、完整性门、路径同源。

守卫重点：
- **同交易日幂等**：重跑不产生重复行，且能修正当日错值（替换而非追加）；
- **不完整快照拒绝覆盖**：一次只回来十几个板块时，绝不能把一整天数据洗掉；
- **路径同源**：默认落点 = ``runtime_data_root()/pit_cache/``，且调用时解析 env
  （#336 那类「写 /tmp 读 repo 根」的静默失效不能再出现）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.concept_board import ConceptBoardItem
from aqsp.data.theme_history import (
    COLUMNS,
    HISTORY_FILENAME,
    MIN_BOARDS_PER_SNAPSHOT,
    append_backfill_rows,
    append_board_snapshot,
    board_change_series,
    empty_history,
    load_board_history,
)


def _item(name: str, change_pct: float, *, up: int = 10, down: int = 2, inflow: float = 1.5):
    return ConceptBoardItem(
        board_code=f"BK{abs(hash(name)) % 10000:04d}",
        board_name=name,
        up_count=up,
        down_count=down,
        change_pct=change_pct,
        main_net_inflow=inflow,
        main_net_ratio=0.5,
    )


def _snapshot(count: int = MIN_BOARDS_PER_SNAPSHOT, *, offset: float = 0.0) -> list[ConceptBoardItem]:
    return [_item(f"板块{idx:03d}", offset + idx * 0.1) for idx in range(count)]


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def test_append_creates_file_with_expected_columns(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    written = append_board_snapshot(_snapshot(), trade_date="2026-10-12", path=target)

    assert written == MIN_BOARDS_PER_SNAPSHOT
    frame = pd.read_csv(target)
    assert tuple(frame.columns) == COLUMNS
    assert frame["trade_date"].unique().tolist() == ["2026-10-12"]


def test_append_is_idempotent_for_same_trade_date(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    append_board_snapshot(_snapshot(), trade_date="2026-10-12", path=target)
    append_board_snapshot(_snapshot(), trade_date="2026-10-12", path=target)

    frame = pd.read_csv(target)
    assert len(frame) == MIN_BOARDS_PER_SNAPSHOT


def test_append_replaces_corrected_values_for_same_date(tmp_path: Path) -> None:
    """当日值抓错了，重跑必须能**改掉**，而不是留两份并存。"""
    target = tmp_path / "theme_history.csv"

    append_board_snapshot([_item("AI应用", 1.0)], trade_date="2026-10-12", path=target, force=True)
    append_board_snapshot([_item("AI应用", 9.9)], trade_date="2026-10-12", path=target, force=True)

    frame = pd.read_csv(target)
    assert len(frame) == 1
    assert frame.loc[0, "change_pct"] == pytest.approx(9.9)


def test_append_keeps_other_dates_intact(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    append_board_snapshot([_item("AI应用", 1.0)], trade_date="2026-10-12", path=target, force=True)
    append_board_snapshot([_item("AI应用", 2.0)], trade_date="2026-10-13", path=target, force=True)

    frame = pd.read_csv(target)
    assert frame["trade_date"].tolist() == ["2026-10-12", "2026-10-13"]
    assert frame["change_pct"].tolist() == pytest.approx([1.0, 2.0])


def test_append_sorts_by_date_then_board(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    append_board_snapshot(
        [_item("B板块", 1.0), _item("A板块", 2.0)], trade_date="2026-10-13", path=target, force=True
    )
    append_board_snapshot([_item("C板块", 3.0)], trade_date="2026-10-12", path=target, force=True)

    frame = pd.read_csv(target)
    assert frame["trade_date"].tolist() == ["2026-10-12", "2026-10-13", "2026-10-13"]
    assert frame["board_name"].tolist() == ["C板块", "A板块", "B板块"]


def test_append_accepts_compact_and_date_objects(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    append_board_snapshot([_item("AI应用", 1.0)], trade_date="20261012", path=target, force=True)
    append_board_snapshot(
        [_item("AI应用", 2.0)], trade_date=pd.Timestamp("2026-10-13").date(), path=target, force=True
    )

    frame = pd.read_csv(target)
    assert frame["trade_date"].tolist() == ["2026-10-12", "2026-10-13"]


def test_append_rejects_incomplete_snapshot(tmp_path: Path) -> None:
    """抓取不完整（远少于门槛）⇒ 拒绝覆盖，且**不留下任何半截文件**。"""
    target = tmp_path / "theme_history.csv"
    append_board_snapshot(_snapshot(), trade_date="2026-10-12", path=target)

    with pytest.raises(DataError, match="拒绝覆盖当日数据"):
        append_board_snapshot(_snapshot(5), trade_date="2026-10-13", path=target)

    frame = pd.read_csv(target)
    assert frame["trade_date"].unique().tolist() == ["2026-10-12"]


def test_append_force_allows_incomplete_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    written = append_board_snapshot(
        _snapshot(3), trade_date="2026-10-12", path=target, force=True
    )

    assert written == 3


def test_append_rejects_empty_snapshot(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="快照为空"):
        append_board_snapshot([], trade_date="2026-10-12", path=tmp_path / "h.csv")


def test_append_rejects_blank_board_names(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="快照为空"):
        append_board_snapshot([_item("  ", 1.0)], trade_date="2026-10-12", path=tmp_path / "h.csv")


@pytest.mark.parametrize("bad", ["", "2026-13-45", "2026101", "abc"])
def test_append_rejects_bad_trade_date(bad: str, tmp_path: Path) -> None:
    with pytest.raises(DataError, match="非法交易日"):
        append_board_snapshot(_snapshot(1), trade_date=bad, path=tmp_path / "h.csv", force=True)


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    append_board_snapshot(_snapshot(), trade_date="2026-10-12", path=target)

    assert sorted(p.name for p in tmp_path.iterdir()) == [HISTORY_FILENAME]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty_frame(tmp_path: Path) -> None:
    frame = load_board_history(path=tmp_path / "nope.csv")

    assert frame.empty
    assert tuple(frame.columns) == COLUMNS
    assert frame.equals(empty_history())


def test_load_raises_when_columns_missing(tmp_path: Path) -> None:
    """损坏要人看：静默当空表会让下一次写入把能救的数据一起洗掉。"""
    target = tmp_path / "theme_history.csv"
    target.write_text("trade_date,board_name\n2026-10-12,AI应用\n", encoding="utf-8")

    with pytest.raises(DataError, match="缺列"):
        load_board_history(path=target)


def test_default_path_lives_under_runtime_pit_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(runtime_root))

    resolved = Path(
        __import__("aqsp.data.theme_history", fromlist=["_default_cache_path"])._default_cache_path()
    ).resolve()

    assert resolved.parent == (runtime_root / "pit_cache").resolve()
    assert resolved.name == HISTORY_FILENAME
    assert "src" not in resolved.parts


def test_default_path_follows_env_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = __import__("aqsp.data.theme_history", fromlist=["_default_cache_path"])

    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path / "first"))
    assert Path(module._default_cache_path()).resolve().parent == (
        tmp_path / "first" / "pit_cache"
    ).resolve()

    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path / "second"))
    assert Path(module._default_cache_path()).resolve().parent == (
        tmp_path / "second" / "pit_cache"
    ).resolve(), "缓存根在 import 期被固化，未随 env 变化"


# ---------------------------------------------------------------------------
# 切片给指数层
# ---------------------------------------------------------------------------


def test_board_change_series_returns_sorted_pairs(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"
    append_board_snapshot([_item("AI应用", 3.0)], trade_date="2026-10-13", path=target, force=True)
    append_board_snapshot([_item("AI应用", 1.0)], trade_date="2026-10-12", path=target, force=True)

    pairs = board_change_series(load_board_history(path=target), "AI应用")

    assert pairs == (("2026-10-12", 1.0), ("2026-10-13", 3.0))


def test_board_change_series_unknown_board_returns_empty(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"
    append_board_snapshot([_item("AI应用", 1.0)], trade_date="2026-10-12", path=target, force=True)

    assert board_change_series(load_board_history(path=target), "不存在的题材") == ()
    assert board_change_series(empty_history(), "AI应用") == ()


def test_board_change_series_keeps_none_for_dirty_values(tmp_path: Path) -> None:
    """脏值透传成 ``None``，由指数层跳过——这里补 0 会把缺数变成平盘。"""
    target = tmp_path / "theme_history.csv"
    target.write_text(
        "trade_date,board_name,change_pct,up_count,down_count,main_net_inflow\n"
        "2026-10-12,AI应用,1.5,10,2,1.0\n"
        "2026-10-13,AI应用,oops,10,2,1.0\n",
        encoding="utf-8",
    )

    pairs = board_change_series(load_board_history(path=target), "AI应用")

    assert pairs == (("2026-10-12", 1.5), ("2026-10-13", None))


# ---------------------------------------------------------------------------
# 冷启动回补（#406）：半字段（NaN）行 + 只补空洞
# ---------------------------------------------------------------------------


def _backfill_rows(
    *,
    days: int = 2,
    names: tuple[str, ...] = ("AI应用", "低空经济"),
) -> list[tuple[str, str, float]]:
    return [
        (f"2026-10-{12 + day:02d}", name, 1.0 + day)
        for day in range(days)
        for name in names
    ]


def test_backfill_writes_nan_columns(tmp_path: Path) -> None:
    """半字段行：change_pct 有值，三截面字段**为空（NaN）而非 0**。

    「0 家上涨」与「无数据」是两种语义——回补来源没有这三列，填 0 会
    把缺数伪装成真实统计。
    """
    target = tmp_path / "theme_history.csv"

    written = append_backfill_rows(_backfill_rows(), path=target, force=True)

    assert written == 4
    frame = pd.read_csv(target)
    assert tuple(frame.columns) == COLUMNS
    assert frame["change_pct"].notna().all()
    for column in ("up_count", "down_count", "main_net_inflow"):
        assert frame[column].isna().all(), f"{column} 应为 NaN（无数据），不是 0"


def test_backfill_does_not_overwrite_full_snapshot(tmp_path: Path) -> None:
    """已有全字段快照的行优先：回补的半字段行永不覆盖当日 cron 快照。"""
    target = tmp_path / "theme_history.csv"
    append_board_snapshot([_item("AI应用", 5.0)], trade_date="2026-10-12", path=target, force=True)

    written = append_backfill_rows(
        [("2026-10-12", "AI应用", 99.0)], path=target, force=True
    )

    assert written == 0
    frame = pd.read_csv(target)
    assert len(frame) == 1
    assert frame.loc[0, "change_pct"] == pytest.approx(5.0)
    assert frame.loc[0, "up_count"] == 10  # 全字段行的截面统计未被 NaN 洗掉


def test_backfill_skips_dirty_rows_but_keeps_valid(tmp_path: Path) -> None:
    """脏行（非法日期 / 空名 / NaN 涨跌幅）静默跳过，不阻断整批回补。"""
    target = tmp_path / "theme_history.csv"
    rows = [
        ("2026-10-12", "AI应用", 1.5),
        ("bad-date", "坏日期", 1.0),
        ("2026-10-12", "  ", 1.0),
        ("2026-10-12", "NaN板块", float("nan")),
    ]

    written = append_backfill_rows(rows, path=target, force=True)

    assert written == 1
    frame = pd.read_csv(target)
    assert len(frame) == 1
    assert frame.loc[0, "board_name"] == "AI应用"


def test_backfill_all_invalid_raises(tmp_path: Path) -> None:
    target = tmp_path / "theme_history.csv"

    with pytest.raises(DataError, match="全部无效"):
        append_backfill_rows([("bad", "坏", 1.0)], path=target, force=True)


def test_backfill_thin_day_raises_and_force_bypasses(tmp_path: Path) -> None:
    """某交易日板块数低于门槛 ⇒ 抛错（残缺快照不静默落盘）；force 可绕过。"""
    target = tmp_path / "theme_history.csv"
    rows = [("2026-10-12", f"板块{idx:03d}", 1.0) for idx in range(3)]

    with pytest.raises(DataError, match="板块数低于门槛"):
        append_backfill_rows(rows, path=target, min_boards=MIN_BOARDS_PER_SNAPSHOT)

    written = append_backfill_rows(
        rows, path=target, min_boards=MIN_BOARDS_PER_SNAPSHOT, force=True
    )
    assert written == 3


def test_backfill_noop_when_all_filled(tmp_path: Path) -> None:
    """重跑幂等：全部为空洞已填 ⇒ 返回 0，不报错、不改文件。"""
    target = tmp_path / "theme_history.csv"
    append_backfill_rows(_backfill_rows(), path=target, force=True)
    before = target.read_bytes()

    written = append_backfill_rows(_backfill_rows(), path=target, force=True)

    assert written == 0
    assert target.read_bytes() == before


def test_backfill_merges_into_existing_history_sorted(tmp_path: Path) -> None:
    """回补行与当日快照行合并后仍按 (trade_date, board_name) 排序。"""
    target = tmp_path / "theme_history.csv"
    append_board_snapshot(
        [_item("AAA板块", 2.0), _item("ZZZ板块", 3.0)],
        trade_date="2026-10-15",
        path=target,
        force=True,
    )

    append_backfill_rows(
        [("2026-10-13", "BBB板块", 1.0), ("2026-10-13", "AAA板块", 0.5)],
        path=target,
        force=True,
    )

    frame = pd.read_csv(target)
    assert frame["trade_date"].tolist() == ["2026-10-13", "2026-10-13", "2026-10-15", "2026-10-15"]
    assert frame["board_name"].tolist() == ["AAA板块", "BBB板块", "AAA板块", "ZZZ板块"]


def test_backfill_rows_feed_board_change_series(tmp_path: Path) -> None:
    """回补行的 change_pct 必须能被 vendor 序列消费（board_change_series 只取这两列）。"""
    target = tmp_path / "theme_history.csv"
    append_backfill_rows(_backfill_rows(names=("AI应用",)), path=target, force=True)

    pairs = board_change_series(load_board_history(path=target), "AI应用")

    assert pairs == (("2026-10-12", 1.0), ("2026-10-13", 2.0))
