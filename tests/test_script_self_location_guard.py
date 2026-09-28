"""守卫：经「根级兼容软链」可达的脚本，不得用裸 `parents[N]` 算仓库根并**拿去组合路径**。

背景（2026-09-28 重组暴露，实测确认）：
scripts/ 重组把 107 个脚本移到子目录、并在 `scripts/` 根留同名兼容软链（prod cron 仍按
根路径调用）。而 `Path(__file__).resolve()` **会跟随软链**落到真身（`scripts/<子目录>/x.py`），
于是 `resolve().parents[1]` 得到的是 **`scripts/`** 而不是仓库根 —— 只要它被拿去组合路径
（`PROJECT_ROOT / "data/..."`、`cwd=PROJECT_ROOT`、读 `.env`/`.venv`），就会**静默指错位置**：
读到不存在的文件、或把产物写到错误目录（属「静默失效」类事故）。

正确做法：向上寻找含 `pyproject.toml` 的目录（本项目统一用 `_find_project_root`）。

本守卫只检查**同时满足**两条件的脚本，避免误伤仅用 `parents[N]` 引导 `sys.path` 的写法
（那种即使算错，调用方通常已通过 `PYTHONPATH` 提供正确路径）：
  1. 该脚本在 `scripts/` 根有同名软链（⇒ 会被按根路径调用）；
  2. 其真身源码里出现了 `PROJECT_ROOT /` 或 `REPO_ROOT /` 这类**路径组合**用法。
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = REPO_ROOT / "scripts"
BARE_PARENTS = "Path(__file__).resolve().parents["
ROBUST_MARKER = "_find_project_root"
PATH_COMPOSE_MARKERS = ("PROJECT_ROOT /", "REPO_ROOT /", "cwd=PROJECT_ROOT")


def _root_symlinked_python_scripts() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for link in sorted(SCRIPTS_ROOT.glob("*")):
        if link.is_symlink() and link.suffix == ".py" and link.resolve().is_file():
            out.append(link.resolve())
    return out


def test_root_symlinked_scripts_do_not_compose_paths_from_bare_parents() -> None:
    offenders: list[str] = []
    for real in _root_symlinked_python_scripts():
        text = real.read_text(encoding="utf-8")
        if BARE_PARENTS not in text or ROBUST_MARKER in text:
            continue
        if not any(marker in text for marker in PATH_COMPOSE_MARKERS):
            continue
        offenders.append(str(real.relative_to(REPO_ROOT)))
    assert not offenders, (
        "以下脚本经 scripts/ 根软链调用时会把仓库根算成 scripts/（且拿它组合路径）⇒ 静默指错位置。\n"
        "修法：改用向上寻找 pyproject.toml（参考 write_home_snapshot.py 的 _find_project_root）：\n"
        + "\n".join(offenders)
    )
