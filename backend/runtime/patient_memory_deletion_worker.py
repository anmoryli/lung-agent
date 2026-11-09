"""后台删除患者的会话、LangGraph checkpoint 和记忆记录。"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from backend.integrations.memory.vector_store import PatientMemoryVectorStore
from backend.runtime.active_runs import ActiveRunRegistry
from backend.shared.sessions.session_store import PatientSessionStore


class PatientMemoryDeletionWorker:
    """持久队列驱动的清理线程；等患者当前请求落盘后再统一删除。"""

    def __init__(
        self,
        memory: PatientMemoryVectorStore,
        sessions: PatientSessionStore,
        checkpoint_database: Path,
        active_runs: ActiveRunRegistry,
        interval_seconds: int = 3,
    ) -> None:
        self.memory = memory
        self.sessions = sessions
        self.checkpoint_database = checkpoint_database
        self.active_runs = active_runs
        self.interval_seconds = max(1, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.memory.recover_patient_deletion_jobs()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="patient-memory-deleter", daemon=False
        )
        self._thread.start()
        logging.info("患者数据删除 worker 已启动，队列轮询间隔 %s 秒", self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=35)

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.memory.claim_patient_deletion_job()
            if job is None:
                self._stop.wait(self.interval_seconds)
                continue

            patient_id = job["patient_id"]
            if not self.active_runs.begin_patient_deletion(patient_id):
                self.memory.defer_patient_deletion(job, self.interval_seconds)
                self._stop.wait(self.interval_seconds)
                continue

            succeeded = False
            try:
                thread_ids = self.sessions.thread_ids_for_patient(patient_id)
                self._delete_patient_checkpoints(thread_ids)
                self.sessions.delete_patient(patient_id)
                self.memory.finish_patient_deletion(patient_id)
                succeeded = True
                logging.info("患者数据清理完成 patient=%s", patient_id)
            except Exception as error:
                self.memory.retry_patient_deletion(job, error)
                logging.exception("患者数据清理失败 patient=%s", patient_id)
            finally:
                self.active_runs.finish_patient_deletion(patient_id, succeeded)

    def _delete_patient_checkpoints(self, thread_ids: list[str]) -> None:
        if not thread_ids or not self.checkpoint_database.exists():
            return

        async def delete_threads() -> None:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_database)) as checkpointer:
                for thread_id in thread_ids:
                    await checkpointer.adelete_thread(thread_id)

        asyncio.run(delete_threads())
