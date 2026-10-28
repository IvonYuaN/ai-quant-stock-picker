"""Task-scoped runtime facts; publication time never proves a job succeeded."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
import re
from typing import Any

from aqsp.core.time import to_shanghai

FAILED_RESEARCH_RUNS = frozenset({'failed', 'error', 'partial_failed', 'unknown'})


@dataclass(frozen=True)
class ResearchRun:
    status: str = ''
    updated_at: str = ''
    detail: str = ''

    def to_dict(self) -> dict[str, str]:
        """Serialize only public runtime fields."""
        return asdict(self)


def source_time(value: Any, *, current: datetime) -> datetime | None:
    """Return an aware, non-future source time, never a read/write timestamp."""
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > current:
            return None
        return to_shanghai(stamp)
    except ValueError:
        return None


def intraday_run(raw: Mapping[str, Any], selected_date: str, *, current: datetime) -> ResearchRun:
    """Read a same-session intraday event without borrowing another task's state."""
    if not raw:
        return ResearchRun('unknown', detail='未取得盘中运行记录，行情与研究条件仍需独立核验')
    stamp = source_time(raw.get('updated_at'), current=current)
    task = raw.get('task_id')
    if stamp is None or not isinstance(task, str) or not (task == 'intraday' or task.startswith('intraday-')):
        return ResearchRun('unknown', detail='盘中运行记录缺少可核验时点或任务身份')
    if stamp.date().isoformat() != selected_date:
        return ResearchRun('unknown', detail='盘中运行记录不属于本研究日，未沿用其成败结论')
    if task != 'intraday':
        match = re.fullmatch(r'intraday-(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})', task)
        if not match or match[1] != selected_date:
            return ResearchRun('unknown', detail='盘中任务身份与研究日期不一致')
        try:
            started = datetime.fromisoformat(f'{match[1]}T{match[2]}:{match[3]}:00+08:00')
        except ValueError:
            return ResearchRun('unknown', detail='盘中任务身份的时点无效')
        if started > stamp:
            return ResearchRun('unknown', detail='盘中运行记录早于其任务身份时点')
    status = str(raw.get('status') or '')
    if status not in {'running', 'failed', 'error', 'partial_failed', 'completed', 'success', 'ok', 'skipped'}:
        return ResearchRun('unknown', stamp.isoformat(), '盘中运行状态无法识别')
    return ResearchRun(status, stamp.isoformat(), str(raw.get('reason') or raw.get('detail') or '')[:500])
