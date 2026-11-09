"""后台定时抽取患者记忆；队列任务落 SQLite，服务重启后继续处理。"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Literal

from pydantic import BaseModel, Field

from backend.integrations.memory.vector_store import PatientMemoryVectorStore
from backend.shared.sessions.session_store import PatientSessionStore


class ExtractedMemory(BaseModel):
    """模型从一条用户原话里整理出的候选记忆。"""

    content: str = Field(description="简短、单一的事实，不添加原话没有的内容")
    category: Literal[
        "allergy", "medication", "preference", "service_need", "communication",
        "constraint", "event", "fact", "episode", "other",
    ]
    quote: str = Field(description="支持这条记忆的用户原话片段，必须逐字引用")
    importance: int = Field(description="重要度，1 到 5")
    confidence: float = Field(description="抽取置信度，0 到 1")
    expires_in_days: int | None = Field(description="用户明确给出有效期时填写，否则留空")
    relation: Literal["new", "supersedes", "conflicts"]
    related_memory_id: str | None = Field(description="替代或冲突的现有记忆 ID；新记忆留空")
    protected: bool = Field(description="过敏、药物、医疗禁忌和精确约束设为 true")
    explicit: bool = Field(description="用户是否明确要求长期记住这条事实")


class MemoryExtractionResult(BaseModel):
    """整轮抽取结果。"""

    memories: list[ExtractedMemory]


class PatientMemoryExtractionWorker:
    """单线程轮询持久队列，每个周期最多发起一次抽取调用。"""

    def __init__(self, memory: PatientMemoryVectorStore, interval_seconds: int = 15) -> None:
        self.memory = memory
        self.interval_seconds = max(1, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.memory.recover_extraction_jobs()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="patient-memory-extractor", daemon=False
        )
        self._thread.start()
        logging.info("患者长期记忆抽取 worker 已启动，队列轮询间隔 %s 秒", self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=35)

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.memory.claim_extraction_job()
            if job is None:
                self._stop.wait(self.interval_seconds)
                continue
            try:
                written = self._extract(job)
                self.memory.finish_extraction_job(job["id"], written)
                logging.info(
                    "患者长期记忆抽取完成 patient=%s conversation=%s 写入=%s",
                    job["patient_id"], job["conversation_id"], written,
                )
            except Exception as error:
                self.memory.retry_extraction_job(job, error)
                logging.exception(
                    "患者长期记忆抽取失败 patient=%s conversation=%s attempt=%s",
                    job["patient_id"], job["conversation_id"], job["attempts"] + 1,
                )
            self._stop.wait(self.interval_seconds)

    def _extract(self, job: dict) -> int:
        source = self.memory.get_archive_text(job["source_entry_id"])
        if not source:
            raise ValueError("记忆抽取任务找不到对应的用户原话归档。")

        from langchain.chat_models import init_chat_model

        selected = PatientSessionStore().get_selected_model()
        model_id = selected["model_id"] if selected else os.getenv(
            "DEEPAGENTS_MODEL", "openai:deepseek-flash"
        ).split(":", 1)[-1]
        model = init_chat_model(f"openai:{model_id}", timeout=30)
        extractor = model.with_structured_output(MemoryExtractionResult, method="function_calling")
        active_memories = self.memory.active_long_term_memories(job["patient_id"])
        result = extractor.invoke(
            self._prompt(source, active_memories),
            config={"run_name": "patient_memory_extraction", "tags": ["patient-memory", "scheduled-extraction"]},
        )

        written = 0
        active_ids = {item["memory_id"] for item in active_memories}
        for candidate in result.memories:
            explicit = candidate.explicit
            quote = candidate.quote.strip()
            if not self._quote_is_acceptable(source, candidate):
                logging.warning("患者记忆抽取跳过：原话引用未通过校验 patient=%s", job["patient_id"])
                continue
            if not explicit and (candidate.confidence < 0.7 or candidate.importance < 2):
                continue
            if candidate.expires_in_days is not None and not 1 <= candidate.expires_in_days <= 3650:
                continue
            relation_id = candidate.related_memory_id or ""
            if candidate.relation != "new" and relation_id not in active_ids:
                logging.warning("患者记忆抽取跳过：替代/冲突目标不在当前患者有效记忆中。")
                continue
            self.memory.remember(
                job["patient_id"],
                candidate.content,
                f"{job['conversation_id']}:{job['task_key']}",
                category=candidate.category,
                importance=max(3, candidate.importance) if explicit else candidate.importance,
                expires_in_days=candidate.expires_in_days,
                supersedes_id=relation_id if candidate.relation == "supersedes" else "",
                conflicts_with_id=relation_id if candidate.relation == "conflicts" else "",
                evidence=quote if quote else source,
                confidence=1.0 if explicit else candidate.confidence,
                protected=candidate.protected or candidate.category in {"allergy", "medication", "constraint"},
            )
            written += 1
        return written

    @staticmethod
    def _quote_is_acceptable(source: str, candidate: ExtractedMemory) -> bool:
        """普通事实必须有逐字证据；显式写入由意图识别单独兜底。"""
        return candidate.explicit or bool(candidate.quote.strip() and candidate.quote.strip() in source)

    @staticmethod
    def _prompt(source: str, active_memories: list[dict]) -> str:
        return """你负责从患者本轮原话里整理长期记忆候选。显式记忆要求优先级最高；普通事实只在稳定、跨会话有用时提取。

规则：
- 不从助手回复、工具结果或医疗常识推断事实。只能依据下面的用户原话。
- 一条记忆只写一个事实，保留患者自述属性；过敏和用药属于用户报告，不等于医生核实。
- 普通抽取必须给出原文中连续、逐字一致的 quote；普通闲聊、一次性问题、临时症状、报告内容和助手推断不写入。
- “记住”“以后都”“别忘了”“写入记忆”等语义明确要求长期保存时，标 explicit=true；不要只看关键词。
- 如果用户明确更新现有事实，relation=supersedes；冲突但不能判断新旧时 relation=conflicts。只有确实对应现有条目时才填写 related_memory_id。
- 不确定就返回空列表。不要为了填满结果编造内容。

现有有效记忆：
{memories}

本轮用户原话：
{source}""".format(
            memories=json.dumps(active_memories, ensure_ascii=False), source=source,
        )
