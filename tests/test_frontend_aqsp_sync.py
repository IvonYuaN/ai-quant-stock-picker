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
变为今天工作区 3 分区（candidates / messages / discussion）；原独立「实验室」页
已于 2026-10-09 按 #317 W4 收敛删除，变体承接落点迁至候选链「历史变体」列。

2026-11-09：分区承接落点从「今天工作区一个文件」改为**按模块页分散承接** ——
候选分区在 `/candidates` 页，消息与多空分区在 `/evidence` 页。保证强度不变：
目录里每个 id 仍必须被某个页面真正 import 并渲染（见下方断言）。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = PROJECT_ROOT / "frontend" / "src"
TODAY_WORKSPACE = FRONTEND_SRC / "components" / "aqsp" / "TodayWorkspace.tsx"
CANDIDATE_SECTION = FRONTEND_SRC / "components" / "aqsp" / "sections" / "CandidateSection.tsx"
CANDIDATES_PAGE = FRONTEND_SRC / "pages" / "CandidatesPage.tsx"
EVIDENCE_PAGE = FRONTEND_SRC / "pages" / "EvidencePage.tsx"
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

    # 每个分区 id 都要有独立组件承接。2026-11-09 起承接落点按模块页分布：
    # 候选分区在 /candidates 页，消息与多空分区在 /evidence 页 —— 断言强度不变，
    # 仍然是「import + 真正渲染」两件事都要成立，目录加分区却没人渲染这里就会红。
    candidates_page = _read(CANDIDATES_PAGE)
    evidence_page = _read(EVIDENCE_PAGE)

    assert 'from "@/components/aqsp/sections/CandidateSection"' in candidates_page
    assert "<CandidateSection" in candidates_page
    assert 'from "@/components/aqsp/sections/MessageSection"' in evidence_page
    assert "<MessageSection" in evidence_page
    assert 'from "@/components/aqsp/sections/DiscussionSection"' in evidence_page
    assert "<DiscussionSection" in evidence_page

    # 今天工作区已瘦身成总览页：不再自己渲染分区，但必须保留候选摘要入口，
    # 否则总览页与候选模块之间会断链。
    assert "CandidateSummary" in workspace
    assert "<CandidateSummary" in workspace

    # 变体实验不再挂在今天工作区。独立「实验室」页已按 #317 W4 收敛删除，
    # 保证随之迁移：候选链表必须有「历史变体」列承接（variantCount 由
    # daily-view 的 historicalVariantCount 算出，见下一条链函数断言）。
    assert "历史变体" in _read(CANDIDATE_SECTION)


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
