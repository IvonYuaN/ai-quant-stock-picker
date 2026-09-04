"""Tests for ``aqsp.utils.concurrency.DaemonWorkerPool``.

核心回归目标（2026-09-04 生产事故）：
标准 ``ThreadPoolExecutor`` 的工作线程是非 daemon 且会被登记进
``_threads_queues``，解释器退出时被 ``_python_exit`` 无限 join ——
只要一个抓取线程卡住，进程就退不出去，被外层 ``timeout`` 杀成 124。
``test_pool_does_not_block_interpreter_exit_when_task_hangs`` 是对这条的端到端守卫。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import FIRST_COMPLETED, wait
from concurrent.futures import thread as _futures_thread
from pathlib import Path

import pytest

from aqsp.utils.concurrency import DaemonWorkerPool

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def test_pool_returns_result_when_task_succeeds() -> None:
    pool = DaemonWorkerPool(max_workers=2)
    future = pool.submit(lambda: 21 * 2)
    assert future.result(timeout=5) == 42
    pool.shutdown(wait=False, cancel_futures=True)


def test_pool_propagates_exception_when_task_raises() -> None:
    def _boom() -> None:
        raise ValueError("kaboom")

    pool = DaemonWorkerPool(max_workers=1)
    future = pool.submit(_boom)
    with pytest.raises(ValueError, match="kaboom"):
        future.result(timeout=5)
    pool.shutdown(wait=False, cancel_futures=True)


def test_pool_respects_max_workers_when_many_tasks_submitted() -> None:
    max_workers = 2
    lock = threading.Lock()
    state = {"running": 0, "peak": 0}

    def _task() -> None:
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.05)
        with lock:
            state["running"] -= 1

    pool = DaemonWorkerPool(max_workers=max_workers)
    futures = [pool.submit(_task) for _ in range(6)]
    for future in futures:
        future.result(timeout=10)
    assert state["peak"] <= max_workers
    pool.shutdown(wait=False, cancel_futures=True)


def test_pool_skips_task_when_future_cancelled_before_start() -> None:
    started = threading.Event()
    gate = threading.Event()

    def _blocker() -> None:
        started.set()
        gate.wait(5)

    def _never() -> str:
        raise AssertionError("cancelled task must not run")

    pool = DaemonWorkerPool(max_workers=1)
    blocker = pool.submit(_blocker)
    assert started.wait(5), "先占住唯一的并发名额"
    victim = pool.submit(_never)
    assert victim.cancel() is True
    gate.set()
    blocker.result(timeout=5)
    assert victim.cancelled()
    pool.shutdown(wait=False, cancel_futures=True)


def test_pool_rejects_submit_when_already_shutdown() -> None:
    pool = DaemonWorkerPool(max_workers=1)
    pool.shutdown(wait=False, cancel_futures=True)
    with pytest.raises(RuntimeError, match="after shutdown"):
        pool.submit(lambda: 1)


def test_pool_cancels_pending_futures_when_shutdown_with_cancel_futures() -> None:
    started = threading.Event()
    gate = threading.Event()

    def _blocker() -> None:
        started.set()
        gate.wait(5)

    pool = DaemonWorkerPool(max_workers=1)
    blocker = pool.submit(_blocker)
    assert started.wait(5)
    victim = pool.submit(lambda: "never")
    pool.shutdown(wait=False, cancel_futures=True)
    gate.set()
    blocker.result(timeout=5)
    assert victim.cancelled()


def test_pool_futures_work_with_wait_first_completed() -> None:
    gate = threading.Event()
    pool = DaemonWorkerPool(max_workers=4)
    slow = pool.submit(lambda: gate.wait(5))
    fast = pool.submit(lambda: "fast")
    done, pending = wait({slow, fast}, timeout=5, return_when=FIRST_COMPLETED)
    assert fast in done
    gate.set()
    pool.shutdown(wait=False, cancel_futures=True)
    # wait 只保证至少一个完成；pending 可能为空也可能含 slow
    assert slow in done or slow in pending


def test_pool_rejects_invalid_max_workers_when_zero() -> None:
    with pytest.raises(ValueError, match="max_workers"):
        DaemonWorkerPool(max_workers=0)


def test_pool_threads_are_daemon_and_not_registry_when_running() -> None:
    """机制层守卫：daemon + 不进 _threads_queues，两者缺一都会被 atexit join。"""
    gate = threading.Event()
    pool = DaemonWorkerPool(max_workers=1, thread_name_prefix="aqsp-test-pool")
    future = pool.submit(lambda: gate.wait(5))

    deadline = time.monotonic() + 5
    threads: list[threading.Thread] = []
    while time.monotonic() < deadline:
        threads = [t for t in threading.enumerate() if "aqsp-test-pool" in t.name]
        if threads:
            break
        time.sleep(0.01)

    try:
        assert threads, "未观察到池子创建的工作线程"
        for thread in threads:
            assert thread.daemon is True, "工作线程必须是 daemon"
            assert (
                thread not in _futures_thread._threads_queues
            ), "工作线程不得登记进 _threads_queues（否则 _python_exit 会 join 它）"
    finally:
        gate.set()
        future.result(timeout=5)
        pool.shutdown(wait=False, cancel_futures=True)


def test_pool_does_not_block_interpreter_exit_when_task_hangs() -> None:
    """端到端守卫：卡死的任务不能拖住解释器退出（生产 1200s 超时的根因）。"""
    script = textwrap.dedent(
        """
        import sys, time
        sys.path.insert(0, {src!r})
        from aqsp.utils.concurrency import DaemonWorkerPool

        pool = DaemonWorkerPool(max_workers=2, thread_name_prefix="aqsp-hang")
        stuck = pool.submit(time.sleep, 120)
        ok = pool.submit(lambda: 1)
        assert ok.result(timeout=10) == 1
        pool.shutdown(wait=False, cancel_futures=True)
        print("exiting", flush=True)
        """
    ).format(src=str(SRC_DIR))

    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - 固定参数的解释器调用
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert completed.returncode == 0, completed.stderr
    assert "exiting" in completed.stdout
    assert elapsed < 20, (
        f"解释器退出被卡死的任务拖住了 {elapsed:.1f}s；"
        "DaemonWorkerPool 必须让进程立即退出"
    )
