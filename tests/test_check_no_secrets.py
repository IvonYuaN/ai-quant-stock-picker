from __future__ import annotations

from pathlib import Path

from scripts.check_no_secrets import find_non_empty_secret_assignments, iter_files


def test_secret_scan_allows_empty_env_example_placeholders() -> None:
    text = "\n".join(
        [
            "GITHUB_TOKEN=",
            "GITEE_TOKEN=",
            "TUSHARE_TOKEN=",
            "TELEGRAM_BOT_TOKEN=",
        ]
    )

    assert find_non_empty_secret_assignments(Path(".env.example"), text) == []


def test_secret_scan_blocks_non_empty_env_example_values() -> None:
    text = "GITHUB_TOKEN=real-token-value"

    assert find_non_empty_secret_assignments(Path(".env.example"), text) == [
        ".env.example:1: non-empty GITHUB_TOKEN"
    ]


def test_secret_scan_ignores_source_code_env_reads() -> None:
    text = 'password = os.environ.get("AQSP_SMTP_PASSWORD")'

    assert find_non_empty_secret_assignments(Path("src/aqsp/example.py"), text) == []


def test_secret_scan_allows_github_actions_secret_references() -> None:
    text = "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}"

    assert (
        find_non_empty_secret_assignments(Path(".github/workflows/monitor.yml"), text)
        == []
    )


def test_secret_scan_skips_private_and_runtime_paths_when_collecting_files(
    tmp_path: Path,
) -> None:
    files = [
        tmp_path / "src" / "aqsp" / "example.py",
        tmp_path / "private_data" / "token.txt",
        tmp_path / "A股量化分析数据" / "raw.csv",
        tmp_path / "data" / "archive" / "old.jsonl",
        tmp_path / "data" / "predictions.jsonl",
        tmp_path / "data" / "open_source_research.jsonl",
        tmp_path / "outputs" / "dashboard.html",
    ]
    for file in files:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("placeholder", encoding="utf-8")

    collected = {path.relative_to(tmp_path).as_posix() for path in iter_files(tmp_path)}

    assert "src/aqsp/example.py" in collected
    assert "data/open_source_research.jsonl" in collected
    assert "private_data/token.txt" not in collected
    assert "A股量化分析数据/raw.csv" not in collected
    assert "data/archive/old.jsonl" not in collected
    assert "data/predictions.jsonl" not in collected
    assert "outputs/dashboard.html" not in collected
