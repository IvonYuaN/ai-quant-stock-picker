from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from aqsp.core.time import now_shanghai
from scripts.open_dashboard import (
    ALLOW_FOREGROUND_BROWSER_ENV,
    DEFAULT_PORT,
    DashboardPortProbe,
    DashboardLaunchResult,
    PROJECT_ROOT,
    ensure_dashboard_server,
    foreground_browser_allowed,
    open_dashboard,
    render_dashboard_bundle,
    probe_dashboard_port,
)


def test_render_dashboard_bundle_writes_html_and_db(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    intraday_csv_path = tmp_path / "intraday_latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper.jsonl"
    debate_path = tmp_path / "debate.jsonl"
    output_path = tmp_path / "dist/dashboard/index.html"
    db_path = tmp_path / "dist/dashboard/aqsp.db"
    today = now_shanghai().date().isoformat()

    pd.DataFrame(
        [{"symbol": "600519", "name": "贵州茅台", "date": today, "score": "71"}]
    ).to_csv(csv_path, index=False)
    pd.DataFrame(
        [{"symbol": "600900", "name": "长江电力", "date": today, "score": "55"}]
    ).to_csv(intraday_csv_path, index=False)
    ledger_path.write_text(
        json.dumps(
            {"signal_date": "2026-05-29", "symbol": "600519"}, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    debate_path.write_text(
        json.dumps(
            {
                "symbol": "600900",
                "name": "长江电力",
                "debate_date": today,
                "related_signal_date": today,
                "final_consensus": "keep",
                "recommended_adjustment": "keep",
                "final_vote": {"support": 2, "oppose": 0, "watch": 1},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    render_dashboard_bundle(
        csv_path=csv_path,
        ledger_path=ledger_path,
        paper_ledger_path=paper_path,
        intraday_csv_path=intraday_csv_path,
        output_path=output_path,
        db_path=db_path,
        title="固定端口面板",
        debate_path=debate_path,
    )

    assert output_path.exists()
    assert db_path.exists()
    html = (output_path.parent / "archive.html").read_text(encoding="utf-8")
    entry = output_path.read_text(encoding="utf-8")
    assert "固定端口面板" in html
    assert "短线决策看板" in html
    assert "aqsp-static-two-column" in html
    assert "研究候选已解锁" in html
    assert "候选来源 盘中实时" in html
    assert "600900" in html
    assert "长江电力" in html
    assert "Agent讨论" in html
    assert 'content="canonical-research-surface"' in entry
    assert "https://lh.ifidy.cn" in entry


def test_ensure_dashboard_server_reuses_existing_server(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "scripts.open_dashboard.probe_dashboard_port",
        lambda _host, _port: DashboardPortProbe(
            "current", "current AQSP Streamlit dashboard"
        ),
    )

    started, pid = ensure_dashboard_server(
        directory=tmp_path,
        host="127.0.0.1",
        port=DEFAULT_PORT,
        log_path=tmp_path / "server.log",
    )

    assert started is False
    assert pid is None


def test_ensure_dashboard_server_starts_current_streamlit_app(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    class _Process:
        pid = 4321

        def terminate(self) -> None:
            raise AssertionError("streamlit should become reachable")

        def wait(self, timeout: float) -> None:
            return None

    monkeypatch.setattr("scripts.open_dashboard.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "scripts.open_dashboard.subprocess.Popen",
        lambda command, **kwargs: commands.append(command) or _Process(),
    )

    probes = iter(
        (
            DashboardPortProbe("free", "no application is listening"),
            DashboardPortProbe("current", "current AQSP Streamlit dashboard"),
        )
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.probe_dashboard_port",
        lambda _host, _port: next(probes),
    )
    started, pid = ensure_dashboard_server(
        directory=tmp_path,
        host="127.0.0.1",
        port=DEFAULT_PORT,
        log_path=tmp_path / "server.log",
    )

    assert started is True
    assert pid == 4321
    assert commands == [
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(PROJECT_ROOT / "src" / "aqsp" / "web" / "dashboard.py"),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(DEFAULT_PORT),
            "--server.headless",
            "true",
        ]
    ]


def test_ensure_dashboard_server_rejects_old_streamlit_on_default_port(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "scripts.open_dashboard.probe_dashboard_port",
        lambda _host, _port: DashboardPortProbe(
            "occupied", "another Streamlit application"
        ),
    )

    try:
        ensure_dashboard_server(
            directory=tmp_path,
            host="127.0.0.1",
            port=DEFAULT_PORT,
            log_path=tmp_path / "server.log",
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("old Streamlit must not be silently reused")

    assert "another Streamlit application" in message
    assert "will not silently reuse" in message
    assert "--port 8502" in message


def test_open_dashboard_does_not_render_static_archive_for_production_launch(
    monkeypatch, tmp_path: Path
) -> None:
    render_calls: list[bool] = []
    monkeypatch.setattr(
        "scripts.open_dashboard.render_dashboard_bundle",
        lambda **_kwargs: render_calls.append(True),
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.ensure_dashboard_server",
        lambda **_kwargs: (False, None),
    )

    open_dashboard(
        csv_path=tmp_path / "latest.csv",
        ledger_path=tmp_path / "predictions.jsonl",
        paper_ledger_path=tmp_path / "paper.jsonl",
        output_path=tmp_path / "dist/dashboard/index.html",
        db_path=tmp_path / "dist/dashboard/aqsp.db",
    )

    assert render_calls == []


def test_dashboard_entry_scripts_do_not_point_at_legacy_homepages() -> None:
    start_script = (PROJECT_ROOT / "scripts" / "start_dashboard.sh").read_text(
        encoding="utf-8"
    )
    open_script = (PROJECT_ROOT / "scripts" / "open_dashboard.py").read_text(
        encoding="utf-8"
    )

    for text in (start_script, open_script):
        assert "dashboard_beginner" not in text
        assert "agents.html" not in text
    assert "start_vibe_research.sh" in start_script
    assert "STREAMLIT_APP" in open_script


def test_probe_dashboard_port_classifies_current_and_legacy_pages(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.open_dashboard._read_url_text",
        lambda _url: "<title>AQSP 日期任务研究台</title>",
    )
    assert probe_dashboard_port("127.0.0.1", DEFAULT_PORT).status == "current"

    monkeypatch.setattr(
        "scripts.open_dashboard._listener_command",
        lambda _port: "",
    )
    monkeypatch.setattr(
        "scripts.open_dashboard._read_url_text",
        lambda _url: "<title>新手看板</title><script>streamlit</script>",
    )
    assert probe_dashboard_port("127.0.0.1", DEFAULT_PORT).status == "occupied"


def test_open_dashboard_uses_fixed_default_port_without_opening_browser_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "dist/dashboard/index.html"
    db_path = tmp_path / "dist/dashboard/aqsp.db"
    calls: list[str] = []

    monkeypatch.setattr(
        "scripts.open_dashboard.render_dashboard_bundle",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.ensure_dashboard_server",
        lambda **_kwargs: (True, 4321),
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.webbrowser.open",
        lambda url: calls.append(url),
    )

    result = open_dashboard(
        csv_path=tmp_path / "latest.csv",
        ledger_path=tmp_path / "predictions.jsonl",
        paper_ledger_path=tmp_path / "paper.jsonl",
        output_path=output_path,
        db_path=db_path,
        render_static_artifact=False,
    )

    assert isinstance(result, DashboardLaunchResult)
    assert result.url == "http://127.0.0.1:8501"
    assert result.server_started is True
    assert result.pid == 4321
    assert result.browser_opened is False
    assert calls == []


def test_open_dashboard_blocks_foreground_browser_without_env_opt_in(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ALLOW_FOREGROUND_BROWSER_ENV, raising=False)
    output_path = tmp_path / "dist/dashboard/index.html"
    db_path = tmp_path / "dist/dashboard/aqsp.db"
    calls: list[str] = []

    monkeypatch.setattr(
        "scripts.open_dashboard.render_dashboard_bundle",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.ensure_dashboard_server",
        lambda **_kwargs: (True, 4321),
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.webbrowser.open",
        lambda url: calls.append(url),
    )

    result = open_dashboard(
        csv_path=tmp_path / "latest.csv",
        ledger_path=tmp_path / "predictions.jsonl",
        paper_ledger_path=tmp_path / "paper.jsonl",
        output_path=output_path,
        db_path=db_path,
        open_browser=True,
    )

    assert result.url == "http://127.0.0.1:8501"
    assert result.browser_opened is False
    assert calls == []


def test_open_dashboard_opens_browser_only_with_explicit_env_opt_in(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ALLOW_FOREGROUND_BROWSER_ENV, "1")
    output_path = tmp_path / "dist/dashboard/index.html"
    db_path = tmp_path / "dist/dashboard/aqsp.db"
    calls: list[str] = []

    monkeypatch.setattr(
        "scripts.open_dashboard.render_dashboard_bundle",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.ensure_dashboard_server",
        lambda **_kwargs: (True, 4321),
    )
    monkeypatch.setattr(
        "scripts.open_dashboard.webbrowser.open",
        lambda url: calls.append(url),
    )

    result = open_dashboard(
        csv_path=tmp_path / "latest.csv",
        ledger_path=tmp_path / "predictions.jsonl",
        paper_ledger_path=tmp_path / "paper.jsonl",
        output_path=output_path,
        db_path=db_path,
        open_browser=True,
    )

    assert result.url == "http://127.0.0.1:8501"
    assert result.browser_opened is True
    assert calls == ["http://127.0.0.1:8501"]


def test_foreground_browser_allowed_requires_explicit_env_opt_in(
    monkeypatch,
) -> None:
    monkeypatch.delenv(ALLOW_FOREGROUND_BROWSER_ENV, raising=False)
    assert foreground_browser_allowed() is False

    monkeypatch.setenv(ALLOW_FOREGROUND_BROWSER_ENV, "true")
    assert foreground_browser_allowed() is True
