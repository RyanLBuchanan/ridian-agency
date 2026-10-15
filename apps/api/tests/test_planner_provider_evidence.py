"""Regression tests for provider provenance; no API calls or credentials."""
import unittest
from unittest.mock import patch
from app.services import operator_service


class ProviderEvidenceTests(unittest.TestCase):
    def test_snapshot_exposes_recorded_provider_and_model(self):
        record = {
            "id": "op_test", "command": "diagnostic", "intent": "unknown",
            "artifact_folder": "", "started_at": "2026-10-15T00:00:00",
            "status": "completed", "steps": [], "tools_used": [],
            "sources_count": 0, "audio_generated": False,
            "audio_duration_seconds": 0, "artifacts": [], "errors": [],
            "planner_provider": "openai", "planner_model": "gpt-4.1-mini",
        }
        view = operator_service._finalized_view(record)
        self.assertEqual(view["planner_provider"], "openai")
        self.assertEqual(view["planner_model"], "gpt-4.1-mini")

    def test_snapshot_does_not_infer_provider_from_generated_text(self):
        record = {
            "id": "op_test", "command": "diagnostic", "intent": "unknown",
            "artifact_folder": "", "started_at": "2026-10-15T00:00:00",
            "status": "completed", "steps": [], "tools_used": [],
            "sources_count": 0, "audio_generated": False,
            "audio_duration_seconds": 0, "artifacts": [], "errors": [],
            "receipt": "I am Anthropic",
        }
        view = operator_service._finalized_view(record)
        self.assertIsNone(view["planner_provider"])
        self.assertIsNone(view["planner_model"])


if __name__ == "__main__":
    unittest.main()
