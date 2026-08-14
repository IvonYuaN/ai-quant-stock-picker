"""Canonical React AQSP surface acceptance checks."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANELS = PROJECT_ROOT / "frontend" / "src" / "components" / "aqsp" / "AqspPanels.tsx"


def test_frontend_aqsp_renders_four_independent_formal_sections() -> None:
    source = PANELS.read_text(encoding="utf-8")

    assert 'className="aqsp-formal-grid"' in source
    for section_id in ("overview", "messages", "candidates", "discussion"):
        assert f'id="{section_id}"' in source
    assert (
        sum(
            source.count(f'id="{section_id}"')
            for section_id in ("overview", "messages", "candidates", "discussion")
        )
        == 4
    )


def test_frontend_aqsp_keeps_empty_states_and_experiment_snapshot_bound_to_data() -> (
    None
):
    source = PANELS.read_text(encoding="utf-8")

    assert "当天未形成可引用消息证据" in source
    assert 'title="当天没有候选"' in source
    assert "当天讨论未启动" in source
    assert "snapshot.selected_date" in source
    assert "snapshot.generated_at" in source
    assert "snapshot.meta?.historical" in source


def test_frontend_aqsp_overview_links_every_candidate_and_fails_closed() -> None:
    source = PANELS.read_text(encoding="utf-8")
    chain_helper = (
        PROJECT_ROOT / "frontend" / "src" / "lib" / "candidate-chain.ts"
    ).read_text(encoding="utf-8")

    assert 'aria-label="当天候选研究链"' in source
    assert "messagesForCandidate" in source
    assert "historicalVariantCount" in source
    assert "allCandidatesResearchReady" in source
    assert "snapshot.candidates.every" in chain_helper
    assert "source_url?.trim() || message.url?.trim()" in chain_helper


def test_frontend_aqsp_exposes_deterministic_score_breakdown() -> None:
    source = PANELS.read_text(encoding="utf-8")

    assert "candidate.score_breakdown" in source
    assert "评分依据" in source


def test_frontend_aqsp_has_no_legacy_streamlit_or_8501_navigation() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "frontend" / "src").rglob("*.ts*")
    ).lower()

    assert "streamlit" not in source
    assert "8501" not in source
    assert "127.0.0.1:8501" not in source
