"""长期记忆的到期、权重和版本处理回归。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.integrations.memory.vector_store import PatientMemoryVectorStore
from backend.runtime.patient_memory_worker import ExtractedMemory, PatientMemoryExtractionWorker


class PatientLongTermMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = PatientMemoryVectorStore(root / "memory.sqlite3", root / "models")
        self.patient_id = "demo-patient-0001"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_expired_items_leave_the_injected_summary(self) -> None:
        self.store.remember(
            self.patient_id, "喜欢短信提醒", "test-thread", expires_in_days=10
        )
        profile = self.store.build_long_term_profile(self.patient_id)
        self.assertIn("喜欢短信提醒", profile["summary"])

        with sqlite3.connect(self.store.database) as connection:
            connection.execute(
                "UPDATE patient_long_term_memories SET expires_at=? WHERE patient_id=?",
                ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), self.patient_id),
            )
        profile = self.store.build_long_term_profile(self.patient_id)
        self.assertNotIn("喜欢短信提醒", profile["summary"])
        expired = self.store.list_patient_entries(self.patient_id)[0]
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["valid_to"], expired["expires_at"])

    def test_low_weight_items_become_stale_and_are_not_injected(self) -> None:
        self.store.remember(self.patient_id, "喜欢短信提醒", "test-thread", importance=1)
        with sqlite3.connect(self.store.database) as connection:
            connection.execute(
                "UPDATE patient_long_term_memories SET updated_at=? WHERE patient_id=?",
                ((datetime.now(timezone.utc) - timedelta(days=240)).isoformat(), self.patient_id),
            )
        profile = self.store.build_long_term_profile(self.patient_id)
        self.assertNotIn("喜欢短信提醒", profile["summary"])
        self.assertEqual(self.store.list_patient_entries(self.patient_id)[0]["status"], "stale")

    def test_superseded_memory_remains_auditable(self) -> None:
        self.store.remember(self.patient_id, "用短信联系", "test-thread")
        prior = self.store.list_patient_entries(self.patient_id)[0]
        self.store.remember(
            self.patient_id, "改用电话联系", "test-thread", supersedes_id=prior["entry_id"]
        )
        entries = {item["text"]: item for item in self.store.list_patient_entries(self.patient_id)}
        self.assertEqual(entries["用短信联系"]["status"], "superseded")
        self.assertIsNotNone(entries["用短信联系"]["valid_to"])
        self.assertEqual(entries["改用电话联系"]["status"], "active")
        self.assertIn("改用电话联系", self.store.build_long_term_profile(self.patient_id)["summary"])

    def test_unresolved_conflict_keeps_both_out_of_the_summary(self) -> None:
        self.store.remember(self.patient_id, "更喜欢电话联系", "test-thread")
        prior = self.store.list_patient_entries(self.patient_id)[0]
        self.store.remember(
            self.patient_id, "更喜欢短信联系", "test-thread", conflicts_with_id=prior["entry_id"]
        )
        statuses = {item["status"] for item in self.store.list_patient_entries(self.patient_id)}
        self.assertEqual(statuses, {"conflicted"})
        summary = self.store.build_long_term_profile(self.patient_id)["summary"]
        self.assertNotIn("电话联系", summary)
        self.assertNotIn("短信联系", summary)

    def test_health_constraints_are_marked_protected(self) -> None:
        self.store.remember(self.patient_id, "自述对青霉素过敏", "test-thread", category="allergy")
        entry = self.store.list_patient_entries(self.patient_id)[0]
        self.assertTrue(entry["protected"])

    def test_extraction_queue_is_durable_idempotent_and_recoverable(self) -> None:
        self.store.enqueue_extraction(self.patient_id, "conversation-1", "turn-1", "source-1")
        self.store.enqueue_extraction(self.patient_id, "conversation-1", "turn-1", "source-1")
        job = self.store.claim_extraction_job()
        self.assertIsNotNone(job)
        assert job is not None

        with sqlite3.connect(self.store.database) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM patient_memory_extraction_jobs WHERE source_entry_id='source-1'"
            ).fetchone()[0], 1)
            connection.execute(
                "UPDATE patient_memory_extraction_jobs SET status='processing' WHERE id=?", (job["id"],)
            )

        # 模拟服务重启：processing 会回到 pending，之后仍能被 worker 领取。
        self.store.recover_extraction_jobs()
        recovered = self.store.claim_extraction_job()
        self.assertEqual(recovered["id"], job["id"])
        self.store.finish_extraction_job(job["id"], 2)
        self.assertEqual(self.store.extraction_status(self.patient_id)["latest"]["status"], "done")

    def test_ordinary_extraction_requires_an_exact_source_quote(self) -> None:
        valid = ExtractedMemory(
            content="患者偏好短信提醒", category="preference", quote="我更喜欢短信提醒",
            importance=3, confidence=0.9, expires_in_days=None, relation="new",
            related_memory_id=None, protected=False, explicit=False,
        )
        invented = valid.model_copy(update={"quote": "患者喜欢短信提醒"})
        explicit = invented.model_copy(update={"explicit": True})
        self.assertTrue(PatientMemoryExtractionWorker._quote_is_acceptable("我更喜欢短信提醒", valid))
        self.assertFalse(PatientMemoryExtractionWorker._quote_is_acceptable("我更喜欢短信提醒", invented))
        self.assertTrue(PatientMemoryExtractionWorker._quote_is_acceptable("我更喜欢短信提醒", explicit))


if __name__ == "__main__":
    unittest.main()
