import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import continuation
import demo


class ContinuationTests(unittest.TestCase):
    def test_segment_ranges_and_visibility_cutoffs(self):
        for k, end in enumerate((191, 239, 286, 334, 382, 430), 1):
            self.assertEqual(demo.visible_part(f"review{k}"), k)
            self.assertEqual(demo.visible_end(f"review{k}"), end)
        self.assertEqual(demo.visible_end("initial"), 143)
        for k in range(2, 8):
            names = continuation.part_files(k)
            self.assertEqual(len(names), 2 * (k + 1))
            self.assertIn(f"data/daily/part_{k:02d}.csv", names)
            self.assertNotIn(f"data/daily/part_{k + 1:02d}.csv", names)
        with self.assertRaises(RuntimeError):
            demo.visible_part("review7")  # after the last segment there is no review decision

    def test_real_first_checkpoint(self):
        base, workspace, eval_run = demo.paths("first-demo")
        continuation.verify_evaluator_state(eval_run, 1)
        continuation.verify_workspace(workspace, 1)
        self.assertEqual(continuation.read_phase(base), "review1_rechecked")

    def test_workspace_refuses_early_or_extra_partition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            for k in (0, 1):
                for kind, suffix in (("daily", ".csv"), ("minute", ".csv.gz")):
                    p = root / "data" / kind / f"part_{k:02d}{suffix}"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("fixture")
            with patch.object(continuation.demo, "_hash", return_value="same"):
                continuation.verify_workspace(root, 1)
                with self.assertRaises(RuntimeError):
                    continuation.verify_workspace(root, 2)
                (root / "data/daily/part_02.csv").write_text("future")
                with self.assertRaises(RuntimeError):
                    continuation.verify_workspace(root, 1)

    def test_model_pin_refuses_mid_run_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            (base / "phase.json").write_text('{"phase":"review1_rechecked"}')
            first = {"provider": "openai_compatible", "base_url": "https://api.deepseek.com", "model": "deepseek-flash"}
            continuation.assert_model_lock(base, first)
            continuation.assert_model_lock(base, first)
            with self.assertRaisesRegex(RuntimeError, "固定模型"):
                continuation.assert_model_lock(base, {**first, "model": "4-pro"})

    def test_transition_routes_and_no_review_seven(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://api.deepseek.com", "model": "deepseek-flash"}
        cases = [("review1_rechecked", "segment", 2), ("review3_done", "segment", 4),
                 ("segment2_done", "review", 2), ("segment6_done", "review", 6),
                 ("review6_done", "segment", 7), ("segment7_done", "summarize", None)]
        for phase, function, k in cases:
            with self.subTest(phase=phase):
                with patch.object(continuation.demo, "paths", return_value=(pathlib.Path("/tmp/fixture"), None, None)), \
                     patch.object(continuation, "assert_model_lock"), \
                     patch.object(continuation, "read_phase", side_effect=[phase, phase]), \
                     patch.object(continuation, "segment") as seg, \
                     patch.object(continuation, "review") as rev, \
                     patch.object(continuation, "summarize") as summ:
                    continuation.next_step("first-demo", cfg)
                    if function == "segment": seg.assert_called_once_with("first-demo", k)
                    if function == "review": rev.assert_called_once_with("first-demo", k, cfg)
                    if function == "summarize": summ.assert_called_once_with("first-demo")
                if phase == "segment7_done":
                    rev.assert_not_called()


if __name__ == "__main__":
    unittest.main()
