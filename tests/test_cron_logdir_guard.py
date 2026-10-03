"""守卫：`install_server_cron.sh` 产出的每一条 cron 行，其日志重定向目录都必须被脚本创建。

背景（2026-10-03 审计 prod crontab 实证）：
cron 行把 stdout/stderr 重定向到**日志文件**时，若该文件的**父目录不存在**，
cron 在重定向阶段就失败 ⇒ **整条命令静默不执行**（连 shell 都进不去），而 cron
daemon 仍显示"已调度"。这是本项目 10-01 已踩过的真事故（③④ 两行
`flock -c ... >> /opt/aqsp/data/logs/fetch_ic_diagnosis/cron.log`，因目录未预建
而整条静默失效，cron.log 一直空白却被误判为"在跑"；见 MEMORY「③/② prod cron
静默失败已修」）。

当时的修复是**手工**在那两行 `flock -c` 串内前置 `mkdir -p`。但脚本里真正保障
目录的只有一处：`mkdir -p "$(dirname "$CRON_LOG")"`（默认 `/opt/aqsp/logs/cron.log`）。
**其它 per-action 日志目录并不由脚本创建** —— 例如 event-data 实际重定向到
`/opt/aqsp/data/logs/event-data/cron-wrap.log`（prod crontab 实测），该目录自
2026-09-30 偶然存在才没出事；一旦被清理，整行又会静默失效，且**没有任何测试
会发现**。

本守卫（只读、零副作用）：
  1. 切出 `emit_jobs()` 函数体（不执行脚本其余部分，绝不写 crontab）；
  2. 跑出它 echo 的 cron 行；
  3. 对每行解析 `>> <path>` 重定向目标，收集其父目录；
  4. 断言每个父目录都能在脚本里找到对应的 `mkdir -p` 保障
     （`dirname "$CRON_LOG"` 视为覆盖 `$(dirname "$CRON_LOG")` 那一处）。

断言失败 ⇒ 该 cron 行存在「目录不存在就静默失效」的风险面，须在脚本里补
`mkdir -p`（幂等、零风险），而不是指望目录碰巧存在。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CRON_SCRIPT = PROJECT_ROOT / "scripts" / "install_server_cron.sh"

# cron 行里的重定向：`>> /some/path/file.log 2>&1`（也可能 `&> file` / `2> file`）
REDIRECT = re.compile(r"(?:>>|&>)\s*([^\s;&|]+)")


def _bash_supports_lower_expansion() -> bool:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover
        return False
    return (
        subprocess.run(
            [bash, "-c", ": ${AQSP_PROBE_VAR,,}"], capture_output=True
        ).returncode
        == 0
    )


def _emit_jobs_lines() -> list[str]:
    """跑 emit_jobs() 拿到它 echo 的 cron 行（纯 stdout，不碰 crontab）。

    注意 bash 版本：macOS 自带 bash 3.2 **不支持 `${VAR,,}`**（小写展开），
    而 `emit_jobs` 每个开关都写成 `[[ "${ENABLE_X,,}" =~ ^(1|true|yes|on)$ ]]`
    ⇒ bash 3.2 下 `${ENABLE_X,,}` 语法错、整个函数体逐行失败、**一行都不吐**
    （本守卫初版就踩了这个坑：行数 0 而测试仍"通过"= 橡皮章）。
    故这里与 `test_scheduling_completion.py` 同样做 `,,}`→`}` 降级。
    另外必须把**全部** `ENABLE_*` 都显式置 1，否则未设置的开关会因空值不匹配
    正则而整条 cron 行缺失，守卫覆盖面被悄悄削弱。
    """
    text = CRON_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^emit_jobs\(\) \{.*?^\}", text)
    assert match, "install_server_cron.sh 中找不到 emit_jobs 函数"
    body = match.group(0)
    if not _bash_supports_lower_expansion():
        # bash 3.2：${var,,} 语法错 → 降级为 ${var}；开关值全为小写 "1"，语义不变
        body = body.replace(",,}", "}")
    env = {
        "ENABLE_INTRADAY": "1",
        "ENABLE_MIDDAY": "1",
        "ENABLE_DAILY": "1",
        "ENABLE_COLDSTART": "1",
        "ENABLE_WALKFORWARD_GATE": "1",
        "ENABLE_RUNNER_SYNC": "1",
        "ENABLE_NEWS": "1",
        "ENABLE_EVENT_DATA": "1",
        "ENABLE_MONITOR": "1",
        "CRON_LOG": "/opt/aqsp/logs/cron.log",
        "SCHEDULER_BIN": "/opt/aqsp/scripts/bt_task.sh",
    }
    setup = "; ".join(f"export {k}={v!r}" for k, v in env.items())
    result = subprocess.run(
        ["bash", "-c", f"{setup}; {body}; emit_jobs"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def test_emit_jobs_produces_cron_lines() -> None:
    """前置断言：真的拿到了 cron 行（否则下面的守卫是空转的橡皮章）。"""
    lines = _emit_jobs_lines()
    assert len(lines) >= 9, (
        f"emit_jobs 只产出 {len(lines)} 行（预期 ≥9 条 action）；"
        "若为 0，多半是 bash 3.2 的 ${VAR,,} 语法错未被降级处理。"
    )
    # 每种 action 至少出现一次，防止"某开关没置上 ⇒ 该 action 的行整体缺失"
    for action in (
        "intraday",
        "midday",
        "daily",
        "coldstart",
        "walkforward-gate",
        "runner-sync",
        "news",
        "event-data",
        "monitor",
    ):
        assert any(action in ln for ln in lines), f"缺少 {action} 的 cron 行"


def test_every_redirected_cron_log_dir_is_created_by_script() -> None:
    """每条 cron 行的重定向目录，脚本必须显式 mkdir -p 保障（防静默失效）。"""
    lines = _emit_jobs_lines()
    text = CRON_SCRIPT.read_text(encoding="utf-8")

    # 脚本里所有 mkdir -p 保障
    mkdir_blob = "\n".join(re.findall(r"mkdir\s+-p\s+[^\n]*", text))
    # 归一化：把 "$(dirname "$CRON_LOG")" 视作 CRON_LOG 家族（脚本唯一显式保障）
    covers_cron_log = "dirname" in mkdir_blob and "CRON_LOG" in mkdir_blob
    # 其它显式 mkdir -p 里写死的目录字面量
    literals = [
        frag.strip().strip('"')
        for frag in re.findall(r'mkdir\s+-p\s+"?([^"\n]+)"?', mkdir_blob)
        if "$" not in frag
    ]

    offenders: list[str] = []
    for line in lines:
        for target in REDIRECT.findall(line):
            if not target.startswith("/"):
                # 相对路径 → 依赖 cron 的 cwd，不在本次审计范围
                continue
            parent = str(Path(target).parent)
            covered = covers_cron_log and (
                "$CRON_LOG" in parent or parent.endswith("/logs")
            )
            if not covered:
                covered = any(
                    lit and lit in parent for lit in literals
                )
            if not covered:
                offenders.append(f"  {parent}   ← {line.strip()[:110]}")

    assert not offenders, (
        "以下 cron 行的重定向目录没有 mkdir -p 保障；目录一旦不存在，"
        "整条 cron 会静默不执行（10-01 已发生过）：\n" + "\n".join(offenders)
    )


def test_cron_log_dir_creation_precedes_job_emission() -> None:
    """脚本必须先 mkdir 日志目录、再产出 cron 行（顺序颠倒 = 仍会失效）。"""
    text = CRON_SCRIPT.read_text(encoding="utf-8")
    mkdir_pos = text.find('mkdir -p "$(dirname "$CRON_LOG")"')
    if mkdir_pos < 0:
        mkdir_pos = text.find('mkdir -p "$(dirname "${CRON_LOG}")"')
    assert mkdir_pos >= 0, "脚本未创建 CRON_LOG 目录"
    emit_pos = text.find("emit_jobs()")
    assert emit_pos >= 0, "脚本找不到 emit_jobs"
    assert mkdir_pos < emit_pos, (
        "mkdir -p 日志目录必须早于 emit_jobs（否则 cron 行先被产出、目录后建，"
        "首次安装仍可能静默失效）"
    )
