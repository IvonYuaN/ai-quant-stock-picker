from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_dashboard_ingress_proxies_to_streamlit() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    assert "location /" in config
    assert "proxy_pass http://127.0.0.1:8501;" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert 'proxy_set_header Connection "upgrade";' in config


def test_production_dashboard_ingress_redirects_legacy_paths_to_root() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    for path in (
        "/dashboard",
        "/dashboard/",
        "/dist/dashboard",
        "/dist/dashboard/",
        "/dist/dashboard/index.html",
        "/dist/dashboard/archive.html",
        "/dist/dashboard/agents.html",
        "/agents.html",
        "/dashboard_beginner",
        "/dashboard_beginner.py",
        "/archive.html",
    ):
        assert f"location = {path}" in config
    assert "location ~ ^/(?:dashboard/)?(?:beginner|agent|agents|dashboard_beginner)" in config
    assert all(token in config for token in ("beginner", "agent", "agents", "dashboard_beginner"))
    assert "(?:static|beginner|agent|agents)" not in config
    assert "return 302 /$is_args$args;" in config


def test_production_dashboard_ingress_does_not_redirect_streamlit_static_assets() -> None:
    config = (PROJECT_ROOT / "deploy" / "nginx" / "aqsp-dashboard.conf").read_text(
        encoding="utf-8"
    )

    assert "location ~ ^/(?:dashboard/)?(?:beginner|agent|agents|dashboard_beginner)" in config
    assert "location ~ ^/(?:dashboard/)?(?:static" not in config
