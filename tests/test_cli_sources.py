from __future__ import annotations

import argparse
import json

from aqsp import cli


def test_run_sources_json_includes_auth_and_workload_fields(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "aqsp.cli.inspect_source_readiness",
        lambda entry, probe_auth=False: type(
            "Snapshot",
            (),
            {
                "auth_kind": "login_session",
                "auth_status": "ok",
                "auth_message": "登录正常",
                "auth_checked_at": "2026-06-02T18:00:00+08:00",
                "active_probe": probe_auth,
                "workload_fit": {
                    "live_short": "avoid",
                    "walkforward": "supplement",
                    "pit": "candidate",
                },
            },
        )(),
    )

    exit_code = cli.run_sources(
        argparse.Namespace(ready_only=True, json=True, probe_auth=False)
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output[0]["auth_status"] == "ok"
    assert output[0]["workload_fit"]["live_short"] == "avoid"
    assert "auth_kind" in output[0]
