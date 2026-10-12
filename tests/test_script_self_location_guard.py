"""守卫：经「根级兼容软链」可达的脚本，若拿 `parents[N]` 的**结果组合路径**，该结果必须真的是仓库根。

背景（2026-09-28 重组暴露，实测确认）：
`scripts/` 重组把 107 个脚本移到子目录、并在 `scripts/` 根留同名兼容软链（prod cron 仍按根路径调用）。
而 `Path(__file__).resolve()` **会跟随软链**落到真身（`scripts/<子目录>/x.py`），于是
`resolve().parents[1]` 得到的是 **`scripts/`** 而不是仓库根 —— 只要它被拿去组合路径
（`project_root / "data/..."`、`cwd=project_root`），就会**静默指错位置**（属「静默失效」类事故）。

## 🔴 本文件是重写版：原判据是**橡皮章**（issue #338）

原实现用**大写字面量**做判据：`PATH_COMPOSE_MARKERS = ("PROJECT_ROOT /", "REPO_ROOT /", "cwd=PROJECT_ROOT")`。
实测 2026-10-12：69 个根软链脚本 → 18 个含裸 `parents[` → 15 个未用 `_find_project_root`
→ 但**按大写标记命中数为 0**（脚本里普遍写小写 `project_root = ...`）
⇒ 守卫**恒通过**，属于「零候选 = 空转的橡皮章」。

⚠️ 而**只把标记改成大小写不敏感会更糟**：会把 8 个**写法完全正确**的脚本报成违规
（它们用 `parents[2]` 且真身在 `scripts/<子目录>/`，落点正是仓库根）。
⇒ 病根不在「标记大小写」，而在**判据本身**：原判据判的是「有没有出现某种写法」，
而真正的风险是「**算出来的那个『根』到底是不是仓库根**」。

## 现判据（行为式，可证伪）

对每个「根软链脚本」（排除已用 `_find_project_root` 的）：
  1. 找出所有 `Path(__file__).resolve().parents[N]`；
  2. 若该脚本**存在非 `sys.path` 的「root 变量组合路径」用法**（`project_root / ...`、`cwd=project_root`，
     大小写不敏感），则它是一个候选；
  3. **断言：候选至少有一个被使用的 `parents[N]` 解析结果 == 仓库根**。

这样：
  - 落点正确（`scripts/<sub>/x.py` 用 `parents[2]`）⇒ 通过；
  - 落点错误（同深度用 `parents[1]` ⇒ 得到 `scripts/`）⇒ **失败**，且报错里直接给出应改成的索引；
  - 仅用 `parents[N]` 引导 `sys.path` 的脚本 ⇒ 按原 docstring 的意图**自动排除**（不靠硬编码名单）。

## 前置断言（防空转）

本文件另有两条前置断言：根软链脚本数 ≥ 50、候选数 ≥ 1。
否则「扫描集为空」会让主断言变成 `assert not []` 式的恒真 —— 这正是 #338 的教训。
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = REPO_ROOT / "scripts"

# 脚本若已采用本项目的正确做法，直接豁免
ROBUST_MARKER = "_find_project_root"

# `Path(__file__).resolve().parents[N]`（含 `parents[N]` 形式）
_PARENTS_RX = re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]")

# 「root 变量组合路径」的写法。🔴 必须**大小写不敏感**：脚本里普遍写小写 `project_root`
# （原实现写死大写 ⇒ 命中 0 ⇒ 恒通过，见 docstring）。
_COMPOSE_RX = re.compile(
    r"(?i)(?:\b(?:project_root|repo_root)\s*/|cwd\s*=\s*(?:project_root|repo_root)\b)"
)

# 扫描集下限（实测 69）。低于此值说明软链布局变了 ⇒ 应人工确认后再调，不要静默放宽。
_MIN_ROOT_SYMLINKED_SCRIPTS = 50
# 候选下限（实测 6）。为 0 说明判据或仓库布局变了 ⇒ 必须显式处理，不能让守卫空转。
_MIN_CANDIDATES = 1


def _root_symlinked_python_scripts() -> list[pathlib.Path]:
    """`scripts/` 根下有同名软链的 `.py` 脚本的**真身**路径（去重后排序）。"""
    out: set[pathlib.Path] = set()
    for link in sorted(SCRIPTS_ROOT.glob("*")):
        if link.is_symlink() and link.suffix == ".py" and link.resolve().is_file():
            out.add(link.resolve())
    return sorted(out)


def _composing_lines(text: str) -> list[str]:
    """返回「root 变量组合路径」的行，**排除**纯 `sys.path` 引导行。

    原 docstring 明确要避免误伤「仅用 `parents[N]` 引导 `sys.path`」的写法
    （那种即使算错，调用方通常已通过 `PYTHONPATH` 提供正确路径）。
    """
    return [
        line
        for line in text.splitlines()
        if _COMPOSE_RX.search(line) and "sys.path" not in line
    ]


def _candidates() -> list[tuple[pathlib.Path, list[int], list[str]]]:
    """返回 (真身路径, 用到的 parents 索引, 组合路径示例行)。"""
    out: list[tuple[pathlib.Path, list[int], list[str]]] = []
    for real in _root_symlinked_python_scripts():
        text = real.read_text(encoding="utf-8")
        if ROBUST_MARKER in text:
            continue
        indices = sorted({int(m.group(1)) for m in _PARENTS_RX.finditer(text)})
        if not indices:
            continue
        lines = _composing_lines(text)
        if not lines:
            continue
        out.append((real, indices, lines))
    return out


def test_scan_set_is_not_empty() -> None:
    """前置断言①：根软链脚本集不能为空/骤减 —— 否则下面的守卫是空转的橡皮章。"""
    scripts = _root_symlinked_python_scripts()
    assert len(scripts) >= _MIN_ROOT_SYMLINKED_SCRIPTS, (
        f"`scripts/` 根下的同名软链脚本只有 {len(scripts)} 个（预期 ≥ "
        f"{_MIN_ROOT_SYMLINKED_SCRIPTS}）。软链布局变了？请人工确认后同步本文件的常量。"
    )


def test_candidate_set_is_not_empty() -> None:
    """前置断言②：候选（用 root 变量组合路径的裸 `parents[N]` 脚本）不能为 0。

    🔴 这是 #338 的核心教训：原守卫因判据写死大写标记而**命中 0 ⇒ 恒通过**。
    候选为 0 时本断言失败，迫使维护者显式处理，而不是让守卫静默变成橡皮章。
    """
    cands = _candidates()
    assert len(cands) >= _MIN_CANDIDATES, (
        f"候选数为 {len(cands)}（预期 ≥ {_MIN_CANDIDATES}）。若确实已无此类脚本，"
        f"请显式调整 _MIN_CANDIDATES 并说明原因；不要让它静默恒通过。"
    )


def test_root_composing_scripts_resolve_repo_root() -> None:
    """主断言：用 `parents[N]` 结果组合路径的脚本，其落点必须是**仓库根**。"""
    offenders: list[str] = []
    for real, indices, lines in _candidates():
        rel = real.relative_to(REPO_ROOT)
        correct = [n for n in indices if real.parents[n].resolve() == REPO_ROOT]
        if correct:
            continue
        # 给出可直接照抄的修法
        expected = len(rel.parts) - 1  # scripts/<sub>/x.py ⇒ parents[2] == 仓库根
        resolved = {n: str(real.parents[n].relative_to(REPO_ROOT)) for n in indices}
        offenders.append(
            f"  {rel}\n"
            f"      实用 parents{indices} ⇒ 落点 {resolved}（都不是仓库根）\n"
            f"      应为 parents[{expected}]，或改用 _find_project_root()\n"
            f"      组合路径示例: {lines[0].strip()[:100]}"
        )
    assert not offenders, (
        "以下脚本经 `scripts/` 根软链调用时，把「根」算成了非仓库根并**拿去组合路径** "
        "⇒ 会静默读写错误位置：\n"
        + "\n".join(offenders)
        + "\n修法：把索引改成上方给出的 parents[N]，或改用向上寻找 pyproject.toml 的 "
        "_find_project_root()（参考 scripts/analysis/write_home_snapshot.py）。"
    )


def test_syspath_only_scripts_are_exempt() -> None:
    """职责边界（可执行契约）：仅用 `parents[N]` 引导 `sys.path` 的脚本**不纳入**本守卫。

    这类写法即使算错，调用方通常已通过 `PYTHONPATH` 提供正确路径（原 docstring 的既定口径）。
    本断言把该口径写成可执行契约，避免将来有人「顺手」把它们也纳进来造成误伤。
    """
    composing_without_syspath = set()
    syspath_only = set()
    for real in _root_symlinked_python_scripts():
        text = real.read_text(encoding="utf-8")
        if ROBUST_MARKER in text or not _PARENTS_RX.search(text):
            continue
        all_lines = [line for line in text.splitlines() if _COMPOSE_RX.search(line)]
        if not all_lines:
            continue
        if _composing_lines(text):
            composing_without_syspath.add(real)
        else:
            syspath_only.add(real)

    assert not (composing_without_syspath & syspath_only), "分类不应重叠"
    # 豁免集与候选集必须互斥且完整覆盖「有组合用法」的脚本
    cands = {real for real, _, _ in _candidates()}
    assert cands == composing_without_syspath, (
        "候选集与「有非 sys.path 组合用法」的集合不一致："
        f"候选={sorted(p.relative_to(REPO_ROOT) for p in cands)}，"
        f"实算={sorted(p.relative_to(REPO_ROOT) for p in composing_without_syspath)}"
    )
