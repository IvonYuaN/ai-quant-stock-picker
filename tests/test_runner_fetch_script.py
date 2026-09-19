"""runner_fetch.sh 的新鲜度契约测试。

背景（2026-09-19）：旧版 runner_fetch.sh 只按「远端文件存在」就拉，不校验
runner_gate.sh 已经落好的 RESULT_READY 标记，于是 runner 上 9 月的旧
report.md / gate_summary.md 会被拉成 runner.* 前缀，在 prod 侧伪造出
「本周有结果」。旧版还从不回传 walkforward_production_status.json，
导致 prod 无法区分「本轮超时」与「本轮没跑」。

这里用假 ssh/rsync 回放远端事实，锁死退出码契约：
  0 成功 / 2 让位跳过 / 3 跑了没结果 / 4 产物陈旧 / 1 连不上
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "runner_fetch.sh"


_FAKE_SSH = """#!/usr/bin/env bash
# 回放远端 probe 事实；参数与 stdin 都忽略（stdin 要 drain，否则写端 SIGPIPE）
if [ "${FAKE_SSH_FAIL:-0}" = "1" ]; then
  echo "ssh: connect to host runner port 31777: Connection refused" >&2
  cat >/dev/null 2>&1 || true
  exit 255
fi
cat >/dev/null 2>&1 || true
now=$(date +%s)
echo "NOW=$now"
if [ "${FAKE_READY_PRESENT:-0}" = "1" ]; then
  echo "READY_PRESENT=1"
  echo "READY_MTIME=$(( now - ${FAKE_READY_AGE_H:-0} * 3600 ))"
  echo "READY_VALUE=2026-09-19T05:13:00Z"
else
  echo "READY_PRESENT=0"
fi
if [ "${FAKE_GATE_PRESENT:-0}" = "1" ]; then
  echo "GATE_PRESENT=1"
  echo "GATE_MTIME=$(( now - ${FAKE_GATE_AGE_H:-0} * 3600 ))"
else
  echo "GATE_PRESENT=0"
fi
if [ "${FAKE_STATUS_PRESENT:-0}" = "1" ]; then
  echo "STATUS_PRESENT=1"
  echo "STATUS_MTIME=$(( now - ${FAKE_STATUS_AGE_MIN:-0} * 60 ))"
  echo "STATUS_VALUE=${FAKE_STATUS_VALUE:-}"
  echo "STATUS_DETAIL=${FAKE_STATUS_DETAIL:-}"
else
  echo "STATUS_PRESENT=0"
