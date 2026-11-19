"""Offline scheduler contracts. Stub only the process boundary, not scheduler logic."""
import copy
import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import batch
import bench


class BatchTests(unittest.TestCase):
    def setUp(self):
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-batch-", dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.project = self.base / "project"
        self.package = self.project / "star50_trend_task_v1/star50_trend_task_v1"
        (self.package / "data").mkdir(parents=True)
        _, self.case = bench.load_task(HERE / "tasks/star50_trend.json", "star50_479_dev")
        bench.write_json(self.package / "data/splits.json", {k: self.case[k] for k in ("num_days", "research", "segments")})
        bench.write_json(self.package / "MANIFEST.json", {"sha256": {"data/splits.json": bench.digest(self.package / "data/splits.json")}})
        runner = self.project / "demo_runner"
        runner.mkdir()
        for name in batch.SOURCES:
            (runner / name).write_text("fixture", encoding="utf-8")
        (runner / "tests").mkdir()
        (runner / "tests/test_autonomous.py").write_text("fixture", encoding="utf-8")
        self.spec = bench.read(HERE / "configs/batch.offline.json")
        self.spec.update({"project_root": str(self.project), "package_root": str(self.package),
            "task": str(HERE / "tasks/star50_trend.json"), "track": "end_to_end", "seed_run_id": None})
        self.config = self.base / "config.json"
        self.folder = self.base / "batch"
        self.states = {}
        self.calls = []
        self.fail_once = None

    def initialize(self):
        bench.write_json(self.config, self.spec)
        return batch.initialize(self.config, self.folder)

    def fake_worker(self, folder, job, action):
        rid = job["run_id"]
        self.calls.append((rid, action))
        state = self.states.get(rid)
        if action == "prepare":
            self.assertIsNone(state)
            state = {"seq": 0, "phase": "prepared", "segments": 0, "checkpoint": "cp0"}
            self.states[rid] = state
        if action == "step":
            if self.fail_once == rid and state["phase"] == "segment2_done":
                self.fail_once = None
                error = bench.Invalid("HTTP 402 fixture; no requests")
                error.state = state
                raise error
            seq = state["seq"] + 1
            phase, segments, _ = bench.expected_stages()[seq]
            state = {"seq": seq, "phase": phase, "segments": segments, "checkpoint": "cp" + str(seq)}
            self.states[rid] = state
        return {"ok": True, "state": state}

    def test_matrix_two_models_two_repeats(self):
        lock = self.initialize()
        self.assertEqual(len(lock["jobs"]), 4)
        self.assertEqual(len({j["run_id"] for j in lock["jobs"]}), 4)
        result = batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual((result["completed"], result["failed"]), (4, 0))
        self.assertEqual(sum(action == "step" for _, action in self.calls), 60)
        self.assertTrue(all(r["attempts"] == 1 for r in result["runs"]))

    def test_failed_job_resumes_without_repeating_committed_stages(self):
        lock = self.initialize()
        first_id = lock["jobs"][0]["run_id"]
        self.fail_once = first_id
        result = batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual((result["completed"], result["failed"]), (3, 1))
        self.assertEqual(self.states[first_id]["phase"], "segment2_done")
        before_calls = len(self.calls)
        result = batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual((result["completed"], result["failed"]), (4, 0))
        self.assertEqual(sum(action == "prepare" for rid, action in self.calls if rid == first_id), 1)
        self.assertEqual(sum(action == "step" for rid, action in self.calls[before_calls:] if rid != first_id), 0)
        self.assertEqual(result["runs"][0]["failure_count"], 1)
        self.assertEqual(result["runs"][0]["attempts"], 2)

    def test_interrupted_attempt_preserved(self):
        lock = self.initialize()
        rid = lock["jobs"][0]["run_id"]
        batch.emit(self.folder, rid, "running", state=None)
        self.states[rid] = {"seq": 4, "phase": "segment2_done", "segments": 2, "checkpoint": "cp4"}
        result = batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual(result["completed"], 4)
        self.assertTrue(any(e["status"] == "interrupted" for e in batch.journal(self.folder)))

    def test_locked_protocol_cannot_change(self):
        self.initialize()
        path = self.folder / "protocol.lock.json"
        value = bench.read(path)
        value["spec"]["repeats"] = 3
        bench.write_json(path, value)
        with self.assertRaisesRegex(bench.Invalid, "协议被改动"):
            batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual(self.calls, [])

    def test_code_change_stops_before_worker(self):
        self.initialize()
        (self.project / "demo_runner/agent_tools.py").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(bench.Invalid, "锁定文件已改变"):
            batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual(self.calls, [])

    def test_package_change_stops_before_worker(self):
        self.initialize()
        (self.package / "data/splits.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(bench.Invalid, "锁定文件已改变"):
            batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual(self.calls, [])

    def test_reinitialize_cannot_overwrite(self):
        self.initialize()
        with self.assertRaises(bench.Invalid):
            batch.initialize(self.config, self.folder)

    def test_literal_key_and_key_files_rejected(self):
        for key in ("api_key", "api_key_file", "headers"):
            value = copy.deepcopy(self.spec)
            value["models"][0][key] = "DO-NOT-STORE-SECRETS"
            with self.assertRaises(bench.Invalid):
                batch.validate_spec(value)

    def test_duplicate_models_rejected(self):
        self.spec["models"][1]["id"] = self.spec["models"][0]["id"]
        with self.assertRaises(bench.Invalid):
            batch.validate_spec(self.spec)

    def test_shared_seed_requires_fresh(self):
        self.spec["track"] = "maintenance_shared_v1"
        self.spec["seed_run_id"] = "some-source"
        self.spec["policy"]["context_mode"] = "continuous"
        with self.assertRaises(bench.Invalid):
            batch.validate_spec(self.spec)

    def test_review_protocol_is_registered_and_validated(self):
        self.assertEqual(batch.validate_spec(self.spec)["policy"]["interaction_protocol"], "bundled_review_v1")
        self.spec["policy"]["interaction_protocol"] = "guided_delivery_v1"
        self.assertEqual(batch.validate_spec(self.spec)["policy"]["interaction_protocol"], "guided_delivery_v1")
        self.spec["policy"]["interaction_protocol"] = "guided_delivery_v2"
        self.assertEqual(batch.validate_spec(self.spec)["policy"]["interaction_protocol"], "guided_delivery_v2")
        self.spec["policy"]["interaction_protocol"] = "unknown"
        with self.assertRaisesRegex(bench.Invalid, "交互协议"):
            batch.validate_spec(self.spec)

    def test_docker_backend_requires_immutable_image_id(self):
        self.spec["policy"]["execution_backend"] = "docker"
        self.spec["policy"]["docker_image"] = "financial-agent-sandbox:latest"
        with self.assertRaisesRegex(bench.Invalid, "sha256"):
            batch.validate_spec(self.spec)
        self.spec["policy"]["docker_image"] = "sha256:" + "a" * 64
        self.assertEqual(batch.validate_spec(self.spec)["policy"]["execution_backend"], "docker")

    def test_fake_mode_never_uses_real_endpoint(self):
        self.spec["models"][0]["base_url"] = "https://api.deepseek.com"
        with self.assertRaises(bench.Invalid):
            batch.validate_spec(self.spec)

    def test_exclusive_lock(self):
        self.initialize()
        with batch.exclusive(self.folder):
            with self.assertRaises(bench.Invalid):
                with batch.exclusive(self.folder):
                    self.fail("concurrent acquisition")

    def test_status_is_read_only_while_scheduler_holds_lock(self):
        lock = self.initialize()
        rid = lock["jobs"][0]["run_id"]
        batch.emit(self.folder, rid, "running", state={"phase": "segment2_done"})
        before = list(self.folder.rglob("*"))
        with batch.exclusive(self.folder):
            result = batch.read_status(self.folder)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["runs"][0]["state"]["phase"], "segment2_done")
        self.assertFalse((self.folder / "batch-results.json").exists())

    def test_old_results_readable_but_source_drift_still_blocks_execution(self):
        self.initialize()
        (self.project / "demo_runner/agent_tools.py").write_text("changed", encoding="utf-8")
        self.assertEqual(batch.read_status(self.folder)["expected_runs"], 4)
        with self.assertRaises(bench.Invalid):
            batch.load_lock(self.folder)

    def test_missing_key_records_failure_without_preparing(self):
        # No API or fake worker key lookup; only test the scheduler's gate.
        self.spec["mode"] = "real"
        from unittest.mock import patch
        with patch.object(batch.sys, "platform", "linux"), patch.dict(batch.os.environ, {}, clear=True):
            self.initialize()
            result = batch.run_batch(self.folder, self.fake_worker)
        self.assertEqual(result["failed"], 4)
        self.assertEqual(sum(action in ("prepare", "step") for _, action in self.calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
