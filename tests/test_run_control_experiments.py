"""Tests for the #199 control-experiment driver.

The driver's whole job is isolation: three 11.5h gate runs must not overwrite
each other, and must never be mistaken for the weekly production verdict.
Those two properties are what these tests pin down.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "runner"
    / "run_control_experiments.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_control_experiments", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module through sys.modules when building the
    # class, so it must be registered before exec_module().
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rce = _load_module()


def _fake_gate(runner_root: Path, body: str, *, out_override: bool = True) -> Path:
    """Install a stand-in runner_gate.sh at the path the driver expects."""
    script = runner_root / "aqsp-scheduler-current" / rce.GATE_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    header = "#!/usr/bin/env bash\nset -eu\n"
    if out_override:
        header += 'OUT="${OUT:-/nonexistent/gate_run}"\n'
    script.write_text(header + body, encoding="utf-8")
    return script


def _spec(name: str = "baseline") -> "rce.Spec":
    return rce.Spec(name=name, env={"GRID_PROFILE": "stable_plus"}, note="t")


# --------------------------------------------------------------------------
# isolation: OUT per spec, RESULT_READY never raised
# --------------------------------------------------------------------------


def test_build_env_isolates_out_and_disables_ready(tmp_path: Path) -> None:
    out_root = tmp_path / "199_control"
    env = rce.build_env(_spec("net_fees"), out_root, runner_root=tmp_path, base_env={})

    assert env["OUT"] == str((out_root / "net_fees").resolve())
    assert env["MARK_READY"] == "0"  # research run must not signal prod
    assert env["GRID_PROFILE"] == "stable_plus"
    assert env["RUNNER_ROOT"] == str(tmp_path)


def test_build_env_does_not_mutate_caller_env(tmp_path: Path) -> None:
    base = {"MARK_READY": "1", "PATH": "/bin"}
    rce.build_env(_spec(), tmp_path / "out", runner_root=tmp_path, base_env=base)
    assert base == {"MARK_READY": "1", "PATH": "/bin"}


def test_build_env_refuses_production_gate_run(tmp_path: Path) -> None:
    """Writing into <runner-root>/gate_run would be picked up by prod's fetch."""
    (tmp_path / "gate_run").mkdir()
    with pytest.raises(ValueError, match="production gate_run"):
        rce.build_env(
            _spec(""), tmp_path / "gate_run", runner_root=tmp_path, base_env={}
        )


def test_each_spec_gets_a_distinct_out(tmp_path: Path) -> None:
    out_root = tmp_path / "199_control"
    outs = {
        rce.build_env(s, out_root, runner_root=tmp_path, base_env={})["OUT"]
        for s in rce.SPECS
    }
    assert len(outs) == len(rce.SPECS)


# --------------------------------------------------------------------------
# resumability
# --------------------------------------------------------------------------


def test_pending_skips_specs_with_existing_results(tmp_path: Path) -> None:
    out_root = tmp_path / "199_control"
    out_root.mkdir()
    rce.result_path(rce.SPECS[0], out_root).write_text("{}", encoding="utf-8")

    todo = rce.pending(list(rce.SPECS[:2]), out_root)
    assert [s.name for s in todo] == [rce.SPECS[1].name]


def test_pending_force_reruns_everything(tmp_path: Path) -> None:
    out_root = tmp_path / "199_control"
    out_root.mkdir()
    rce.result_path(rce.SPECS[0], out_root).write_text("{}", encoding="utf-8")
    assert len(rce.pending(list(rce.SPECS[:2]), out_root, force=True)) == 2


# --------------------------------------------------------------------------
# thin-CSCV opt-in
# --------------------------------------------------------------------------


def test_thin_cscv_spec_is_point_estimate_only() -> None:
    shrink = next(s for s in rce.SPECS if s.name == "shrink")
    assert shrink.verdict_meaningful is False
    assert shrink.env["GRID_PROFILE"] == "stable"  # N=5 < MIN_CSCV_VARIANTS


def test_select_specs_excludes_thin_cscv_by_default() -> None:
    names = [s.name for s in rce.select_specs()]
    assert "shrink" not in names
    assert "baseline" in names and "net_fees" in names


def test_select_specs_allows_thin_cscv_on_opt_in() -> None:
    names = [s.name for s in rce.select_specs(allow_thin_cscv=True)]
    assert "shrink" in names


def test_select_specs_only_filters_by_name() -> None:
    assert [s.name for s in rce.select_specs(only="net_fees")] == ["net_fees"]
    assert rce.select_specs(only="nope") == []


def test_render_plan_flags_point_estimate_only(tmp_path: Path) -> None:
    plan = rce.render_plan(list(rce.SPECS), tmp_path / "out")
    shrink_line = next(ln for ln in plan.splitlines() if ln.startswith("shrink"))
    assert "POINT-ESTIMATE-ONLY" in shrink_line
    assert "GRID_PROFILE=stable" in shrink_line