fi
echo "SKIP_TAIL=${FAKE_SKIP_TAIL:-}"
echo "SHA=deadbeef"
"""

_FAKE_RSYNC = """#!/usr/bin/env bash
# 只创建目标文件，不真的传输；最后一个参数是目标路径
for last in "$@"; do :; done
mkdir -p "$(dirname "$last")"
printf 'stub\\n' >"$last"
exit 0
"""


def _fake_bin(
    tmp_path: Path,
    *,
    ready_present: int = 0,
    ready_age_h: int = 0,
    gate_present: int = 0,
    gate_age_h: int = 0,
    status_present: int = 0,
    status_value: str = "",
    status_detail: str = "",
    status_age_min: int = 0,
    skip_tail: str = "",
    ssh_fail: bool = False,
) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    (fake_bin / "ssh").write_text(_FAKE_SSH, encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)
    (fake_bin / "rsync").write_text(_FAKE_RSYNC, encoding="utf-8")
    (fake_bin / "rsync").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GATE_DIR": str(tmp_path / "gate_run"),
        "FAKE_READY_PRESENT": str(ready_present),
        "FAKE_READY_AGE_H": str(ready_age_h),
        "FAKE_GATE_PRESENT": str(gate_present),
        "FAKE_GATE_AGE_H": str(gate_age_h),
        "FAKE_STATUS_PRESENT": str(status_present),
        "FAKE_STATUS_VALUE": status_value,
        "FAKE_STATUS_DETAIL": status_detail,
        "FAKE_STATUS_AGE_MIN": str(status_age_min),
        "FAKE_SKIP_TAIL": skip_tail,
        "FAKE_SSH_FAIL": "1" if ssh_fail else "0",
    }
    return fake_bin, env


def _run(tmp_path: Path, **kwargs) -> tuple[subprocess.CompletedProcess[str], Path]:
    _, env = _fake_bin(tmp_path, **kwargs)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    return result, Path(env["GATE_DIR"])


def _result_env(gate_dir: Path) -> dict[str, str]:
    payload = gate_dir / "runner_fetch_result.env"
    assert payload.exists(), "runner_fetch_result.env 未生成"
    parsed: dict[str, str] = {}
    for line in payload.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


def test_runner_fetch_accepts_fresh_result(tmp_path: Path) -> None:
    result, gate_dir = _run(
        tmp_path,
        ready_present=1,
        ready_age_h=2,
        gate_present=1,
        gate_age_h=2,
        status_present=1,
        status_value="completed",
    )

    assert result.returncode == 0, result.stderr
    assert (gate_dir / "runner.walkforward_gate.json").exists()
    assert (gate_dir / "runner.report.md").exists()
    env = _result_env(gate_dir)
    assert env["status"] == "ok"
    assert env["verdict"] == "FRESH"
    assert env["ready_age_hours"] == "2"


def test_runner_fetch_quarantines_stale_result_instead_of_faking_current(
    tmp_path: Path,
) -> None:
    """旧版会把 9 月旧产物拉成 runner.* —— 现在必须隔离且不落该前缀。"""
    result, gate_dir = _run(
        tmp_path,
        ready_present=1,
        ready_age_h=240,  # 10 天前的标记
        gate_present=1,
        gate_age_h=240,
        status_present=1,
        status_value="failed",
    )

    assert result.returncode == 4, result.stderr
    assert not (gate_dir / "runner.walkforward_gate.json").exists()
    assert not (gate_dir / "runner.report.md").exists()
    quarantined = list((gate_dir / "runner.stale").glob("*/walkforward_gate.json"))
    assert quarantined, "陈旧产物必须被隔离留证"
    env = _result_env(gate_dir)
    assert env["status"] == "stale"
    assert env["verdict"] == "STALE"


def test_runner_fetch_reports_missing_result_and_always_returns_runner_status(
    tmp_path: Path,
) -> None:
    result, gate_dir = _run(
        tmp_path,
        ready_present=0,
        gate_present=0,
        status_present=1,
        status_value="timeout",
        status_detail="child walkforward timed out",
    )

    assert result.returncode == 3, result.stderr
    # 状态文件是 prod 区分「超时 / 没跑 / 成功」的唯一证据，必须无条件回传
    assert (gate_dir / "runner.walkforward_production_status.json").exists()
    env = _result_env(gate_dir)
    assert env["status"] == "missing"
    assert env["runner_status"] == "timeout"


def test_runner_fetch_classifies_load_skip_as_skipped_not_failed(
    tmp_path: Path,
) -> None:
    """让位跳过 ≠ 失败：必须与「跑了没结果」分开。"""
    result, gate_dir = _run(
        tmp_path,
        ready_present=0,
        gate_present=0,
        status_present=0,
        skip_tail="2026-09-19 01:00:01 load1=6.12 > 4,让位于业务,跳过本轮",
    )

    assert result.returncode == 2, result.stderr
    env = _result_env(gate_dir)
    assert env["status"] == "skipped"
    assert env["verdict"] == "SKIPPED"


def test_runner_fetch_flags_stale_running_status_instead_of_waiting_forever(
    tmp_path: Path,
) -> None:
    """进程被杀但状态仍写 running 时，必须提示「疑似已死」而不是让人干等一周。"""
    result, gate_dir = _run(
        tmp_path,
        ready_present=0,
        gate_present=0,
        status_present=1,
        status_value="running",
        status_detail="child walkforward running; elapsed=840s",
        status_age_min=600,
    )

    assert result.returncode == 3, result.stderr
    assert "疑似进程已死" in result.stdout
    env = _result_env(gate_dir)
    assert env["runner_status"] == "running"
    assert env["status_age_minutes"] == "600"


def test_runner_fetch_fails_closed_when_runner_unreachable(tmp_path: Path) -> None:
    result, gate_dir = _run(tmp_path, ssh_fail=True)

    assert result.returncode == 1, result.stderr
    assert not (gate_dir / "runner_fetch_result.env").exists()
