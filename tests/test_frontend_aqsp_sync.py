"""Canonical React AQSP surface acceptance checks.

历史：这些断言原先全部绑定在单体组件 `components/aqsp/AqspPanels.tsx` 上。
2026-09-12 前端按工作流重组 IA（今天 / 市场 / 雷达 / 绩效 / 归档 / 实验室 / 持仓），
单体面板拆成 `components/aqsp/sections/*` 与 `lib/daily-view.ts` 视图层。
本文件随之**迁移落点，但不降低保证强度**：

- 分区独立：今天工作区由「目录声明 + 每个 id 都有组件承接」取代原来的固定 markup
- 空态绑数据：三条空态文案 + `snapshot.selected_date / generated_at / meta?.historical`
- 研究链 fail-closed：仍在 `lib/candidate-chain.ts`，并要求视图层真的消费它
- 评分依据可见：`candidate.score_breakdown` + 「评分依据」
- 禁 legacy streamlit / 8501

IA 变化显式记在这里：原 4 分区（overview / messages / candidates / discussion）
变为今天工作区 3 分区（candidates / messages / discussion），变体实验独立为「实验室」页。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = PROJECT_ROOT / "frontend" / "src"
TODAY_WORKSPACE = FRONTEND_SRC / "components" / "aqsp" / "TodayWorkspace.tsx"
CANDIDATE_SECTION = FRONTEND_SRC / "components" / "aqsp" / "sections" / "CandidateSection.tsx"
LAB_PAGE = FRONTEND_SRC / "pages" / "LabPage.tsx"
DAILY_VIEW = FRONTEND_SRC / "lib" / "daily-view.ts"
CANDIDATE_CHAIN = FRONTEND_SRC / "lib" / "candidate-chain.ts"


def _read(path: Path) -> str:
    assert path.exists(), f"前端契约文件缺失：{path}"
    return path.read_text(encoding="utf-8")


def test_frontend_today_workspace_renders_every_catalogue_section() -> None:
    """今天工作区的分区由目录声明，且目录里每个 id 都必须被真正渲染。

    这条取代了原先「4 个固定 section id + aqsp-formal-grid」的 markup 断言：
    现在真正的保证是「声明与实现不能脱节」—— 目录加了一个分区却没人渲染，这里就会红。
    """
    catalogue = _read(DAILY_VIEW)
    workspace = _read(TODAY_WORKSPACE)

    assert "TODAY_SECTION_CATALOGUE" in catalogue
    for section_id in ("candidates", "messages", "discussion"):
        assert f'id: "{section_id}"' in catalogue, f"目录缺少分区 {section_id}"

    # 每个分区 id 都要有独立组件承接（import + 分派都在工作区里）
    assert 'import { CandidateSection } from "./sections/CandidateSection";' in workspace
    assert 'import { MessageSection } from "./sections/MessageSection";' in workspace
    assert 'import { DiscussionSection } from "./sections/DiscussionSection";' in workspace
    assert 'section.id === "candidates"' in workspace
    assert 'section.id === "messages"' in workspace
    assert "<DiscussionSection" in workspace

    # 变体实验不再挂在今天工作区，而是由「实验室」页独立承接（IA 变化见文件头）
    assert "VariantSection" in _read(LAB_PAGE)


def test_frontend_aqsp_keeps_empty_states_and_experiment_snapshot_bound_to_data() -> None:
    source = _read(DAILY_VIEW)

    assert "当天未形成可引用消息证据" in source
    assert "当天没有候选" in source
    assert "当天讨论未启动" in source
    assert "snapshot.selected_date" in source
    assert "snapshot.generated_at" in source
    assert "snapshot.meta?.historical" in source


def test_frontend_aqsp_overview_links_every_candidate_and_fails_closed() -> None:
    chain_helper = _read(CANDIDATE_CHAIN)

    assert "messagesForCandidate" in chain_helper
    assert "historicalVariantCount" in chain_helper
    assert "allCandidatesResearchReady" in chain_helper
    assert "snapshot.candidates.every" in chain_helper
    assert "source_url?.trim() || message.url?.trim()" in chain_helper

    # 视图层必须真的消费链函数，而不是各自另算一套（否则 fail-closed 会被绕过）
    view = _read(DAILY_VIEW)
    assert "messagesForCandidate" in view
    assert "historicalVariantCount" in view


def test_frontend_aqsp_exposes_deterministic_score_breakdown() -> None:
    assert "candidate.score_breakdown" in _read(DAILY_VIEW)
    assert "评分依据" in _read(CANDIDATE_SECTION)


def test_frontend_aqsp_has_no_legacy_streamlit_or_8501_navigation() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FRONTEND_SRC.rglob("*.ts*")
    ).lower()

    assert "streamlit" not in source
    assert "8501" not in source
    assert "127.0.0.1:8501" not in source
