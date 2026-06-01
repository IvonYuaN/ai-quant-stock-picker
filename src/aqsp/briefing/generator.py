from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from aqsp.core.time import now_shanghai
from aqsp.core.types import PickResult
from aqsp.research.summary import ResearchSummary
from aqsp.ratings import is_tradable_rating

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_THEMES = {
    "volume": ["量", "成交", "换手", "放量"],
    "momentum": ["动量", "趋势", "突破", "均线", "金叉"],
    "value": ["低估", "PE", "PB", "股息", "估值"],
    "quality": ["盈利", "ROE", "毛利率", "利润"],
    "technical": ["MACD", "KDJ", "RSI", "技术"],
}

_REGIME_DESCRIPTIONS = {
    "stable_bull": "稳定上涨：低波动 + 正趋势",
    "volatile_bull": "波动上涨：高波动 + 正趋势",
    "stable_bear": "稳定下跌：低波动 + 负趋势",
    "volatile_bear": "波动下跌：高波动 + 负趋势",
    "stable_sideways": "稳定盘整：低波动 + 无趋势",
    "volatile_sideways": "波动盘整：高波动 + 无趋势",
}


@dataclass(frozen=True)
class BriefingSection:
    title: str
    content: str


@dataclass(frozen=True)
class Briefing:
    date: str
    sections: list[BriefingSection]

    def to_markdown(self) -> str:
        lines = [f"# AI 量化选股日报 - {self.date}", ""]
        for section in self.sections:
            lines.append(f"## {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")
        lines.append("> 仅供研究，不构成投资建议。")
        return "\n".join(lines)


