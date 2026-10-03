"""守卫：受版本控制的 shell 脚本不得含「丢了 `#` 的中文注释行」（裸中文命令行）。

背景（2026-10-03 整体排查实测确认，见当日日志 `2026-10-03.md` 与本文件 docstring）：
`scripts/data/fetch_ic_diagnosis.sh` 第 18 行原为
    退出码是 best-effort 语义：调用方（daily 链路）只记日志、绝不因此阻断跑批。
本该是头部注释（上下文全是 `#` 注释），却**漏了行首 `#`**。bash 于是把 `退出码是` 当成
命令去执行 ⇒ 运行期报 `line 18: 退出码是: command not found`（stderr，退出码 127）。

为什么长期没被现有测试抓到：`tests/test_fetch_ic_diagnosis_script.py` 只断言
`result.returncode`（0/1/2/3），**从不检查 stderr**；且该行位于 `set -euo pipefail`
**之前**，127 不会中断脚本 ⇒ 回流功能一直"看起来正常"，只是每次 prod cron 都在
cron.log 里多打一行错误噪音。属于「静默劣化」类缺陷。

本守卫做两件事：
  1. **静态**：扫描所有 tracked `*.sh`，若某行「去掉前导空白后首字符是中文、且不是注释、
     且不在 heredoc 正文 / echo/printf 等输出串 / 跨行双引号字符串里」⇒ 判定为疑似丢了
     `#` 的裸命令行并失败。纯 ASCII 脚本、以及中文只出现在注释或字符串里的写法都不受
     影响（无误伤）。
  2. **动态**：对已知踩坑脚本 fetch_ic_diagnosis.sh 直接跑头部（`set -e` 之前的部分），
     断言 stderr 里不出现 `command not found` —— 静态规则万一漏判，动态兜底。
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

CJK = re.compile(r"[\u4e00-\u9fff]")
# `<<EOF` `<<'EOF'` `<<-EOF` `<<"EOF"`：只取真正的 here-doc 起始（`<<<` 是 herestring，不算）
HEREDOC_OPEN = re.compile(r"<<(?!<)\s*-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# 明显的「输出串起始」前缀：这些行里的中文是给人看的消息，不是命令
OUTPUT_PREFIXES = ("echo", "printf", "log", "cat", "die", "fail", "warn")


def _tracked_shell_scripts() -> list[pathlib.Path]:
    try:
        names = subprocess.run(
            ["git", "ls-files", "*.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        names = [str(p.relative_to(REPO_ROOT)) for p in REPO_ROOT.rglob("*.sh")]
    return [REPO_ROOT / n for n in names]


def _in_heredoc_body(lines: list[str]) -> set[int]:
    """返回处于 here-doc 正文内的行号集合（1-based），正确处理 `<<TAG` 开闭。"""
    body: set[int] = set()
    tag: str | None = None
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if tag is not None:
            body.add(lineno)
            # 闭合标记：独立一行、与 tag 同名（<<- 允许制表符缩进，已被 strip 处理）
            if stripped == tag:
                tag = None
            continue
        m = HEREDOC_OPEN.search(raw)
        if m:
            # 形如 `cat <<EOF` 的开启行本身不是正文；下一行才是
            tag = m.group(2)
    return body


def _in_multiline_dquote(lines: list[str]) -> set[int]:
    """返回处于跨行双引号字符串内的行号集合（1-based），用于容纳多行错误/用法提示。"""
    inside: set[int] = set()
    in_str = False
    for lineno, raw in enumerate(lines, 1):
        # 粗略统计未转义的双引号数；开启行本身不算正文
        if in_str:
            inside.add(lineno)
        # 去掉行内注释后再数（# 后的内容不算）
        code = re.sub(r"(?<!\\)#.*$", "", raw)
        n = len(re.findall(r'(?<!\\)"', code))
        if n % 2 == 1:
            in_str = not in_str
    return inside


def _suspect_bare_cjk_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """返回该脚本中疑似「丢了 # 的裸中文命令行」的 (行号, 内容)。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    heredoc_body = _in_heredoc_body(lines)
    dquote_body = _in_multiline_dquote(lines)

    suspects: list[tuple[int, str]] = []
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.lstrip()
        if lineno in heredoc_body or lineno in dquote_body:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if not CJK.match(stripped[0]):
            continue
        if stripped.startswith(OUTPUT_PREFIXES) or stripped.startswith(("'", '"')):
            continue
        suspects.append((lineno, stripped[:80]))
    return suspects


@pytest.mark.parametrize("script", _tracked_shell_scripts(), ids=lambda p: p.name)
def test_shell_scripts_have_no_bare_cjk_command_lines(script: pathlib.Path) -> None:
    """tracked *.sh 不得含「首字符为中文、且非注释/非输出串」的裸命令行。"""
    if not script.is_file():
        pytest.skip("软链/非普通文件")
    suspects = _suspect_bare_cjk_lines(script)
    assert not suspects, (
        f"{script.relative_to(REPO_ROOT)} 疑似有丢了 `#` 的中文注释行，bash 会当命令执行"
        f"（运行期 `command not found`）：\n"
        + "\n".join(f"  L{ln}: {text}" for ln, text in suspects)
    )


def test_fetch_ic_diagnosis_header_runs_without_command_not_found() -> None:
    """动态兜底：IC 回流脚本 `set -e` 之前的头部执行时，stderr 不得报 command not found。

    真实事故：第 18 行漏 `#`，bash 报 `退出码是: command not found`。因在 `set -e` 之前，
    退出码仍为 0，只有 stderr 有噪音 —— 断言 stderr 才能抓住。
    """
    script = REPO_ROOT / "scripts" / "data" / "fetch_ic_diagnosis.sh"
    assert script.is_file(), f"未找到 {script}"
    lines = script.read_text(encoding="utf-8").splitlines()
    end = next((i for i, ln in enumerate(lines) if ln.strip().startswith("set -e")), 40)
    header = "\n".join(lines[:end]) + "\n"
    proc = subprocess.run(
        ["bash", "-s"], input=header, capture_output=True, text=True, check=False
    )
    assert "command not found" not in proc.stderr, (
        "fetch_ic_diagnosis.sh 头部含裸中文命令行（丢了 # 的注释行）：\n" + proc.stderr
    )
