from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from aqsp.core.time import today_shanghai
from aqsp.data.registry import DataSourceRegistryEntry
from aqsp.data.source_health import read_source_auth, record_source_auth
from aqsp.utils.env import read_project_env_value

WorkloadId = Literal["live_short", "walkforward", "pit"]
SourceRole = Literal["realtime", "observation", "historical"]

_COMPOSITE_SOURCE_IDS = frozenset({"auto", "local_first", "online_first", "multi"})

_WORKLOAD_FIT: dict[str, dict[WorkloadId, str]] = {
    "auto": {
        "live_short": "candidate",
        "walkforward": "avoid",
        "pit": "avoid",
    },
    "local_first": {
        "live_short": "candidate",
        "walkforward": "avoid",
        "pit": "avoid",
    },
    "online_first": {
        "live_short": "primary",
        "walkforward": "fallback_only",
        "pit": "avoid",
    },
    "multi": {
        "live_short": "candidate",
        "walkforward": "candidate",
        "pit": "avoid",
    },
    "eastmoney": {
        "live_short": "primary",
        "walkforward": "fallback_only",
        "pit": "avoid",
    },
    "sina": {
        "live_short": "fallback",
        "walkforward": "avoid",
        "pit": "avoid",
    },
    "tencent": {
        "live_short": "fallback",
        "walkforward": "avoid",
        "pit": "avoid",
    },
    "mootdx": {
        "live_short": "fallback",
        "walkforward": "candidate",
        "pit": "avoid",
    },
    "akshare": {
        "live_short": "candidate",
        "walkforward": "candidate",
        "pit": "avoid",
    },
    "tdx_vipdoc": {
        "live_short": "avoid",
        "walkforward": "primary",
        "pit": "avoid",
    },
    "sqlite_db": {
        "live_short": "avoid",
        "walkforward": "primary",
        "pit": "avoid",
    },
    "baostock": {
        "live_short": "avoid",
        "walkforward": "supplement",
        "pit": "candidate",
    },
    "tushare": {
        "live_short": "avoid",
        "walkforward": "supplement",
        "pit": "primary",
    },
    "efinance": {
        "live_short": "candidate",
        "walkforward": "supplement",
        "pit": "avoid",
    },
    "adata": {
        "live_short": "avoid",
        "walkforward": "candidate",
        "pit": "avoid",
    },
    "qstock": {
        "live_short": "avoid",
        "walkforward": "candidate",
        "pit": "avoid",
    },
    "xtquant_qmt": {
        "live_short": "future_primary",
        "walkforward": "avoid",
        "pit": "avoid",
    },
}

_ALLOWED_WORKLOAD_FITS: dict[WorkloadId, frozenset[str]] = {
    "live_short": frozenset({"primary", "fallback", "candidate", "future_primary"}),
    "walkforward": frozenset(
        {"primary", "fallback", "fallback_only", "candidate", "supplement"}
    ),
    "pit": frozenset({"primary", "candidate", "supplement"}),
}


@dataclass(frozen=True)
class SourceReadinessSnapshot:
    source_id: str
    auth_kind: str
    auth_status: str
    auth_message: str
    auth_checked_at: str
    active_probe: bool
    workload_fit: dict[WorkloadId, str]


def workload_fit_for_source(source_id: str) -> dict[WorkloadId, str]:
    return dict(
        _WORKLOAD_FIT.get(
            source_id,
            {
                "live_short": "unknown",
                "walkforward": "unknown",
                "pit": "unknown",
            },
        )
    )


def source_supports_workload(source_id: str, workload: WorkloadId) -> bool:
    fit = workload_fit_for_source(source_id).get(workload, "unknown")
    return fit in _ALLOWED_WORKLOAD_FITS[workload]


def source_role_for_workload(source_id: str, workload: WorkloadId) -> SourceRole | None:
    """Return the role a source may play for one workload.

    A ``candidate`` feed is useful for research and observation, but it is not
    eligible to form a live_short recommendation until promoted by validation.
    """
    if source_id in _COMPOSITE_SOURCE_IDS:
        return None
    fit = workload_fit_for_source(source_id).get(workload, "unknown")
    if fit not in _ALLOWED_WORKLOAD_FITS[workload]:
        return None
    if workload != "live_short":
        return "historical"
    if fit in {"primary", "fallback", "future_primary"}:
        return "realtime"
    if fit == "candidate":
        return "observation"
    return None


def recommended_sources_for_workload(workload: WorkloadId) -> tuple[str, ...]:
    allowed = _ALLOWED_WORKLOAD_FITS[workload]
    return tuple(
        source_id
        for source_id, fits in _WORKLOAD_FIT.items()
        if fits.get(workload, "unknown") in allowed
    )


def workload_guard_message(source_id: str, workload: WorkloadId) -> str:
    fit = workload_fit_for_source(source_id).get(workload, "unknown")
    if fit in _ALLOWED_WORKLOAD_FITS[workload]:
        return ""
    recommended = " / ".join(recommended_sources_for_workload(workload)[:6])
    return (
        f"数据源 {source_id} 不适合 {workload}（fit={fit}）。 推荐改用: {recommended}"
    )


