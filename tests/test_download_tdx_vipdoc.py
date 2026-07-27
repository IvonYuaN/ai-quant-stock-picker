from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts import download_tdx_vipdoc
from scripts.download_tdx_vipdoc import (
    download_zip,
    is_zip_file,
    prepare_vipdoc,
    safe_extract,
)


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


def test_download_tdx_uses_proxy_safe_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "hsjday.zip"
    calls: list[tuple[str, bool, int, dict[str, str]]] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, *, chunk_size: int):
            assert chunk_size == 1024 * 1024
            yield b"PK\x03\x04payload"

    class Session:
        trust_env = False

        def get(
            self,
            url: str,
            *,
            stream: bool,
            timeout: int,
            headers: dict[str, str],
        ) -> Response:
            calls.append((url, stream, timeout, headers))
            return Response()

    monkeypatch.setattr(
        download_tdx_vipdoc,
        "requests_session_without_implicit_proxy",
        Session,
    )

    download_zip("https://example.test/hsjday.zip", output, timeout=7)

    assert output.read_bytes().startswith(b"PK\x03\x04")
    assert calls == [
        (
            "https://example.test/hsjday.zip",
            True,
            7,
            download_tdx_vipdoc.TDX_DOWNLOAD_HEADERS,
        )
    ]
