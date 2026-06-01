"""LLM 可降级调用契约 — CONSTITUTION §3.2 / §1.3 #16 落地。

声明已写但代码缺失：宪法 §3.2 要求所有 LLM 调用走
aqsp.utils.llm_safe.llm_call_or_fallback，带 timeout / cost_cap / fallback 模板，
且 LLM 异常永远不能冒到 cli return code（#16）。

红线复核：
- 核心路径不硬依赖 LLM（#4）：anthropic/openai 全部函数内惰性 import，
  顶层不 import，配合 _constitution_check._check_no_top_level_llm_import。
- LLM 挂掉降级而非崩（#4/#16）：任何异常都吞掉走 fallback，绝不向上抛。
- 不动 thresholds、不接券商、不重构既有策略、不新增 Agent。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# 默认护栏
_DEFAULT_TIMEOUT_S = 20.0
_DEFAULT_COST_CAP_USD = 0.05
_LLM_CALLS_LOG = Path("data/llm_calls.jsonl")


@dataclass(frozen=True)
class LlmResult:
    """LLM 调用结果。degraded=True 表示走了 fallback（未实际用到 LLM 输出）。"""

    text: str
    degraded: bool
    reason: str = ""
    model: str = ""
    latency_s: float = 0.0
    est_cost_usd: float = 0.0


def _append_log(record: dict) -> None:
    """记录到 data/llm_calls.jsonl（§3.2 要求的可观测性）。
    记录本身失败也不能冒泡——监控不能反过来弄崩主链路。
    """
    try:
        _LLM_CALLS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LLM_CALLS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 静默：日志写不进去不影响降级语义
        pass


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

    降级触发条件（任一）：
    - enable_llm=False（默认）
    - ENABLE_LLM_BRIEFING 环境变量未开
    - 缺 API key
    - SDK 未安装 / import 失败
    - 调用超时或抛错
    - 预估成本超过 cost_cap_usd

    参数 _client_factory 仅用于测试注入假 client，生产留空。
    """
    started = time.monotonic()
    base_record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "model": model or "",
        "prompt_chars": len(prompt),
    }

    # 门 1：显式开关
    if not enable_llm:
        return _degrade(base_record, started, "enable_llm=False", fallback, model)
    if os.getenv("ENABLE_LLM_BRIEFING", "").lower() not in ("1", "true", "yes"):
        return _degrade(
            base_record, started, "ENABLE_LLM_BRIEFING 未开", fallback, model
        )

    # 门 2：API key
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not _client_factory and not api_key:
        return _degrade(base_record, started, "缺 API key", fallback, model)

    # 门 3：惰性 import + 调用，全程兜底
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
    except Exception as exc:  # noqa: BLE001 — 故意全吞，#16 红线
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
    """惰性 import（#4：顶层不 import LLM SDK）。"""
    import anthropic  # noqa: PLC0415 — 必须函数内 import

    return anthropic.Anthropic()


def _invoke(
    client: object, prompt: str, model: Optional[str], timeout_s: float
) -> tuple[str, float]:
    """实际调用。返回 (文本, 预估成本 USD)。

    成本估算用粗略 token≈chars/4 + 公开价目，仅用于 cost_cap 拦截，不求精确。
    具体 client 接口由仓主按所选 SDK 落地——本草稿给的是 anthropic messages 形态。
    """
    mdl = model or "claude-3-5-haiku-20241022"
    resp = client.messages.create(  # type: ignore[attr-defined]
        model=mdl,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout_s,
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    in_tok = len(prompt) / 4
    out_tok = len(text) / 4
    # haiku 粗略价（USD/Mtok）：in 0.8 / out 4.0
    est_cost = (in_tok * 0.8 + out_tok * 4.0) / 1_000_000
    return text, est_cost
