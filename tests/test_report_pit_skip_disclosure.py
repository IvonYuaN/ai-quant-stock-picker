"""守卫：报告必须如实标注「本次跑批跳过 PIT 财务 ⇒ 哪些维度真的空转」。

背景（2026-10-06 引入，**2026-10-09 订正**）：
- `cli.py:3691` 规定 `--streaming` **必须**配 `--skip-pit-financials`（否则 PIT 帧无界占内存）。
- 生产 gate 走 `--streaming --stream-batch-size 500` ⇒ **架构上必然跳过财务**。
- 跳过 ⇒ frames 无财务列 ⇒ **quality / value 恒为常数**（各子项缺列时返 0.5，
  故加权和仍是常数）。
  ⚠️ 原文档写的是「quality/value/mean_reversion 三维」——**对第三维是错的**：
  `mean_reversion` 是纯价量因子（只读 close/volume），跳过财务对它毫无影响。
  实测见 `tests/test_pit_skip_note_factor_accuracy.py`。
- 但逐变体表仍会显示它们的 7 维权重（`planb_*` 档位 qual/val=0.4），
  **判读者极易误以为这些权重在起作用**。

因此：只要本次跑批带了 `skip_pit_financials`，报告 TL;DR 必须出现这段警示；
不带时**不得**出现（否则会误导另一种场景的读者）。这是纯渲染契约，不改任何测量量。

分工：本文件守**绑定关系**（警示挂在开关上、正文来自单一事实来源）；
「文案点名的维度是否与实测一致」由 `tests/test_pit_skip_note_factor_accuracy.py`
以行为断言守住。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

CLI = REPO_ROOT / "src" / "aqsp" / "cli.py"

from aqsp.services.walkforward_data import (  # noqa: E402
    PIT_SKIP_IDLE_FACTORS,
    pit_skip_note,
)


def test_warning_is_bound_to_skip_pit_financials() -> None:
    """警示必须绑定在 `skip_pit_financials` 开关上（不是无条件打印）。"""
    src = CLI.read_text(encoding="utf-8")
    # 1) 开关确实存在（不是我们臆造的名字）
    assert '"--skip-pit-financials"' in src, "CLI 缺少 --skip-pit-financials 开关"
    # 2) 警示块与该开关绑定
    assert 'getattr(args, "skip_pit_financials", False)' in src
    # 3) 正文必须复用单一事实来源，不得在报告段再手写一份文案
    #    （2026-10-06 起两处各写一份，2026-10-09 才发现两份都把 mean_reversion 写错）
    assert "pit_skip_note()" in src, (
        "报告段未复用 services.walkforward_data.pit_skip_note()；"
        "两处文案会再次漂移"
    )


def test_rendered_note_covers_idle_dims_and_excludes_mean_reversion() -> None:
    """渲染文案必须点名真正空转的维度，并**显式排除** mean_reversion。"""
    note = pit_skip_note()
    for dim in PIT_SKIP_IDLE_FACTORS:
        assert dim in note, f"警示未点名空转维度 {dim}"
    assert "mean_reversion" in note, (
        "警示必须出现 mean_reversion —— 不是把它列为空转，而是显式说明它不受影响；"
        "漏掉它会让判读者以为 stable_plus 的 WF-MR1 退化了"
    )
    assert "不受影响" in note, "警示缺少「价量因子不受影响」的排除说明"


def test_streaming_requires_skip_pit_financials() -> None:
    """守住根因：`--streaming` 必须显式 `--skip-pit-financials`（内存有界约束）。

    若将来有人放开这个约束，报告里的空转警示就不该再无条件出现 ——
    本测试锁住当前的强绑定关系，是 F10 结论的代码级依据。
    """
    src = CLI.read_text(encoding="utf-8")
    assert "--streaming 必须显式 --skip-pit-financials" in src, (
        "streaming 对 skip-pit-financials 的强绑定约束已消失；"
        "F10（维度空转）的架构前提需重新评估，事实基准 §F10 应同步更新"
    )
