from __future__ import annotations

from dataclasses import dataclass

from aqsp.briefing.generator import Briefing
from aqsp.utils.llm_safe import llm_call_or_fallback


@dataclass(frozen=True)
class EnhancedBriefing:
    """增强后的简报，包含原始简报和可选的优化后的 Markdown"""

    original: Briefing
    optimized_markdown: str | None = None

    def to_markdown(self) -> str:
        if self.optimized_markdown is not None:
            return self.optimized_markdown
        return self.original.to_markdown()


def enhance_briefing(
    briefing: Briefing, enable_llm: bool = False
) -> Briefing | EnhancedBriefing:
    """使用 llm_call_or_fallback 来增强 briefing。
    根据宪法 §3.2/#16，任何异常都会吞掉并降级，绝不冒泡。
    """
    if not enable_llm:
        return briefing
    original_markdown = briefing.to_markdown()
    prompt = f"""请优化以下每日量化选股简报的表述，使其更清晰专业，但不要改变任何核心信息：

{original_markdown}

请返回优化后的完整简报。
    """
    result = llm_call_or_fallback(
        prompt=prompt,
        fallback=original_markdown,
        enable_llm=enable_llm,
        caller="enhance_briefing",
    )
    if result.degraded:
        return briefing
    return EnhancedBriefing(original=briefing, optimized_markdown=result.text)
