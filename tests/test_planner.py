import os
import tempfile
import unittest
from unittest.mock import patch

from brain.memory import Memory
from brain.planner import Planner, load_env_file


class PlannerFallbackTests(unittest.TestCase):
    def test_missing_gemini_key_uses_fallback_response(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name

        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                with patch("brain.planner.load_env_file", return_value={}):
                    planner = Planner(Memory(path=path))

            self.assertIsNone(planner.client)
            self.assertTrue(planner.uses_fallback)
            reply = planner.respond("hello")
            self.assertIn("not configured", reply.lower())
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_load_env_file_populates_api_key(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as handle:
            handle.write("GEMINI_API_KEY=test-key\n")
            path = handle.name

        try:
            os.environ.pop("GEMINI_API_KEY", None)
            loaded = load_env_file(path)
            self.assertEqual(loaded["GEMINI_API_KEY"], "test-key")
            self.assertEqual(os.environ["GEMINI_API_KEY"], "test-key")
        finally:
            if os.path.exists(path):
                os.remove(path)
            os.environ.pop("GEMINI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
