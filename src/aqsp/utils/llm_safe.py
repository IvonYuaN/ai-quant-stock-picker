"""LLM 可降级调用契约 — CONSTITUTION §3.2 / §1.3 #16 落地。

支持多种免费/低价模型 API：
- 智谱GLM-4.7-Flash (推荐首选)
- 通义千问 qwen-turbo (新用户500万tokens免费180天)
- 硅基流动 (注册送14元，部分模型永久免费)
- DeepSeek (不免费，2元/百万tokens，效果最好)
- OpenAI 兼容 / Anthropic / 自定义端点

红线复核：
- 核心路径不硬依赖 LLM（#4）：所有 SDK 都是惰性 import
- LLM 挂掉降级而非崩（#4/#16）：任何异常都吞掉走 fallback
- 不动 thresholds、不接券商、不重构既有策略、不新增 Agent
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# 默认护栏
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_COST_CAP_USD = 0.05
_LLM_CALLS_LOG = Path("data/llm_calls.jsonl")
_SILICONFLOW_MODELS_CACHE = Path("data/siliconflow_models.json")
_SILICONFLOW_FREE_MODELS = {
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen2-1.5B-Instruct",
    "THUDM/glm-4-9b-chat",
    "internlm/internlm2_5-7b-chat",
    "mistralai/Mistral-7B-Instruct-v0.2",
}


@dataclass(frozen=True)
class LlmResult:
    """LLM 调用结果。degraded=True 表示走了 fallback（未实际用到 LLM 输出）。"""

    text: str
    degraded: bool
    reason: str = ""
    model: str = ""
    latency_s: float = 0.0
    est_cost_usd: float = 0.0


@dataclass(frozen=True)
class SiliconFlowModelChoice:
    model: str
    source: str
    free_only: bool


def _append_log(record: dict) -> None:
    """记录到 data/llm_calls.jsonl（§3.2 要求的可观测性）。
    记录本身失败也不能冒泡——监控不能反过来弄崩主链路。
    """
    try:
        _LLM_CALLS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LLM_CALLS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def get_siliconflow_free_models() -> tuple[str, ...]:
    return tuple(sorted(_SILICONFLOW_FREE_MODELS))


def _save_siliconflow_models(payload: dict[str, Any]) -> None:
    try:
        _SILICONFLOW_MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SILICONFLOW_MODELS_CACHE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_siliconflow_models_cache() -> dict[str, Any]:
    if not _SILICONFLOW_MODELS_CACHE.exists():
        return {}
    try:
        return json.loads(_SILICONFLOW_MODELS_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def refresh_siliconflow_models(
    *, timeout_s: float = _DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    import requests

    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise ValueError("SILICONFLOW_API_KEY 未配置")

    response = requests.get(
        "https://api.siliconflow.cn/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout_s,
    )
    response.raise_for_status()
    payload = response.json()
    _save_siliconflow_models(payload)
    return payload


def choose_siliconflow_model(
    preferred_model: str | None = None,
    *,
    free_only: bool | None = None,
) -> SiliconFlowModelChoice:
    model = (preferred_model or os.getenv("LLM_MODEL", "")).strip()
    free_only = (
        _env_flag("SILICONFLOW_FREE_ONLY", "true") if free_only is None else free_only
    )

    if model:
        if free_only and model not in _SILICONFLOW_FREE_MODELS:
            raise ValueError(f"SiliconFlow free-only 模式拒绝收费或未知模型: {model}")
        if model.startswith("Pro/"):
            raise ValueError(f"SiliconFlow free-only 模式拒绝 Pro 模型: {model}")
        return SiliconFlowModelChoice(model=model, source="env", free_only=free_only)

    cache = _load_siliconflow_models_cache()
    cached_names = {
        item.get("id", "")
        for item in cache.get("data", [])
        if isinstance(item, dict) and item.get("id")
    }

    candidates = [
        "Qwen/Qwen2.5-7B-Instruct",
        "THUDM/glm-4-9b-chat",
        "internlm/internlm2_5-7b-chat",
        "mistralai/Mistral-7B-Instruct-v0.2",
        "Qwen/Qwen2-7B-Instruct",
        "Qwen/Qwen2-1.5B-Instruct",
    ]
    for candidate in candidates:
        if not free_only:
            return SiliconFlowModelChoice(
                model=candidate, source="default_priority", free_only=free_only
            )
        if candidate in _SILICONFLOW_FREE_MODELS and (
            not cached_names or candidate in cached_names
        ):
            source = "cache_priority" if cached_names else "built_in_priority"
            return SiliconFlowModelChoice(
                model=candidate,
                source=source,
                free_only=free_only,
            )

    raise ValueError("未找到可用的 SiliconFlow 免费模型")


def llm_call_or_fallback(
    prompt: str,
    fallback: str,
    *,
    enable_llm: bool = False,
    model: Optional[str] = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    cost_cap_usd: float = _DEFAULT_COST_CAP_USD,
    caller: str = "unknown",
    _client_factory: Optional[Callable[[], object]] = None,
) -> LlmResult:
    """可降级 LLM 调用。任何失败都返回 fallback，绝不抛异常给上游。

    环境变量配置优先级：
    1. LLM_PROVIDER (deepseek/qwen/glm/siliconflow/openai/anthropic/custom)
    2. 对应提供商的 API_KEY
    3. LLM_BASE_URL (可选，自定义端点)
    4. LLM_MODEL (可选，指定模型名)
    """
    started = time.monotonic()
    base_record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "model": model or "",
        "prompt_chars": len(prompt),
    }

    if not enable_llm:
        return _degrade(base_record, started, "enable_llm=False", fallback, model)
    if os.getenv("ENABLE_LLM_BRIEFING", "").lower() not in ("1", "true", "yes"):
        return _degrade(
            base_record, started, "ENABLE_LLM_BRIEFING 未开", fallback, model
        )

    try:
        client = _client_factory() if _client_factory else _make_client()
        text, est_cost = _invoke(client, prompt, model, timeout_s)
        if est_cost > cost_cap_usd:
            return _degrade(
                base_record,
                started,
                f"预估成本 {est_cost:.4f} > cap {cost_cap_usd}",
                fallback,
                model,
            )
        latency = time.monotonic() - started
        _append_log(
            {
                **base_record,
                "degraded": False,
                "latency_s": round(latency, 3),
                "est_cost_usd": round(est_cost, 5),
            }
        )
        return LlmResult(
            text=text,
            degraded=False,
            model=model or "",
            latency_s=latency,
            est_cost_usd=est_cost,
        )
    except Exception as exc:
        return _degrade(
            base_record, started, f"{type(exc).__name__}: {exc}", fallback, model
        )


def _degrade(
    base_record: dict, started: float, reason: str, fallback: str, model
) -> LlmResult:
    latency = time.monotonic() - started
    _append_log(
        {
            **base_record,
            "degraded": True,
            "reason": reason,
            "latency_s": round(latency, 3),
        }
    )
    return LlmResult(
        text=fallback,
        degraded=True,
        reason=reason,
        model=model or "",
        latency_s=latency,
    )


def _make_client() -> object:
    """根据环境变量创建合适的客户端。

    支持的 provider:
    - glm (默认，智谱GLM-4.7-Flash)
    - qwen (阿里云通义千问，新用户500万tokens)
    - siliconflow (硅基流动，注册送14元)
    - deepseek (不免费，2元/百万tokens)
    - openai / anthropic / custom
    """
    provider = os.getenv("LLM_PROVIDER", "glm").lower()

    if provider in ["deepseek", "qwen", "glm", "siliconflow", "custom", "openai"]:
        # 使用 OpenAI SDK 兼容的所有服务
        import openai

        api_key = os.getenv(f"{provider.upper()}_API_KEY") or os.getenv(
            "OPENAI_API_KEY"
        )
        base_url = os.getenv("LLM_BASE_URL")

        if provider == "deepseek" and not base_url:
            base_url = "https://api.deepseek.com/v1"
        elif provider == "qwen" and not base_url:
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        elif provider == "glm" and not base_url:
            base_url = "https://open.bigmodel.cn/api/paas/v4/"
        elif provider == "siliconflow" and not base_url:
            base_url = "https://api.siliconflow.cn/v1"
        elif provider == "custom" and not base_url:
            base_url = os.getenv("CUSTOM_BASE_URL")

        if not api_key:
            api_key = os.getenv("API_KEY", "dummy")

        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
    elif provider == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    raise ValueError(f"不支持的 LLM provider: {provider}")


def _invoke(
    client: object, prompt: str, model: Optional[str], timeout_s: float
) -> tuple[str, float]:
    """实际调用。返回 (文本, 预估成本 USD)。"""
    provider = os.getenv("LLM_PROVIDER", "glm").lower()

    if not model:
        if provider == "siliconflow":
            model = choose_siliconflow_model().model
        else:
            model = {
                "glm": "glm-4.7-flash",
                "qwen": "qwen-turbo",
                "deepseek": "deepseek-chat",
                "openai": "gpt-4o-mini",
                "anthropic": "claude-3-5-haiku-20241022",
                "custom": os.getenv("LLM_MODEL", "gpt-4o-mini"),
            }.get(provider, "glm-4.7-flash")

    if provider == "anthropic":
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_s,
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        in_tok = len(prompt) / 4
        out_tok = len(text) / 4
        est_cost = (in_tok * 0.8 + out_tok * 4.0) / 1_000_000
    else:
        # OpenAI 兼容的调用方式
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            timeout=timeout_s,
        )
        text = resp.choices[0].message.content or ""
        in_tok = len(prompt) / 4
        out_tok = len(text) / 4
        est_cost = 0.0

    return text, est_cost
