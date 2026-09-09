"""请求级租户识别 —— 解决「同一把共享 API Key 下，不同用户的数据互相串号」问题。

设计：
- 本地/私有模式（未设 VR_API_KEY）：租户固定为 "local"，数据落在原始的
  ~/.vibe-research/ 下，与旧版路径 100% 向后兼容（既有持仓/研报照常可读）。
- 公网/鉴权模式（设了 VR_API_KEY）：每个「不同的 Key」→ 独立的租户目录
  ~/.vibe-research/users/<hash>/，不同 Key 之间天然隔离。
- 同一把 Key 下想再细分多人：前端/调用方带 `X-User-Id` 请求头，按该值再切分
  租户（适合「一个部署、多个人各自数据」的场景）。

用 ContextVar 传递租户，FastAPI 在 async 中间件里 set、sync 路由经 anyio 线程池
仍能继承该上下文（与 request-id 中间件同机制），无需改动每个路由签名。
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar

# 默认 "local"：未设 API Key 的单用户场景（向后兼容旧路径）。
current_tenant: ContextVar[str] = ContextVar("aqsp_tenant", default="local")


def tenant_hash(seed: str) -> str:
    """把任意字符串（Key / X-User-Id）压成稳定的短哈希，用作目录名。"""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def resolve_tenant_id(x_user_id: str, api_key: str) -> str:
    """按优先级解析租户：显式 X-User-Id > API Key 哈希 > local。"""
    xuid = (x_user_id or "").strip()
    if xuid:
        return "u_" + tenant_hash(xuid)
    if api_key:
        return "k_" + tenant_hash(api_key)
    return "local"
