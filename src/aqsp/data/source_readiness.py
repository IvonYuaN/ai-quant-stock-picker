from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from aqsp.core.time import today_shanghai
from aqsp.data.registry import DataSourceRegistryEntry
from aqsp.data.source_health import read_source_auth, record_source_auth

WorkloadId = Literal["live_short", "walkforward", "pit"]

_WORKLOAD_FIT: dict[str, dict[WorkloadId, str]] = {
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
        token = os.getenv("TUSHARE_TOKEN", "").strip()
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
    token = os.getenv("TUSHARE_TOKEN", "").strip()
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
