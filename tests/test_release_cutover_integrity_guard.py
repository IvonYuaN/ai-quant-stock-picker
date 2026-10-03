"""守卫：切链（`ln -sfn` / `switch_links`）必须在**完整性校验之后**，且校验不可被绕过。

背景（2026-10-03 线上真事故 + 修复）：
`85f596e7` 同步进 runner 时**残缺**（`src/aqsp` 仅 166 个 .py、整个 `src/aqsp/data/`
子包缺失），而 `aqsp-scheduler-current` 软链仍指向它 ⇒ IC 以
`ModuleNotFoundError: No module named 'aqsp.data'` 1 秒崩溃。
根因是 `runner_sync.sh` 里 `ln -sfn` 是 rsync 之后的**独立一条命令**（rsync 中断也
照样切链）且切链前无任何完整性检查。已修（#298）并加了三条断言。

本守卫把**两条切链路径**的不变量锁死：
- **runner 侧** `scripts/runner/runner_sync.sh`：rsync 失败即 exit、切链前体检
  （关键子包 + .py 数量下限）、体检失败不切链。
- **prod 侧** `scripts/deploy/deploy_immutable_release.sh`：`check_release` /
  `check_runtime_dependencies` / `run_scheduler_check` / `assert_idle_before_switch`
  全部**先于** `switch_links`（实测已是正确顺序 L688/614/165 → L694，但**没有任何
  测试锁住这个顺序**，将来被人调换顺序不会有人发现）。

prod 侧为什么天然更安全：它用 `git archive | tar -x` 在**本机**暂存目录展开（无
rsync 中断possibility），且切链前会真跑 `import aqsp.cli` 探针 + manifest 一致性校验。
本测试确保这些性质**不被回归破坏**。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SYNC = PROJECT_ROOT / "scripts" / "runner" / "runner_sync.sh"
PROD_DEPLOY = PROJECT_ROOT / "scripts" / "deploy" / "deploy_immutable_release.sh"


def _first_code_line(text: str, needle: str) -> int:
    """`needle` 首次出现在**非注释行**的 1-based 行号（注释同名字样要忽略）。"""
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        if needle in line:
            return i
    raise AssertionError(f"找不到真正执行的 {needle!r}（脚本形态变了？请同步更新本守卫）")


@pytest.fixture(scope="module", params=[RUNNER_SYNC, PROD_DEPLOY], ids=["runner_sync", "prod_deploy"])
def script(request: pytest.FixtureRequest) -> Path:
    assert request.param.is_file(), f"未找到 {request.param}"
    # 读一遍确保可解码（编码问题早暴露），内容由各测试自行读取
    assert request.param.read_text(encoding="utf-8") is not None
    bash = shutil.which("bash")
    if bash:
        r = subprocess.run([bash, "-n", str(request.param)], capture_output=True, text=True)
        assert r.returncode == 0, f"{request.param.name} bash -n 失败：{r.stderr}"
    return request.param


def test_cutover_never_precedes_integrity_checks(script: Path) -> None:
    """切链必须排在完整性校验之后（否则残缺 release 会被切上去）。"""
    text = script.read_text(encoding="utf-8")
    if script == RUNNER_SYNC:
        cut = _first_code_line(text, "ln -sfn")
        # runner：切链前必须先 rsync 成功（if ! rsync）+ 完整性体检
        rsync = _first_code_line(text, "rsync -aP --delete")
        assert rsync < cut, "rsync 出现在切链之后"
    else:
        cut = _first_code_line(text, 'switch_links "$RELEASE_DIR"')
        # prod：以下校验都必须在 switch_links 之前
        for check in (
            'check_release "$RELEASE_DIR"',
            "run_scheduler_check",
            "assert_idle_before_switch",
        ):
            pos = _first_code_line(text, check)
            assert pos < cut, (
                f"prod deploy 的 {check}（L{pos}）排在切链（L{cut}）之后 —— "
                "校验必须在切链前，否则残缺 release 会被切上去"
            )


def test_no_unconditional_cutover_after_failed_step(script: Path) -> None:
    """rsync/体检失败后不得继续切链（失败路径必须 exit）。"""
    text = script.read_text(encoding="utf-8")
    if script != RUNNER_SYNC:
        pytest.skip("prod 侧用 git archive 本机展开 + set -e，无 rsync 中断场景")
    cut = _first_code_line(text, "ln -sfn")
    lines = text.splitlines()
    pre_lines = lines[:cut]

    # rsync 必须是 `if ! rsync …; then` ⇒ 失败进入 then 分支
    rsync_i = next(
        (i for i, ln in enumerate(pre_lines) if re.search(r"if\s*!\s*rsync", ln)), None
    )
    assert rsync_i is not None, "rsync 未被 `if !` 包裹"
    # 该 if 块内必须有 exit
    rsync_block = "\n".join(pre_lines[rsync_i : rsync_i + 6])
    assert re.search(r"exit\s+1", rsync_block), (
        "rsync 失败分支缺少 exit 1：失败后仍会切链"
    )

    # 体检失败分支：远端 heredoc 里用 `exit 3/4` 表达"体检不过"，本地 then 里 exit 1
    heredoc_i = next(
        (i for i, ln in enumerate(pre_lines) if "TOO_FEW_PY" in ln or "MISSING:" in ln),
        None,
    )
    assert heredoc_i is not None, "找不到完整性体检的失败标记（TOO_FEW_PY / MISSING）"
    # 远端非 0 退出 ⇒ 本地 `if !` 的 then 分支必须 exit
    after = "\n".join(pre_lines[heredoc_i : heredoc_i + 8])
    assert re.search(r"^\s*exit\s+1", after, re.M), (
        "体检不过（远端非 0）后本地未 exit 1：体检失败仍会切链"
    )


def test_critical_subpackage_guard_present(script: Path) -> None:
    """完整性体检的关键子包名单必须含 src/aqsp/data（IC/factor_ic 直接 import）。"""
    text = script.read_text(encoding="utf-8")
    if script != RUNNER_SYNC:
        pytest.skip("prod 侧用 import aqsp.cli 探针 + manifest 校验，不做子包清单")
    assert re.search(r"for\s+sub\s+in\s+[^\n;]*src/aqsp/data", text), (
        "runner 切链体检的子包名单缺 src/aqsp/data"
    )
    # 体检必须在切链之前
    assert _first_code_line(text, "for sub in") < _first_code_line(text, "ln -sfn")


def test_release_sha_written_after_cutover(script: Path) -> None:
    """runner 侧：`RELEASE_SHA` 必须在切链**之后**写。

    语义差异（两条路径含义不同，故分别断言）：
    - **runner**：`/opt/aqsp-runner/RELEASE_SHA` 是「当前正在跑哪个 release」的**活标记**，
      必须晚于切链写入；否则切链/校验失败时会留下「记录=新 SHA、实跑=旧 SHA」的不一致
      （这正是 10-02 IC 事故里日志显示 3805ed3e 而实跑 85f596e7 的成因）。
    - **prod**：`RELEASE_SHA` 由 `stamp_manifest()` 写进 **staged release 目录内部**，
      调用点在切链**之前**是正确的（先盖章再上架）；「当前跑哪个」由软链本身表达。
      故 prod 侧只断言「stamp_manifest 在切链之前被调用」。
    """
    text = script.read_text(encoding="utf-8")
    if script == RUNNER_SYNC:
        cut = _first_code_line(text, "ln -sfn")
        sha = _first_code_line(text, "RELEASE_SHA")
        assert sha > cut, (
            f"runner 侧 RELEASE_SHA（L{sha}）必须写在切链（L{cut}）之后，"
            "否则失败时会留下「记录=新 SHA、实跑=旧 SHA」的不一致"
        )
    else:
        cut = _first_code_line(text, 'switch_links "$RELEASE_DIR"')
        calls = [
            i
            for i, ln in enumerate(text.splitlines(), 1)
            if re.search(r'^\s*stamp_manifest\s+"', ln) and not ln.strip().startswith("#")
        ]
        assert calls, "找不到 stamp_manifest 的调用点"
        assert calls[0] < cut, (
            f"prod 侧 stamp_manifest 调用（L{calls[0]}）应早于切链（L{cut}）：先盖章再上架"
        )
