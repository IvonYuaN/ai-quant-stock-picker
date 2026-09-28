from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

import requests

TDX_HSJDAY_URL = "https://data.tdx.com.cn/vipdoc/hsjday.zip"
TDX_VIPDATA_PAGE = "https://www.tdx.com.cn/article/vipdata.html"
ZIP_MAGIC = b"PK\x03\x04"
DEFAULT_ROOT = Path("private_data/tdx")
TDX_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    ),
    "Referer": TDX_VIPDATA_PAGE,
}


def is_zip_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < len(ZIP_MAGIC):
        return False
    with path.open("rb") as fh:
        return fh.read(len(ZIP_MAGIC)) == ZIP_MAGIC


def safe_extract(zip_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            member_name = member.filename.replace("\\", "/")
            target = (dest / member_name).resolve()
            if not str(target).startswith(str(dest) + "/") and target != dest:
                raise ValueError(f"拒绝解压可疑路径: {member.filename}")
            if member.is_dir() or member_name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def download_zip(url: str, output: Path, timeout: int = 60) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_suffix(output.suffix + ".part")
    with requests.get(
        url, stream=True, timeout=timeout, headers=TDX_DOWNLOAD_HEADERS
    ) as response:
        response.raise_for_status()
        first_chunk = True
        with tmp_output.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    if first_chunk and not chunk.startswith(ZIP_MAGIC):
                        tmp_output.unlink(missing_ok=True)
                        raise ValueError(
                            "官网返回内容不是 zip，可能触发了反爬 HTML/JS 页面"
                        )
                    first_chunk = False
                    fh.write(chunk)
    tmp_output.replace(output)


def prepare_vipdoc(zip_path: Path, extract_root: Path) -> Path:
    if not is_zip_file(zip_path):
        raise ValueError(f"{zip_path} 不是有效 zip。官网可能返回了反爬 HTML/JS 页面。")
    safe_extract(zip_path, extract_root)
    vipdoc = extract_root / "vipdoc"
    if not vipdoc.exists():
        vipdoc = extract_root
    return vipdoc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="下载并解压通达信官方沪深京日线完整包到 private_data。"
    )
    parser.add_argument("--url", default=TDX_HSJDAY_URL)
    parser.add_argument(
        "--zip", dest="zip_path", default=str(DEFAULT_ROOT / "hsjday.zip")
    )
    parser.add_argument("--extract-root", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--use-existing",
        action="store_true",
        help="不联网，直接校验并解压 --zip 指定的本地文件。",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="解压前删除已有 vipdoc 目录。",
    )
    args = parser.parse_args()

    zip_path = Path(args.zip_path)
    extract_root = Path(args.extract_root)
    if args.replace:
        shutil.rmtree(extract_root / "vipdoc", ignore_errors=True)

    try:
        if not args.use_existing:
            print(f"downloading={args.url}")
            download_zip(args.url, zip_path)
        vipdoc = prepare_vipdoc(zip_path, extract_root)
    except (requests.RequestException, ValueError) as exc:
        print(f"error={exc}")
        print(f"manual_download={TDX_VIPDATA_PAGE}")
        print(f"save_zip_to={zip_path}")
        print(
            "curl_fallback=curl -L --fail -A 'Mozilla/5.0' "
            f"-e '{TDX_VIPDATA_PAGE}' '{args.url}' -o '{zip_path}'"
        )
        print(
            "then_run=python3 scripts/download_tdx_vipdoc.py "
            f"--use-existing --zip {zip_path}"
        )
        return 1

    print(f"zip={zip_path}")
    print(f"vipdoc={vipdoc}")
    print(f"env=AQSP_TDX_VIPDOC_PATH={vipdoc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
