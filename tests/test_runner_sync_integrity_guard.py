"""守卫：`runner_sync.sh` 的切链（`ln -sfn`）必须与「rsync 成功 + release 完整性体检」绑定。

事故背景（2026-10-03 实测定位，线上真实故障）：
`85f596e7` 于 10-01 03:21 同步进 runner 后**残缺** —— `src/aqsp` 下仅 **166** 个
`.py`（正常 release 应 222）、**整个 `src/aqsp/data/` 子包不存在**。而
`aqsp-scheduler-current` 软链仍指向它 ⇒ 10-02 02:00 的 IC 以
`ModuleNotFoundError: No module named 'aqsp.data'` **1 秒崩溃**。

旧代码的三个缺陷（已在脚本里修掉，此测试锁死修复后的不变量）：
1. `ln -sfn` 是 rsync 之后的**独立一条命令** ⇒ rsync 中断/失败也照样切链；
2. 切链前**不做任何完整性检查** ⇒ 残缺 release 也能被指向；
3. `RELEASE_SHA` 写的是**目标** SHA ⇒ 日志显示 3805ed3e 而实跑的是残缺的 85f596e7
   （日志与实跑 release 不一致，是本次排查最大的陷阱）。

本守卫（纯静态读脚本 + 语法检查，零副作用、不连任何服务器）断言：
- `rsync` 被 `if !` 包裹且失败即 `exit`（不切链）；
- 存在切链前的**完整性体检**（关键子包存在性 + `.py` 数量下限）；
- 体检失败路径会 `exit` 且不会走到 `ln -sfn`；
- 关键子包名单里必须含 `src/aqsp/data`（IC/factor_ic 直接 import 它，缺了必崩）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = PROJECT_ROOT / "scripts" / "runner_sync.sh"


@pytest.fixture(scope="module")
def sync_text() -> str:
    assert SYNC_SCRIPT.is_file(), f"未找到 {SYNC_SCRIPT}"
    return SYNC_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sync_bash(sync_text: str) -> str:
    """校验 bash 语法（macOS 自带 bash 3.2 即可做语法检查）。"""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover
        pytest.skip("无 bash")
    r = subprocess.run([bash, "-n", str(SYNC_SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"runner_sync.sh bash -n 失败：{r.stderr}"
    return bash


def _first_code_offset(sync_text: str, needle: str) -> int:
    """`needle` 首次出现在**非注释行**的字符偏移。

    事故修复时我在注释里也写了 `ln -sfn` / `RELEASE_SHA` 字样，直接 `str.find` 会
    命中注释 ⇒ 必须逐行跳过以 `#` 开头的注释行，取第一条真正执行的命令。
    """
    lines = sync_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        if needle in line:
            return sum(len(prev) + 1 for prev in lines[:i])
    raise AssertionError(f"脚本里找不到真正执行的 {needle}（形态变了？请同步更新本守卫）")


def _ln_switch_pos(sync_text: str) -> int:
    """切链命令 `ln -sfn` 的真实执行位置。"""
    return _first_code_offset(sync_text, "ln -sfn")


def _before_switch(sync_text: str) -> str:
    return sync_text[: _ln_switch_pos(sync_text)]


def test_sync_script_syntax_ok(sync_bash: str) -> None:
    assert sync_bash  # fixture 已断言 -n 通过


def test_rsync_failure_blocks_symlink_switch(sync_text: str) -> None:
    """rsync 必须被 `if !` 包裹且失败即退出——否则中断同步也会切链到残缺 release。"""
    assert re.search(r"if\s*!\s*rsync\b", sync_text), (
        "runner_sync.sh 的 rsync 未被 `if !` 包裹：rsync 失败/中断仍会继续切链"
    )
    rsync_block = _before_switch(sync_text)
    assert re.search(r"if\s*!\s*rsync.*?exit\s+1", rsync_block, re.S), (
        "rsync 失败分支缺少 exit 1：失败后仍会执行切链"
    )


def test_integrity_check_present_before_switch(sync_text: str) -> None:
    """切链前必须做完整性体检（关键子包存在 + .py 数量下限）。"""
    pre = _before_switch(sync_text)
    # 体检的实质：存在遍历子包的存在性检查 + .py 数量下限
    assert re.search(r"for\s+sub\s+in\s+[^\n;]+;", pre), "切链前缺少子包存在性遍历检查"
    assert re.search(r"-ge\s+\d{3}", pre), "切链前缺少 .py 数量下限检查（find | wc -l + -ge N）"
    # 体检失败必须 exit（在切链之前）
    assert re.search(r"(TOO_FEW_PY|MISSING).*?exit", pre, re.S), (
        "体检失败分支缺少 exit：体检不过仍会切链"
    )


def test_critical_subpackage_aqsp_data_is_guarded(sync_text: str) -> None:
    """关键子包名单必须含 src/aqsp/data（IC/factor_ic 直接 import，缺了必崩）。"""
    m = re.search(r"for\s+sub\s+in\s+([^\n;]+);", sync_text)
    assert m, "找不到体检的子包遍历循环"
    subs = m.group(1)
    assert "src/aqsp/data" in subs, (
        f"完整性体检的子包名单缺 src/aqsp/data（实际：{subs.strip()}）"
    )


def test_release_sha_written_after_integrity_gate(sync_text: str) -> None:
    """RELEASE_SHA 必须在体检+切链之后写（否则日志 SHA 与实跑 release 不一致）。"""
    ln_pos = _ln_switch_pos(sync_text)
    sha_pos = _first_code_offset(sync_text, "RELEASE_SHA")
    assert sha_pos > ln_pos, (
        "RELEASE_SHA 写在切链之前：rsync/切链失败时会留下「记录=新 SHA、实跑=旧 SHA」的不一致"
    )
