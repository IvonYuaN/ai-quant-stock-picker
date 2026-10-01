"""planb_b_trigger（方案B 口径B 触发巡检）契约测试。

预注册判据（`outputs/预注册裁决单_方案B_口径B_2026-10-01.md` §三，改判据=作废重写）：
  - 某因子连续 5 数据日（as_of 去重，限 as_of < 2026-11-07）三判全过 ⇒ 落 marker：
      1) 逐日单窗 146 截面 |t|≥2（ledger per-day t；旧行无 t 字段 ⇒ 回退当日 t）
      2) 当日双窗 73/73 sign_match==true（同号辅判，不要求双 |t|≥2）
      3) held-out：recent(末30截面) mean 与 overall mean 同号
  - marker 幂等（已存在不重写）；proposal-only（只写 marker，绝不写打分/排序/下单）。
全部 tmp 目录构造 JSON 输入，不碰真实 IC 产物、不碰库。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from scripts.analysis import planb_b_trigger

_SIG = "momentum"  # 用一个显著负 IC 因子做「达标」样本


def _base_days(n: int, as_of_start: str, t_all: float = -2.5):
    """生成 n 个连续数据日的 IC ledger（每行带 per-day t + factors mean，同号）。"""
    d0 = date.fromisoformat(as_of_start)
    lines = []
    for i in range(n):
        as_of = (d0 + timedelta(days=i)).isoformat()
        lines.append(
            {
                "as_of": as_of,
                "run_at": f"{as_of}T02:10:00Z",
                "factors": {_SIG: -0.04},
                "t": {_SIG: t_all},
            }
        )
    return lines


def _write_inputs(
    tmp_path: Path,
    ledger_lines: list,
    sign_match: bool = True,
    today_t: float = -2.5,
    single_mean: float = -0.04,
    recent_mean: float | None = -0.05,
    dual_mean: bool = True,
):
    single = {
        "as_of": "2026-09-30",
        "n_sections": 146,
        "factors": {
            _SIG: {"mean": single_mean, "t": today_t, "recent": {"mean": recent_mean}}
        },
    }
    dual = {
        "as_of_b": "2026-09-30",
        "factors": {
            _SIG: {
                "t_a": -2.0,
                "t_b": -2.0,
                "sign_match": sign_match,
                "hit": dual_mean,
                "n_a": 73,
                "n_b": 73,
            }
        },
    }
    (tmp_path / "factor_ic_latest.json").write_text(json.dumps(single))
    (tmp_path / "dual_window_latest.json").write_text(json.dumps(dual))
    (tmp_path / "ic_history.jsonl").write_text(
        "\n".join(json.dumps(x) for x in ledger_lines)
    )
    return tmp_path


def _marker(tmp_path: Path) -> Path:
    return tmp_path / f"planb_b_event_{_SIG}.marker"


def test_streak_5_days_writes_marker(tmp_path):
    _write_inputs(tmp_path, _base_days(5, "2026-09-20"))
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert _marker(tmp_path).is_file(), "5 数据日 streak 应落 marker"
    ev = json.loads(_marker(tmp_path).read_text())
    assert ev["factor"] == _SIG
    assert ev["streak"] >= 5
    assert len(ev["days"]) == 5


def test_idempotent_marker_not_rewritten(tmp_path):
    _write_inputs(tmp_path, _base_days(5, "2026-09-20"))
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        planb_b_trigger.main()
        planb_b_trigger.main()  # 二次跑：marker 已存在 ⇒ skip，不重写
    finally:
        sys.argv = ["planb_b_trigger"]
    assert _marker(tmp_path).is_file()
    ev = json.loads(_marker(tmp_path).read_text())
    assert ev["streak"] >= 5  # 内容保持首次写入（幂等）


def test_streak_4_days_no_marker(tmp_path):
    _write_inputs(tmp_path, _base_days(4, "2026-09-21"))
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert not _marker(tmp_path).exists(), "4 数据日不足 5 ⇒ 不触发"


def test_sign_match_false_breaks_streak(tmp_path):
    _write_inputs(tmp_path, _base_days(5, "2026-09-20"), sign_match=False)
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert not _marker(tmp_path).exists(), "双窗异号 ⇒ 辅判不过 ⇒ 不触发"


def test_heldout_recent_sign_mismatch(tmp_path):
    # recent mean 与 overall mean 异号（overall 负、recent 正）⇒ held-out 翻车
    _write_inputs(tmp_path, _base_days(5, "2026-09-20"), recent_mean=0.05)
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert not _marker(tmp_path).exists(), "held-out 异号 ⇒ 不触发"


def test_perday_t_below_2_breaks_streak(tmp_path):
    # 第 3 天 |t|<2（弱读数）⇒ 连续 5 天被断
    lines = _base_days(5, "2026-09-20")
    lines[2]["t"] = {_SIG: -1.0}
    _write_inputs(tmp_path, lines)
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert not _marker(tmp_path).exists(), "逐日 |t|<2 打断 streak ⇒ 不触发"


def test_invalid_date_window_excluded(tmp_path):
    # 全部数据日 as_of >= 2026-11-07（兜底失效线）⇒ 不计 streak
    _write_inputs(tmp_path, _base_days(5, "2026-11-10"))
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert not _marker(tmp_path).exists(), "as_of≥失效线 ⇒ 本单作废窗口外 ⇒ 不触发"


def test_old_ledger_lines_without_t_field(tmp_path):
    # 旧 ledger 行无 t 字段（向后兼容）⇒ 回退当日 t，不 crash、不 KeyError
    lines = _base_days(5, "2026-09-20")
    for x in lines:
        x.pop("t", None)  # 模拟 09-30 前旧行
    _write_inputs(tmp_path, lines, today_t=-2.5)
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    # today_t=-2.5（|t|≥2）回退命中 ⇒ 仍应触发（行为向后兼容，不误杀）
    assert _marker(tmp_path).is_file()


def test_as_of_dedup_same_day_counts_once(tmp_path):
    # 同 as_of 重复 2 次 + 另外 4 个不同日 = 5 个 as_of，但重复日只算 1
    lines = _base_days(4, "2026-09-22")
    lines.append(dict(lines[0]))  # 重复第 0 天（同 as_of）
    _write_inputs(tmp_path, lines)
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    # 4 个不同 as_of < 5 ⇒ 不触发
    assert not _marker(tmp_path).exists()


def test_missing_input_files_no_crash(tmp_path):
    # 空目录（无任何 IC 产物）⇒ 全部 streak=0，不 crash，不写 marker
    import sys

    sys.argv = ["planb_b_trigger", "--dir", str(tmp_path)]
    try:
        assert planb_b_trigger.main() == 0
    finally:
        sys.argv = ["planb_b_trigger"]
    assert not any(tmp_path.glob("*.marker"))
