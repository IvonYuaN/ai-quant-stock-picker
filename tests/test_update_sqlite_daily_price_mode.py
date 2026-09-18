"""`scripts/update_sqlite_daily.py` 的价基不变量：默认 raw + 拒绝非 raw 既有库。

背景（"文档写明的意图"在代码里失效，本文件把它们锁住）：

1. `docs/research/raw-sqlite-cutoff-audit-2026-07-13.md` 明文写：
   「**默认 `price_mode=raw`，qfq 只能显式指定，避免验证库被误写成 qfq**」。
   但 `update_sqlite_daily()` 的函数签名默认曾是 `"qfq"`（CLI 默认是 `raw`，故只有
   程序化调用方会踩）。
2. 价基守卫原写成 `price_mode() == "invalid"`，而 `SqliteDbSource.price_mode()` 只会
   返回 `"raw"` / `"qfq"` / `"unknown"` ⇒ 守卫**永不触发**（形同虚设）。

为什么这件事重要：验证链路（gate / walk-forward）以 `adjust=""` 读
`open/high/low/close`。把这些列写成前复权价会**静默污染验证价基**——比 qfq 列缺失更隐蔽，
因为读数看起来完全正常。`scripts/coldstart_daily.sh:254` 也已有同向的 fail-closed
（`sqlite_db` 运行时要求 raw 历史库），本文件的守卫与之对齐。
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import scripts.update_sqlite_daily as updater

_SCRIPT = Path("scripts/update_sqlite_daily.py")


def _make_empty_daily_table(db_path: Path) -> None:
    """建一个只有表结构、零行的库。

    空表让 ``price_mode()`` 的样本循环无事可做，从而落到**文件名**判定 —— 于是可以用
    文件名精确构造出 ``"raw"`` / ``"qfq"`` 两种价基，无需任何真实行情数据。
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE daily_qfq ("
            "trade_date TEXT, ts_code TEXT, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL, amount REAL, open_qfq REAL,"
            " high_qfq REAL, low_qfq REAL, close_qfq REAL)"
        )


def test_library_default_price_mode_is_raw() -> None:
    """函数默认必须是 raw（qfq 只能显式指定）—— 这是 2026-07-13 审计文档写明的设计。"""
    default = (
        inspect.signature(updater.update_sqlite_daily)
        .parameters["price_mode"]
        .default
    )
    assert default == "raw"


def test_cli_price_mode_default_is_raw() -> None:
    src = _SCRIPT.read_text(encoding="utf-8")
    idx = src.index('"--price-mode"')
    block = src[idx : idx + 260]
    assert 'default="raw"' in block
    assert 'default="qfq"' not in block


def test_adjustflag_mapping_raw_means_unadjusted() -> None:
    """raw → baostock adjustflag 3（不复权）；qfq → 2。防止映射被反向改错。"""
    assert updater._adjustflag_for_price_mode("raw") == "3"
    assert updater._adjustflag_for_price_mode("qfq") == "2"
    with pytest.raises(ValueError):
        updater._adjustflag_for_price_mode("hfq")


def test_updater_refuses_qfq_basis_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既有库价基不是 raw → fail-closed，且**在联网取数之前**就拒绝。"""
    db_path = tmp_path / "market_qfq.db"
    _make_empty_daily_table(db_path)
    # 若守卫失效，执行会走到 _load_baostock；这里让它直接炸，确保测试不联网。
    monkeypatch.setattr(
        updater, "_load_baostock", lambda: pytest.fail("守卫未拦截，已进入取数阶段")
    )

    with pytest.raises(RuntimeError, match="price basis is 'qfq'"):
        updater.update_sqlite_daily(
            db_path,
            target_day=date(2026, 1, 5),
            sleep_seconds=0.0,
            limit=1,
            symbols=("000001",),
        )


def test_updater_proceeds_past_guard_for_raw_basis_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw 价基的库不得被误拦：守卫应放行，直到取数阶段才停下。

    用哨兵异常证明"确实越过了守卫"——不联网、也不依赖 baostock 是否安装。
    """

    def _sentinel() -> object:
        raise RuntimeError("SENTINEL: passed the price-basis guard")

    db_path = tmp_path / "market_raw.db"
    _make_empty_daily_table(db_path)
    monkeypatch.setattr(updater, "_load_baostock", _sentinel)

    with pytest.raises(RuntimeError, match="SENTINEL"):
        updater.update_sqlite_daily(
            db_path,
            target_day=date(2026, 1, 5),
            sleep_seconds=0.0,
            limit=1,
            symbols=("000001",),
        )
