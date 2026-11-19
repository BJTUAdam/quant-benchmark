import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from export_trajectory_html import build


class TrajectoryExportTests(unittest.TestCase):
    def test_full_dialogue_is_escaped_and_fallback_is_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "events").mkdir()
            cp = root / "checkpoints/001"
            cp.mkdir(parents=True)
            (root / "events/001.json").write_text(json.dumps({
                "phase": "initial_done", "checkpoint": "checkpoints/001"}), encoding="utf-8")
            (cp / "transcript.json").write_text(json.dumps({
                "status": "harness_fallback", "delivery": "harness_fallback",
                "fallback": {"reason": "model_call_budget_exhausted"},
                "messages": [{"role": "assistant", "content": "<script>bad()</script> sk-example"}]
            }), encoding="utf-8")
            result = build(root)
            self.assertIn("harness_fallback", result)
            self.assertIn("&lt;script&gt;", result)
            self.assertNotIn("<script>", result)
            self.assertNotIn("sk-example", result)
            self.assertIn("[REDACTED]", result)
            (root / "events/001.json").write_text(json.dumps({
                "phase": "initial_done", "checkpoint": "../outside"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                build(root)


if __name__ == "__main__":
    unittest.main()