def inspect_source_readiness(
    entry: DataSourceRegistryEntry,
    *,
    probe_auth: bool = False,
) -> SourceReadinessSnapshot:
    cached = read_source_auth(entry.id)
    auth_kind = _auth_kind_for_entry(entry)

    if auth_kind == "none":
        return SourceReadinessSnapshot(
            source_id=entry.id,
            auth_kind=auth_kind,
            auth_status="not_required",
            auth_message="无需登录或 token。",
            auth_checked_at=cached.checked_at if cached else "",
            active_probe=False,
            workload_fit=workload_fit_for_source(entry.id),
        )

    if entry.id == "baostock":
        if probe_auth:
            state = _probe_baostock()
            return SourceReadinessSnapshot(
                source_id=entry.id,
                auth_kind=auth_kind,
                auth_status=state.status,
                auth_message=state.message,
                auth_checked_at=state.checked_at,
                active_probe=True,
                workload_fit=workload_fit_for_source(entry.id),
            )
        if cached is not None:
            return SourceReadinessSnapshot(
                source_id=entry.id,
                auth_kind=auth_kind,
                auth_status=cached.status,
                auth_message=cached.message,
                auth_checked_at=cached.checked_at,
                active_probe=False,
                workload_fit=workload_fit_for_source(entry.id),
            )
        return SourceReadinessSnapshot(
            source_id=entry.id,
            auth_kind=auth_kind,
            auth_status="not_checked",
            auth_message="需要会话登录；尚未执行真实登录探测。",
            auth_checked_at="",
            active_probe=False,
            workload_fit=workload_fit_for_source(entry.id),
        )

    if entry.id == "tushare":
        token = os.getenv("TUSHARE_TOKEN", "").strip() or read_project_env_value(
            "TUSHARE_TOKEN"
        )
        if probe_auth:
            state = _probe_tushare()
            return SourceReadinessSnapshot(
                source_id=entry.id,
                auth_kind=auth_kind,
                auth_status=state.status,
                auth_message=state.message,
                auth_checked_at=state.checked_at,
                active_probe=True,
                workload_fit=workload_fit_for_source(entry.id),
            )
        if not token:
            return SourceReadinessSnapshot(
                source_id=entry.id,
                auth_kind=auth_kind,
                auth_status="missing_env",
                auth_message="缺少 TUSHARE_TOKEN，PIT 披露日不可用。",
                auth_checked_at="",
                active_probe=False,
                workload_fit=workload_fit_for_source(entry.id),
            )
        if cached is not None and cached.status != "missing_env":
            return SourceReadinessSnapshot(
                source_id=entry.id,
                auth_kind=auth_kind,
                auth_status=cached.status,
                auth_message=cached.message,
                auth_checked_at=cached.checked_at,
                active_probe=False,
                workload_fit=workload_fit_for_source(entry.id),
            )
        return SourceReadinessSnapshot(
            source_id=entry.id,
            auth_kind=auth_kind,
            auth_status="configured",
            auth_message="TUSHARE_TOKEN 已配置；尚未执行远程校验。",
            auth_checked_at="",
            active_probe=False,
            workload_fit=workload_fit_for_source(entry.id),
        )

    return SourceReadinessSnapshot(
        source_id=entry.id,
        auth_kind=auth_kind,
        auth_status="unknown",
        auth_message="未定义鉴权探测逻辑。",
        auth_checked_at=cached.checked_at if cached else "",
        active_probe=False,
        workload_fit=workload_fit_for_source(entry.id),
    )


def _auth_kind_for_entry(entry: DataSourceRegistryEntry) -> str:
    if entry.id == "baostock":
        return "login_session"
    if entry.id == "tushare":
        return "env_token"
    return "none"


def _probe_baostock():
    try:
        import baostock as bs
    except ImportError:
        record_source_auth("baostock", "missing_package", "未安装 baostock 包。")
        return read_source_auth("baostock")

    try:
        result = bs.login()
    except Exception as exc:
        record_source_auth("baostock", "login_failed", f"登录异常: {exc}")
        return read_source_auth("baostock")

    error_code = str(getattr(result, "error_code", "") or "")
    error_msg = str(getattr(result, "error_msg", "") or "").strip()
    if error_code != "0":
        message = error_msg or f"error_code={error_code}"
        record_source_auth("baostock", "login_failed", f"登录失败: {message}")
        return read_source_auth("baostock")

    try:
        bs.logout()
    except Exception:
        pass
    record_source_auth("baostock", "ok", "baostock 登录成功。")
    return read_source_auth("baostock")


def _probe_tushare():
    token = os.getenv("TUSHARE_TOKEN", "").strip() or read_project_env_value(
        "TUSHARE_TOKEN"
    )
    if not token:
        record_source_auth("tushare", "missing_env", "缺少 TUSHARE_TOKEN。")
        return read_source_auth("tushare")

    try:
        import tushare as ts
    except ImportError:
        record_source_auth("tushare", "missing_package", "未安装 tushare 包。")
        return read_source_auth("tushare")

    try:
        pro = ts.pro_api(token)
        target = today_shanghai()
        start = (target - timedelta(days=1)).strftime("%Y%m%d")
        end = target.strftime("%Y%m%d")
        df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
    except Exception as exc:
        record_source_auth("tushare", "auth_failed", f"token 校验失败: {exc}")
        return read_source_auth("tushare")

    rows = 0 if df is None else len(df.index)
    record_source_auth("tushare", "ok", f"trade_cal 校验成功，返回 {rows} 行。")
    return read_source_auth("tushare")
