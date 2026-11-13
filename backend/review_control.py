"""复盘控制面配置 —— 纯逻辑层。

本模块只负责配置的模型、校验、差异、文件读写与计划载荷：
- 不安装依赖、不改 config/、不注册定时任务、不发送任何外部通知；
- 不执行任何自动复盘，也不产生实际归档或删除动作；plan_archive / plan_delete
  只返回计划动作载荷，是否执行由控制面人工确认后另行决定。
- 文件读写一律限定在调用方传入的租户目录内；租户标识与报告 id 的合法性由
  resolve_tenant_dir / safe_report_path 收敛，拒绝路径穿越。

配置落盘采用临时文件 + os.replace 原子写；读到损坏 JSON 一律 fail-closed 抛异常，
绝不静默回落到默认值（默认值只在配置文件尚不存在这一明确状态下返回）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

# 配置文件名，固定落在租户目录根下
SETTINGS_FILENAME = "review_settings.json"

# 业务约束常量
MODES = ("auto", "manual")
ALLOWED_CHANNELS = frozenset({"inapp", "wecom", "email", "webhook"})
ALLOWED_TIMEZONE = "Asia/Shanghai"
MAX_PROMPT_LENGTH = 2000
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650

_RUN_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_TENANT_ID_RE = re.compile(r"^(?:local|[A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ReviewControlError(Exception):
    """复盘控制面错误基类。"""


class SettingsValidationError(ReviewControlError):
    """配置业务校验失败；fields 列明所有非法字段名，供 UI 逐项提示。"""

    def __init__(self, fields: list[str], message: str = "复盘控制面配置非法") -> None:
        self.fields = list(fields)
        super().__init__(f"{message}：字段 {', '.join(self.fields)}")


class SettingsCorruptError(ReviewControlError):
    """配置文件损坏或结构内容不合法；fail-closed，绝不回落默认值。"""


class TenantDirectoryError(ReviewControlError):
    """租户目录不存在或不是目录；拒绝隐式创建或静默回落。"""


class PathTraversalError(ReviewControlError):
    """租户标识或报告 id 试图越出允许的目录范围。"""


class PlanError(ReviewControlError):
    """计划动作入参非法。"""


class ReviewSettings(BaseModel):
    """复盘控制面配置模型。

    字段类型由 pydantic 保证，业务约束（时刻、渠道白名单、prompt 上限、
    retention 范围、时区）集中在 validate() 校验，便于一次性列明所有错误字段。
    默认值刻意保守：关闭且手动，绝不因为新增配置就产生自动行为。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: str = "manual"
    run_time: str = "15:30"
    channels: list[str] = []
    prompt: str = ""
    retention_days: int = 90
    timezone: str = ALLOWED_TIMEZONE

    def validate(self) -> None:
        """逐项校验；非法时抛出 SettingsValidationError 并聚合全部字段名。"""
        errors: list[str] = []

        if not isinstance(self.enabled, bool):
            errors.append("enabled")
        if self.mode not in MODES:
            errors.append("mode")
        if not isinstance(self.run_time, str) or not _RUN_TIME_RE.fullmatch(self.run_time):
            errors.append("run_time")
        if not _channels_ok(self.channels):
            errors.append("channels")
        if not isinstance(self.prompt, str) or len(self.prompt) > MAX_PROMPT_LENGTH:
            errors.append("prompt")
        if (
            isinstance(self.retention_days, bool)
            or not isinstance(self.retention_days, int)
            or not MIN_RETENTION_DAYS <= self.retention_days <= MAX_RETENTION_DAYS
        ):
            errors.append("retention_days")
        if self.timezone != ALLOWED_TIMEZONE:
            errors.append("timezone")

        if errors:
            raise SettingsValidationError(errors)


def _channels_ok(channels: object) -> bool:
    """渠道必须是字符串列表，且每项都在白名单内；允许空列表。"""
    if not isinstance(channels, list):
        return False
    return all(isinstance(c, str) and c in ALLOWED_CHANNELS for c in channels)


def default_settings() -> ReviewSettings:
    """返回一份合法且保守的默认配置。"""
    settings = ReviewSettings()
    settings.validate()
    return settings


def diff(old: ReviewSettings, new: ReviewSettings) -> list[dict]:
    """返回逐字段变更清单，顺序按模型字段声明顺序，未变化的字段不出现。"""
    changes: list[dict] = []
    for field in ReviewSettings.model_fields:
        before = getattr(old, field)
        after = getattr(new, field)
        if before != after:
            changes.append({"field": field, "old": before, "new": after})
    return changes


def settings_path(tenant_dir: Path | str) -> Path:
    """配置文件在租户目录内的固定落点。"""
    return Path(tenant_dir) / SETTINGS_FILENAME


def _require_tenant_dir(tenant_dir: Path | str) -> Path:
    """租户目录必须已存在且是目录；否则给明确语义，不隐式创建。"""
    root = Path(tenant_dir)
    if not root.is_dir():
        raise TenantDirectoryError(f"租户目录不存在或不是目录：{root}")
    return root


