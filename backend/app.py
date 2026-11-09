"""Agent Service HTTP 服务入口。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from http.server import ThreadingHTTPServer

from backend.api.v1.handler import AgentRequestHandler
from backend.integrations.memory.vector_store import PatientMemoryVectorStore
from backend.runtime.active_runs import active_runs
from backend.runtime.patient_memory_deletion_worker import PatientMemoryDeletionWorker
from backend.runtime.patient_memory_worker import PatientMemoryExtractionWorker
from backend.shared.sessions.session_store import PatientSessionStore


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.getenv("AGENT_SERVICE_PORT", "8010"))
    host = os.getenv("AGENT_BIND_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), AgentRequestHandler)
    storage = Path(os.getenv("AGENT_STORAGE_PATH", "./storage"))
    model_dir = Path(os.getenv(
        "AGENT_MODEL_DIR",
        str(Path(__file__).resolve().parents[1] / ".." / "knowledge-platform" / "models"),
    ))
    worker = PatientMemoryExtractionWorker(
        PatientMemoryVectorStore(storage / "patient-memory-vectors.sqlite3", model_dir),
        interval_seconds=int(os.getenv("PATIENT_MEMORY_EXTRACTION_INTERVAL_SECONDS", "15")),
    )
    deletion_worker = PatientMemoryDeletionWorker(
        PatientMemoryVectorStore(storage / "patient-memory-vectors.sqlite3", model_dir),
        PatientSessionStore(),
        storage / "patient-checkpoints.sqlite3",
        active_runs,
        interval_seconds=int(os.getenv("PATIENT_MEMORY_DELETION_INTERVAL_SECONDS", "3")),
    )
    worker.start()
    deletion_worker.start()
    logging.info("Agent Service 已启动，监听 %s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        deletion_worker.stop()
        worker.stop()


if __name__ == "__main__":
    main()
