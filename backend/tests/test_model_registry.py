"""模型能力元数据的校验和本地选择持久化回归。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from backend.runtime.model_registry import ModelRegistry
from backend.shared.sessions.session_store import PatientSessionStore


class ModelRegistryTests(unittest.TestCase):
    def test_models_without_explicit_context_limits_are_not_selectable(self) -> None:
        models = ModelRegistry._parse_models({
            "data": [
                {"id": "deepseek-flash", "name": "DeepSeek Flash", "context_window": 1_048_576, "max_output_tokens": 393_216},
                {"id": "unknown-window", "name": "Unknown"},
            ]
        })
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["context_window"], 1_048_576)
        self.assertEqual(models[0]["input_budget_tokens"], 655_360)

    def test_selected_model_capabilities_survive_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AGENT_STORAGE_PATH": directory}):
            first_store = PatientSessionStore()
            first_store.set_selected_model({
                "id": "deepseek-flash", "name": "DeepSeek Flash",
                "context_window": 1_048_576, "max_output_tokens": 393_216,
            })
            reopened = PatientSessionStore()
            selected = reopened.get_selected_model()
        self.assertEqual(selected["model_id"], "deepseek-flash")
        self.assertEqual(selected["context_window"], 1_048_576)
        self.assertEqual(selected["input_budget_tokens"], 655_360)


if __name__ == "__main__":
    unittest.main()
