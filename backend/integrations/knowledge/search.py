"""调用独立知识平台的 BM25、向量和图谱混合检索接口。"""

from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


logger = logging.getLogger(__name__)


def search_cancer_knowledge(query: str, limit: int = 6) -> dict:
    """按需查找肿瘤科普资料，并保留来源和图谱命中路径。"""
    query = query.strip()
    if len(query) < 2 or len(query) > 200:
        return {"ok": False, "error": "检索词需要在 2 到 200 个字符之间。"}

    base_url = os.getenv("KNOWLEDGE_PLATFORM_URL", "http://127.0.0.1:8020").rstrip("/")
    url = f"{base_url}/api/vector/search?{urlencode({'q': query, 'limit': limit})}"
    try:
        with urlopen(url, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        logger.warning("知识检索服务返回 HTTP 错误，status=%s", error.code)
        return {"ok": False, "error": "肿瘤知识检索暂时不可用。"}
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        logger.warning("知识检索服务连接失败：%s", error.__class__.__name__)
        return {"ok": False, "error": "肿瘤知识检索暂时不可用。"}

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
        "retrieval": data.get("retrieval", {}),
        "results": results,
    }
