"""分板块覆盖审计的行为级单测。

锁住三件事：
1. **能抓到整块缺失**（塌陷、停更）——这是 2026-11-11 实测的两处真实缺陷：
   prod 库 688/689/302 停在 20231229（落后 1048 天）；
   runner 库 688 在 2024/2025 只剩 1 只票（564 只全缺）。
2. **不误报"成长"**：板块随新股上市逐年增长是正常的，用「与峰值比」会大量误报。
3. **不误报"开板首年"**：科创板 2019-07 开板 ⇒ 2019 只有 111 个交易日，正常。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "data"))

from audit_board_coverage import audit  # noqa: E402


def _make_db(path: Path, rows: list[tuple[str, str]]) -> None:
    """rows = [(ts_code, trade_date), ...] → 建最小 daily_qfq 表。"""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT, close REAL, volume REAL, amount REAL)"
    )
    con.executemany(
        "INSERT INTO daily_qfq (ts_code, trade_date, close, volume, amount) VALUES (?,?,1,1,1)",
        rows,
    )
    con.commit()
    con.close()


def test_healthy_boards_produce_no_warnings(tmp_path: Path) -> None:
    """两个板块都正常增长 ⇒ 零告警（防误报「成长」）。"""
    rows: list[tuple[str, str]] = []
    for year in (2024, 2025):
        for day in (f"{year}0102", f"{year}0103", f"{year}1230", f"{year}1231"):
            for i in range(5):
                rows.append((f"60000{i}.SH", day))
                rows.append((f"00000{i}.SZ", day))
    # 2026 年：600 板多了一只新股（增长），应仍无告警
    for day in ("20260102", "20260105"):
        for i in range(5):
            rows.append((f"60000{i}.SH", day))
        rows.append(("600009.SH", day))
        rows.append(("000000.SZ", day))
    db = tmp_path / "ok.db"
    _make_db(db, rows)
    _detail, warnings = audit(str(db))
    assert warnings == [], f"不应有告警，实际：{warnings}"


def test_detects_collapse_two_years_in_a_row(tmp_path: Path) -> None:
    """整块塌陷且**连续多年**都要报（只比上一年会漏掉第二年起）。"""
    rows: list[tuple[str, str]] = []
    # 2023：688 有 30 只
    for day in ("20230103", "20230104", "20231229"):
        for i in range(30):
            rows.append((f"688{i:03d}.SH", day))
        rows.append(("600000.SH", day))
    # 2024/2025：688 只剩 1 只
    for year in (2024, 2025):
        for day in (f"{year}0102", f"{year}1231"):
            rows.append(("688000.SH", day))
            rows.append(("600000.SH", day))
    db = tmp_path / "collapse.db"
    _make_db(db, rows)
    _detail, warnings = audit(str(db))
    joined = "\n".join(warnings)
    assert "688 2024" in joined and "塌陷" not in joined  # 文案是"疑整块缺该年"
    assert "688 2025" in joined, f"连续第二年塌陷必须也报，实际：{warnings}"


def test_detects_stale_board(tmp_path: Path) -> None:
    """板块停更超过阈值 ⇒ 告警（prod 真实形态：落后 1048 天）。"""
    rows: list[tuple[str, str]] = []
    for day in ("20240102", "20250630"):
        for i in range(20):
            rows.append((f"688{i:03d}.SH", day))   # 688 停在 2025-06-30
        rows.append(("600000.SH", day))
    for day in ("20250630", "20260105", "20261111"):
        rows.append(("600000.SH", day))
    db = tmp_path / "stale.db"
    _make_db(db, rows)
    _detail, warnings = audit(str(db), stale_days=30)
    assert any("688" in w and "停更" in w for w in warnings), warnings


def test_first_year_short_days_is_not_flagged(tmp_path: Path) -> None:
    """开板首年交易日天然偏少 ⇒ 不得报（科创板 2019-07 开板）。"""
    rows: list[tuple[str, str]] = []
    # 688 首年只有 2 个交易日；次年起有 4 个
    for day in ("20240722", "20241231"):
        for i in range(25):
            rows.append((f"688{i:03d}.SH", day))
    for day in ("20250102", "20250630", "20250930", "20251231"):
        for i in range(25):
            rows.append((f"688{i:03d}.SH", day))
        rows.append(("600000.SH", day))
    db = tmp_path / "inception.db"
    _make_db(db, rows)
    _detail, warnings = audit(str(db))
    assert not any("2024" in w and "交易日" in w for w in warnings), warnings
