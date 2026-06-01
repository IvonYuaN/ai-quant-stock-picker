from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.download_tdx_vipdoc import is_zip_file, prepare_vipdoc, safe_extract


def test_download_tdx_rejects_html_challenge_when_not_zip(tmp_path: Path) -> None:
    fake_zip = tmp_path / "hsjday.zip"
    fake_zip.write_text("<script>location.href='challenge'</script>", encoding="utf-8")

    assert is_zip_file(fake_zip) is False
    with pytest.raises(ValueError, match="不是有效 zip"):
        prepare_vipdoc(fake_zip, tmp_path / "tdx")


def test_download_tdx_extracts_valid_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "hsjday.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("vipdoc/sh/lday/sh600519.day", b"")

    vipdoc = prepare_vipdoc(zip_path, tmp_path / "tdx")

    assert vipdoc == tmp_path / "tdx" / "vipdoc"
    assert (vipdoc / "sh" / "lday" / "sh600519.day").exists()


def test_download_tdx_normalizes_windows_zip_paths(tmp_path: Path) -> None:
    zip_path = tmp_path / "hsjday.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("sh\\lday\\sh600519.day", b"")

    vipdoc = prepare_vipdoc(zip_path, tmp_path / "tdx")

    assert vipdoc == tmp_path / "tdx"
    assert (vipdoc / "sh" / "lday" / "sh600519.day").exists()


def test_download_tdx_safe_extract_blocks_path_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", "bad")

    with pytest.raises(ValueError, match="可疑路径"):
        safe_extract(zip_path, tmp_path / "tdx")
