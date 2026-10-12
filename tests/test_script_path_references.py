"""守卫：代码里引用的 `scripts/<...>.(py|sh|mjs|js)` 路径必须真实存在。

背景（issue #350，承接 #341）：仓库里存在「引用了不存在的 `scripts/` 路径」这类**悬空引用**。
它们不会让 CI 变红，但会让运维/排障按图索骥时**静默走空**：

  - `scripts/maintenance/daily_run.sh` 调 `scripts/resolve_notify_level.py`（**全仓不存在**），
    被 `|| echo` 兜底 ⇒ `AQSP_NOTIFY_LEVEL_RESOLVED` 恒为 `info` ⇒ `= "critical"` 分支**不可达**
    （#341：critical 抑制能力在链路迁移中被整体丢弃）。
  - `scripts/monitor/analyze_logs.py` 的用法说明写 `python scripts/analyze_logs.py ...`，
    但该文件在 `monitor/` 下且**没有顶层同名软链** ⇒ 照抄即报「文件不存在」。
  - `scripts/maintenance/verify_reorganization.sh` 引用 `scripts/create_symlinks.sh`
    （git 全历史从未存在）与占位符 `scripts/path/to/script.sh`。

## 口径（与 `.workbuddy-ai/tools/audit_dangling_script_refs.py` 一致）

只扫**执行/调用语义**的引用：`scripts` / `src` / `backend` / `deploy` 下指定扩展名的文件里、
**非 `#` 开头的行**中出现的 `scripts/<path>.<ext>` 字面量。排除注释行、`_external`、
`node_modules`、`.venv`、`.pytest_run`。

## 为什么带「白名单」而不是直接把 15 处全修掉

这 4 类悬空引用的修法都**含口径选择**，不属 agent 可自行决定的范畴：
  - `resolve_notify_level.py` → #341 的 **A 删 legacy / B 恢复能力 / C 修引用 / D 仅记录**；
  - `analyze_logs.py` → #350 的 **A 补顶层软链 / B 改 docstring**；
  - `create_symlinks.sh` + 占位符 → #350 建议**删掉整个 `verify_reorganization.sh`**。

⇒ 本守卫的职责是**拦住新增悬空引用**，并把存量**显式登记**（而不是藏起来）。
配一条「白名单不得过期」的断言：一旦某条被修好，对应条目立刻变红，**强制同步清理**。
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

SCAN_DIRS = ("scripts", "src", "backend", "deploy")
SCAN_EXTS = {
    ".sh",
    ".py",
    ".mjs",
    ".js",
    ".yaml",
    ".yml",
    ".json",
    ".service",
    ".timer",
    ".conf",
}
SKIP_PARTS = {"_external", "node_modules", ".git", ".venv", "__pycache__", ".pytest_run"}

# `scripts/<path>.(py|sh|mjs|js)`；左侧否定字符避免匹配到 `./scripts/...` 之外的粘连
_REF_RX = re.compile(r"(?<![\w/.-])(scripts/[A-Za-z0-9_./-]+\.(?:py|sh|mjs|js))")

# 扫描集下限（实测 537 文件 / 211 引用）。低于下限说明扫描逻辑或目录布局变了。
_MIN_FILES = 300
_MIN_REFS = 100

# 🔴 存量悬空引用白名单：ref -> 「为什么还没修 + 归属 issue」。
# 修好任一条后，`test_known_dangling_allowlist_is_not_stale` 会失败 ⇒ 请同步删掉对应条目。
KNOWN_DANGLING: dict[str, str] = {
    "scripts/resolve_notify_level.py": (
        "#341 —— daily_run.sh 调用但全仓不存在；修法待仓主定"
        "（A 删 legacy / B 恢复 critical 抑制能力 / C 修引用 / D 仅记录）"
    ),
    "scripts/analyze_logs.py": (
        "#350 —— monitor/analyze_logs.py 的用法说明引用根路径，但缺顶层同名软链；"
        "修法待定（A 补软链 / B 改 docstring 为 scripts/monitor/analyze_logs.py）"
    ),
    "scripts/create_symlinks.sh": (
        "#350 —— verify_reorganization.sh 引用；该脚本在 git 全历史中从未存在"
    ),
    "scripts/path/to/script.sh": (
        "#350 —— verify_reorganization.sh 里的占位符（非真实引用）"
    ),
}


def _scanned_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for rel in SCAN_DIRS:
        base = REPO_ROOT / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_EXTS:
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            out.append(path)
    return sorted(out)


def _scan() -> tuple[int, dict[str, list[str]]]:
    """返回 (引用总数, {悬空 ref: [位置...]})。"""
    total = 0
    dangling: dict[str, list[str]] = {}
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for match in _REF_RX.finditer(line):
                ref = match.group(1)
                total += 1
                if not (REPO_ROOT / ref).exists():
                    dangling.setdefault(ref, []).append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}"
                    )
    return total, dangling


def test_scan_set_is_not_empty() -> None:
    """前置断言：扫描面不能为空/骤减 —— 否则下面的守卫是空转的橡皮章。"""
    files = _scanned_files()
    total, _ = _scan()
    assert len(files) >= _MIN_FILES, (
        f"扫描到的文件只有 {len(files)} 个（预期 ≥ {_MIN_FILES}）—— 目录布局或扩展名清单变了？"
    )
    assert total >= _MIN_REFS, (
        f"扫到的 `scripts/...` 路径引用只有 {total} 处（预期 ≥ {_MIN_REFS}）—— "
        f"正则或口径变了？否则「零悬空」可能只是没扫到东西。"
    )


def test_no_new_dangling_script_refs() -> None:
    """主断言：不得出现**新增**的悬空 `scripts/` 路径引用。"""
    _, dangling = _scan()
    new = sorted(ref for ref in dangling if ref not in KNOWN_DANGLING)
    detail = "\n".join(
        f"  {ref}\n      " + "\n      ".join(dangling[ref]) for ref in new
    )
    assert not new, (
        "以下 `scripts/` 路径被引用但**不存在**（悬空引用会让运维按图索骥时静默走空）：\n"
        f"{detail}\n"
        "修法：把引用改成真实路径，或补顶层兼容软链（见 scripts/maintenance/create_symlinks.sh 的约定）。"
        "若确认是「暂不修」的存量债务，请显式加入本文件的 KNOWN_DANGLING 并注明归属 issue。"
    )


def test_known_dangling_allowlist_is_not_stale() -> None:
    """白名单不得过期：条目对应的引用若已不存在，必须同步删除该条目。

    这条断言让白名单**自我清理** —— 否则它会随着问题被修复而越积越像「永久豁免」，
    最终失去意义（把「已知债务清单」变成「免死金牌」）。
    """
    _, dangling = _scan()
    stale = sorted(ref for ref in KNOWN_DANGLING if ref not in dangling)
    assert not stale, (
        "以下条目已不再是悬空引用（对应问题应已修复）⇒ 请从 KNOWN_DANGLING 中删除：\n  "
        + "\n  ".join(stale)
    )


def test_known_dangling_entries_cite_an_issue() -> None:
    """白名单每条都要注明归属 issue —— 否则无从追踪、也不会有人来清。"""
    bad = sorted(ref for ref, why in KNOWN_DANGLING.items() if "#" not in why)
    assert not bad, f"以下白名单条目未注明归属 issue：{bad}"
