from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from aqsp.core.errors import DataError


@dataclass(frozen=True)
class PitTimestampPolicy:
    artifact_type: str
    timestamp_columns: tuple[str, ...]
    as_of: date | None = None


def validate_point_in_time_frame(
    frame: pd.DataFrame,
    policy: PitTimestampPolicy,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    normalized = frame.copy()
    timestamp_column = _first_present_column(normalized, policy.timestamp_columns)
    if timestamp_column is None:
        expected = ", ".join(policy.timestamp_columns)
        raise DataError(f"{policy.artifact_type} 缺少 point-in-time 时间列: {expected}")
    parsed = pd.to_datetime(normalized[timestamp_column], errors="coerce")
    if parsed.isna().any():
        bad_count = int(parsed.isna().sum())
        raise DataError(
            f"{policy.artifact_type} 存在无效 point-in-time 时间戳: "
            f"{timestamp_column} {bad_count} 行"
        )
    if policy.as_of is not None and (parsed.dt.date > policy.as_of).any():
        raise DataError(
            f"{policy.artifact_type} 含有 as-of 之后才可见的数据: "
            f"{timestamp_column} > {policy.as_of.isoformat()}"
        )
    normalized[timestamp_column] = parsed
    return normalized


def _first_present_column(
    frame: pd.DataFrame,
    candidates: tuple[str, ...],
) -> str | None:
    for column in candidates:
        if column in frame.columns:
            return column
    return None
