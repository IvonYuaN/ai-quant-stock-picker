"""Adapt the existing server monitor to the web tenant LIVE risk service."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aqsp.core.time import is_market_open, now_shanghai

if TYPE_CHECKING:
    from aqsp.monitor.checker import MonitorResult


@dataclass(frozen=True)
class LivePositionMonitorConfig:
    timeout_seconds: int = 120


def _subprocess_identity(data_root: Path) -> dict[str, int | list[int]]:
    """Let a root-run monitor create locks as the API's data owner."""
    if os.geteuid() != 0:
        return {}
    owner = data_root.stat()
    if owner.st_uid == 0:
        return {}
    return {"user": owner.st_uid, "group": owner.st_gid, "extra_groups": []}


def check_live_positions(
    *, notify: bool = False, dry_run: bool = False
) -> list[MonitorResult]:
    """Run per-tenant service during market hours, using the existing monitor cadence.

    No cron, ACL, service configuration or holdings are created by discovery.
    A missing user store is a no-op; a configured unreadable store is explicit.
    """
    if dry_run or not is_market_open(now_shanghai()):
        return []
    project = Path(os.getenv("AQSP_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
    data_root = Path(
        os.getenv("VR_DATA_DIR")
        or os.getenv("AQSP_VIBE_DATA_DIR")
        or str(
            Path(os.getenv("AQSP_RUNTIME_DATA_ROOT", project / "data"))
            / "vibe-research"
        )
    )
    explicit_root = bool(os.getenv("VR_DATA_DIR") or os.getenv("AQSP_VIBE_DATA_DIR"))
    scheduler_run = os.getenv("AQSP_RUN_TASK_ID", "").strip().lower() == "monitor"
    if not data_root.exists():
        if explicit_root or scheduler_run:
            from aqsp.monitor.checker import MonitorResult

            return [
                MonitorResult(
                    name="live_position_risk",
                    triggered=True,
                    severity="warning",
                    message="未找到 Web 持仓目录，未检查持仓风控；请核对 monitor 的 VR_DATA_DIR/AQSP_VIBE_DATA_DIR 与 API 环境",
                )
            ]
        return []
    if not any((data_root / name).is_file() or any((data_root / 'users').glob('*/' + name))
               for name in ('portfolio.json', 'candidate-observations.json')):
        return []
    from aqsp.monitor.checker import MonitorResult

    script = project / "backend" / "live_risk_service.py"
    command = [sys.executable, str(script), "--monitor"]
    if notify:
        command.append("--notify")
    env = {**os.environ, "VR_DATA_DIR": str(data_root)}
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            env=env,
            timeout=LivePositionMonitorConfig().timeout_seconds,
            **_subprocess_identity(data_root),
        )
        summary = json.loads(result.stdout.strip().splitlines()[-1])
        if summary["tenants_checked"] == 0 and summary["failed_tenants"] == 0:
            return []
        incomplete = bool(summary["failed_tenants"] or summary["skipped_checks"])
        return [
            MonitorResult(
                name="live_position_risk",
                triggered=incomplete,
                severity="warning",
                message=(
                    f"持仓风控：已检查 {summary['tenants_checked']} 个数据桶；{summary['open_events']} 条未处理提醒；{summary['skipped_checks']} 项未能检查；{summary['failed_tenants']} 个数据桶失败"
                    + (f"；候选观察已检查 {summary.get('candidate_checks', 0)} 项" if summary.get('candidate_checks') else '')
                ),
                details=summary,
            )
        ]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return [
            MonitorResult(
                name="live_position_risk",
                triggered=True,
                severity="warning",
                message="持仓风控检查失败，未产生完整结论；请检查数据目录权限、API依赖和服务日志",
            )
        ]
