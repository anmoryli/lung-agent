"""独立保存患者会话向量，和 FHIR、知识文档数据库分开。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from math import exp, log
from pathlib import Path
from typing import Any


MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MODEL_DIMENSIONS = 512
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
MEMORY_HALF_LIFE_DAYS = {
    "allergy": 3650, "medication": 3650, "constraint": 3650,
    "event": 1095, "fact": 1095,
    "service_need": 365, "communication": 365, "preference": 365, "other": 365,
    "episode": 180,
}


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
        if not query.strip() or limit <= 0:
            return []
        query_vector = self._embed(QUERY_INSTRUCTION + query)
        candidate_limit = min(max(limit * 8, limit), 100)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT entry_id, text, source, created_at, kind, distance, embedding "
                "FROM patient_memory_vectors WHERE embedding MATCH ? AND k = ? "
                "AND patient_id = ? AND kind = 'archive' ORDER BY distance",
                (self._pack(query_vector), candidate_limit, patient_id),
            ).fetchall()
            matches = []
            seen_texts: set[str] = set()
            retained = []
            for row in rows:
                # 每次对话都要归档，但同一句话多次出现时，检索上下文只留一份。
                normalized_text = re.sub(r"\s+", " ", row[1]).strip().casefold()
                if row[0].endswith(":user"):
                    # 去掉口语问题里的虚词“的”，例如“最近的一份”和“最近一份”。
                    normalized_text = normalized_text.replace("的", "")
                if normalized_text in seen_texts:
                    continue
                vector = struct.unpack(f"{MODEL_DIMENSIONS}f", row[6])
                if any(
                    self._same_report_answer(row[0], row[1], vector, previous)
                    for previous in retained
                ):
                    continue
                seen_texts.add(normalized_text)
                retained.append({"entry_id": row[0], "text": row[1], "vector": vector})
                matches.append({
                    "entry_id": row[0],
                    "text": row[1],
                    "source": row[2],
                    "created_at": row[3],
                    "kind": row[4],
                    "score": round(1 - row[5], 4),
                })
                if len(matches) == limit:
                    break
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

    @staticmethod
    def _same_report_answer(
        entry_id: str, text: str, vector: tuple[float, ...], previous: dict[str, Any]
    ) -> bool:
        """只合并同一份报告的近似助手答复，数值或风险词不同就保留两条。"""
        if not entry_id.endswith(":assistant") or not previous["entry_id"].endswith(":assistant"):
            return False
        report_ids = {
            value.casefold() for value in re.findall(r"(?:demo-)?report[-_][\w-]+", text, flags=re.IGNORECASE)
        }
        previous_ids = {
            value.casefold()
            for value in re.findall(r"(?:demo-)?report[-_][\w-]+", previous["text"], flags=re.IGNORECASE)
        }
        if not report_ids.intersection(previous_ids):
            return False
        if PatientMemoryVectorStore._number_signature(text) != PatientMemoryVectorStore._number_signature(
            previous["text"]
        ):
            return False

        def polarity(source: str) -> tuple[bool, ...]:
            normalized = source.replace("没法", "无法").replace("不能", "无法").replace("查不到", "无法")
            markers = ("不", "没有", "无法", "未", "异常", "正常", "阳性", "阴性", "升高", "降低", "过敏")
            return tuple(marker in normalized for marker in markers)

        quoted_claims = lambda source: {
            re.sub(r"\s+", " ", value).strip().casefold()
            for value in re.findall(r"[“\"]([^”\"]{8,})[”\"]", source)
        }
        current_claims = quoted_claims(text)
        previous_claims = quoted_claims(previous["text"])
        if current_claims and previous_claims:
            if not current_claims.intersection(previous_claims):
                return False
        elif polarity(text) != polarity(previous["text"]):
            return False
        left = vector
        right = previous["vector"]
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        similarity = dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
        return similarity >= 0.90

    @staticmethod
    def _number_signature(text: str) -> tuple[str, ...]:
        # 年份有时一个答复省略，日期和报告数值仍要一致才合并。
        without_list_numbers = re.sub(r"(?m)^\s*\d+[.、)]\s*", "", text)
        without_year = re.sub(
            r"(?<!\d)(?:19|20)\d{2}(?=(?:\s*年|\s*[-/.]\s*\d{1,2}\s*[-/.]))",
            "",
            without_list_numbers,
        )
        numbers = re.findall(r"\d+(?:\.\d+)?", without_year)
        return tuple(sorted(str(int(value)) if value.isdigit() else value for value in numbers))

    def list_patient_entries(self, patient_id: str) -> list[dict[str, Any]]:
        """把独立的长期记忆和向量归档放在一份列表中供调试页查看。"""
        with self._connection() as connection:
            archive_rows = connection.execute(
                "SELECT entry_id, kind, text, source, created_at "
                "FROM patient_memory_vectors WHERE patient_id = ? AND kind='archive' ORDER BY created_at DESC",
                (patient_id,),
            ).fetchall()
            memory_rows = connection.execute(
                "SELECT memory_id, category, content, importance, confidence, status, source, evidence, "
                "created_at, updated_at, valid_from, valid_to, expires_at, supersedes_id, conflicts_with_id, "
                "protected, weight "
                "FROM patient_long_term_memories WHERE patient_id = ? ORDER BY updated_at DESC",
                (patient_id,),
            ).fetchall()
        archives = [
            {
                "entry_id": row[0],
                "kind": row[1],
                "text": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in archive_rows
        ]
        memories = [
            {
                "entry_id": row[0], "kind": "long_term", "text": row[2],
                "category": row[1], "importance": row[3], "confidence": row[4],
                "status": row[5], "source": row[6], "evidence": row[7],
                "created_at": row[8], "updated_at": row[9], "valid_from": row[10],
                "valid_to": row[11], "expires_at": row[12], "supersedes_id": row[13],
                "conflicts_with_id": row[14], "protected": bool(row[15]), "weight": row[16],
            }
            for row in memory_rows
        ]
        return sorted([*archives, *memories], key=lambda item: item.get("updated_at", item["created_at"]), reverse=True)

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

    def archive(self, patient_id: str, conversation_id: str, task_key: str, side: str, text: str) -> str | None:
        clean = text.strip()[:6000]
        if not clean:
            return None
        entry_id = f"{patient_id}:{conversation_id}:{task_key}:{side}"
        self._upsert(patient_id, entry_id, clean, conversation_id, "archive")
        return entry_id

    def enqueue_extraction(
        self, patient_id: str, conversation_id: str, task_key: str, source_entry_id: str
    ) -> None:
        """把本轮用户原话放入持久队列，后台 worker 再抽取稳定事实。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO patient_memory_extraction_jobs
                   (patient_id, conversation_id, task_key, source_entry_id, status, attempts,
                    created_at, run_after)
                   VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)""",
                (patient_id, conversation_id, task_key, source_entry_id, now, now),
            )

    def recover_extraction_jobs(self) -> None:
        """服务重启时把上次处理中断的任务放回队列。"""
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_memory_extraction_jobs SET status='pending', started_at=NULL "
                "WHERE status='processing'"
            )

    def claim_extraction_job(self) -> dict[str, Any] | None:
        """领取一条到期任务，数据库写锁保证同一时刻只有一个 worker 领取。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, patient_id, conversation_id, task_key, source_entry_id, attempts "
                "FROM patient_memory_extraction_jobs WHERE status='pending' AND run_after<=? "
                "ORDER BY id LIMIT 1", (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE patient_memory_extraction_jobs SET status='processing', started_at=? WHERE id=?",
                (now, row[0]),
            )
        return {
            "id": row[0], "patient_id": row[1], "conversation_id": row[2],
            "task_key": row[3], "source_entry_id": row[4], "attempts": row[5],
        }

    def get_archive_text(self, entry_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT text FROM patient_memory_vectors WHERE entry_id=? AND kind='archive'", (entry_id,)
            ).fetchone()
        return row[0] if row else None

    def active_long_term_memories(self, patient_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT memory_id, category, content, importance, confidence, protected "
                "FROM patient_long_term_memories WHERE patient_id=? AND status='active' "
                "ORDER BY protected DESC, importance DESC, updated_at DESC LIMIT ?",
                (patient_id, limit),
            ).fetchall()
        return [
            {"memory_id": row[0], "category": row[1], "content": row[2],
             "importance": row[3], "confidence": row[4], "protected": bool(row[5])}
            for row in rows
        ]

    def finish_extraction_job(self, job_id: int, memories_written: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_memory_extraction_jobs SET status='done', finished_at=?, "
                "memories_written=?, last_error=NULL WHERE id=?",
                (now, memories_written, job_id),
            )

    def retry_extraction_job(self, job: dict[str, Any], error: Exception) -> None:
        attempts = int(job["attempts"]) + 1
        status = "failed" if attempts >= 5 else "pending"
        delay = min(3600, 15 * (2 ** (attempts - 1)))
        run_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_memory_extraction_jobs SET status=?, attempts=?, run_after=?, "
                "started_at=NULL, last_error=? WHERE id=?",
                (status, attempts, run_after.isoformat(), str(error)[:500], job["id"]),
            )

    def extraction_status(self, patient_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            counts = connection.execute(
                "SELECT status, COUNT(*) FROM patient_memory_extraction_jobs "
                "WHERE patient_id=? GROUP BY status", (patient_id,),
            ).fetchall()
            latest = connection.execute(
                "SELECT status, created_at, finished_at, memories_written, last_error "
                "FROM patient_memory_extraction_jobs WHERE patient_id=? ORDER BY id DESC LIMIT 1",
                (patient_id,),
            ).fetchone()
        latest_job = (
            {
                "status": latest[0], "created_at": latest[1], "finished_at": latest[2],
                "memories_written": latest[3], "last_error": latest[4],
            }
            if latest else None
        )
        return {"counts": {row[0]: row[1] for row in counts}, "latest": latest_job}

    def request_patient_deletion(self, patient_id: str) -> None:
        """先关掉记忆写入，再把完整删除交给后台队列。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO patient_memory_preferences(patient_id, enabled, updated_at)
                   VALUES (?, 0, ?)
                   ON CONFLICT(patient_id) DO UPDATE SET enabled=0, updated_at=excluded.updated_at""",
                (patient_id, now),
            )
            connection.execute(
                """INSERT INTO patient_memory_deletion_jobs(patient_id, status, attempts, created_at, run_after)
                   VALUES (?, 'pending', 0, ?, ?)
                   ON CONFLICT(patient_id) DO UPDATE SET status='pending', attempts=0,
                       created_at=excluded.created_at, run_after=excluded.run_after,
                       started_at=NULL, last_error=NULL""",
                (patient_id, now, now),
            )

    def set_patient_memory_enabled(self, patient_id: str, enabled: bool) -> None:
        """保存患者的记忆开关；待处理删除任务完成前仍不开放记忆。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO patient_memory_preferences(patient_id, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(patient_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (patient_id, int(enabled), now),
            )

    def patient_memory_enabled(self, patient_id: str) -> bool:
        with self._connection() as connection:
            preference = connection.execute(
                "SELECT enabled FROM patient_memory_preferences WHERE patient_id=?", (patient_id,)
            ).fetchone()
            deletion = connection.execute(
                "SELECT 1 FROM patient_memory_deletion_jobs WHERE patient_id=? AND status IN ('pending', 'processing')",
                (patient_id,),
            ).fetchone()
        return (preference is None or bool(preference[0])) and deletion is None

    def patient_deletion_pending(self, patient_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM patient_memory_deletion_jobs WHERE patient_id=? "
                "AND status IN ('pending', 'processing')", (patient_id,),
            ).fetchone()
        return row is not None

    def recover_patient_deletion_jobs(self) -> None:
        """服务重启后，把上次中断的清理任务放回队列。"""
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_memory_deletion_jobs SET status='pending', started_at=NULL "
                "WHERE status='processing'"
            )

    def claim_patient_deletion_job(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT patient_id, attempts FROM patient_memory_deletion_jobs "
                "WHERE status='pending' AND run_after<=? ORDER BY created_at LIMIT 1", (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE patient_memory_deletion_jobs SET status='processing', started_at=? WHERE patient_id=?",
                (now, row[0]),
            )
        return {"patient_id": row[0], "attempts": row[1]}

    def retry_patient_deletion(self, job: dict[str, Any], error: Exception) -> None:
        attempts = int(job["attempts"]) + 1
        delay = min(3600, 5 * (2 ** min(attempts - 1, 9)))
        run_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_memory_deletion_jobs SET status='pending', attempts=?, run_after=?, "
                "started_at=NULL, last_error=? WHERE patient_id=?",
                (attempts, run_after.isoformat(), str(error)[:500], job["patient_id"]),
            )

    def defer_patient_deletion(self, job: dict[str, Any], delay_seconds: int) -> None:
        """患者仍有运行时只延后清理，不记作失败。"""
        run_after = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_memory_deletion_jobs SET status='pending', run_after=?, started_at=NULL "
                "WHERE patient_id=?",
                (run_after.isoformat(), job["patient_id"]),
            )

    def finish_patient_deletion(self, patient_id: str) -> None:
        """清除患者记忆数据，并保留关闭状态和清理任务记录。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            row_ids = connection.execute(
                "SELECT rowid FROM patient_memory_vectors WHERE patient_id=?", (patient_id,)
            ).fetchall()
            connection.executemany("DELETE FROM patient_memory_vectors WHERE rowid=?", row_ids)
            for table in (
                "patient_long_term_memories", "patient_long_term_profiles",
                "patient_memory_retrievals", "patient_memory_extraction_jobs",
            ):
                connection.execute(f"DELETE FROM {table} WHERE patient_id=?", (patient_id,))
            connection.execute(
                "UPDATE patient_memory_deletion_jobs SET status='done', finished_at=?, started_at=NULL, "
                "last_error=NULL WHERE patient_id=?",
                (now, patient_id),
            )

    def remember(
        self, patient_id: str, text: str, source: str, category: str = "preference",
        importance: int = 3, expires_in_days: int | None = None,
        supersedes_id: str = "", evidence: str = "", conflicts_with_id: str = "",
        confidence: float = 0.9, protected: bool | None = None,
    ) -> None:
        """把证据明确的长期事实单独入档，并更新每轮注入的短摘要。"""
        clean = text.strip()[:1000]
        if not clean:
            return
        if category not in MEMORY_HALF_LIFE_DAYS:
            raise ValueError("长期记忆类型不在已支持的类别中。")
        if not isinstance(importance, int) or not 1 <= importance <= 5:
            raise ValueError("长期记忆重要度必须是 1 到 5。")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("长期记忆置信度必须在 0 到 1 之间。")
        if expires_in_days is not None and not 1 <= expires_in_days <= 3650:
            raise ValueError("过期天数必须在 1 到 3650 天之间。")
        if supersedes_id and conflicts_with_id:
            raise ValueError("一条新记忆只能替代旧记忆，或标记未解决冲突，不能同时执行两种关系。")
        clean_evidence = (evidence.strip() or clean)[:1000]
        if protected is None:
            protected = category in {"allergy", "medication", "constraint"}
        digest = hashlib.sha256(f"{category}:{clean.casefold()}".encode("utf-8")).hexdigest()[:24]
        memory_id = f"memory:{patient_id}:{digest}"
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
        with self._connection() as connection:
            # 和删除请求共用 SQLite 写锁，防止旧抽取任务在清理后补写记忆。
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            preference = connection.execute(
                "SELECT enabled FROM patient_memory_preferences WHERE patient_id=?", (patient_id,)
            ).fetchone()
            deletion = connection.execute(
                "SELECT 1 FROM patient_memory_deletion_jobs WHERE patient_id=? "
                "AND status IN ('pending', 'processing')", (patient_id,),
            ).fetchone()
            if (preference is not None and not preference[0]) or deletion is not None:
                return
            status = "active"
            if supersedes_id:
                prior = connection.execute(
                    "SELECT memory_id FROM patient_long_term_memories WHERE memory_id=? AND patient_id=?",
                    (supersedes_id, patient_id),
                ).fetchone()
                if not prior:
                    raise ValueError("要替代的记忆不属于当前患者或已不存在。")
                connection.execute(
                    "UPDATE patient_long_term_memories SET status='superseded', valid_to=?, updated_at=? "
                    "WHERE memory_id=? AND patient_id=?",
                    (now.isoformat(), now.isoformat(), supersedes_id, patient_id),
                )
            if conflicts_with_id:
                prior = connection.execute(
                    "SELECT memory_id FROM patient_long_term_memories WHERE memory_id=? AND patient_id=?",
                    (conflicts_with_id, patient_id),
                ).fetchone()
                if not prior:
                    raise ValueError("冲突记忆不属于当前患者或已不存在。")
                connection.execute(
                    "UPDATE patient_long_term_memories SET status='conflicted', updated_at=? "
                    "WHERE memory_id=? AND patient_id=?",
                    (now.isoformat(), conflicts_with_id, patient_id),
                )
                status = "conflicted"
            connection.execute(
                """INSERT INTO patient_long_term_memories
                    (memory_id, patient_id, category, content, importance, confidence, status,
                    source, evidence, created_at, updated_at, valid_from, valid_to, expires_at, supersedes_id,
                    conflicts_with_id, protected, last_accessed_at, access_count, weight)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(memory_id) DO UPDATE SET
                     content=excluded.content, importance=excluded.importance,
                     confidence=excluded.confidence,
                     status=excluded.status, source=excluded.source, evidence=excluded.evidence,
                     updated_at=excluded.updated_at, valid_from=excluded.valid_from,
                     valid_to=excluded.valid_to,
                     expires_at=excluded.expires_at, protected=excluded.protected,
                     supersedes_id=excluded.supersedes_id,
                     conflicts_with_id=excluded.conflicts_with_id,
                     weight=excluded.weight""",
                (memory_id, patient_id, category, clean, importance, float(confidence), status, source, clean_evidence,
                 now.isoformat(), now.isoformat(), now.isoformat(), None, expires_at, supersedes_id or None,
                 conflicts_with_id or None, int(protected), now.isoformat(), 0, importance * 18),
            )
        self.build_long_term_profile(patient_id)

    def build_long_term_profile(self, patient_id: str, max_chars: int = 900) -> dict[str, Any]:
        """每轮重算过期和衰减权重，再维护一段短小、可直接注入提示词的档案。"""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        with self._connection() as connection:
            connection.execute(
                "UPDATE patient_long_term_memories SET status='expired', valid_to=expires_at, updated_at=? "
                "WHERE patient_id=? AND status='active' AND expires_at IS NOT NULL AND expires_at<=?",
                (now_iso, patient_id, now_iso),
            )
            rows = connection.execute(
                "SELECT memory_id, category, content, importance, confidence, updated_at, "
                "access_count, expires_at, status, protected, valid_from "
                "FROM patient_long_term_memories WHERE patient_id=? AND status='active'",
                (patient_id,),
            ).fetchall()
            ranked = []
            for row in rows:
                # 注入不等于更新事实，衰减从记忆最后一次被确认或修订时开始算。
                updated = datetime.fromisoformat(row[5])
                age_days = max(0.0, (now - updated).total_seconds() / 86400)
                half_life_days = MEMORY_HALF_LIFE_DAYS.get(row[1], 365)
                decay = exp(-log(2) * age_days / half_life_days)
                weight = round(min(100.0, row[3] * 20 * row[4] * decay), 1)
                connection.execute(
                    "UPDATE patient_long_term_memories SET weight=? WHERE memory_id=?",
                    (weight, row[0]),
                )
                if weight < 12 and not row[9]:
                    connection.execute(
                        "UPDATE patient_long_term_memories SET status='stale' WHERE memory_id=?",
                        (row[0],),
                    )
                ranked.append((weight, row))
            ranked.sort(key=lambda item: (bool(item[1][9]), item[0]), reverse=True)
            parts = []
            used = 0
            included_ids = []
            for weight, row in ranked:
                if weight < 12 and not row[9]:
                    continue
                validity = f"有效自 {row[10][:10]}" if row[10] else ""
                part = f"- [{row[1]} · 权重 {weight:.1f} · {validity}] {row[2]}"
                if used + len(part) > max_chars and not row[9]:
                    continue
                parts.append(part)
                included_ids.append(row[0])
                used += len(part)
            for memory_id in included_ids:
                connection.execute(
                    "UPDATE patient_long_term_memories SET last_accessed_at=?, access_count=access_count+1 "
                    "WHERE memory_id=?",
                    (now_iso, memory_id),
                )
            summary = "\n".join(parts) if parts else "暂无已确认的长期偏好或服务安排。"
            current = connection.execute(
                "SELECT summary, revision FROM patient_long_term_profiles WHERE patient_id=?",
                (patient_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO patient_long_term_profiles(patient_id, summary, revision, updated_at) VALUES (?, ?, 1, ?)",
                    (patient_id, summary, now_iso),
                )
                revision = 1
            elif current[0] != summary:
                revision = current[1] + 1
                connection.execute(
                    "UPDATE patient_long_term_profiles SET summary=?, revision=?, updated_at=? WHERE patient_id=?",
                    (summary, revision, now_iso, patient_id),
                )
            else:
                revision = current[1]
        active_count = sum(1 for weight, row in ranked if weight >= 12 or row[9])
        return {"summary": summary, "revision": revision, "active_count": active_count, "included_count": len(included_ids)}

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
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS patient_long_term_memories (
                        memory_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, category TEXT NOT NULL,
                        content TEXT NOT NULL, importance INTEGER NOT NULL, confidence REAL NOT NULL,
                        status TEXT NOT NULL, source TEXT NOT NULL, evidence TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, valid_from TEXT,
                        valid_to TEXT, expires_at TEXT, protected INTEGER NOT NULL DEFAULT 0,
                        supersedes_id TEXT, conflicts_with_id TEXT, last_accessed_at TEXT,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        weight REAL NOT NULL DEFAULT 0
                    )"""
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_patient_long_term_active "
                    "ON patient_long_term_memories(patient_id, status, updated_at DESC)"
                )
                memory_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(patient_long_term_memories)").fetchall()
                }
                if "conflicts_with_id" not in memory_columns:
                    connection.execute("ALTER TABLE patient_long_term_memories ADD COLUMN conflicts_with_id TEXT")
                if "valid_from" not in memory_columns:
                    connection.execute("ALTER TABLE patient_long_term_memories ADD COLUMN valid_from TEXT")
                if "valid_to" not in memory_columns:
                    connection.execute("ALTER TABLE patient_long_term_memories ADD COLUMN valid_to TEXT")
                if "protected" not in memory_columns:
                    connection.execute(
                        "ALTER TABLE patient_long_term_memories ADD COLUMN protected INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS patient_memory_extraction_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        patient_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        task_key TEXT NOT NULL,
                        source_entry_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        run_after TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        memories_written INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT
                    )"""
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_patient_memory_jobs_due "
                    "ON patient_memory_extraction_jobs(status, run_after, id)"
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS patient_memory_preferences (
                        patient_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS patient_memory_deletion_jobs (
                        patient_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL, run_after TEXT NOT NULL, started_at TEXT,
                        finished_at TEXT, last_error TEXT
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS patient_long_term_profiles (
                        patient_id TEXT PRIMARY KEY, summary TEXT NOT NULL,
                        revision INTEGER NOT NULL, updated_at TEXT NOT NULL
                    )"""
                )
                migrated = connection.execute(
                    "SELECT 1 FROM patient_memory_migrations WHERE migration_key='long_term_rows_v1'"
                ).fetchone() if self._table_exists(connection, "patient_memory_migrations") else None
                if not self._table_exists(connection, "patient_memory_migrations"):
                    connection.execute(
                        "CREATE TABLE patient_memory_migrations (migration_key TEXT PRIMARY KEY, completed_at TEXT NOT NULL)"
                    )
                if not migrated:
                    legacy_rows = connection.execute(
                        "SELECT entry_id, patient_id, text, source, created_at FROM patient_memory_vectors WHERE kind='long_term'"
                    ).fetchall()
                    for legacy in legacy_rows:
                        category = "preference"
                        digest = hashlib.sha256(f"{category}:{legacy[2].casefold()}".encode("utf-8")).hexdigest()[:24]
                        memory_id = f"memory:{legacy[1]}:{digest}"
                        connection.execute(
                            """INSERT OR IGNORE INTO patient_long_term_memories
                               (memory_id, patient_id, category, content, importance, confidence, status,
                                source, evidence, created_at, updated_at, last_accessed_at, access_count, weight)
                               VALUES (?, ?, ?, ?, 3, 0.9, 'active', ?, ?, ?, ?, ?, 0, 54)""",
                            (memory_id, legacy[1], category, legacy[2], legacy[3], legacy[2], legacy[4], legacy[4], legacy[4]),
                        )
                    connection.execute(
                        "INSERT INTO patient_memory_migrations(migration_key, completed_at) VALUES ('long_term_rows_v1', ?)",
                        (datetime.now(timezone.utc).isoformat(),),
                    )
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
