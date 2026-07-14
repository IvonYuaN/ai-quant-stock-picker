from pathlib import Path

from scripts.render_agent_dashboard import render_agent_dashboard


def test_render_agent_dashboard_redirects_every_legacy_output(tmp_path: Path) -> None:
    output = tmp_path / "legacy-agent-page.html"

    render_agent_dashboard(output_path=str(output))

    content = output.read_text(encoding="utf-8")
    assert "canonical-research-surface" in content
    assert "https://lh.ifidy.cn" in content
    assert "Agent性能分析" not in content
