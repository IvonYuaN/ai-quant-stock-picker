"""守卫：`scripts/` 侧脚本的 `__file__` 层级推导必须落在**仓库根**。

背景（#339）：`scripts/` 在 2026-09-28 重组，脚本被下沉到子目录
（`scripts/x.py` → `scripts/<子目录>/x.py`），但文件内的 `Path(__file__)` 层级
未同步 +1。又因 `Path(__file__).resolve()` **会跟随 `scripts/` 根级兼容软链**
落到真身，`parents[1]` 得到的是 **`scripts/`** 而不是仓库根 ⇒
`sys.path.insert(0, ... / "src")` 插入的是**不存在的** `scripts/src`，
或把产物写到 `scripts/` 下。线上长期被入口脚本的全局 `PYTHONPATH` 掩盖。

本守卫对下列文件断言：**所有** `Path(__file__).resolve().parents[N]` 求值后
必须等于仓库根。这样即便将来有人再改层级，也会立刻红。

（只覆盖本 PR 修复的 17 个文件；通用守卫的口径收敛见 #338。）
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 本 PR 修复的文件（真身路径，相对仓库根）。
FIXED_SCRIPTS = (
    "scripts/data/backfill_index_to_sqlite.py",
    "scripts/data/import_tdx_vipdoc_to_sqlite.py",
    "scripts/data/refresh_sqlite_batch.py",
    "scripts/data/generate_cold_start_signals.py",
    "scripts/deploy/preflight_upload.py",
    "scripts/dev/diagnose_momentum.py",
    "scripts/dev/example_fetcher_usage.py",
    "scripts/dev/smoke_market_context_runtime.py",
    "scripts/dev/test_adjustment.py",
    "scripts/dev/test_debate.py",
    "scripts/maintenance/cleanup_ledger.py",
    "scripts/maintenance/daily_pipeline.py",
    "scripts/maintenance/repair_gate_notify_state.py",
    "scripts/monitor/check_before_live.py",
    "scripts/monitor/verify_dashboard.py",
    "scripts/runner/run_dual_window_gate.py",
    "scripts/runner/run_production_walkforward_gate.py",
)

#: 匹配 `Path(__file__).resolve().parents[N]`（不含 `_find_project_root(...)` 入参，
#: 那种用法本来就靠向上搜索，层级无所谓）。
PARENTS_EXPR = re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[\d+\]")


def _parents_expressions(real_path: pathlib.Path) -> list[str]:
    text = real_path.read_text(encoding="utf-8")
    return [m.group(0) for m in PARENTS_EXPR.finditer(text)]


@pytest.mark.parametrize("rel", FIXED_SCRIPTS)
def test_file_path_levels_resolve_to_repo_root(rel: str) -> None:
    real_path = REPO_ROOT / rel
    assert real_path.is_file(), f"{rel} 不存在（文件被移动？请同步更新本测试）"

    exprs = _parents_expressions(real_path)
    assert exprs, f"{rel} 里已找不到 Path(__file__).resolve().parents[N] —— 测试已失效"

    for expr in exprs:
        resolved = eval(expr, {"Path": pathlib.Path, "__file__": str(real_path)})  # noqa: S307
        assert resolved == REPO_ROOT, (
            f"{rel} 的 `{expr}` 解析到 {resolved}，应为仓库根 {REPO_ROOT}。\n"
            "原因：本文件真身在 scripts/<子目录>/，resolve() 会跟随 scripts/ 根软链 ⇒ "
            "层级需与目录深度一致（scripts/<子目录>/x.py ⇒ parents[2]）。"
        )


def test_scripts_src_does_not_exist() -> None:
    """失败模式固化：`parents[1] / "src"` 会指向不存在的 `scripts/src`。"""
    assert not (REPO_ROOT / "scripts" / "src").exists()
