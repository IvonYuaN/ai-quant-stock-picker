from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from aqsp.briefing import (
    Briefing,
    BriefingGenerator,
    BriefingSection,
    enhance_briefing,
    send_briefing,
)
from aqsp.core.types import PickResult


def _make_pick(**overrides) -> PickResult:
    defaults = dict(
        symbol="600519",
        name="贵州茅台",
        date="2026-05-27",
        close=1500.0,
        score=8.5,
        rating="A",
        entry_type="next_open",
        ideal_buy=1490.0,
        stop_loss=1450.0,
        take_profit=1600.0,
        position="half",
        strategies=("momentum", "value"),
        reasons=("动量突破MA20", "PE低估"),
        risks=("高位震荡风险",),
    )
    defaults.update(overrides)
    return PickResult(**defaults)


class TestBriefingSection:
    def test_frozen(self):
        section = BriefingSection(title="test", content="body")
        with pytest.raises(AttributeError):
            section.title = "new"


class TestBriefing:
    def test_to_markdown_basic(self):
        briefing = Briefing(
            date="2026-05-27 10:00",
            sections=[BriefingSection(title="sec1", content="body1")],
        )
        md = briefing.to_markdown()
        assert "# AI 量化选股日报 - 2026-05-27 10:00" in md
        assert "## sec1" in md
        assert "body1" in md
        assert "仅供研究" in md

    def test_to_markdown_multiple_sections(self):
        briefing = Briefing(
            date="2026-05-27",
            sections=[
                BriefingSection(title="A", content="a"),
                BriefingSection(title="B", content="b"),
            ],
        )
        md = briefing.to_markdown()
        assert md.index("## A") < md.index("## B")


class TestBriefingGenerator:
    def test_generate_returns_briefing(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        assert isinstance(briefing, Briefing)
        assert briefing.date
        assert len(briefing.sections) == 4

    def test_generate_empty_picks(self):
        gen = BriefingGenerator()
        briefing = gen.generate(picks=[], frames={})
        assert isinstance(briefing, Briefing)
        assert len(briefing.sections) == 4
        md = briefing.to_markdown()
        assert "无候选标的" in md

    def test_regime_section_with_regime(self):
        gen = BriefingGenerator()
        briefing = gen.generate(picks=[], frames={}, regime="stable_bull")
        regime_sec = next(s for s in briefing.sections if s.title == "市场态势")
        assert "稳定上涨" in regime_sec.content

    def test_regime_section_with_circuit_breaker(self):
        gen = BriefingGenerator()
        cb = MagicMock()
        cb.triggered = True
        cb.reason = "组合保护冷却期中"
        briefing = gen.generate(picks=[], frames={}, circuit_breaker_status=cb)
        regime_sec = next(s for s in briefing.sections if s.title == "市场态势")
        assert "组合保护中" in regime_sec.content

    def test_evidence_section_shows_strategies(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        evidence_sec = next(s for s in briefing.sections if s.title == "候选证据链")
        assert "600519" in evidence_sec.content
        assert "momentum" in evidence_sec.content
        assert "动量突破MA20" in evidence_sec.content

    def test_theme_section_categorizes(self):
        gen = BriefingGenerator()
        picks = [
            _make_pick(symbol="000001", reasons=("放量突破", "动量强劲")),
            _make_pick(symbol="000002", reasons=("PE低估",)),
        ]
        briefing = gen.generate(picks=picks, frames={})
        theme_sec = next(s for s in briefing.sections if s.title == "题材热度")
        assert "量价" in theme_sec.content
        assert "动量" in theme_sec.content

    def test_next_day_section_shows_top5(self):
        gen = BriefingGenerator()
        picks = [_make_pick(symbol=f"sym{i:03d}") for i in range(10)]
        briefing = gen.generate(picks=picks, frames={})
        next_sec = next(s for s in briefing.sections if s.title == "明日重点")
        assert next_sec.content.count("**") <= 10

    def test_render_template(self):
        gen = BriefingGenerator()
        picks = [_make_pick()]
        briefing = gen.generate(picks=picks, frames={})
        rendered = gen.render_template(briefing, picks)
        assert "AI 量化选股日报" in rendered
        assert "600519" in rendered


class TestEnhanceBriefing:
    def test_noop_when_disabled(self):
        briefing = Briefing(date="d", sections=[])
        result = enhance_briefing(briefing, enable_llm=False)
        assert result is briefing

    def test_noop_when_env_not_set(self):
        briefing = Briefing(date="d", sections=[])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_LLM_BRIEFING", None)
            result = enhance_briefing(briefing, enable_llm=True)
            assert result is briefing

    def test_noop_when_env_false(self):
        briefing = Briefing(date="d", sections=[])
        with patch.dict(os.environ, {"ENABLE_LLM_BRIEFING": "false"}):
            result = enhance_briefing(briefing, enable_llm=True)
            assert result is briefing


class TestSendBriefing:
    def test_calls_notifier(self):
        briefing = Briefing(
            date="d", sections=[BriefingSection(title="t", content="c")]
        )
        mock_notifier = MagicMock()
        send_briefing(briefing, notifier=mock_notifier)
        mock_notifier.assert_called_once()
        assert "t" in mock_notifier.call_args[0][0]

    @patch("aqsp.notifier.notify_markdown")
    def test_calls_default_notifier(self, mock_notify):
        briefing = Briefing(date="d", sections=[])
        send_briefing(briefing)
        mock_notify.assert_called_once()
