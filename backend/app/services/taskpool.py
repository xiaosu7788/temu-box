"""Shared bounded task pool with dynamic concurrency and queue limits."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


class QueueFullError(RuntimeError):
    pass


class TaskPool:
    """固定线程数 + 动态信号量实现运行期可调的并发上限。

    并发数通过 apply() 热更新：运行中的任务不会被中断，
    新提交的任务立即按新并发数排队执行。
    """

    def __init__(self, name: str, workers: int, queue_limit: int, max_threads: int = 16) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_threads), thread_name_prefix=name)
        self._lock = threading.Lock()
        self._workers = max(1, workers)
        self._queue_limit = max(1, queue_limit)
        self._sem = threading.BoundedSemaphore(self._workers)
        self._queued = 0
        self._active = 0

    def apply(self, workers: int, queue_limit: int) -> None:
        with self._lock:
            self._workers = max(1, workers)
            self._queue_limit = max(1, queue_limit)
            self._sem = threading.BoundedSemaphore(self._workers)

    def stats(self) -> dict:
        with self._lock:
            return {
                "workers": self._workers,
                "queue_limit": self._queue_limit,
                "queued": self._queued,
                "active": self._active,
            }

    def full(self) -> bool:
        with self._lock:
            return self._queued >= self._queue_limit

    def submit(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if self._queued >= self._queue_limit:
                raise QueueFullError(f"任务队列已满（上限 {self._queue_limit} 个），请稍后再试")
            self._queued += 1

        def wrapper() -> None:
            # 一次性捕获信号量引用，避免热更新后 acquire/release 落在不同对象上
            sem = self._sem
            try:
                sem.acquire()
                try:
                    with self._lock:
                        self._active += 1
                    fn()
                finally:
                    with self._lock:
                        self._active -= 1
                    sem.release()
            finally:
                with self._lock:
                    self._queued -= 1

        self._executor.submit(wrapper)
