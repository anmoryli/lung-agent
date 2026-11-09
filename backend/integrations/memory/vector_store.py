"""独立保存患者会话向量，和 FHIR、知识文档数据库分开。"""

from __future__ import annotations

import os
import hashlib
import json
import sqlite3
import struct
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MODEL_DIMENSIONS = 512
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class PatientMemoryVectorStore:
    """用现有 BGE 模型和 sqlite-vec 保存、检索患者自己的历史对话。"""

    def __init__(self, database: Path, model_dir: Path) -> None:
        self.database = database
        self.model_dir = model_dir
        self._embedder: Any = None
        self._model_lock = threading.Lock()
        self._schema_lock = threading.Lock()

    def search(
        self, patient_id: str, query: str, limit: int = 5, conversation_id: str = ""
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        query_vector = self._embed(QUERY_INSTRUCTION + query)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT entry_id, text, source, created_at, kind, distance "
                "FROM patient_memory_vectors WHERE embedding MATCH ? AND k = ? "
                "AND patient_id = ? ORDER BY distance",
                (self._pack(query_vector), limit, patient_id),
            ).fetchall()
            matches = [
                {
                    "entry_id": row[0],
                    "text": row[1],
                    "source": row[2],
                    "created_at": row[3],
                    "kind": row[4],
                    "score": round(1 - row[5], 4),
                }
                for row in rows
            ]
            # 保存实际召回结果，调试页可以核对模型本轮看到了什么。
            connection.execute(
                "INSERT INTO patient_memory_retrievals "
                "(patient_id, conversation_id, query, results, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    patient_id,
                    conversation_id,
                    query[:6000],
                    json.dumps(matches, ensure_ascii=False, separators=(",", ":")),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return matches

    def list_patient_entries(self, patient_id: str) -> list[dict[str, Any]]:
        """把患者的记忆和对话归档连同来源信息列出来，供调试页查看。"""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT entry_id, kind, text, source, created_at "
                "FROM patient_memory_vectors WHERE patient_id = ? ORDER BY created_at DESC",
                (patient_id,),
            ).fetchall()
        return [
            {
                "entry_id": row[0],
                "kind": row[1],
                "text": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def list_retrievals(self, patient_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT conversation_id, query, results, created_at "
                "FROM patient_memory_retrievals WHERE patient_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (patient_id, limit),
            ).fetchall()
        return [
            {
                "conversation_id": row[0],
                "query": row[1],
                "results": json.loads(row[2]),
                "created_at": row[3],
            }
            for row in rows
        ]

    def archive(self, patient_id: str, conversation_id: str, task_key: str, side: str, text: str) -> None:
        clean = text.strip()[:6000]
        if not clean:
            return
        entry_id = f"{patient_id}:{conversation_id}:{task_key}:{side}"
        self._upsert(patient_id, entry_id, clean, conversation_id, "archive")

    def remember(self, patient_id: str, text: str, source: str) -> None:
        """保存用户明确要求记住的非临床信息，不再增加同意步骤。"""
        clean = text.strip()[:1000]
        if not clean:
            return
        digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]
        self._upsert(patient_id, f"memory:{patient_id}:{digest}", clean, source, "long_term")

    def _upsert(self, patient_id: str, entry_id: str, text: str, source: str, kind: str) -> None:
        vector = self._pack(self._embed(text))
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT rowid FROM patient_memory_vectors WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            if existing:
                connection.execute("DELETE FROM patient_memory_vectors WHERE rowid = ?", (existing[0],))
            connection.execute(
                "INSERT INTO patient_memory_vectors(embedding, entry_id, patient_id, kind, text, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (vector, entry_id, patient_id, kind, text, source, created_at),
            )

    def delete_patient(self, patient_id: str) -> None:
        with self._connection() as connection:
            row_ids = connection.execute(
                "SELECT rowid FROM patient_memory_vectors WHERE patient_id = ?", (patient_id,)
            ).fetchall()
            connection.executemany(
                "DELETE FROM patient_memory_vectors WHERE rowid = ?",
                row_ids,
            )

    def _embed(self, text: str) -> list[float]:
        with self._model_lock:
            if self._embedder is None:
                from fastembed import TextEmbedding

                self.model_dir.mkdir(parents=True, exist_ok=True)
                self._embedder = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(self.model_dir), threads=2)
            return [float(value) for value in next(self._embedder.embed([text]))]

    @staticmethod
    def _pack(vector: list[float]) -> bytes:
        if len(vector) != MODEL_DIMENSIONS:
            raise ValueError(f"BGE 向量维度应为 {MODEL_DIMENSIONS}，实际是 {len(vector)}。")
        return struct.pack(f"{MODEL_DIMENSIONS}f", *vector)

    @contextmanager
    def _connection(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            import sqlite_vec

            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            with self._schema_lock:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS patient_memory_vectors USING vec0("
                    "entry_id TEXT, patient_id TEXT, kind TEXT, "
                    f"embedding FLOAT[{MODEL_DIMENSIONS}] distance_metric=cosine, "
                    "+text TEXT, +source TEXT, +created_at TEXT)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS patient_memory_retrievals ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "patient_id TEXT NOT NULL, conversation_id TEXT NOT NULL, "
                    "query TEXT NOT NULL, results TEXT NOT NULL, created_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_patient_memory_retrievals_patient "
                    "ON patient_memory_retrievals(patient_id, id DESC)"
                )
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
