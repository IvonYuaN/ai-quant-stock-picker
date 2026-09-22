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

    with advisory_lock(file_path):
        with open(file_path, "a", encoding="utf-8") as handle:
            handle.write(json_line)
            handle.flush()
            os.fsync(handle.fileno())


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
        # mkstemp 默认建 0600：该模式会把文件的 **ACL mask 压成 `---`**，
        # 于是所有靠 ACL 授权的读者（如以 aqsp-vibe 运行的 API）都读不到。
        # 历史事故：/opt/aqsp/data 下 21 个文件（predictions.jsonl、walkforward_gate.json
        # 等）因此对 API 不可读，绩效页直接报 Errno 13。统一抬到 0640
        # （owner rw / group r / ACL mask r--），与 web/home_snapshot.py 的做法一致。
        os.chmod(tmp_path, 0o640)
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
