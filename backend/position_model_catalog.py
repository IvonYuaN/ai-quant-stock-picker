"""Bounded provider model discovery; no automatic upgrades or key persistence."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import atomic_write_text

if TYPE_CHECKING:
    from position_discussion import ModelEndpoint


@dataclass(frozen=True)
class CatalogLimits:
    """Operational I/O budgets, unrelated to trading thresholds."""
    timeout_seconds: float = 6.0
    cache_seconds: float = 3600.0
    refresh_floor_seconds: float = 30.0
    max_response_bytes: int = 100_000
    max_models: int = 200


async def read_catalog(
    endpoint: ModelEndpoint, cache_root: Path, *, refresh: bool = False,
    limits: CatalogLimits | None = None,
) -> dict[str, Any]:
    """Read a cached provider directory; listing never proves text capability."""
    limits = limits or CatalogLimits()
    current = now_shanghai()
    identity = hashlib.sha256(endpoint.url.encode()).hexdigest()
    # Different credentials may have different catalogs. Only a digest is used
    # in the cache name; neither credential nor upstream response is persisted.
    cache_key = hashlib.sha256((endpoint.url + endpoint.key).encode()).hexdigest()
    path = cache_root / "model_catalog" / f"{cache_key}.json"
    cached = None
    try:
        candidate = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        age = current.timestamp() - float(candidate["timestamp"])
        if 0 <= age < (limits.refresh_floor_seconds if refresh or not candidate.get("directory_ok") else limits.cache_seconds):
            cached = candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if cached is None:
        models, issue, directory_ok, complete = [], "", False, False
        try:
            async def fetch() -> dict[str, Any]:
                url = endpoint.url.rsplit("/", 1)[0] + "/models" if endpoint.anthropic else endpoint.url.removesuffix("/chat/completions") + "/models"
                headers = ({"x-api-key": endpoint.key, "anthropic-version": "2023-06-01"}
                           if endpoint.anthropic else {"Authorization": f"Bearer {endpoint.key}"})
                async with httpx.AsyncClient(timeout=limits.timeout_seconds, follow_redirects=False) as client:
                    async with client.stream("GET", url, headers=headers) as response:
                        if response.status_code != 200:
                            raise ValueError(f"提供商模型目录 HTTP {response.status_code}，仍可按当前配置尝试分析")
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > limits.max_response_bytes:
                                raise ValueError("模型目录超出读取上限")
                            chunks.append(chunk)
                data = json.loads(b"".join(chunks))
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    raise ValueError("提供商未返回可识别的模型目录")
                return data
            data = await asyncio.wait_for(fetch(), timeout=limits.timeout_seconds)
            all_models = sorted({row["id"] for row in data["data"] if isinstance(row, dict)
                                 and isinstance(row.get("id"), str) and 0 < len(row["id"]) <= 200
                                 and endpoint.key not in row["id"]})
            models = all_models[:limits.max_models]
            complete = len(all_models) <= limits.max_models and not data.get("has_more") and not data.get("next")
            directory_ok = True
        except (httpx.HTTPError, TimeoutError):
            issue = "暂时无法读取模型目录，仍可按当前配置尝试分析"
        except ValueError as exc:
            # All ValueErrors above are fixed safe messages or JSON errors.
            issue = str(exc) if not isinstance(exc, json.JSONDecodeError) else "模型目录响应无法解析"
        cached = {"timestamp": current.timestamp(), "checked_at": current.isoformat(),
                  "models": models, "directory_ok": directory_ok, "directory_complete": complete, "issue": issue}
        try:
            await asyncio.to_thread(atomic_write_text, path, json.dumps(cached, ensure_ascii=False))
        except OSError:
            pass  # Catalog discovery must not make the core position view fail.
    complete = bool(cached.get("directory_complete", False))
    listed = True if endpoint.model in cached["models"] else False if cached["directory_ok"] and complete else None
    return {"catalog_id": identity, "current_model": endpoint.model, "directory_complete": complete,
            "models": cached["models"], "checked_at": cached["checked_at"],
            "listed": listed,
            "issue": cached["issue"],
            "notice": "目录只证明提供商列出该型号，文本能力、费用和分析结果仍须验证；不会自动换型号。"}
