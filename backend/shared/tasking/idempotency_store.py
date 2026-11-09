"""用 SQLite 缓存同一 taskKey 的主 Agent 回复。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


class IdempotencyConflict(ValueError):
    """同一 taskKey 被不同请求内容重复使用。"""


class IdempotencyStore:
    def __init__(self) -> None:
        self.database = Path(os.getenv("AGENT_STORAGE_PATH", "./storage")) / "invocations.sqlite3"
        self._lock_guard = threading.Lock()
        self._task_locks: dict[str, tuple[threading.Lock, int]] = {}

    def run_once(
        self,
        task_key: str,
        role: str,
        message: str,
        context: dict,
        operation: Callable[[], str],
    ) -> tuple[str, bool]:
        """串行检查并保存结果，重复请求直接返回第一次的回复。"""
        self.database.parent.mkdir(parents=True, exist_ok=True)
        request_json = json.dumps(
            {"role": role, "message": message, "context": context},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()

        with self._task_lock(task_key):
            with self._connection() as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS agent_invocations (
                        task_key TEXT PRIMARY KEY,
                        request_hash TEXT NOT NULL,
                        reply TEXT NOT NULL
                    )"""
                )
                existing = connection.execute(
                    "SELECT request_hash, reply FROM agent_invocations WHERE task_key = ?",
                    (task_key,),
                ).fetchone()
                if existing:
                    if existing[0] != request_hash:
                        raise IdempotencyConflict(
                            "这个 taskKey 已经用于另一条请求，请为新任务换一个 taskKey。"
                        )
                    return existing[1], True

            reply = operation()
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO agent_invocations (task_key, request_hash, reply) VALUES (?, ?, ?)",
                    (task_key, request_hash, reply),
                )
            return reply, False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _task_lock(self, task_key: str) -> Iterator[None]:
        """只锁同一个任务 key，不让不同用户的问题排队等模型。"""
        with self._lock_guard:
            lock, users = self._task_locks.get(task_key, (threading.Lock(), 0))
            self._task_locks[task_key] = (lock, users + 1)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._lock_guard:
                current_lock, users = self._task_locks[task_key]
                if users == 1:
                    del self._task_locks[task_key]
                else:
                    self._task_locks[task_key] = (current_lock, users - 1)
