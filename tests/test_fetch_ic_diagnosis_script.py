"""fetch_ic_diagnosis.sh 的新鲜度契约测试。

背景（2026-09-25，IC 回流调度化）：runner 侧 ic_diagnosis_runner.sh 落
pit_cache/factor_ic/，由 prod 主动 pull 回来给收评日报的「因子 IC 健康」段
消费。runner 是只读计算节点、无连回生产的 SSH 权限，回流走 prod→runner
单向 pull（权限面最小）。IC_READY 是唯一新鲜度证据：runner 跑失败/超时/让位
都会删掉 IC_READY，prod 侧据此判「没有新结果」，绝不把 9 月旧产物当成本周
结果。本测试用假 ssh/rsync 回放远端事实，锁死退出码契约：

  0 成功（新鲜，已拉取）
  1 连不上 runner（网络/主机密钥/公钥）
  2 NO_RESULT（无 IC_READY / 有 IC_READY 但 JSON 缺失）
  3 STALE（IC_READY 陈旧 > MAX_AGE_HOURS，保留本地旧产物不冒充）
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "fetch_ic_diagnosis.sh"


_FAKE_SSH = """#!/usr/bin/env bash
# 回放远端事实：末参含 "bash -s" ⇒ 是 probe 命令，回放远端状态行；
# 其余（如 host:test -f 的存在性探测）一律放行（exit 0）。
# stdin 必须 drain（probe 走 heredoc，否则写端 SIGPIPE）。
cmd="${@: -1}"
if [ "${FAKE_SSH_FAIL:-0}" = "1" ]; then
  echo "ssh: connect to host runner port 31777: Connection refused" >&2
  cat >/dev/null 2>&1 || true
  exit 255
fi
cat >/dev/null 2>&1 || true
case "$cmd" in
  *bash\\ -s*)
    now=$(date +%s)
    echo "NOW=$now"
    if [ "${FAKE_READY_PRESENT:-0}" = "1" ]; then
      echo "READY_PRESENT=1"
      echo "READY_MTIME=$(( now - ${FAKE_READY_AGE_H:-0} * 3600 ))"
      echo "READY_VALUE=2026-09-25T02:00:00Z"
    else
      echo "READY_PRESENT=0"
    fi
    if [ "${FAKE_JSON_PRESENT:-1}" = "1" ]; then
      echo "JSON_PRESENT=1"
      echo "JSON_MTIME=$(( now - ${FAKE_READY_AGE_H:-0} * 3600 ))"
    else
      echo "JSON_PRESENT=0"
    fi
    echo "STATUS_VALUE=${FAKE_STATUS_VALUE:-}"
    ;;
esac
exit 0
"""

_FAKE_RSYNC = """#!/usr/bin/env bash
# 只创建目标文件、不真传输：从 host:remote 源参提取文件名，写进末参目标目录
target="${@: -1}"
mkdir -p "$target"
for a in "$@"; do
  case "$a" in
    *:*)
      path="${a#*:}"
      base="${path##*/}"
      printf 'stub\\n' > "${target%/}/$base"
      ;;
  esac
done
exit 0
"""


def _fake_bin(
    tmp_path: Path,
    *,
    ready_present: int = 0,
    ready_age_h: int = 0,
    json_present: int = 1,
    status_value: str = "",
    ssh_fail: bool = False,
    dry_run: str = "0",
    max_age_hours: str = "36",
) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    (fake_bin / "ssh").write_text(_FAKE_SSH, encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)
    (fake_bin / "rsync").write_text(_FAKE_RSYNC, encoding="utf-8")
    (fake_bin / "rsync").chmod(0o755)

    dest_dir = str(tmp_path / "factor_ic")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEST_DIR": dest_dir,
        "DRY_RUN": dry_run,
        "MAX_AGE_HOURS": max_age_hours,
        "FAKE_READY_PRESENT": str(ready_present),
        "FAKE_READY_AGE_H": str(ready_age_h),
        "FAKE_JSON_PRESENT": str(json_present),
        "FAKE_STATUS_VALUE": status_value,
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
    return result, Path(env["DEST_DIR"])


def test_fetch_ic_pulls_fresh_result(tmp_path: Path) -> None:
    result, dest = _run(
        tmp_path,
        ready_present=1,
        ready_age_h=2,
        json_present=1,
        status_value="completed",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("factor_ic_latest.json", "ic_history.jsonl", "report.md", "IC_READY"):
        assert (dest / name).exists(), f"新鲜产物应被拉取：{name}"
        assert (dest / name).read_text(encoding="utf-8") == "stub\n"


def test_fetch_ic_no_result_when_missing_ready(tmp_path: Path) -> None:
    """runner 没跑 / load 让位 / 失败 ⇒ 无 IC_READY：保留本地旧产物，exit 2。"""
    result, dest = _run(tmp_path, ready_present=0, json_present=1)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (dest / "factor_ic_latest.json").exists(), "NO_RESULT 时不得拉取"


def test_fetch_ic_no_result_when_json_missing(tmp_path: Path) -> None:
    """有 IC_READY 但 JSON 产物缺失（runner 侧写一半就挂）⇒ 视为无结果，exit 2。"""
    result, dest = _run(tmp_path, ready_present=1, ready_age_h=2, json_present=0)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (dest / "factor_ic_latest.json").exists()


def test_fetch_ic_quarantines_stale_ready(tmp_path: Path) -> None:
    """IC_READY 陈旧（> MAX_AGE_HOURS）⇒ 保留本地旧产物，绝不冒充本周结果，exit 3。"""
    result, dest = _run(
        tmp_path,
        ready_present=1,
        ready_age_h=48,  # 超过默认 36h 上限
        json_present=1,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert not (dest / "factor_ic_latest.json").exists(), "STALE 时不得把旧产物当新的拉下来"


def test_fetch_ic_stale_threshold_is_env_tunable(tmp_path: Path) -> None:
    """把 MAX_AGE_HOURS 抬到 60：48h 的产物转为新鲜，exit 0。"""
    result, dest = _run(
        tmp_path,
        ready_present=1,
        ready_age_h=48,
        json_present=1,
        max_age_hours="60",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (dest / "factor_ic_latest.json").exists()


def test_fetch_ic_dry_run_judges_but_does_not_pull(tmp_path: Path) -> None:
    result, dest = _run(
        tmp_path,
        ready_present=1,
        ready_age_h=2,
        json_present=1,
        dry_run="1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (dest / "factor_ic_latest.json").exists(), "DRY_RUN 不得实际拉取"


def test_fetch_ic_fails_closed_when_runner_unreachable(tmp_path: Path) -> None:
    result, dest = _run(tmp_path, ssh_fail=True, ready_present=1, json_present=1)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (dest / "factor_ic_latest.json").exists(), "连不上时保留本地旧产物"