def test_render_plan_empty() -> None:
    assert rce.render_plan([], Path("/nowhere")) == "nothing to run"


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def test_main_dry_run_executes_nothing(tmp_path: Path, monkeypatch) -> None:
    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("dry-run must not run a gate")

    monkeypatch.setattr(rce, "run_one", _boom)
    rc = rce.main(["--runner-root", str(tmp_path), "--out", str(tmp_path / "o")])
    assert rc == 0


def test_main_returns_2_when_nothing_selected(tmp_path: Path) -> None:
    assert rce.main(["--runner-root", str(tmp_path), "--only", "nope"]) == 2


def test_run_one_copies_result_and_returns_zero(tmp_path: Path) -> None:
    out_root = tmp_path / "199_control"
    _fake_gate(
        tmp_path,
        'printf \'{"both_pass": false}\' > "$OUT/walkforward_gate.json"\n',
    )
    rc = rce.run_one(
        _spec("baseline"),
        out_root,
        tmp_path,
        tmp_path / "aqsp-scheduler-current" / rce.GATE_SCRIPT,
    )
    assert rc == 0
    target = rce.result_path(_spec("baseline"), out_root)
    assert json.loads(target.read_text(encoding="utf-8")) == {"both_pass": False}


def test_run_one_treats_load_guard_skip_as_pending(tmp_path: Path) -> None:
    """A skip exits 0 without a gate file; that is not success."""
    out_root = tmp_path / "199_control"
    _fake_gate(tmp_path, 'echo "load too high, skip"\nexit 0\n')
    rc = rce.run_one(
        _spec("baseline"),
        out_root,
        tmp_path,
        tmp_path / "aqsp-scheduler-current" / rce.GATE_SCRIPT,
    )
    assert rc == 1
    assert not rce.result_path(_spec("baseline"), out_root).exists()


def test_run_one_propagates_failure_exit_code(tmp_path: Path) -> None:
    out_root = tmp_path / "199_control"
    _fake_gate(tmp_path, "exit 7\n")
    rc = rce.run_one(
        _spec("baseline"),
        out_root,
        tmp_path,
        tmp_path / "aqsp-scheduler-current" / rce.GATE_SCRIPT,
    )
    assert rc == 7


def test_main_execute_stops_after_first_failure(tmp_path: Path, monkeypatch) -> None:
    out_root = tmp_path / "199_control"
    _fake_gate(tmp_path, "exit 0\n")
    calls: list[str] = []

    def _fail(spec, *a, **k):
        calls.append(spec.name)
        return 3

    monkeypatch.setattr(rce, "run_one", _fail)
    rc = rce.main(["--execute", "--runner-root", str(tmp_path), "--out", str(out_root)])
    assert rc == 3
    assert len(calls) == 1  # did not start a second 11.5h gate


def test_main_execute_requires_gate_script(tmp_path: Path) -> None:
    rc = rce.main(
        ["--execute", "--runner-root", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert rc == 1


# --------------------------------------------------------------------------
# 旧 release 守卫：OUT 不可覆盖时必须在开跑前拒绝，而不是白跑 11.5h
# --------------------------------------------------------------------------


def test_supports_out_override_detects_default_form(tmp_path: Path) -> None:
    script = _fake_gate(tmp_path, "exit 0\n", out_override=True)
    assert rce.supports_out_override(script) is True


def test_supports_out_override_rejects_hardcoded_form(tmp_path: Path) -> None:
    script = _fake_gate(tmp_path, "exit 0\n", out_override=False)
    script.write_text(
        '#!/usr/bin/env bash\nRUNNER_ROOT=/opt/aqsp-runner\nOUT="$RUNNER_ROOT/gate_run"\n',
        encoding="utf-8",
    )
    assert rce.supports_out_override(script) is False


def test_supports_out_override_ignores_mention_in_comment(tmp_path: Path) -> None:
    """注释里出现 ${OUT:-...} 不算支持 —— 只看真正赋值的那一行。"""
    script = _fake_gate(tmp_path, "exit 0\n", out_override=False)
    script.write_text(
        '#!/usr/bin/env bash\n# 曾经写成 OUT="${OUT:-x}"，现在是硬编码\n'
        'OUT="$RUNNER_ROOT/gate_run"\n',
        encoding="utf-8",
    )
    assert rce.supports_out_override(script) is False


def test_supports_out_override_missing_file(tmp_path: Path) -> None:
    assert rce.supports_out_override(tmp_path / "nope.sh") is False


def test_main_execute_refuses_old_release_before_running(
    tmp_path: Path, monkeypatch
) -> None:
    """旧 release 上必须开跑前就返回 1，且一次 gate 都不启动。"""
    script = _fake_gate(tmp_path, "exit 0\n", out_override=False)
    script.write_text(
        '#!/usr/bin/env bash\nOUT="$RUNNER_ROOT/gate_run"\n', encoding="utf-8"
    )
    started: list[str] = []

    def _spy(spec, *a, **k):  # pragma: no cover - 不该被调用
        started.append(spec.name)
        return 0

    monkeypatch.setattr(rce, "run_one", _spy)
    rc = rce.main(
        ["--execute", "--runner-root", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert rc == 1
    assert started == []
