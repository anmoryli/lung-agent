"""调用独立知识平台的 BM25、向量和图谱混合检索接口。"""

from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from typing import Literal


logger = logging.getLogger(__name__)


def search_cancer_knowledge(query: str, limit: int = 6, mode: Literal["local", "global"] = "local") -> dict:
    """按具体条目或全库主题检索肿瘤科普资料，并保留原始来源。"""
    query = query.strip()
    if len(query) < 2 or len(query) > 200:
        return {"ok": False, "error": "检索词需要在 2 到 200 个字符之间。"}
    if mode not in {"local", "global"}:
        return {"ok": False, "error": "检索模式只支持 local 或 global。"}

    base_url = os.getenv("KNOWLEDGE_PLATFORM_URL", "http://127.0.0.1:8020").rstrip("/")
    endpoint = "/api/graph/global-search" if mode == "global" else "/api/vector/search"
    params = {"q": query} if mode == "global" else {"q": query, "limit": limit}
    url = f"{base_url}{endpoint}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        logger.warning("知识检索服务返回 HTTP 错误，status=%s", error.code)
        return {"ok": False, "error": "肿瘤知识检索暂时不可用。"}
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        logger.warning("知识检索服务连接失败：%s", error.__class__.__name__)
        return {"ok": False, "error": "肿瘤知识检索暂时不可用。"}

    if mode == "global":
        communities = []
        for report in data.get("communities", []):
            evidence = [
                {
                    "title": item.get("title", ""),
                    "text": item.get("text", "")[:1400],
                    "source_title": item.get("source_title", ""),
                    "source_url": item.get("source_url", ""),
                    "publisher": item.get("publisher", ""),
                }
                for item in report.get("evidence", [])
            ]
            communities.append({
                "category": report.get("category", ""),
                "summary": report.get("summary", ""),
                "topics": report.get("topics", []),
                "score": report.get("score", 0),
                "evidence": evidence,
            })
        return {
            "ok": True,
            "query": data.get("query", query),
            "mode": mode,
            "retrieval": data.get("retrieval", {}),
            "communities": communities,
        }

    results = []
    for item in data.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "text": item.get("text", "")[:1400],
            "category": item.get("category", ""),
            "topic": item.get("topic", ""),
            "source_title": item.get("source_title", ""),
            "source_url": item.get("source_url", ""),
            "publisher": item.get("publisher", ""),
            "source_checked_on": item.get("source_checked_on", ""),
            "retrieval_sources": item.get("retrieval_sources", []),
            "graph_paths": item.get("graph_paths", []),
        })
    return {
        "ok": True,
        "query": data.get("query", query),
        "mode": mode,
        "retrieval": data.get("retrieval", {}),
        "results": results,
    }
