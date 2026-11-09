"""从模型服务的能力接口读取模型窗口，并保存当前调试模型。"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from backend.shared.sessions.session_store import PatientSessionStore


class ModelRegistry:
    """只暴露服务端能查到明确上下文窗口的模型。"""

    def __init__(self, sessions: PatientSessionStore) -> None:
        self.sessions = sessions
        self._models: list[dict[str, Any]] = []
        self._loaded_at = 0.0

    async def list_models(self, *, refresh: bool = False) -> dict[str, Any]:
        if refresh or time.monotonic() - self._loaded_at > 300 or not self._models:
            self._models = await self._fetch_models()
            self._loaded_at = time.monotonic()
        selected = self.sessions.get_selected_model()
        if selected is None:
            configured_id = os.getenv("DEEPAGENTS_MODEL", "openai:deepseek-flash").strip().split(":", 1)[-1]
            selected = next((model for model in self._models if model["id"] == configured_id), None)
            if selected is None:
                raise RuntimeError(f"配置的模型 {configured_id} 不在模型服务的能力清单中。")
            self.sessions.set_selected_model(selected)
            selected = self.sessions.get_selected_model()
        else:
            current = next((model for model in self._models if model["id"] == selected["model_id"]), None)
            if current is not None and any(current[key] != selected[stored] for key, stored in (
                ("name", "display_name"), ("context_window", "context_window"),
                ("max_output_tokens", "max_output_tokens"),
            )):
                self.sessions.set_selected_model(current)
                selected = current
        return {"models": self._models, "selected": selected}

    async def select_model(self, model_id: str) -> dict[str, Any]:
        catalog = await self.list_models(refresh=True)
        selected = next((model for model in catalog["models"] if model["id"] == model_id), None)
        if selected is None:
            raise ValueError("所选模型没有可验证的上下文窗口，无法启用。")
        self.sessions.set_selected_model(selected)
        return self.sessions.get_selected_model() or selected

    async def active_model(self) -> dict[str, Any]:
        catalog = await self.list_models()
        selected = catalog["selected"]
        current = next((model for model in catalog["models"] if model["id"] == selected["model_id"]), None)
        if current is None:
            return await self.select_model(os.getenv("DEEPAGENTS_MODEL", "openai:deepseek-flash").split(":", 1)[-1])
        if any(current[key] != selected[stored] for key, stored in (
            ("name", "display_name"), ("context_window", "context_window"),
            ("max_output_tokens", "max_output_tokens"),
        )):
            self.sessions.set_selected_model(current)
        return current

    async def _fetch_models(self) -> list[dict[str, Any]]:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("请先配置 OPENAI_API_KEY，才能读取模型能力清单。")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
        return self._parse_models(payload)

    @staticmethod
    def _parse_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
        models = []
        for item in payload.get("data", []):
            model_id = item.get("id")
            context_window = item.get("context_window")
            max_output_tokens = item.get("max_output_tokens")
            if (
                not isinstance(model_id, str)
                or not isinstance(context_window, int)
                or not isinstance(max_output_tokens, int)
                or context_window <= max_output_tokens
            ):
                continue
            models.append({
                "id": model_id,
                "name": item.get("name") or model_id,
                "context_window": context_window,
                "max_output_tokens": max_output_tokens,
                # provider 的总窗口同时容纳输入和输出，输入预算先预留最大输出空间。
                "input_budget_tokens": context_window - max_output_tokens,
            })
        if not models:
            raise RuntimeError("模型服务没有返回包含 context_window 和 max_output_tokens 的能力资料。")
        return models
