from __future__ import annotations

from aqsp.briefing.generator import Briefing
from aqsp.utils.llm_safe import llm_call_or_fallback


def enhance_briefing(briefing: Briefing, enable_llm: bool = False) -> Briefing:
    """LLM 增强仅做旁路尝试，绝不改写主链事实内容。"""
    if not enable_llm:
        return briefing

    original_markdown = briefing.to_markdown()
    llm_call_or_fallback(
        prompt=(
            "请阅读以下每日量化选股简报，仅用于生成表达优化建议。"
            "不要改写任何价格、评分、止损、止盈、风险或执行结论。\n\n"
            f"{original_markdown}"
        ),
        fallback="旁路增强未启用",
        enable_llm=enable_llm,
        caller="enhance_briefing",
    )
    return briefing
