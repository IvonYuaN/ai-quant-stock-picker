"""Canonical React AQSP surface acceptance checks."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANELS = PROJECT_ROOT / "frontend" / "src" / "components" / "aqsp" / "AqspPanels.tsx"


def test_frontend_aqsp_renders_four_independent_formal_sections() -> None:
    source = PANELS.read_text(encoding="utf-8")

    assert 'className="aqsp-formal-grid"' in source
    for section_id in ("overview", "messages", "candidates", "discussion"):
        assert f'id="{section_id}"' in source
    assert sum(source.count(f'id="{section_id}"') for section_id in ("overview", "messages", "candidates", "discussion")) == 4


def test_frontend_aqsp_keeps_empty_states_and_experiment_snapshot_bound_to_data() -> None:
    source = PANELS.read_text(encoding="utf-8")

    assert 'title="当天没有有效消息"' in source
    assert 'title="当天没有候选"' in source
    assert 'title="当天没有有效讨论"' in source
    assert "snapshot.selected_date" in source
    assert "snapshot.generated_at" in source
    assert "snapshot.meta?.historical" in source


def test_frontend_aqsp_has_no_legacy_streamlit_or_8501_navigation() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "frontend" / "src").rglob("*.ts*")
    ).lower()

    assert "streamlit" not in source
    assert "8501" not in source
    assert "127.0.0.1:8501" not in source
