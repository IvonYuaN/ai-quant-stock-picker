from __future__ import annotations

from pathlib import Path

import pytest

from aqsp._constitution_check import (
    ConstitutionViolation,
    _check_no_top_level_llm_import,
)


def test_check_no_top_level_llm_import_passes_for_repo() -> None:
    _check_no_top_level_llm_import()


def test_check_no_top_level_llm_import_detects_static_violation(
    monkeypatch, tmp_path: Path
) -> None:
    pkg_dir = tmp_path / "src" / "aqsp"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "bad.py").write_text("import openai\n", encoding="utf-8")

    fake_file = pkg_dir / "_constitution_check.py"
    fake_file.write_text("# placeholder\n", encoding="utf-8")

    monkeypatch.setattr("aqsp._constitution_check.__file__", str(fake_file))

    with pytest.raises(ConstitutionViolation):
        _check_no_top_level_llm_import()
