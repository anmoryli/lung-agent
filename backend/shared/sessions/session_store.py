"""服务端患者会话目录；LangGraph checkpoint 仍负责 Agent 恢复。"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
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
            connection.execute(
                """CREATE TABLE IF NOT EXISTS patient_model_settings (
                    setting_key TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    context_window INTEGER NOT NULL,
                    max_output_tokens INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
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

    def list_manual_handoffs(self) -> list[dict[str, Any]]:
        """给管理端列出自动恢复后仍需人工处理的会话。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT thread_id, patient_id, title, messages, agent_state, updated_at "
                "FROM patient_sessions WHERE status='manual_handoff_required' "
                "ORDER BY updated_at ASC"
            ).fetchall()
        return [
            {
                "thread_id": row["thread_id"],
                "patient_id": row["patient_id"],
                "title": row["title"],
                "messages": json.loads(row["messages"]),
                "handoff": json.loads(row["agent_state"]).get("handoff", {}),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def resolve_manual_handoff(self, thread_id: str, reply: str) -> bool:
        """把人工结论追加回原会话，并原子关闭待处理状态。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT messages, agent_state FROM patient_sessions "
                "WHERE thread_id=? AND status='manual_handoff_required'",
                (thread_id,),
            ).fetchone()
            if row is None:
                return False
            messages = json.loads(row["messages"])
            messages.append({
                "id": f"assistant-{uuid.uuid4().hex}",
                "role": "assistant",
                "content": reply,
            })
            agent_state = json.loads(row["agent_state"])
            handoff = agent_state.get("handoff", {})
            handoff["resolved"] = True
            agent_state = {"activity": "人工已处理", "handoff": handoff}
            cursor = connection.execute(
                "UPDATE patient_sessions SET messages=?, agent_state=?, status='ready', "
                "updated_at=? WHERE thread_id=? AND status='manual_handoff_required'",
                (_dump(messages[-200:]), _dump(agent_state), datetime.now(timezone.utc).isoformat(), thread_id),
            )
        return cursor.rowcount == 1

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

    def delete_patient(self, patient_id: str) -> None:
        """删除患者会话目录；checkpoint 由后台清理任务单独处理。"""
        with self._connect() as connection:
            connection.execute("DELETE FROM patient_sessions WHERE patient_id=?", (patient_id,))

    def thread_ids_for_patient(self, patient_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT thread_id FROM patient_sessions WHERE patient_id=?", (patient_id,)
            ).fetchall()
        return [row[0] for row in rows]

    def get_selected_model(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT model_id, display_name, context_window, max_output_tokens, updated_at "
                "FROM patient_model_settings WHERE setting_key='patient.default'"
            ).fetchone()
        if row is None:
            return None
        selected = dict(row)
        selected["input_budget_tokens"] = selected["context_window"] - selected["max_output_tokens"]
        return selected

    def set_selected_model(self, model: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO patient_model_settings
                   (setting_key, model_id, display_name, context_window, max_output_tokens, updated_at)
                   VALUES ('patient.default', ?, ?, ?, ?, ?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                     model_id=excluded.model_id, display_name=excluded.display_name,
                     context_window=excluded.context_window,
                     max_output_tokens=excluded.max_output_tokens, updated_at=excluded.updated_at""",
                (model["id"], model["name"], model["context_window"], model["max_output_tokens"], now),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
