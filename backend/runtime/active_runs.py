"""记录运行中的 SSE 请求及客户端取消状态。"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any


@dataclass
class ActiveRun:
    thread_id: str
    patient_id: str
    future: Future[Any] | None
    events: queue.Queue


class ActiveRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, ActiveRun] = {}
        self._interrupted_threads: set[str] = set()
        self._patients_blocked_for_deletion: set[str] = set()
        self._patients_being_deleted: set[str] = set()

    def reserve(self, run_id: str, thread_id: str, patient_id: str, events: queue.Queue) -> bool:
        with self._lock:
            if patient_id in self._patients_blocked_for_deletion:
                return False
            if run_id in self._runs or any(run.thread_id == thread_id for run in self._runs.values()):
                return False
            self._runs[run_id] = ActiveRun(thread_id, patient_id, None, events)
            return True

    def request_patient_deletion(self, patient_id: str) -> None:
        """一收到删除请求就挡住新运行，让新数据不混进待清理范围。"""
        with self._lock:
            self._patients_blocked_for_deletion.add(patient_id)

    def cancel_patient_deletion_request(self, patient_id: str) -> None:
        """删除任务未能写入持久队列时，撤回进程内的请求屏障。"""
        with self._lock:
            if patient_id not in self._patients_being_deleted:
                self._patients_blocked_for_deletion.discard(patient_id)

    def begin_patient_deletion(self, patient_id: str) -> bool:
        """没有该患者的运行时，原子地挡住新请求并开始清理。"""
        with self._lock:
            if patient_id in self._patients_being_deleted:
                return False
            if any(run.patient_id == patient_id for run in self._runs.values()):
                return False
            self._patients_blocked_for_deletion.add(patient_id)
            self._patients_being_deleted.add(patient_id)
            return True

    def finish_patient_deletion(self, patient_id: str, succeeded: bool) -> None:
        with self._lock:
            self._patients_being_deleted.discard(patient_id)
            if succeeded:
                self._patients_blocked_for_deletion.discard(patient_id)

    def attach(self, run_id: str, future: Future[Any]) -> None:
        with self._lock:
            active = self._runs[run_id]
            active.future = future

    def finish(self, run_id: str) -> None:
        future: Future[Any] | None = None
        with self._lock:
            active = self._runs.get(run_id)
            if active is None:
                return
            if active.future is not None and not active.future.done():
                future = active.future
            else:
                self._runs.pop(run_id, None)
        if future is not None:
            # 客户端断开不代表 Agent 停止；等后台任务真结束后再释放患者清理屏障。
            future.add_done_callback(lambda _completed: self.finish(run_id))

    def cancel(self, run_id: str, thread_id: str) -> bool:
        with self._lock:
            active = self._runs.get(run_id)
            if active is None or active.thread_id != thread_id or active.future is None:
                return False
            if not active.future.cancel():
                return False
            self._interrupted_threads.add(thread_id)
            active.events.put(("cancelled", None))
            return True

    def take_interruption(self, thread_id: str) -> bool:
        with self._lock:
            if thread_id not in self._interrupted_threads:
                return False
            self._interrupted_threads.remove(thread_id)
            return True


active_runs = ActiveRunRegistry()
