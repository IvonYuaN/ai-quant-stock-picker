from __future__ import annotations

import os

from aqsp.briefing.generator import Briefing


def enhance_briefing(briefing: Briefing, enable_llm: bool = False) -> Briefing:
    if not enable_llm:
        return briefing
    if os.getenv("ENABLE_LLM_BRIEFING", "").lower() not in ("1", "true", "yes"):
        return briefing
    return briefing
