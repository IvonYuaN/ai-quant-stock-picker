"""运行时数据根目录解析（读/写 pit_cache 与运行时数据的单一来源）。

解决 09-26 全局逐行排查发现的「读/写 fallback 不同源」类隐患：此前 13 处
``os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()`` 在未设 env 时回落
``/tmp``，而自动链路（``release_task_entrypoint.sh`` 恒设 ``AQSP_RUNTIME_DATA_ROOT``）
走 ``/opt/aqsp/data``，导致裸 CLI / 未设 env 入口「写 /tmp 读 repo 根」的静默失效。

收敛到本 helper：设了环境变量（绝对路径）⇒ 用它；否则回落「当前 repo / release 根」
（= ``__file__`` 上溯 4 层），**绝不回落 ``/tmp``**。

与既有两处同源实现保持一致：
  - ``scripts/daily_pipeline._runtime_data_root``（回落 project_root）
  - ``briefing/closing_review._factor_ic_runtime_root``（回落 repo 根）
二者在 release 布局下均等价于本 helper 的回落基准。
"""
from __future__ import annotations

import os
from pathlib import Path

# src/aqsp/core/runtime.py → parents[0]=core, [1]=aqsp, [2]=src, [3]=repo/release 根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def runtime_data_root(env_name: str = "AQSP_RUNTIME_DATA_ROOT") -> Path:
    """解析运行时数据根目录。

    1. 环境变量 ``env_name``（默认 ``AQSP_RUNTIME_DATA_ROOT``）存在且为绝对路径 ⇒ 用它；
    2. 否则回落 repo / release 根（= ``Path(__file__).resolve().parents[3]``）。

    prod 自动链路 entrypoint 恒设 ``AQSP_RUNTIME_DATA_ROOT=/opt/aqsp/data``，走 env 分支；
    此回落只约束裸 CLI / 未设 env 的非 entrypoint 入口，使其与自动链路同源。
    """
    raw = str(os.environ.get(env_name, "") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path.resolve(strict=False)
    return _PROJECT_ROOT
