from __future__ import annotations

import argparse

from aqsp import cli


def test_run_doctor_passes_probe_flags(monkeypatch) -> None:
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured[:] = argv
        return 0

    monkeypatch.setattr("scripts.server_doctor.main", fake_main)

    args = argparse.Namespace(probe_auth=True, probe_llm=True)
    exit_code = cli.run_doctor(args)

    assert exit_code == 0
    assert captured == ["--probe-auth", "--probe-llm"]
