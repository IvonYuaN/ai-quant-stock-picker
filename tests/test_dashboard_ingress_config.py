from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_dashboard_ingress_proxies_to_vibe_research() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    assert "location /" in config
    assert "proxy_pass http://127.0.0.1:5899;" in config
    assert "proxy_pass http://127.0.0.1:8900;" in config
    assert "location ^~ /api/" in config
    assert "Streamlit" not in config


def test_production_dashboard_ingress_redirects_legacy_paths_to_root() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    assert "location ~ ^/(?:dist/)?dashboard" in config
    assert "location ~ ^/(?:beginner|agent|agents|dashboard_beginner)" in config
    assert all(
        token in config
        for token in ("beginner", "agent", "agents", "dashboard_beginner")
    )
    assert "return 302 /$is_args$args;" in config


def test_production_dashboard_ingress_does_not_redirect_streamlit_static_assets() -> (
    None
):
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    assert "location ~ ^/(?:beginner|agent|agents|dashboard_beginner)" in config
    assert "proxy_pass http://127.0.0.1:8501" not in config


def test_production_dashboard_ingress_has_no_legacy_streamlit_health_route() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    assert "_stcore" not in config
    assert "proxy_pass http://127.0.0.1:5899;" in config
    assert "proxy_pass http://127.0.0.1:8900;" in config


def test_mainline_ingress_keeps_legacy_streamlit_out_of_production_upstream() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "vibe-research-mainline.conf").read_text(
        encoding="utf-8"
    )

    assert "proxy_pass http://127.0.0.1:5899;" in config
    assert "proxy_pass http://127.0.0.1:8900;" in config
    assert "Streamlit upstream" not in config
    assert "proxy_pass http://127.0.0.1:8501" not in config
