"""可退出的并发 fan-out：daemon 工作线程 + 标准 Future。

为什么不用标准 ``ThreadPoolExecutor``（2026-09-04 生产实证）：

CPython 3.9+ 的 ``ThreadPoolExecutor`` 工作线程是**非 daemon** 线程，且会被登记进
``concurrent.futures.thread._threads_queues``。解释器退出时
``concurrent.futures.thread._python_exit`` 会对其中**每一个**线程执行
**无上限的 ``t.join()``**。于是只要有一个抓取线程卡在没有读超时的 socket 上，
**即便主线工作早已全部落盘**，进程也会一直挂到外层 ``timeout`` 把它杀掉。

实测：2026-09-04 09:50 那轮盘中刷新，``intraday_latest.csv`` / ``.md`` /
``intraday_predictions.jsonl`` 在 2 分钟内全部落盘，进程却挂了 18 分钟，
最终被 ``timeout 1200s`` 杀掉（退出码 124、耗时 1256 秒）。现场：
线程 ``futex_wait_queue``、socket fd 仍开着、负载 0.00。

本模块的池子用 **daemon 线程 + 裸 ``concurrent.futures.Future``**：
- 线程由我们自己创建，不会被登记进 ``_threads_queues`` → ``_python_exit`` 不 join；
- 线程是 daemon → ``threading._shutdown`` 也不 join。

因此被放弃的在途抓取不会阻塞解释器退出。这与既有调用点的意图完全一致——
它们本来就已经用 ``shutdown(wait=False, cancel_futures=True)`` 声明
「这些结果不要了」，只是过去仍会被 atexit 的 join 强行拖住。

并发语义沿用 ``max_workers``：**同时运行**的任务数不超过该值（信号量闸门），
但线程是一次性的、不复用。本项目的 fan-out 规模都很小
（分时批次约 13 个、实时数据源约 5 个），一次性建线程足够且更可预测。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


class DaemonWorkerPool:
    """``concurrent.futures.Executor`` 的最小替代：只用 daemon 工作线程。

    只提供本项目 fan-out 用到的子集（``submit`` / ``shutdown``），
    返回的都是标准 ``concurrent.futures.Future``，可直接交给
    ``wait(..., return_when=FIRST_COMPLETED)`` 与 ``as_completed``。
    """

    def __init__(
        self,
        max_workers: int,
        thread_name_prefix: str = "aqsp-daemon",
    ) -> None:
        if max_workers < 1:
            raise ValueError(f"max_workers 必须 >= 1，收到 {max_workers}")
        self._max_workers = int(max_workers)
        self._prefix = thread_name_prefix
        self._gate = threading.Semaphore(self._max_workers)
        self._lock = threading.Lock()
        self._futures: set[Future[Any]] = set()
        self._closed = False
        self._counter = 0

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def submit(
        self,
        fn: Callable[..., _T],
        *args: Any,
        **kwargs: Any,
    ) -> Future[_T]:
        """提交一个任务到 daemon 工作线程。闭池后再提交抛 ``RuntimeError``。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: Future[_T] = Future()
            self._futures.add(future)
            index = self._counter
            self._counter += 1
        thread = threading.Thread(
            target=self._run,
            args=(future, fn, args, kwargs),
            name=f"{self._prefix}_{index}",
            daemon=True,
        )
        thread.start()
        return future

    def _run(
        self,
        future: Future[_T],
        fn: Callable[..., _T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        # 闸门在「开始执行」前获取：并发运行数受 max_workers 约束。
        # 若任务被 cancel，这里直接返回，不执行 fn。
        with self._gate:
            if not future.set_running_or_notify_cancel():
                return
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - 必须原样交给 Future
                future.set_exception(exc)
                return
            future.set_result(result)

    def shutdown(
        self,
        wait: bool = False,  # noqa: FBT001, FBT002 - 与 Executor 接口同形
        cancel_futures: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """关闭池子。``wait`` 恒不阻塞——本池从不 join 工作线程。

        这是与 ``ThreadPoolExecutor.shutdown`` 唯一的有意差异，也正是本池
        存在的理由：被放弃的在途抓取绝不能阻塞解释器退出（见模块文档）。
        """
        with self._lock:
            self._closed = True
            pending = list(self._futures)
            self._futures.clear()
        if cancel_futures:
            for future in pending:
                future.cancel()
