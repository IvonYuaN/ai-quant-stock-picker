"""调度补全契约测试（整改路线图 P0 #2/#3，2026-09-25）。

A. prod→runner 同步：runner_sync.sh 此前纯手动、无任何调度方 ⇒ runner 3y 库
   停在 20260918、生产 gate 被 blocked_cutoff 秒退。现在注册进 emit_jobs
   （周六 09:20，避开生产 gate 窗口 07:30-17:30 UTC，赶在周六 22:00 gate 前），
   脚本自带新鲜度探针（落后超阈值输出 [ALERT][runner_sync] 告警行）。
B. 排雷层 pit_cache fetchers：调度统一走 bt_task event-data → preload_event_data.sh
   → 全部 fetch_*.py（#185 已注册周一至五 08:20）。锁死「覆盖全部 fetch 脚本」
   与「LockupSource 写出路径 == LockupReleaseFilter 读取路径」两条契约。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CRON_SCRIPT = PROJECT_ROOT / "scripts" / "install_server_cron.sh"
BT_TASK_SCRIPT = PROJECT_ROOT / "scripts" / "bt_task.sh"
RUNNER_SYNC_SCRIPT = PROJECT_ROOT / "scripts" / "runner_sync.sh"
PRELOAD_SCRIPT = PROJECT_ROOT / "scripts" / "preload_event_data.sh"


def _bash_supports_lower_expansion() -> bool:
    bash = shutil.which("bash")
    if bash is None:
        return False
    return (
        subprocess.run(
            [bash, "-c", ": ${AQSP_PROBE_VAR,,}"], capture_output=True
        ).returncode
        == 0
    )


def _extract_emit_jobs() -> str:
    """切出 emit_jobs 函数体，不执行脚本其余部分（绝不写 crontab）。"""
    text = CRON_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^emit_jobs\(\) \{.*?^\}", text)
    assert match, "install_server_cron.sh 中找不到 emit_jobs 函数"
    return match.group(0)


def _run_emit_jobs(env: dict[str, str]) -> list[str]:
    setup = "; ".join(f"export {k}={v!r}" for k, v in env.items())
    body = _extract_emit_jobs()
    if not _bash_supports_lower_expansion():
        # bash 3.2（如 macOS 自带 /bin/bash）不支持 ${var,,}；本测试传的
        # 开关值全为小写，降级为 ${var} 语义不变，逻辑断言依然有效。
        body = body.replace(",,}", "}")
    result = subprocess.run(
        ["bash", "-c", f"{setup}; {body}; emit_jobs"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _emit_env(**overrides: str) -> dict[str, str]:
    env: dict[str, str] = {
        "PROJECT_ROOT": "/opt/aqsp",
        "CRON_LOG": "/opt/aqsp/logs/cron.log",
        "SCHEDULER_BIN": "/opt/aqsp/scripts/bt_task.sh",
        **{
            f"ENABLE_{name}": "true"
            for name in (
                "INTRADAY",
                "MIDDAY",
                "DAILY",
                "COLDSTART",
                "WALKFORWARD_GATE",
                "RUNNER_SYNC",
                "NEWS",
                "EVENT_DATA",
                "MONITOR",
            )
        },
    }
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# A 组：prod→runner 同步调度
# ---------------------------------------------------------------------------


def test_emit_jobs_registers_runner_sync_weekly_before_gate() -> None:
    lines = _run_emit_jobs(_emit_env())

    sync_lines = [line for line in lines if " runner-sync " in line]
    assert len(sync_lines) == 1, f"runner-sync 应恰好注册一次: {sync_lines}"
    assert sync_lines[0].startswith("20 9 * * 6 "), (
        "必须在周六 09:20（北京时间）：周五 daily 已落库、周末无写入、"
        "01:20 UTC 避开生产 gate 窗口 07:30-17:30 UTC，赶在周六 22:00 gate 前"
    )
    assert "bt_task.sh runner-sync" in sync_lines[0]

    # 开关可关；同一 action 允许多时段（intraday/news 本就如此）但整行不得重复
    toggled = _run_emit_jobs(_emit_env(ENABLE_RUNNER_SYNC="false"))
    assert not [x for x in toggled if " runner-sync " in x]
    assert len(lines) == len(set(lines)), f"emit_jobs 输出了重复的 cron 行: {lines}"

    # 新调度不得挤掉既有条目（fetchers 依赖的 event-data、周六 gate、18:00 daily）
    joined = "\n".join(lines)
    assert "20 8 * * 1-5 " in joined and "bt_task.sh event-data >> " in joined
    assert "0 22 * * 6 " in joined and "0 18 * * 1-5 " in joined


def test_install_server_cron_declares_runner_sync_toggle_and_regex() -> None:
    script = CRON_SCRIPT.read_text(encoding="utf-8")
    assert "AQSP_ENABLE_RUNNER_SYNC_CRON" in script
    # 重装 crontab 时旧行清理正则必须覆盖 runner-sync，否则会越积越多
    assert re.search(r"walkforward-gate\|runner-sync", script)


def test_bt_task_has_runner_sync_action() -> None:
    script = BT_TASK_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^    runner-sync\)", script, re.MULTILINE), (
        "bt_task.sh 必须有 runner-sync action（release_task_entrypoint.sh 最终"
        "委托给 bt_task.sh，新增 action 对 simple/immutable 两种模式同时生效）"
    )
    assert "scripts/runner_sync.sh" in script
    assert "runner-sync" in script.split("Usage: bt_task.sh <")[1].split(">")[0]


def test_runner_sync_has_freshness_probe() -> None:
    script = RUNNER_SYNC_SCRIPT.read_text(encoding="utf-8")
    # 探针必须读 MAX(trade_date) 并在落后时输出 monitors 可捕捉的告警行
    assert "SELECT MAX(trade_date) FROM daily_qfq" in script
    assert "[ALERT][runner_sync]" in script
    assert "AQSP_SYNC_STALE_DAYS" in script
    assert "blocked_cutoff" in script


# ---------------------------------------------------------------------------
# B 组：排雷层 pit_cache fetchers 调度契约
# ---------------------------------------------------------------------------


def test_preload_event_data_covers_all_fetch_scripts() -> None:
    text = PRELOAD_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^SOURCES=\(\n(.*?)^\)", text)
    assert match, "preload_event_data.sh 中找不到 SOURCES 列表"

    referenced: list[str] = []
    for entry in match.group(1).splitlines():
        entry = entry.strip().strip('"')
        if not entry:
            continue
        _, _, rel = entry.partition(":")
        assert rel.startswith("scripts/fetch_"), f"非法 SOURCES 条目: {entry}"
        assert (PROJECT_ROOT / rel).exists(), f"引用的脚本不存在: {rel}"
        referenced.append(rel)

    # 五类 pit_cache 数据源必须全部被 preload 覆盖：lockup（LockupReleaseFilter）、
    # longhubang / cls_news / concept_board / event_data（→ EventCalendar）。
    for required in (
        "scripts/fetch_lockup.py",
        "scripts/fetch_longhubang.py",
        "scripts/fetch_cls_news.py",
        "scripts/fetch_concept_board.py",
        "scripts/fetch_event_data.py",
    ):
        assert required in referenced, f"fetch 脚本未被 preload 覆盖: {required}"


def test_fetch_pit_caches_have_scheduled_entry_point() -> None:
    """fetch_* 的唯一调度入口是 bt_task event-data（emit_jobs 周一至五 08:20）。"""
    bt_task = BT_TASK_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^    event-data\)", bt_task, re.MULTILINE)
    assert "preload_event_data.sh" in bt_task

    cron = CRON_SCRIPT.read_text(encoding="utf-8")
    assert "20 8 * * 1-5 " in cron
    assert re.search(r"' event-data >> ", cron)
    # 08:20 盘前、先于 08:35 news、在 07:30-17:30 UTC gate 窗口之外（00:20 UTC）
    assert re.search(r"\n\s*# 盘前刷 pit_cache", cron)


def test_lockup_producer_consumer_path_consistency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LockupSource 写出的 pit_cache/lockup.csv 必须就是 LockupReleaseFilter 读的文件。

    审计确认旧版 filter 读 data/lockup_schedule.csv（全仓不存在）⇒ 解禁排雷
    恒 passed=True；本断言防止写读两套路径规则再次漂移。
    """
    from aqsp.data.lockup import LockupSource
    from aqsp.filters_lethal.lockup_release import LockupReleaseFilter

    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    assert Path(LockupReleaseFilter().data_path) == Path(
        LockupSource()._default_cache_path()
    )
    assert LockupReleaseFilter().data_path.endswith("pit_cache/lockup.csv")

    # 未配置 runtime root 时，写读双方必须回落到同一个临时目录（生产兜底一致）
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT")
    assert Path(LockupReleaseFilter().data_path) == Path(
        LockupSource()._default_cache_path()
    )
