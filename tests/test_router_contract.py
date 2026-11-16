"""路由契约守卫：补齐 include_router 的审计盲区。

背景（2026-11-13 实测 prod venv，FastAPI 0.139.2）
------------------------------------------------
``app.include_router()`` 在新版 FastAPI 里不会把子路由展开进 ``app.routes``，
而是放一个 ``_IncludedRouter`` 包装对象（``original_router`` 里才是真正的
APIRoute）。原有守卫只遍历 ``isinstance(route, APIRoute)``，因此**所有由
include_router 挂进去的端点都完全没被审计**：实测 app.routes 有 94 个 APIRoute
+ 4 个 _IncludedRouter，``/api/aqsp`` 只看到 12 个 GET，而工具层里藏着
2 个 POST 和 1 个 PUT/POST。

契约
----
- 直接注册的 aqsp_bridge 只读快照路由由 ``tests/test_aqsp_bridge.py`` 与
  ``backend/tests/test_aqsp_api.py`` 的既有守卫覆盖，本文件不重复断言；
  两条代码线的 app.py 注册方式不同（分叉线直接注册写端点、main 线经
  include_router 挂载），跨线共用的守卫不能假设「直接注册 = 只读」；
- 整个 ``/api/aqsp`` 命名空间**本来就有**设计内的工作台写端点（策略定义、回测
  任务、因子草稿等，全由 include_router 挂载）。旧守卫「全 GET」一直绿只是因为
  它从未看见这些端点，不能把「全 GET」当成全局事实；
- 但 ``/api/aqsp/portfolio`` 与 ``/api/aqsp/chat`` 两条硬红线必须覆盖全量端点；
- ``/api/lab`` 是「研究工具层」，允许 POST 计算 / PUT 配置，端点与方法集合
  必须在本文件逐项显式登记。新增端点若不更新这里，测试立即失败——
  不给「静默加写端点」留口子。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.routing import APIRoute

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("VR_DATA_DIR", "/tmp/aqsp-router-contract-data")
os.environ.setdefault("VR_REPORTS_DIR", "/tmp/aqsp-router-contract-reports")

import app as app_module  # noqa: E402


def _all_api_routes() -> list[APIRoute]:
    """展开 app.routes，包含 _IncludedRouter.original_router 里的子路由。"""
    out: list[APIRoute] = []
    for route in app_module.app.routes:
        if isinstance(route, APIRoute):
            out.append(route)
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            for sub in getattr(original, "routes", []):
                if isinstance(sub, APIRoute):
                    out.append(sub)
    return out


def test_included_routers_are_actually_enumerated() -> None:
    """守卫自证：包装对象存在，且展开后确实多出端点。"""
    direct = [r for r in app_module.app.routes if isinstance(r, APIRoute)]
    wrappers = [
        r for r in app_module.app.routes
        if getattr(r, "original_router", None) is not None
    ]
    assert wrappers, "没有 _IncludedRouter，守卫口径可能已经失效（FastAPI 行为变化）"
    assert len(_all_api_routes()) > len(direct), (
        "展开后没有多出端点；若 FastAPI 改回直接展开，需同步调整本守卫"
    )


# 直接注册的 aqsp 只读快照路由由 tests/test_aqsp_bridge.py 的既有守卫覆盖。
# 这里刻意不重复「全 GET」断言：分叉线 app.py 直接注册了工作台写端点，
# main 线则经 include_router 挂载，跨线共用守卫不能假设「直接注册 = 只读」。


def test_no_portfolio_or_chat_write_routes_anywhere() -> None:
    """把原守卫的两条硬红线扩展到 include_router 展开后的全量端点。"""
    offenders = [
        (r.path, sorted(r.methods or ()))
        for r in _all_api_routes()
        if r.path.startswith(("/api/aqsp/portfolio", "/api/aqsp/chat"))
    ]
    assert not offenders, f"aqsp 下不得出现持仓 / 对话写端点: {offenders}"


def test_lab_tool_routes_match_declared_contract() -> None:
    """工具层允许非 GET，但端点与方法集合必须逐项显式登记。"""
    expected = {
        ("/api/lab/report-stats/compute", "POST"),
        ("/api/lab/report-stats/contract", "GET"),
        ("/api/lab/rule-combinators/contract", "GET"),
        ("/api/lab/rule-combinators/evaluate", "POST"),
        ("/api/lab/review-control", "GET"),
        ("/api/lab/review-control", "PUT"),
        ("/api/lab/review-control", "POST"),
    }
    actual = {
        (r.path, method)
        for r in _all_api_routes()
        if r.path.startswith("/api/lab")
        for method in (r.methods or ())
    }
    assert actual == expected, (
        f"工具层路由契约变化，需同步更新守卫: "
        f"extra={sorted(actual - expected)}, missing={sorted(expected - actual)}"
    )
