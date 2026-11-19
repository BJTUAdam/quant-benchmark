"""Checks the visibility audit against valid history and injected boundary failures."""
import pathlib
import contextlib
import io
import sys
import tempfile
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import bench
import worker


class VisibilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-worker-", dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.previous = self.root / "prepared"
        self.current = self.root / "initial"
        (self.previous / "workspace").mkdir(parents=True)
        self.current.mkdir()
        (self.previous / "workspace/TASK.md").write_text("test task", encoding="utf-8")
        self.events = [{"phase": "prepared", "checkpoint": "prepared", "visible_part": 0},
                       {"phase": "initial_done", "checkpoint": "initial", "visible_part": 0,
                        "model": {"context_mode": "fresh"}}]
        self.h = types.SimpleNamespace(MODEL_SYSTEM="test system", run_root=lambda run: self.root,
                                       load_events=lambda root: self.events)
        _, self.case = bench.load_task(HERE / "tasks/star50_trend.json", "star50_479_dev")

    def audit(self, tool="read_file", args=None, result="ok", extra=None):
        event = self.events[1]
        phase = event["phase"]
        protocol = event["model"].get("interaction_protocol")
        prompt = "test task" if phase == "initial_done" else "test review"
        if phase == "initial_done" and protocol in ("guided_delivery_v1", "guided_delivery_v2"):
            prompt = self.h.render_guided_initial_prompt(prompt, self.events[1]["model"]["max_model_calls"])
        if phase.startswith("review") and protocol == "guided_delivery_v2":
            prompt = self.h.render_guided_review_prompt(self.previous / "workspace", prompt, 1,
                                                        event["model"]["max_model_calls"])
        messages = [{"role": "system", "content": "test system"}, {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": tool,
                        "arguments": args or {"path": "data/daily/part_00.csv"}}]},
                    {"role": "tool", "tool_call_id": "c1", "content": result}]
        if extra:
            messages.append(extra)
        bench.write_json(self.current / "transcript.json", {"messages": messages})
        return worker.visibility_audit(self.h, "test", self.case)

    def test_visible_data_is_accepted(self):
        self.assertEqual(self.audit()["issues"], [])

    def test_successful_future_file_is_rejected(self):
        with self.assertRaises(bench.Invalid):
            self.audit(args={"path": "data/daily/part_01.csv"})

    def test_denied_future_request_is_not_successful_leak(self):
        result = self.audit(args={"path": "data/daily/part_01.csv"}, result="工具错误：该文件不存在")
        self.assertEqual(result["stages"][0]["successful_tool_responses_checked"], 0)

    def test_successful_inspection_beyond_boundary_is_rejected(self):
        with self.assertRaises(bench.Invalid):
            self.audit(tool="inspect_data", args={"start_t": 0, "end_t": 144, "kind": "daily", "symbol": "S01"})

    def test_successful_backtest_beyond_boundary_is_rejected(self):
        with self.assertRaises(bench.Invalid):
            self.audit(tool="run_backtest", args={"end": 144})

    def test_external_extra_instruction_is_rejected(self):
        with self.assertRaises(bench.Invalid):
            self.audit(extra={"role": "user", "content": "change the financial strategy for me"})

    def test_standard_delivery_feedback_is_accepted(self):
        self.assertEqual(self.audit(extra={"role": "user", "content": "交付校验尚未通过：追加 CHANGELOG.md。"})["issues"], [])

    def test_guided_reminder_is_verified_and_required(self):
        self.events[1]["model"] = {"context_mode": "fresh", "interaction_protocol": "guided_delivery_v1",
                                    "max_model_calls": 2}
        self.h.render_guided_initial_prompt = lambda task, maximum: task + " guided"
        self.h.delivery_reminder_points = lambda maximum: (1,)
        self.h.delivery_reminder = lambda call, maximum: f"交付进度提醒：{call}/{maximum}"
        reminder = {"role": "user", "content": "交付进度提醒：1/2"}
        self.assertEqual(self.audit(extra=reminder)["issues"], [])
        with self.assertRaises(bench.Invalid):
            self.audit()
        with self.assertRaises(bench.Invalid):
            self.audit(extra={"role": "user", "content": "交付进度提醒：任意要求"})

    def test_guided_review_prompt_and_reminder_are_audited(self):
        self.events[0].update({"phase": "segment1_done", "visible_part": 1})
        self.events[1].update({"phase": "review1_done", "visible_part": 1,
                               "model": {"context_mode": "fresh", "interaction_protocol": "guided_delivery_v2",
                                         "max_model_calls": 2}})
        (self.previous / "eval_run/segments/1").mkdir(parents=True)
        (self.previous / "eval_run/segments/1/next_prompt.md").write_text("test review", encoding="utf-8")
        self.h.render_guided_review_prompt = lambda workspace, task, segment, maximum: task + " guided review"
        self.h.delivery_reminder_points = lambda maximum: (1,)
        self.h.review_delivery_reminder = lambda call, maximum: f"交付进度提醒：复盘 {call}/{maximum}"
        reminder = {"role": "user", "content": "交付进度提醒：复盘 1/2"}
        self.assertEqual(self.audit(extra=reminder)["issues"], [])
        with self.assertRaises(bench.Invalid):
            self.audit()
        with self.assertRaises(bench.Invalid):
            self.audit(extra={"role": "user", "content": "交付进度提醒：任意要求"})

    def test_worker_owns_job_lock_before_loading_harness(self):
        from unittest.mock import patch
        project = self.root / "jobs/test"
        def load(lock, job, folder):
            with self.assertRaises(bench.Invalid):
                with worker.batch.exclusive(project):
                    self.fail("job lock missing")
            h = types.SimpleNamespace(run_root=lambda rid: project / "absent-run")
            return h, {}, project
        args = ["worker.py", "--batch", str(self.root), "--run-id", "test", "--action", "status"]
        with patch.object(sys, "argv", args), patch.object(worker.batch, "load_lock", return_value={"jobs": [{"run_id": "test"}]}), patch.object(worker, "load_harness", side_effect=load), contextlib.redirect_stdout(io.StringIO()) as output:
            worker.main()
        self.assertIn('"ok": true', output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
