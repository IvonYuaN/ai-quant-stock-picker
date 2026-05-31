from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataSourceRegistryEntry:
    id: str
    tier: str
    role: str
    runtime_ready: bool
    requires_account: bool
    requires_local_data: bool
    supports_daily: bool
    supports_intraday: bool
    supports_realtime: bool
    default_for: tuple[str, ...]
    failure_mode: str
    setup: str


DATA_SOURCE_REGISTRY: tuple[DataSourceRegistryEntry, ...] = (
    DataSourceRegistryEntry(
        id="tdx_vipdoc",
        tier="local_offline",
        role="primary_history_and_paper",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=True,
        supports_daily=True,
        supports_intraday=False,
        supports_realtime=False,
        default_for=("daily_close", "paper", "walkforward"),
        failure_mode="本地 vipdoc 缺失或未更新；不会卡在线接口。",
        setup="运行 scripts/download_tdx_vipdoc.py，或设置 AQSP_TDX_VIPDOC_PATH。",
    ),
    DataSourceRegistryEntry(
        id="eastmoney",
        tier="free_online",
        role="primary_online_fallback",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=True,
        default_for=("realtime", "intraday", "fallback"),
        failure_mode="网页接口字段变化、限频或网络失败。",
        setup="无需账号；失败时切 sina/tencent/tdx_vipdoc。",
    ),
    DataSourceRegistryEntry(
        id="sina",
        tier="free_online",
        role="light_realtime_fallback",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=True,
        default_for=("realtime_fallback",),
        failure_mode="需要 Referer；历史数据稳定性一般。",
        setup="无需账号。",
    ),
    DataSourceRegistryEntry(
        id="tencent",
        tier="free_online",
        role="light_realtime_fallback",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=True,
        default_for=("realtime_fallback",),
        failure_mode="非正式接口字段变更；日线只允许不复权。",
        setup="无需账号。",
    ),
    DataSourceRegistryEntry(
        id="akshare",
        tier="free_package",
        role="broad_research_data",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=True,
        default_for=("research", "sector", "fund_flow"),
        failure_mode="包装的网页接口变更或依赖未安装。",
        setup="pip install -e '.[data]'。",
    ),
    DataSourceRegistryEntry(
        id="mootdx",
        tier="free_package",
        role="tdx_server_fallback",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=True,
        default_for=("tdx_online_fallback",),
        failure_mode="通达信服务器不可用或 mootdx 未安装。",
        setup="安装 mootdx；只作为备份源。",
    ),
    DataSourceRegistryEntry(
        id="baostock",
        tier="free_login",
        role="historical_and_financial_candidate",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=False,
        default_for=("historical_supplement", "pit_financial_candidate"),
        failure_mode="登录会话或接口限速；盘中能力弱。",
        setup="安装 baostock；后续只用于历史/财务补全。",
    ),
    DataSourceRegistryEntry(
        id="sqlite_db",
        tier="local_cache",
        role="local_structured_cache",
        runtime_ready=True,
        requires_account=False,
        requires_local_data=True,
        supports_daily=True,
        supports_intraday=False,
        supports_realtime=False,
        default_for=("walkforward_cache",),
        failure_mode="缓存为空或过期会返回空数据。",
        setup="先用 collect_stock_data.py 或 daily run 填充缓存。",
    ),
    DataSourceRegistryEntry(
        id="tushare",
        tier="token_api",
        role="pit_calendar_fundamental_future",
        runtime_ready=False,
        requires_account=True,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=False,
        supports_realtime=False,
        default_for=("future_pit_calendar", "future_fundamental"),
        failure_mode="需要 token/积分；不能把 token 写入仓库。",
        setup="设置 TUSHARE_TOKEN；先接交易日历、成分、财报披露日。",
    ),
    DataSourceRegistryEntry(
        id="adata",
        tier="free_package",
        role="local_warehouse_candidate",
        runtime_ready=False,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=False,
        supports_realtime=False,
        default_for=("future_local_warehouse",),
        failure_mode="未接 adapter；需验证不复权/PIT 口径。",
        setup="clone 1nchaos/adata 后按 DataSource 契约接入。",
    ),
    DataSourceRegistryEntry(
        id="efinance",
        tier="free_package",
        role="eastmoney_field_candidate",
        runtime_ready=False,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=False,
        supports_realtime=True,
        default_for=("future_fund_flow", "future_eastmoney_adapter"),
        failure_mode="未接 adapter；字段变化需 fixture 锁定。",
        setup="优先吸收资金流/盘口字段，不直接替代主源。",
    ),
    DataSourceRegistryEntry(
        id="qstock",
        tier="free_package",
        role="research_api_candidate",
        runtime_ready=False,
        requires_account=False,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=False,
        supports_realtime=True,
        default_for=("future_research_fields",),
        failure_mode="未接 adapter；只吸收公开源码可见接口。",
        setup="按字段一致性抽样后再接入。",
    ),
    DataSourceRegistryEntry(
        id="xtquant_qmt",
        tier="local_terminal",
        role="future_realtime_execution_state",
        runtime_ready=False,
        requires_account=True,
        requires_local_data=False,
        supports_daily=True,
        supports_intraday=True,
        supports_realtime=True,
        default_for=("future_executability",),
        failure_mode="依赖券商终端登录；本项目禁止下单。",
        setup="只读行情/停牌/涨跌停/盘口状态，不导入交易接口。",
    ),
)


def list_registry_entries() -> tuple[DataSourceRegistryEntry, ...]:
    return DATA_SOURCE_REGISTRY


def local_data_status(entry: DataSourceRegistryEntry) -> str:
    if entry.id == "tdx_vipdoc":
        candidates = [
            Path("private_data/tdx/vipdoc"),
            Path("private_data/tdx"),
        ]
        if any(path.exists() for path in candidates):
            return "present"
        return "missing"
    if entry.id == "sqlite_db":
        candidates = [
            Path("A股量化分析数据/astocks_qfq.db"),
            Path("data/aqsp_cache.sqlite"),
        ]
        env_path = os.getenv("AQSP_SQLITE_DB_PATH", "").strip()
        if env_path:
            candidates.insert(0, Path(env_path))
        return "present" if any(path.exists() for path in candidates) else "missing"
    return "not_required" if not entry.requires_local_data else "unknown"
