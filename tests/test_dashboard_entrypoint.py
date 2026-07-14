from __future__ import annotations

from pathlib import Path

from aqsp.web.entrypoint import (
    CANONICAL_HEALTH_PATH,
    classify_entry_text,
    classify_health_text,
    public_dashboard_url,
    public_research_health_url,
    render_legacy_redirect,
    write_agent_archive_guard,
    write_dashboard_artifact,
)


def test_public_dashboard_url_uses_configured_canonical_entry(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_DASHBOARD_PUBLIC_URL", "https://example.test/dashboard/")

    assert public_dashboard_url() == "https://example.test/dashboard"


def test_public_dashboard_url_rejects_relative_legacy_entry(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_DASHBOARD_PUBLIC_URL", "dist/dashboard/index.html")

    try:
        public_dashboard_url()
    except ValueError as exc:
        assert "http://" in str(exc)
    else:
        raise AssertionError("relative static dashboard entry must be rejected")


def test_public_research_health_url_uses_the_single_canonical_entry() -> None:
    assert public_research_health_url(base_url="https://example.test/") == (
        f"https://example.test{CANONICAL_HEALTH_PATH}"
    )


def test_public_dashboard_url_rejects_embedded_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_DASHBOARD_PUBLIC_URL", "https://user:secret@example.test")

    try:
        public_dashboard_url()
    except ValueError as exc:
        assert "credentials" in str(exc)
    else:
        raise AssertionError("public entry must not carry URL credentials")


def test_entrypoint_classification_distinguishes_canonical_legacy_and_redirect() -> (
    None
):
    assert classify_entry_text("Vibe-Research AQSP 研究工作台") == "canonical"
    assert classify_entry_text("<div>AQSP 日期任务研究台</div>") == "legacy"
    assert (
        classify_entry_text(
            '<meta name="aqsp-dashboard-entry" content="canonical-research-surface">'
        )
        == "redirect"
    )
    assert classify_entry_text("unrelated page") == "unknown"


def test_health_classification_distinguishes_vibe_api_and_streamlit() -> None:
    assert classify_health_text('{"ok": true, "service": "vibe-research-api"}') == (
        "canonical"
    )
    assert classify_health_text("ok") == "legacy"
    assert classify_health_text("not healthy") == "unknown"


def test_index_artifact_redirects_and_preserves_archive(tmp_path: Path) -> None:
    index = tmp_path / "dist" / "dashboard" / "index.html"

    archive = write_dashboard_artifact(index, "<main>archive</main>")

    assert archive == index.with_name("archive.html")
    assert archive.read_text(encoding="utf-8") == "<main>archive</main>"
    entry = index.read_text(encoding="utf-8")
    assert 'content="canonical-research-surface"' in entry
    assert "https://lh.ifidy.cn" in entry
    assert "archive" not in entry


def test_agent_page_is_retired_to_canonical_entry(tmp_path: Path) -> None:
    output = tmp_path / "agents.html"

    write_agent_archive_guard(output)

    html = output.read_text(encoding="utf-8")
    assert "canonical-research-surface" in html
    assert "https://lh.ifidy.cn" in html


def test_redirect_supports_explicit_target() -> None:
    html = render_legacy_redirect(target_url="https://example.test")

    assert "https://example.test" in html
    assert "canonical-research-surface" in html
