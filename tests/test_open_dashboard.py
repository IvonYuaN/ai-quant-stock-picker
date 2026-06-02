from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.open_dashboard import (
    DEFAULT_PORT,
    DashboardLaunchResult,
    ensure_dashboard_server,
    open_dashboard,
    render_dashboard_bundle,
)


def test_render_dashboard_bundle_writes_html_and_db(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper.jsonl"
    output_path = tmp_path / "dist/dashboard/index.html"
    db_path = tmp_path / "dist/dashboard/aqsp.db"

    pd.DataFrame([{"symbol": "600519", "name": "贵州茅台", "score": "71"}]).to_csv(
        csv_path,
        index=False,
    )
    ledger_path.write_text(
        json.dumps(
            {"signal_date": "2026-05-29", "symbol": "600519"}, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    render_dashboard_bundle(
        csv_path=csv_path,
        ledger_path=ledger_path,
        paper_ledger_path=paper_path,
        output_path=output_path,
        db_path=db_path,
        title="固定端口面板",
    )

    assert output_path.exists()
    assert db_path.exists()
    assert "固定端口面板" in output_path.read_text(encoding="utf-8")


def test_ensure_dashboard_server_reuses_existing_server(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("scripts.open_dashboard._url_reachable", lambda _url: True)

    started, pid = ensure_dashboard_server(
        directory=tmp_path,
        host="127.0.0.1",
        port=DEFAULT_PORT,
        log_path=tmp_path / "server.log",
    )

    assert started is False
    assert pid is None


def test_open_dashboard_uses_fixed_default_port_when_serving(
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
    )

    assert isinstance(result, DashboardLaunchResult)
    assert result.url == "http://127.0.0.1:9876"
    assert result.server_started is True
    assert result.pid == 4321
    assert calls == ["http://127.0.0.1:9876"]
