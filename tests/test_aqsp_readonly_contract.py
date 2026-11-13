"""main 线专用：/api/aqsp 只读契约（覆盖 include_router 子路由）。

为什么这个文件只存在于 main 线
--------------------------------
TickFlow 分叉线的 app.py 直接注册了工作台写端点（策略定义 / 回测任务 /
因子草稿等，都带 /api/aqsp 前缀），那是分叉线的设计；main 线把 /api/aqsp
收敛成只读快照桥接层（12 个 GET），其它能力经 router 挂到别的前缀。
两条代码线不能共用同一个「全 GET」断言，故本文件不进分叉线。

用途：把「任意新增 /api/aqsp 写端点」变成 CI 红灯，包括通过
include_router 挂进去、旧守卫（只遍历 APIRoute）看不见的那种。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.routing import APIRoute

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("VR_DATA_DIR", "/tmp/aqsp-readonly-contract-data")
os.environ.setdefault("VR_REPORTS_DIR", "/tmp/aqsp-readonly-contract-reports")

import app as app_module  # noqa: E402


def _expanded_routes() -> list[APIRoute]:
    out: list[APIRoute] = []
    for route in app_module.app.routes:
        if isinstance(route, APIRoute):
            out.append(route)
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            out.extend(
                sub for sub in original.routes if isinstance(sub, APIRoute)
            )
    return out


def test_included_routers_are_expanded() -> None:
    """守卫自证：包装对象存在，且展开后确实多出端点。"""
    direct = [r for r in app_module.app.routes if isinstance(r, APIRoute)]
    wrappers = [
        r for r in app_module.app.routes
        if getattr(r, "original_router", None) is not None
    ]
    assert wrappers, "没有 _IncludedRouter；若 FastAPI 改回直接展开，需同步调整守卫"
    assert len(_expanded_routes()) > len(direct)


def test_expanded_aqsp_namespace_is_get_only() -> None:
    aqsp = [r for r in _expanded_routes() if r.path.startswith("/api/aqsp")]
    assert aqsp, "/api/aqsp 一个端点都没有，异常"
    bad = [
        (r.path, sorted(r.methods or ()))
        for r in aqsp
        if set(r.methods or ()) != {"GET"}
    ]
    assert not bad, (
        "/api/aqsp 只读契约被破坏（含 include_router 子路由）: " + repr(bad)
    )
