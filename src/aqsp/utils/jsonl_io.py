from __future__ import annotations

from contextlib import contextmanager
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    json_line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"

    with open(file_path, "a", encoding="utf-8") as handle:
        _lock_file(handle)
        try:
            handle.write(json_line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            _unlock_file(handle)


def atomic_write_text(path: str | Path, text: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=f".{file_path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, file_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


@contextmanager
def advisory_lock(path: str | Path) -> Iterator[None]:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_name(f".{file_path.name}.lock")
    with open(lock_path, "a+", encoding="utf-8") as handle:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _lock_file(handle: Any) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
