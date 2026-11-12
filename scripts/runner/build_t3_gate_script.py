#!/usr/bin/env python3
"""Deterministically build the T3-patched gate wrapper (``t3_gate_script.py``).

Why this exists
---------------
The T3 dual-window campaign runs ``--grid-profile htf_mr`` against a full-market
sqlite database that has no index table. The tracked wrapper
``scripts/run_production_walkforward_gate.py`` exposes neither, so the runner
historically carried a **hand-built** copy at ``/opt/aqsp-runner/t3_gate_script.py``
("extract from branch + two patches"). That file was never committed, so when the
runner VM was re-imaged (2026-09-17) it was destroyed with the rest of
``/opt/aqsp-runner`` and the campaign could not be restarted.

This builder reproduces the two patches deterministically from the tracked source
and **refuses to emit output** when either anchor does not apply, so a silently
broken wrapper can never be produced.

Patches
-------
``P1``  **断言** wrapper 的 ``--grid-profile`` 白名单由单一事实源派生
        （``choices=_grid_profile_choices()``）且派生的档位集合含 ``htf_mr``。
        🔴 2026-11-10 改造：此前 P1 是「把**手抄**的 choices 元组替换成含
        ``htf_mr`` 的另一份手抄元组」。但那份手抄副本**本身就是病根** ——
        入口白名单与 ``aqsp.cli`` 档位路由两处维护 ⇒ 新增档位必然漏网
        （``htf_mr`` 长期漏掉、``holder_overlay`` 若照抄会被 argparse 直接拒绝）。
        入口改为从 ``aqsp.cli`` 派生后，``htf_mr`` 自动包含 ⇒ 文本替换已无
        对象可替换（锚点已不存在，留着必然 fail-closed 成地雷）。
        改造后 P1 仍是 fail-closed：不再派生、或所需档位不可路由 ⇒ 构建中止。
``P2``  ``--benchmark-symbol ""`` is injected into the child
        ``aqsp walkforward`` command, skipping index fetching: the full-market DB
        has no ``000300``, so the default would abort with
        ``sqlite_db 指数获取失败``.

Usage
-----
    python scripts/build_t3_gate_script.py --out /tmp/t3_gate_script.py

Then ship it to the runner as ``/opt/aqsp-runner/t3_gate_script.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

def _find_project_root(start: Path) -> Path:
    """向上寻找含 pyproject.toml 的目录（兼容脚本位于子目录/经根软链调用）。"""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start


REPO_ROOT = _find_project_root(Path(__file__).resolve().parent)
DEFAULT_SOURCE = REPO_ROOT / "scripts" / "run_production_walkforward_gate.py"

# P1 — the wrapper's own ``--grid-profile`` choices must offer ``htf_mr``.
#
# 🔴 2026-11-10 重构：此前 P1 是「把手抄的 choices 元组文本替换成含 htf_mr 的
# 另一份元组」。但那份手抄元组**本身就是病根**：入口白名单与 aqsp.cli 的档位
# 路由是两处维护 ⇒ 新增档位必然漏网（htf_mr 长期漏掉、holder_overlay 若照抄
# 会被 argparse 直接拒绝）。现在入口白名单改为**从 aqsp.cli 派生**
# （``choices=_grid_profile_choices()``），htf_mr 自动包含 ⇒ P1 的文本替换
# 已无对象可替换（其锚点在源码里已不存在，留着必然 fail-closed 成地雷）。
#
# P1 改造为**断言**：源码里必须出现派生调用，且派生的档位集合必须含 htf_mr。
# 语义从「打补丁」变为「验证单一事实源生效」，漂移仍 fail-closed，
# 且**不再存在第二份 choices 副本**。
CHOICES_DERIVED_MARKER = "choices=_grid_profile_choices()"
CHOICES_REQUIRED_PROFILE = "htf_mr"

# P2 — anchor is the child command's pool block; insertion keeps argument order stable.
POOL_ANCHOR = '        "--pool",\n        "all",\n'
POOL_PATCHED = '        "--pool",\n        "all",\n        "--benchmark-symbol",\n        "",\n'


class BuildError(RuntimeError):
    """Raised when a patch anchor does not apply exactly once."""


def _apply_once(text: str, anchor: str, patched: str, patch_id: str) -> str:
    """Apply one patch, failing loudly instead of emitting a partial result.

    「已打过补丁」的判定 = **patched 文本已存在**（而非旧实现的
    ``patched in text and anchor not in text``）：P2 的 anchor 是 patched 的
    **前缀**（``--pool","all"`` 出现在 ``--pool","all","--benchmark-symbol",""``
    里），故打过补丁后 anchor 依然存在且 count==1 ⇒ 旧判定永远不触发，
    「重复打补丁」会被静默放过。改为直接看 patched 是否已在文本中。
    """
    if patched in text:
        raise BuildError(f"{patch_id}: source already patched; refusing to double-apply")
    matches = text.count(anchor)
    if matches != 1:
        raise BuildError(
            f"{patch_id}: anchor matched {matches} times (expected exactly 1); "
            "the tracked source drifted — update the anchor, never hand-edit the output"
        )
    return text.replace(anchor, patched, 1)


def _assert_derived_choices_offer_required(source_text: str) -> None:
    """P1：断言 wrapper 的 grid-profile 白名单**由单一事实源派生**且含所需档位。

    🔴 2026-11-10：P1 从「文本替换（注入 htf_mr）」改为「断言」。理由见
    CHOICES_DERIVED_MARKER 处的说明：入口白名单手抄副本就是「新增档位漏网」的
    病根，派生后 htf_mr 自动包含，文本替换已无对象可替换。

    失败语义保持 fail-closed：源码不再从单一事实源派生、或派生集合不含所需
    档位 ⇒ 构建中止，绝不产出一个「不接受 htf_mr」的 wrapper。
    """
    if CHOICES_DERIVED_MARKER not in source_text:
        raise BuildError(
            "P1(grid-profile choices): tracked source no longer derives its "
            f"--grid-profile whitelist ({CHOICES_DERIVED_MARKER!r} not found); "
            "the whitelist must stay derived from aqsp.cli (single source of truth)"
        )
    # 派生集合的实含档位由 aqsp.cli 决定；此处校验「派生调用在位」+「所需档位
    # 在 aqsp.cli 的档位表内」，两者共同保证 wrapper 接受该档位。
    from aqsp.cli import walkforward_grid_profiles

    if CHOICES_REQUIRED_PROFILE not in walkforward_grid_profiles():
        raise BuildError(
            f"P1(grid-profile choices): required profile {CHOICES_REQUIRED_PROFILE!r} "
            "is not routable in aqsp.cli — the wrapper could never accept it"
        )


def build(source_text: str) -> str:
    """Return the patched wrapper source.

    Raises:
        BuildError: if either anchor does not match exactly once.
    """
    _assert_derived_choices_offer_required(source_text)
    return _apply_once(source_text, POOL_ANCHOR, POOL_PATCHED, "P2(benchmark-symbol)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the T3-patched gate wrapper from the tracked source."
    )
    parser.add_argument("--out", required=True, help="write the patched wrapper here")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="tracked wrapper to patch (default: scripts/run_production_walkforward_gate.py)",
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    if not source_path.is_file():
        raise SystemExit(f"source wrapper missing: {source_path}")

    try:
        patched = build(source_path.read_text(encoding="utf-8"))
    except BuildError as exc:
        raise SystemExit(f"build refused: {exc}") from exc

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(patched, encoding="utf-8")
    print(f"wrote {out} ({len(patched.splitlines())} lines) from {source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
