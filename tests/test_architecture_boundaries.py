from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

AKSHARE_ALLOWED_PREFIXES = {
    "src/aqsp/data/",
}
AKSHARE_ALLOWED_FILES = {
    "src/aqsp/data/adjust.py",
    "src/aqsp/data/news_source.py",
    "src/aqsp/data/cn/northbound.py",
    "src/aqsp/data/cn/margin_trading.py",
}
CONCRETE_SOURCE_MODULES = {
    "aqsp.data.akshare_source",
    "aqsp.data.baostock_source",
    "aqsp.data.eastmoney_source",
    "aqsp.data.efinance_source",
    "aqsp.data.mootdx_source",
    "aqsp.data.sina_source",
    "aqsp.data.sqlite_db_source",
    "aqsp.data.tdx_vipdoc_source",
    "aqsp.data.tencent_source",
}
DOMAIN_LAYER_PREFIXES = (
    "src/aqsp/news/",
    "src/aqsp/portfolio/",
    "src/aqsp/risk/",
    "src/aqsp/services/",
    "src/aqsp/strategies/",
)
PUBLIC_DATA_SOURCE_FILES = {
    "src/aqsp/data/akshare_source.py",
    "src/aqsp/data/baostock_source.py",
    "src/aqsp/data/eastmoney_source.py",
    "src/aqsp/data/efinance_source.py",
    "src/aqsp/data/mootdx_source.py",
    "src/aqsp/data/sina_source.py",
    "src/aqsp/data/sqlite_db_source.py",
    "src/aqsp/data/tdx_vipdoc_source.py",
    "src/aqsp/data/tencent_source.py",
}
ENTRYPOINT_FILES = (
    "src/aqsp/cli.py",
    "scripts/daily_pipeline.py",
)


def _imports_akshare(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "akshare" or alias.name.startswith("akshare."):
                    return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "akshare"
        ):
            return True
    return False


def _imports_concrete_source_module(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in CONCRETE_SOURCE_MODULES:
                    return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in CONCRETE_SOURCE_MODULES:
                return True
    return False


def _top_level_imports_concrete_source_module(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in CONCRETE_SOURCE_MODULES:
                    return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in CONCRETE_SOURCE_MODULES:
                return True
    return False


def test_akshare_imports_stay_in_data_adapter_layer() -> None:
    violations: list[str] = []
    for path in (PROJECT_ROOT / "src" / "aqsp").rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if not _imports_akshare(path):
            continue
        if rel in AKSHARE_ALLOWED_FILES:
            continue
        if any(rel.startswith(prefix) for prefix in AKSHARE_ALLOWED_PREFIXES):
            continue
        violations.append(rel)

    assert violations == []


def test_domain_layers_do_not_import_concrete_data_adapters() -> None:
    violations: list[str] = []
    for path in (PROJECT_ROOT / "src" / "aqsp").rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if not rel.startswith(DOMAIN_LAYER_PREFIXES):
            continue
        if _imports_concrete_source_module(path):
            violations.append(rel)

    assert violations == []


def test_cli_does_not_top_level_import_concrete_data_adapters() -> None:
    assert (
        _top_level_imports_concrete_source_module(PROJECT_ROOT / "src/aqsp/cli.py")
        is False
    )


def test_entrypoints_use_source_factory_instead_of_concrete_adapter_constructors() -> (
    None
):
    forbidden = [
        "AkshareSource(",
        "BaostockSource(",
        "EastmoneySource(",
        "EfinanceSource(",
        "MootdxSource(",
        "SinaSource(",
        "SqliteDbSource(",
        "TdxVipdocSource(",
        "TencentSource(",
        "aqsp.data.akshare_source",
        "aqsp.data.baostock_source",
        "aqsp.data.eastmoney_source",
        "aqsp.data.efinance_source",
        "aqsp.data.mootdx_source",
        "aqsp.data.sina_source",
        "aqsp.data.sqlite_db_source",
        "aqsp.data.tdx_vipdoc_source",
        "aqsp.data.tencent_source",
        "fetch_akshare",
    ]
    violations: list[str] = []
    for rel in ENTRYPOINT_FILES:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        violations.extend(f"{rel}:{item}" for item in forbidden if item in text)
    assert violations == []


def test_public_data_sources_fail_loud_on_empty_fetch_results() -> None:
    violations: list[str] = []
    for rel in sorted(PUBLIC_DATA_SOURCE_FILES):
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        if "require_non_empty_fetch_result(" not in text:
            violations.append(rel)

    assert violations == []


def test_source_factory_sqlite_resolver_prefers_raw_default(
    tmp_path: Path, monkeypatch
) -> None:
    from aqsp.data.source_factory import resolve_sqlite_db_path

    market_dir = tmp_path / "A股量化分析数据"
    market_dir.mkdir()
    raw_db = market_dir / "astocks_raw.db"
    qfq_db = market_dir / "astocks_qfq.db"
    raw_db.write_text("", encoding="utf-8")
    qfq_db.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AQSP_SQLITE_DB_PATH", raising=False)

    assert Path(resolve_sqlite_db_path() or "").resolve() == raw_db
