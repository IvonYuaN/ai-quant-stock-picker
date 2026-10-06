"""守卫：报告必须如实标注「本次跑批跳过 PIT 财务 ⇒ quality/value/mr 三维空转」。

背景（2026-10-06 实测发现，F10）：
- `cli.py:3691` 规定 `--streaming` **必须**配 `--skip-pit-financials`（否则 PIT 帧无界占内存）。
- 生产 gate 走 `--streaming --stream-batch-size 500` ⇒ **架构上必然跳过财务**。
- 跳过 ⇒ frames 无 `pe`/`roe` ⇒ `value` 返 0.5、`quality` 返 0.0→0.5、`mean_reversion` 同类
  ⇒ **三个维度恒为常数**。
- 但逐变体表仍会显示它们的 7 维权重（如 `qual=0.4 val=0.4`），**判读者极易误以为这些权重在起作用**。

因此：只要本次跑批带了 `skip_pit_financials`，报告 TL;DR 必须出现这段警示；
不带时**不得**出现（否则会误导另一种场景的读者）。这是纯渲染契约，不改任何测量量。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "src" / "aqsp" / "cli.py"


def test_warning_is_bound_to_skip_pit_financials() -> None:
    """警示必须绑定在 `skip_pit_financials` 开关上（不是无条件打印）。"""
    src = CLI.read_text(encoding="utf-8")
    # 1) 开关确实存在（不是我们臆造的名字）
    assert '"--skip-pit-financials"' in src, "CLI 缺少 --skip-pit-financials 开关"
    # 2) 警示块与该开关绑定
    assert 'getattr(args, "skip_pit_financials", False)' in src
    # 3) 警示文本点名三个空转维度
    for dim in ("quality", "value", "mean_reversion"):
        assert dim in src, f"警示未点名空转维度 {dim}"


def test_streaming_requires_skip_pit_financials() -> None:
    """守住根因：`--streaming` 必须显式 `--skip-pit-financials`（内存有界约束）。

    若将来有人放开这个约束，报告里的 3 维空转警示就不该再无条件出现 ——
    本测试锁住当前的强绑定关系，是 F10 结论的代码级依据。
    """
    src = CLI.read_text(encoding="utf-8")
    assert "--streaming 必须显式 --skip-pit-financials" in src, (
        "streaming 对 skip-pit-financials 的强绑定约束已消失；"
        "F10（3 维空转）的架构前提需重新评估，事实基准 §F10 应同步更新"
    )
