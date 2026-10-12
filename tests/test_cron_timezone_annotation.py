"""守卫：定义生产 cron 的仓库内文件，必须写明「北京时间」。

背景（issue #351 C 项）：`AGENTS.md` §3.4 要求「cron 表达式注释里必须写明北京时间 X:XX」，
但**既有的守卫只扫 `.github/workflows/*.yml`**（`tests/test_github_workflows.py::
test_github_workflow_cron_entries_document_beijing_time`），而生产 cron 的定义点全在别处：

  - `config/backup_cron.example`            —— 备份示例模板（**用户会照抄**）
  - `scripts/deploy/install_server_cron.sh` —— 系统 cron 安装脚本（emit_jobs 里 echo 出 cron 行）
  - `scripts/deploy/install_ic_diagnosis_cron.sh` —— runner IC 诊断 cron（时区是 UTC！）
  - `scripts/deploy/install_coldstart_cron.sh`    —— 冷启动 cron

风险（真实事故面）：crontab **按本机时区解释**。上面第二个脚本明确运行在 UTC 机器上
（`北京时间 − 8h = UTC`）。若照抄一份没有时区标注的 cron 到 UTC 机器，**全部任务整体偏移 8 小时**。
实测 2026-10-12：`config/backup_cron.example` 的 13 条 cron 行**一条都没有时区标注**（已修，见 PR #355）。

## 为什么用「显式清单」而不是自动发现

本仓库里含「5 字段 cron 形态」的文件有 **21 个**，其中绝大多数是**假阳性**：
`tests/test_check_before_live.py`（夹具）、`tests/test_scheduler*.py`、`scripts/analysis/*.py`
（数值/日期形态误匹配）、`src/aqsp/cli.py` 等。自动发现会把它们全拖进来。
⇒ 采用**显式清单**（下面 `CRON_DEFINING_FILES`），并配**前置断言**保证清单没被改空/路径没写错
（否则守卫会静默变成空转的橡皮章 —— 项目已有先例：`tests/test_cron_logdir_guard.py:96-102`）。

## 判据为什么是「表达式上方须出现过北京时间」而不是「邻近 N 行」

两种真实的书写风格并存，只有「上方出现过」能同时容纳：

  1. **行内邻近**：`config/backup_cron.example` —— 每条 cron 上一行就是它的说明；
  2. **文件头块**：`scripts/deploy/install_server_cron.sh` —— 头部注释块**逐条列出**北京时间，
     而 `echo '*/10 9-11 * * 1-5 ...'` 在 40 行开外。若按「邻近 3~4 行」判，这个文件会**误报 10 处**。

⇒ 判据定为：**每个 cron 表达式的上方（文件内更早处）必须出现过「北京时间」**。
这能抓住真正的回归形态：① 新文件完全没有时区标注；② 在文件末尾追加 cron 条目而不补标注。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 显式清单：定义生产/示例 cron 的仓库内文件（真身路径，不含顶层软链以免重复计数）
CRON_DEFINING_FILES: tuple[str, ...] = (
    "config/backup_cron.example",
    "scripts/deploy/install_server_cron.sh",
    "scripts/deploy/install_ic_diagnosis_cron.sh",
    "scripts/deploy/install_coldstart_cron.sh",
)

BEIJING_MARK = "北京时间"

# 5 字段 cron 形态。允许出现在行内任意位置（覆盖三种真实写法）：
#   ① 裸行        0 2 * * * cd ...
#   ② echo 包裹   echo '*/10 9-11 * * 1-5 /bin/bash ...'
#   ③ 变量默认值  CRON_SCHEDULE="${VAR:-0 2 * * 1-5}"
# 前后用「非 cron 字段字符」界定；⚠️ 不能用 `-` 做左侧排除，否则 `${VAR:-30 17 * * 1-5}` 的 `:-` 会被误排除。
_FIELD = r"(?:\*|\d+|\*/\d+|\d+-\d+|\d+,\d+)"
CRON_RX = re.compile(rf"(?<![0-9*/]){_FIELD}\s+{_FIELD}\s+{_FIELD}\s+{_FIELD}\s+{_FIELD}(?![0-9/])")

# 格式图例行（如 `#   *   *   *   *   *   command`）不是真实 cron 条目，排除。
_LEGEND_PREFIX = "#   *"


def _cron_expression_lines(path: Path) -> list[int]:
    """返回该文件里 cron 表达式所在行号（1-based）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        lineno
        for lineno, line in enumerate(lines, 1)
        if CRON_RX.search(line) and not line.strip().startswith(_LEGEND_PREFIX)
    ]


