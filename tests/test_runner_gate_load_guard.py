"""runner_gate.sh 的共享业务机让位守卫。

背景：runner（38.147.170.174）**不是专用计算节点**，而是一台共享生产业务机 ——
宝塔面板 + nginx + MySQL + Redis + pure-ftpd，PM2 上还跑着 ifidy/lanshe 三个线上业务。
AQSP 只是 `/opt/aqsp-runner/` 的租户。2026-09-17 那次 4 路并发 prefetch 被 I/O 拖死，
很可能就是和业务抢盘。

所以 gate 在起跑前必须先看 load：超限就让位并**跳过本轮**。
跳过不是失败 —— 脚本 exit 0 并把原因写进 skip.log，否则 prod 侧无法区分
「这周没跑」和「跑了但没结果」。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "runner_gate.sh"


def _run(runner_root: Path, loadavg: Path, **env_overrides: str):
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "RUNNER_ROOT": str(runner_root),
        "LOADAVG_PATH": str(loadavg),
        "LOAD_GUARD_WAIT_SEC": "0",
    }
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _loadavg(path: Path, load1: str) -> Path:
    path.write_text(f"{load1} 0.10 0.10 1/100 12345\n", encoding="utf-8")
    return path


@pytest.fixture
def runner_root(tmp_path: Path) -> Path:
    root = tmp_path / "aqsp-runner"
    root.mkdir()
    return root


def test_script_keeps_load_guard_enabled_by_default() -> None:
    """生产排期必须开着守卫 —— 默认值不能被改成关闭。"""
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'LOAD_GUARD="${LOAD_GUARD:-1}"' in text
    assert 'MAX_LOAD1="${MAX_LOAD1:-4}"' in text
    assert 'LOADAVG_PATH="${LOADAVG_PATH:-/proc/loadavg}"' in text


def test_high_load_skips_run_without_touching_release(
    runner_root: Path, tmp_path: Path
) -> None:
    """load 超限：跳过本轮、exit 0、写 skip.log，且不去碰 release/DB。"""
    loadavg = _loadavg(tmp_path / "loadavg", "9.99")

    result = _run(runner_root, loadavg)

    assert result.returncode == 0, result.stderr
    assert "让位于业务" in result.stdout

    skip_log = runner_root / "gate_run" / "skip.log"
    assert skip_log.exists()
    assert "9.99" in skip_log.read_text(encoding="utf-8")

    # 停在守卫处，没有继续走前置校验
    assert "缺 release" not in result.stdout + result.stderr


def test_low_load_proceeds_past_guard(runner_root: Path, tmp_path: Path) -> None:
    """load 正常：守卫放行，继续走前置校验。"""
    loadavg = _loadavg(tmp_path / "loadavg", "0.01")

    result = _run(runner_root, loadavg)
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert "load1=0.01" in result.stdout
    assert "缺 release" in combined
    assert not (runner_root / "gate_run" / "skip.log").exists()


def test_load_equal_to_threshold_is_allowed(runner_root: Path, tmp_path: Path) -> None:
    """恰好等于阈值应当放行（边界取闭区间），避免阈值形同虚设。"""
    loadavg = _loadavg(tmp_path / "loadavg", "4.00")

    result = _run(runner_root, loadavg, MAX_LOAD1="4")

    assert "让位于业务" not in result.stdout
    assert "缺 release" in result.stdout + result.stderr


def test_guard_can_be_disabled_for_diagnostics(
    runner_root: Path, tmp_path: Path
) -> None:
    """LOAD_GUARD=0 仅供诊断；此时不检查 load。"""
    loadavg = _loadavg(tmp_path / "loadavg", "99.99")

    result = _run(runner_root, loadavg, LOAD_GUARD="0")

    assert "让位于业务" not in result.stdout
    assert "缺 release" in result.stdout + result.stderr