def load_settings(tenant_dir: Path | str) -> ReviewSettings:
    """读取租户配置。

    - 租户目录不存在：抛 TenantDirectoryError（明确语义，不回落、不创建）。
    - 配置文件不存在：返回合法默认值（尚未配置是明确状态，非损坏回落）。
    - 文件存在但损坏或结构内容非法：抛 SettingsCorruptError（fail-closed）。
    """
    root = _require_tenant_dir(tenant_dir)
    target = settings_path(root)
    if not target.exists():
        return default_settings()
    try:
        raw = target.read_text("utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsCorruptError(f"复盘控制面配置文件不可读或不是合法 JSON：{target}：{exc}") from exc
    if not isinstance(data, dict):
        raise SettingsCorruptError(f"复盘控制面配置顶层必须是对象：{target}")
    try:
        settings = ReviewSettings(**data)
    except ValidationError as exc:
        raise SettingsCorruptError(f"复盘控制面配置结构非法：{target}：{exc}") from exc
    try:
        settings.validate()
    except SettingsValidationError as exc:
        raise SettingsCorruptError(f"复盘控制面配置内容非法（{', '.join(exc.fields)}）：{target}") from exc
    return settings


def save_settings(tenant_dir: Path | str, settings: ReviewSettings) -> Path:
    """原子写配置：先校验，再临时文件 + os.replace；覆盖幂等，不残留临时文件。"""
    settings.validate()
    root = _require_tenant_dir(tenant_dir)
    target = settings_path(root)
    payload = json.dumps(settings.model_dump(), ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{SETTINGS_FILENAME}.", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # ACL 陷阱守卫：临时文件的 owner 是写入者，但默认 umask 让其他用户写不进
        # 租户根目录；640 让 group 可读但只 owner 可写，满足非 root monitor 子进程
        # 在各租户目录下对齐 ACL（test_no_acl_trap.py 守卫）。
        os.chmod(tmp_name, 0o640)
        os.replace(tmp_name, target)
    except BaseException:
        # 任何失败都清理临时文件，避免残留半截文件挡住下次写入
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def resolve_tenant_dir(base_dir: Path | str, tenant_id: str) -> Path:
    """把租户标识映射到数据根目录内的受限目录，拒绝路径穿越。

    local 直接落在数据根；其余租户落在 users/<tid>/。标识只接受
    local 或字母数字、下划线、连字符，且最终解析路径必须仍在数据根内。
    """
    tid = tenant_id if isinstance(tenant_id, str) else ""
    if ".." in tid or not _TENANT_ID_RE.fullmatch(tid):
        raise PathTraversalError(f"非法租户标识（拒绝路径穿越）：{tid!r}")
    base = Path(base_dir).resolve()
    candidate = base if tid == "local" else base / "users" / tid
    resolved = candidate.resolve()
    if resolved != base and base not in resolved.parents:
        raise PathTraversalError(f"租户目录越过数据根目录：{resolved}")
    return resolved


def _validate_report_id(report_id: object) -> str:
    """报告 id 只允许字母数字、下划线、连字符，拒绝分隔符与穿越片段。"""
    if not isinstance(report_id, str) or ".." in report_id or not _REPORT_ID_RE.fullmatch(report_id):
        raise PathTraversalError(f"非法报告 id（拒绝路径穿越）：{report_id!r}")
    return report_id


def safe_report_path(tenant_dir: Path | str, report_id: str) -> Path:
    """把报告 id 解析为租户目录内的路径；越界即抛错，绝不产生目录外落点。"""
    rid = _validate_report_id(report_id)
    root = Path(tenant_dir).resolve()
    target = (root / rid).resolve()
    if target != root and root not in target.parents:
        raise PathTraversalError(f"报告路径越过租户目录：{rid!r}")
    return target


def _build_plan(action: str, report_ids: object, tenant_dir: Path | str | None) -> dict:
    """生成计划动作载荷；只读计算，绝不执行任何文件归档或删除。"""
    if not isinstance(report_ids, (list, tuple)) or not report_ids:
        raise PlanError("report_ids 必须是非空列表")
    root = Path(tenant_dir).resolve() if tenant_dir is not None else None
    items: list[dict] = []
    for raw in report_ids:
        rid = raw if isinstance(raw, str) else str(raw)
        item: dict = {
            "id": rid,
            "action": action,
            "status": "pending_confirmation",
            "require_confirmation": True,
            "target": None,
        }
        try:
            _validate_report_id(raw)
            if root is not None:
                item["target"] = safe_report_path(root, rid).name
            else:
                item["target"] = rid
        except PathTraversalError as exc:
            item["status"] = "rejected"
            item["reason"] = str(exc)
        items.append(item)
    pending = sum(1 for item in items if item["status"] == "pending_confirmation")
    return {
        "action": action,
        "require_confirmation": True,
        "executed": False,
        "note": "仅生成计划动作，未执行任何归档或删除；需人工确认后再执行。",
        "total": len(items),
        "pending": pending,
        "rejected": len(items) - pending,
        "items": items,
    }


def plan_archive(report_ids: list[str], tenant_dir: Path | str | None = None) -> dict:
    """生成归档计划载荷（不执行）。"""
    return _build_plan("archive", report_ids, tenant_dir)


def plan_delete(report_ids: list[str], tenant_dir: Path | str | None = None) -> dict:
    """生成删除计划载荷（不执行）。"""
    return _build_plan("delete", report_ids, tenant_dir)


__all__ = [
    "ALLOWED_CHANNELS",
    "ALLOWED_TIMEZONE",
    "MAX_PROMPT_LENGTH",
    "MAX_RETENTION_DAYS",
    "MIN_RETENTION_DAYS",
    "MODES",
    "SETTINGS_FILENAME",
    "PathTraversalError",
    "PlanError",
    "ReviewControlError",
    "ReviewSettings",
    "SettingsCorruptError",
    "SettingsValidationError",
    "TenantDirectoryError",
    "default_settings",
    "diff",
    "load_settings",
    "plan_archive",
    "plan_delete",
    "resolve_tenant_dir",
    "safe_report_path",
    "save_settings",
    "settings_path",
]