def test_cron_defining_files_are_present_and_nonempty() -> None:
    """前置断言：清单非空且文件都在 —— 否则下面的守卫是空转的橡皮章。"""
    assert len(CRON_DEFINING_FILES) >= 4, (
        "cron 定义文件清单被改小了：本守卫依赖该清单覆盖全部生产 cron 定义点"
    )
    missing = [rel for rel in CRON_DEFINING_FILES if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"清单里的文件不存在（路径写错或文件被删）：{missing}"


def test_cron_defining_files_actually_define_cron() -> None:
    """前置断言：清单里的文件确实含有 cron 表达式。

    若某文件被改成不再定义 cron（或正则失灵），本断言会失败 —— 防止
    「候选为 0 ⇒ 下面的守卫恒通过」这类空转（先例 `tests/test_cron_logdir_guard.py:96-102`）。
    """
    counts = {
        rel: len(_cron_expression_lines(REPO_ROOT / rel)) for rel in CRON_DEFINING_FILES
    }
    zero = [rel for rel, n in counts.items() if n == 0]
    assert not zero, (
        f"这些文件按清单应当定义 cron，但没扫到任何表达式：{zero}；"
        f"若确实已不再定义 cron，请同步更新 CRON_DEFINING_FILES。实测计数：{counts}"
    )


@pytest.mark.parametrize("rel", CRON_DEFINING_FILES)
def test_every_cron_expression_has_beijing_time_annotation_above(rel: str) -> None:
    """每个 cron 表达式上方必须出现过「北京时间」（AGENTS §3.4）。"""
    path = REPO_ROOT / rel
    lines = path.read_text(encoding="utf-8").splitlines()

    offenders: list[str] = []
    for lineno in _cron_expression_lines(path):
        # 取「截至本行」——注释与表达式同行的写法是合法的（如
        # `#   - 北京时间 10:00 = UTC 02:00 ⇒ 默认 schedule `0 2 * * 1-5`；`）。
        up_to_here = "\n".join(lines[:lineno])
        if BEIJING_MARK not in up_to_here:
            offenders.append(f"  L{lineno}: {lines[lineno - 1].strip()[:90]}")

    assert not offenders, (
        f"{rel} 有 cron 表达式在「北京时间」标注之前出现 —— crontab 按本机时区解释，"
        f"UTC 机器上会整体偏移 8 小时（AGENTS §3.4）：\n" + "\n".join(offenders)
    )


def test_guard_scope_excludes_github_workflows() -> None:
    """职责边界：workflow 里的 cron 由 `test_github_workflows.py` 的专门守卫负责，本守卫不重复。

    这条断言把「边界」写成可执行契约：若有人把 workflow 文件塞进本清单，这里会失败。
    """
    assert not [rel for rel in CRON_DEFINING_FILES if rel.startswith(".github/")], (
        "workflow 的 cron 注释由 tests/test_github_workflows.py 负责，勿在本清单重复覆盖"
    )


def _git_tracked_files() -> set[str] | None:
    """返回 git 跟踪的文件集合；**不在 git 工作树内时返回 None**。

    沿用本仓既有约定（`tests/test_shell_cjk_comment_guard.py:41-50` 的
    `try git ... except CalledProcessError`）—— 但那里能回落 `rglob`，
    而「是否被 git 跟踪」在非工作树里**没有有意义的替代**，故此处返回 None 由调用方 skip。
    """
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return set(out.split())


def test_cron_defining_files_are_tracked_by_git() -> None:
    """清单里的文件必须受版本控制（否则改了也不进 PR）。

    ⚠️ 该属性**只在 git 工作树里有意义**。prod 的不可变 release 是 `git archive`
    解包（**无 `.git`**），从那里跑测试时必须 skip —— 否则得到一个与被测对象无关的假失败。
    （2026-10-12 在 prod 预置 release 内做切链前冒烟时**实测到**这个失败：
    同批的 `test_shell_cjk_comment_guard.py` / `test_server_runtime_scripts.py`
    因有 `except` 回落而通过，只有本用例硬失败。）
    """
    tracked = _git_tracked_files()
    if tracked is None:
        pytest.skip("不在 git 工作树内（release 目录无 .git），「被 git 跟踪」属性不适用")
    untracked = [rel for rel in CRON_DEFINING_FILES if rel not in tracked]
    assert not untracked, f"清单里的文件未被 git 跟踪：{untracked}"
