"""服务端患者会话目录；LangGraph checkpoint 仍负责 Agent 恢复。"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PatientSessionStore:
    """保存会话列表需要的摘要和聊天记录。"""

    def __init__(self) -> None:
        storage = Path(os.getenv("AGENT_STORAGE_PATH", "./storage"))
        storage.mkdir(parents=True, exist_ok=True)
        self.database = storage / "patient-sessions.sqlite3"
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS patient_sessions (
                    thread_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    agent_state TEXT NOT NULL,
                    pending_interrupts TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_patient_sessions_updated "
                "ON patient_sessions(patient_id, updated_at DESC)"
            )

    def save(
        self,
        thread_id: str,
        patient_id: str,
        messages: list[dict[str, Any]],
        *,
        status: str,
        agent_state: dict[str, Any] | None = None,
        pending_interrupts: list[dict[str, Any]] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        title = next((item["content"].strip() for item in messages if item["role"] == "user" and item["content"].strip()), "新会话")
        title = title[:80]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO patient_sessions
                   (thread_id, patient_id, title, messages, agent_state, pending_interrupts,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                     patient_id=excluded.patient_id, title=excluded.title,
                     messages=excluded.messages, agent_state=excluded.agent_state,
                     pending_interrupts=excluded.pending_interrupts,
                     status=excluded.status, updated_at=excluded.updated_at""",
                (thread_id, patient_id, title, _dump(messages), _dump(agent_state or {}),
                 _dump(pending_interrupts or []), status, now, now),
            )

    def list_for_patient(self, patient_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT thread_id, title, status, created_at, updated_at "
                "FROM patient_sessions WHERE patient_id=? ORDER BY updated_at DESC",
                (patient_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, thread_id: str, patient_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM patient_sessions WHERE thread_id=? AND patient_id=?",
                (thread_id, patient_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("messages", "agent_state", "pending_interrupts"):
            result[key] = json.loads(result[key])
        return result

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
