"""复盘控制面 API —— 供主 agent 统一接线到 app.py。

只暴露三个端点：GET 读配置、PUT 写配置（带逐字段 diff）、POST 生成归档或删除
计划。POST 只返回计划载荷，绝不执行归档或删除，也不发送通知、不注册定时任务。
所有落点都经 review_control.resolve_tenant_dir 限定在数据根或租户目录内。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import review_control as rc
import tenant

router = APIRouter(prefix="/api/aqsp/review-control", tags=["复盘控制面"])


class PlanRequest(BaseModel):
    """归档或删除计划请求；只生成计划，不执行。"""

    action: str = Field(description="archive 或 delete")
    report_ids: list[str] = Field(description="待生成计划的报告 id 列表")


def _data_root() -> Path:
    """数据根目录；与 portfolio / myreports 的 VR_DATA_DIR 约定一致。"""
    raw = os.environ.get("VR_DATA_DIR") or str(Path.home() / ".vibe-research")
    return Path(raw)


def _current_tenant_id() -> str:
    """当前租户；app.py 的中间件已按 X-User-Id 或 API Key 设置 ContextVar。"""
    return tenant.current_tenant.get()


def _tenant_dir() -> Path:
    """解析并校验租户目录；路径穿越一律转 400，绝不落到数据根之外。"""
    try:
        return rc.resolve_tenant_dir(_data_root(), _current_tenant_id())
    except rc.PathTraversalError as exc:
        raise HTTPException(400, f"租户路径越界：{exc}") from exc


def _ensure_tenant_dir() -> Path:
    """写入前在受限路径内确保租户目录存在，只在该目录内 mkdir。"""
    root = _tenant_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.get("", summary="读取当前租户的复盘控制面配置")
def get_settings() -> dict:
    """读配置；目录或文件不存在时明确标注为未落盘的默认配置。"""
    tdir = _tenant_dir()
    try:
        settings = rc.load_settings(tdir)
    except rc.TenantDirectoryError:
        return {
            "data": {
                "settings": rc.default_settings().model_dump(),
                "exists": False,
                "source": "default",
                "note": "租户目录尚未创建，当前展示的是未落盘的默认配置",
            }
        }
    except rc.SettingsCorruptError as exc:
        raise HTTPException(409, f"复盘控制面配置损坏，拒绝回落默认值：{exc}") from exc
    exists = rc.settings_path(tdir).exists()
    return {
        "data": {
            "settings": settings.model_dump(),
            "exists": exists,
            "source": "file" if exists else "default",
        }
    }


@router.put("", summary="写入当前租户的复盘控制面配置并返回逐字段 diff")
def put_settings(body: rc.ReviewSettings) -> dict:
    """写配置；校验非法列明字段，损坏文件拒绝覆盖，成功后返回 diff。"""
    try:
        body.validate()
    except rc.SettingsValidationError as exc:
        raise HTTPException(422, {"detail": str(exc), "fields": exc.fields}) from exc
    tdir = _ensure_tenant_dir()
    try:
        old = rc.load_settings(tdir)
    except rc.SettingsCorruptError as exc:
        raise HTTPException(409, f"现有配置损坏，拒绝写入以免静默覆盖：{exc}") from exc
    changes = rc.diff(old, body)
    rc.save_settings(tdir, body)
    return {"data": {"settings": body.model_dump(), "diff": changes, "saved": True}}


@router.post("", summary="生成归档或删除计划（仅计划，不执行）")
def post_plan(body: PlanRequest) -> dict:
    """只生成计划载荷；不删文件、不通知、不自动执行。"""
    if body.action not in ("archive", "delete"):
        raise HTTPException(422, "action 必须是 archive 或 delete")
    tdir = _tenant_dir()
    builder = rc.plan_archive if body.action == "archive" else rc.plan_delete
    plan = builder(body.report_ids, tenant_dir=tdir)
    return {"data": plan}
