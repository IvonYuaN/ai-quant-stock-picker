"""fetch_paper_arena.sh 的新鲜度契约测试（#684）。

背景：prod 的 `data/paper_arena.json` 停在 11-23 发版快照，runner 每天 15:10
更新却没有回流通道 ⇒ Web 显示 11-20 的旧净值且后端误标 stale=false。
本脚本补单向 pull（prod→runner，runner 无回连权限），用产物自述的
`generated_at` 判新鲜度 —— mtime 会被 rsync/cp 重置，不能当证据。

退出码契约（best-effort，调用方只记日志不阻断）：
  0 成功（新鲜，已拉取）
  1 连不上 runner（网络/公钥）
  2 NO_RESULT（远端产物缺失 / schema 非 v1 / generated_at 缺失或非法）
  3 STALE（generated_at 陈旧 > MAX_AGE_HOURS，保留本地旧文件不覆盖）

最关键的不变量：**陈旧/失败一律不覆盖本地旧文件**。发版快照虽旧但自洽，
用一个「新鲜度判定失败」的产物覆盖它，只会让页面从「旧但自洽」变成
「来源不明的脏数据」。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "data" / "fetch_paper_arena.sh"


# 假 ssh：回放远端事实。probe 命令（末参含 "bash -s"）回传状态行；
# rsync 调用直接 success（拉一个本地准备好的"远端产物"到目标临时路径）。
_FAKE_SSH = """#!/usr/bin/env bash
cmd="${@: -1}"
if [ "${FAKE_SSH_FAIL:-0}" = "1" ]; then
  echo "ssh: connect to host port 31777: Connection refused" >&2
  cat >/dev/null 2>&1 || true
  exit 255
fi
case "$cmd" in
  *bash\\ -s*)
    cat >/dev/null 2>&1 || true
    now=$(date +%s)
    echo "NOW=${now}"
    if [ "${FAKE_PRESENT:-1}" = "0" ]; then
      echo "PRESENT=0"
    else
      echo "PRESENT=1"
      echo "MTIME=${now}"
      echo "GEN=${FAKE_GEN:-$(date -u +%Y-%m-%dT%H:%M:%S+00:00)}"
      echo "SCHEMA=${FAKE_SCHEMA:-v1}"
    fi
    ;;
  *)
    cat >/dev/null 2>&1 || true
    ;;
esac
exit 0
"""


def _setup(tmp_path: Path, *, gen: str | None = None, present: str = "1",
           schema: str = "v1", ssh_fail: str = "0") -> tuple[dict[str, str], Path, Path]:
    """搭一个假 ssh/rsync 沙箱，返回 (env, 本地旧产物, 远端新产物)。"""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    ssh.write_text(_FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    (fake_bin / "rsync").write_text(
        "#!/usr/bin/env bash\n"
        "# 参数形如: -a -e 'ssh ...' host:/remote/file /dest/tmp\n"
        "args=()\n"
        "for a in \"$@\"; do case \"$a\" in -*) ;; *) args+=(\"$a\") ;; esac; done\n"
        "dst=\"${args[${#args[@]}-1]}\"\n"
        "[ -n \"$dst\" ] || exit 1\n"
        "cp \"${FAKE_REMOTE_FILE:-/nonexistent}\" \"$dst\" 2>/dev/null || exit 1\n"
        "exit 0\n",
        encoding="utf-8")
    (fake_bin / "rsync").chmod(0o755)

    dest_dir = tmp_path / "data"
    dest_dir.mkdir()
    dest = dest_dir / "paper_arena.json"
    old_payload = {"schema_version": "v1", "generated_at": "2026-11-23T13:23:12+08:00",
                   "as_of": "2026-11-20", "sleeves": [], "total_nav": 59806.16}
    dest.write_text(json.dumps(old_payload), encoding="utf-8")

    remote = tmp_path / "remote" / "paper_arena_api.json"
    remote.parent.mkdir()
    new_payload = {**old_payload, "generated_at": gen or "2026-11-26T15:10:05+08:00",
                   "as_of": "2026-11-26", "total_nav": 61068.0}
    remote.write_text(json.dumps(new_payload), encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DEST": str(dest),
        "FAKE_REMOTE_FILE": str(remote),
        "FAKE_PRESENT": present,
        "FAKE_SCHEMA": schema,
        "FAKE_SSH_FAIL": ssh_fail,
        "MAX_AGE_HOURS": "36",
        "RUNNER_HOST": "root@fake",
    })
    if gen is not None:
        env["FAKE_GEN"] = gen
    return env, dest, remote


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=30)


def test_fresh_payload_is_pulled_and_replaces_old(tmp_path: Path) -> None:
    """新鲜产物 ⇒ exit 0，本地旧文件被替换（generated_at/as_of 都是新的）。"""
    env, dest, _ = _setup(tmp_path)
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["as_of"] == "2026-11-26"
    assert data["total_nav"] == 61068.0


def test_stale_payload_never_overwrites_local(tmp_path: Path) -> None:
    """🔴 核心：产物陈旧 ⇒ exit 3 且本地旧文件原封不动（#684 根因防线）。"""
    env, dest, _ = _setup(tmp_path, gen="2026-11-20T15:10:05+08:00")
    before = dest.read_text(encoding="utf-8")
    r = _run(env)
    assert r.returncode == 3, r.stdout + r.stderr
    assert dest.read_text(encoding="utf-8") == before, "陈旧产物覆盖了本地旧文件"


def test_missing_remote_preserves_local(tmp_path: Path) -> None:
    """远端无产物 ⇒ exit 2，本地旧文件保留。"""
    env, dest, _ = _setup(tmp_path, present="0")
    before = dest.read_text(encoding="utf-8")
    r = _run(env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert dest.read_text(encoding="utf-8") == before


def test_bad_schema_preserves_local(tmp_path: Path) -> None:
    """schema 非 v1 ⇒ exit 2（fail-closed），绝不覆盖本地旧文件。"""
    env, dest, _ = _setup(tmp_path, schema="v0")
    before = dest.read_text(encoding="utf-8")
    r = _run(env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert dest.read_text(encoding="utf-8") == before


def test_unparseable_generated_at_preserves_local(tmp_path: Path) -> None:
    """generated_at 不是 ISO ⇒ exit 2，不覆盖。"""
    env, dest, _ = _setup(tmp_path, gen="not-a-date")
    before = dest.read_text(encoding="utf-8")
    r = _run(env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert dest.read_text(encoding="utf-8") == before


def test_runner_unreachable_preserves_local(tmp_path: Path) -> None:
    """连不上 runner ⇒ exit 1，本地旧文件保留。"""
    env, dest, _ = _setup(tmp_path, ssh_fail="1")
    before = dest.read_text(encoding="utf-8")
    r = _run(env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert dest.read_text(encoding="utf-8") == before


def test_dry_run_does_not_touch_local(tmp_path: Path) -> None:
    """DRY_RUN=1 ⇒ exit 0 且本地文件不动（供 cron 上线前演练）。"""
    env, dest, _ = _setup(tmp_path)
    env["DRY_RUN"] = "1"
    before = dest.read_text(encoding="utf-8")
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert dest.read_text(encoding="utf-8") == before