class BriefingGenerator:
    def generate(
        self,
        picks: list[PickResult],
        frames: dict[str, pd.DataFrame],
        regime: str = "",
        validation: object | None = None,
        circuit_breaker_status: object | None = None,
        source_status: dict[str, str | bool] | None = None,
        research_summary: ResearchSummary | None = None,
    ) -> Briefing:
        date_str = now_shanghai().strftime("%Y-%m-%d %H:%M")
        sections = [
            self._build_regime_section(regime, circuit_breaker_status),
            self._build_source_section(source_status),
            self._build_research_section(research_summary),
            self._build_evidence_section(picks),
            self._build_theme_section(picks),
            self._build_next_day_section(picks, frames),
        ]
        return Briefing(date=date_str, sections=sections)

    def _build_regime_section(
        self,
        regime: str,
        circuit_breaker_status: object | None,
    ) -> BriefingSection:
        lines: list[str] = []
        if circuit_breaker_status is not None and getattr(
            circuit_breaker_status, "triggered", False
        ):
            reason = getattr(circuit_breaker_status, "reason", "")
            lines.append(f"> ⚠️ **组合保护中**: {reason}")
            lines.append("")
        desc = _REGIME_DESCRIPTIONS.get(regime, regime or "未知")
        lines.append(f"当前市场态势: **{desc}**")
        return BriefingSection(title="市场态势", content="\n".join(lines))

    def _build_source_section(
        self,
        source_status: dict[str, str | bool] | None,
    ) -> BriefingSection:
        if not source_status:
            return BriefingSection(
                title="数据源状态",
                content="暂无最近一次运行的数据源状态记录。",
            )
        requested = str(source_status.get("requested_source", "") or "")
        actual = str(source_status.get("actual_source", "") or "")
        freshness = str(source_status.get("freshness_tier", "") or "unknown")
        coverage = str(source_status.get("coverage_tier", "") or "unknown")
        label = str(source_status.get("health_label", "") or "unknown")
        message = str(source_status.get("health_message", "") or "暂无说明")
        fallback_used = bool(source_status.get("fallback_used", False))
        route = actual or requested or "unknown"
        if requested and actual and requested != actual:
            route = f"{requested} -> {actual}"
        lines = [
            f"- 路径: **{route}**",
            f"- 层级: fresh={freshness} / cover={coverage}",
            f"- 健康: **{label}**",
            f"- fallback: {'yes' if fallback_used else 'no'}",
            f"- 说明: {message}",
        ]
        if label in {"fallback", "degraded", "cold_start"}:
            lines.append("- 提示: 本次结果请降低信任度，优先人工复核。")
        return BriefingSection(title="数据源状态", content="\n".join(lines))

    def _build_research_section(
        self,
        research_summary: ResearchSummary | None,
    ) -> BriefingSection:
        if research_summary is None:
            return BriefingSection(
                title="研究吸收",
                content="暂无研究吸收摘要；开源研究库还没有接入到本次日报。",
            )
        lines = [
            f"- 研究候选总数: **{research_summary.total_findings}**",
            f"- 已吸收但未直接入分策略族: **{len(research_summary.absorbed_families)}**",
            f"- 已部分实现策略族: **{research_summary.implemented_family_count}**",
            f"- report-only 研究族: **{research_summary.report_only_family_count}**",
            f"- 运行门控研究族: **{research_summary.gated_family_count}**",
        ]
        top_pipelines = list(research_summary.pipeline_summaries[:3])
        if top_pipelines:
            for item in top_pipelines:
                lines.append(
                    f"- 研究管线 {item.pipeline}: P1={item.p1} / total={item.total} / top={item.top_repo or '-'}"
                )
        if research_summary.absorbed_families:
            names = "、".join(
                f"{item.name}({item.runtime_stage})"
                for item in research_summary.absorbed_families[:4]
            )
            lines.append(f"- 已吸收主题: {names}")
        if research_summary.next_actions:
            next_item = research_summary.next_actions[0]
            lines.append(
                f"- 下一接入重点: {next_item.kind}/{next_item.item_id} [{next_item.priority}] - {next_item.blocker or '待补 gate'}"
            )
        prereq_item = next(
            (item for item in research_summary.prereq_items if item.status != "ready"),
            None,
        )
        if prereq_item is not None:
            missing_env = "、".join(prereq_item.missing_env_vars) or "fixture"
            lines.append(
                f"- 当前前置缺口: {prereq_item.kind}/{prereq_item.item_id} - {prereq_item.status} ({missing_env})"
            )
        lines.append("- 原则: 研究内容只做候选和解释，不直接覆盖 runtime 打分。")
        return BriefingSection(title="研究吸收", content="\n".join(lines))

    def _build_evidence_section(self, picks: list[PickResult]) -> BriefingSection:
        lines: list[str] = []
        if not picks:
            lines.append("今日无候选标的。")
            return BriefingSection(title="候选证据链", content="\n".join(lines))
        for pick in picks:
            lines.append(f"### {pick.symbol} {pick.name} (评分: {pick.score})")
            if pick.strategies:
                lines.append(f"- 命中策略: {', '.join(pick.strategies)}")
            for reason in pick.reasons:
                lines.append(f"- {reason}")
            if pick.risks:
                lines.append(f"- 风险: {'；'.join(pick.risks)}")
            lines.append("")
        return BriefingSection(title="候选证据链", content="\n".join(lines))

    def _build_theme_section(self, picks: list[PickResult]) -> BriefingSection:
        lines: list[str] = []
        if not picks:
            lines.append("无题材热度数据。")
            return BriefingSection(title="题材热度", content="\n".join(lines))
        theme_counts: Counter[str] = Counter()
        for pick in picks:
            for reason in pick.reasons:
                for theme, keywords in _THEMES.items():
                    if any(kw in reason for kw in keywords):
                        theme_counts[theme] += 1
        if not theme_counts:
            lines.append("今日候选未归类到已知题材。")
        else:
            for theme, count in theme_counts.most_common():
                label = {
                    "volume": "量价",
                    "momentum": "动量/趋势",
                    "value": "估值",
                    "quality": "质量",
                    "technical": "技术指标",
                }.get(theme, theme)
                lines.append(f"- **{label}**: {count} 条线索")
        return BriefingSection(title="题材热度", content="\n".join(lines))

    def _build_next_day_section(
        self,
        picks: list[PickResult],
        frames: dict[str, pd.DataFrame],
    ) -> BriefingSection:
        lines: list[str] = []
        tradable_picks = [p for p in picks if is_tradable_rating(p.rating)]
        if not tradable_picks:
            lines.append("无可执行重点标的；今日候选均为回避/观察，不进入虚拟买入。")
            return BriefingSection(title="明日重点", content="\n".join(lines))
        for pick in tradable_picks[:5]:
            entry = pick.ideal_buy
            stop = pick.stop_loss
            tp = pick.take_profit
            lines.append(
                f"- **{pick.symbol} {pick.name}**: "
                f"参考买点 {entry} / 止损 {stop} / 止盈 {tp} / 仓位 {pick.position}"
            )
        lines.append("")
        lines.append("> 注: 解禁/财报等事件数据待接入，此处为占位。")
        return BriefingSection(title="明日重点", content="\n".join(lines))

    def render_template(
        self,
        briefing: Briefing,
        picks: list[PickResult],
        circuit_breaker_status: object | None = None,
    ) -> str:
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
        template = env.get_template("default.md.j2")
        cb_triggered = circuit_breaker_status is not None and getattr(
            circuit_breaker_status, "triggered", False
        )
        cb_reason = (
            getattr(circuit_breaker_status, "reason", "") if cb_triggered else ""
        )
        regime_section = ""
        theme_section = ""
        next_day_section = ""
        for section in briefing.sections:
            if section.title == "市场态势":
                regime_section = section.content
            elif section.title == "题材热度":
                theme_section = section.content
            elif section.title == "明日重点":
                next_day_section = section.content
        pick_dicts = [
            {
                "symbol": p.symbol,
                "name": p.name,
                "score": p.score,
                "rating": p.rating,
                "strategies": list(p.strategies),
                "reasons": list(p.reasons),
            }
            for p in picks
        ]
        return template.render(
            date=briefing.date,
            circuit_breaker_triggered=cb_triggered,
            circuit_breaker_reason=cb_reason,
            regime_section=regime_section,
            picks=pick_dicts,
            theme_section=theme_section,
            next_day_section=next_day_section,
        )
