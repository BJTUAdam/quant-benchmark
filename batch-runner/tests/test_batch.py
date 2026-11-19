import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import batchctl
import report
import spec as specmod
import store as storemod
from scheduler import BatchBusy, Scheduler, exclusive

FAKE = str(HERE / "fake_runner.py")


class Env:
    """临时批次目录；quick 参数让测试秒级完成。"""

    def __init__(self, folder, jobs, **extra):
        self.folder = pathlib.Path(folder)
        (self.folder / "runner").mkdir()
        (self.folder / "template.json").write_text(json.dumps({
            "schema_version": "trajectory.finlab.v2", "run_id": "x", "runs_root": "x",
            "finlab_root": "fl", "finlab_python": "fl/py", "api_key_file": "key.env",
            "provider": "openai_compatible", "base_url": "https://api.deepseek.com", "model": "m",
            "api_key_env": "DEEPSEEK_API_KEY", "task": {"kind": "synthetic_demo", "asset": "STOCK"}}), encoding="utf-8")
        self.config = {"batch_id": "t1", "runner_root": "runner", "runner_script": FAKE, "template": "template.json",
                       "out_dir": "out", "concurrency": 1, "max_attempts": 2, "retry_backoff_seconds": 0,
                       "launch_interval_seconds": 0, "poll_seconds": 0.05, "grace_seconds": 5,
                       "job_timeout_seconds": 60, "export_html": False, "jobs": jobs, **extra}
        self.path = self.folder / "batch.json"
        self.path.write_text(json.dumps(self.config), encoding="utf-8")

    def spec(self):
        return specmod.load_spec(self.path)

    def run(self):
        spec = self.spec()
        store = storemod.open_store(spec)
        try:
            store.sync(spec)
            code = Scheduler(spec, store, out=lambda m: None).run()
            return spec, store, code
        except BaseException:
            store.close()
            raise


def stock(job_id, **extra):
    return {"id": job_id, "task": {"kind": "synthetic_demo", "asset": "STOCK"}, **extra}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ.pop("FAKE_MODE", None)
        os.environ["FAKE_SLEEP"] = "0.1"

    def tearDown(self):
        os.environ.pop("FAKE_MODE", None)
        os.environ.pop("FAKE_SLEEP", None)

    def test_repeat_expands_and_runs_with_concurrency_limit(self):
        os.environ["FAKE_SLEEP"] = "0.6"
        env = Env(self.tmp.name, [stock("s", repeat=4)], concurrency=2)
        spec, store, code = env.run()
        self.addCleanup(store.close)
        self.assertEqual(code, 0)
        self.assertEqual([r["job_id"] for r in store.all()], ["s-1", "s-2", "s-3", "s-4"])
        self.assertEqual(store.counts()["completed"], 4)
        stamps = []
        for line in (pathlib.Path(spec["batch_dir"]) / "runs" / "_events.log").read_text().splitlines():
            kind, run_id, at = line.split()
            stamps.append((float(at), 1 if kind == "start" else -1))
        peak = live = 0
        for _, delta in sorted(stamps):
            live += delta
            peak = max(peak, live)
        self.assertEqual(peak, 2)

    def test_generated_config_is_isolated_and_absolute(self):
        env = Env(self.tmp.name, [stock("a"), stock("b")])
        spec, store, _ = env.run()
        self.addCleanup(store.close)
        cfg = json.loads(pathlib.Path(specmod.config_path(spec, "t1-a")).read_text(encoding="utf-8"))
        self.assertEqual(cfg["run_id"], "t1-a")
        self.assertEqual(cfg["runs_root"], os.path.join(spec["batch_dir"], "runs"))
        self.assertTrue(os.path.isabs(cfg["finlab_root"]) and os.path.isabs(cfg["api_key_file"]))
        self.assertEqual(cfg["api_key_file"], os.path.abspath(os.path.join(self.tmp.name, "key.env")))
        self.assertTrue(os.path.isdir(os.path.join(spec["batch_dir"], "runs", "t1-a")))
        self.assertTrue(os.path.isdir(os.path.join(spec["batch_dir"], "runs", "t1-b")))

    def test_retry_uses_new_run_id_and_exhausted_job_fails(self):
        jobs = [{"id": "fut", "task": {"kind": "synthetic_demo", "asset": "FUTURES"}},
                {"id": "fund", "task": {"kind": "synthetic_demo", "asset": "FUND"}}]
        spec, store, code = Env(self.tmp.name, jobs).run()
        self.addCleanup(store.close)
        fut, fund = store.get("fut"), store.get("fund")
        self.assertEqual((fut["status"], fut["attempt"], fut["run_id"]), ("completed", 2, "t1-fut-r2"))
        self.assertEqual(len(json.loads(fut["history"])), 2)
        self.assertEqual((fund["status"], fund["attempt"]), ("failed", 2))
        self.assertIn("FAILED: BOOM", fund["error"])
        self.assertEqual(code, 1)

    def test_timeout_kills_job(self):
        os.environ["FAKE_MODE"] = "hang"
        env = Env(self.tmp.name, [stock("slow")], max_attempts=1, job_timeout_seconds=1.5, grace_seconds=3)
        spec, store, code = env.run()
        self.addCleanup(store.close)
        row = store.get("slow")
        self.assertEqual((row["status"], row["error"]), ("failed", "JOB_TIMEOUT"))
        self.assertEqual(code, 1)

    def test_interrupted_job_resumes_same_run_id(self):
        env = Env(self.tmp.name, [stock("keep")])
        spec = env.spec()
        store = storemod.open_store(spec)
        self.addCleanup(store.close)
        store.sync(spec)
        store.start("keep", 99999999, 1.0)
        self.assertEqual(store.get("keep")["status"], "running")
        self.assertEqual(Scheduler(spec, store, out=lambda m: None).run(), 0)
        row = store.get("keep")
        self.assertEqual((row["status"], row["attempt"], row["run_id"]), ("completed", 1, "t1-keep"))

    def test_in_flight_model_requires_new_attempt(self):
        spec = Env(self.tmp.name, [stock("keep")]).spec()
        store = storemod.open_store(spec)
        self.addCleanup(store.close)
        store.sync(spec)
        store.start("keep", 99999999, 1.0)
        stage = pathlib.Path(report.run_dir_of(spec, "t1-keep")) / "stages" / "00.json"
        specmod.write_json(stage, {"status": "running", "pending": "model", "calls": 2,
                                  "user_agent": {"model_calls": 1, "events": [{"usage": {
                                      "prompt_tokens": 12, "completion_tokens": 3}}]}})
        scheduler = Scheduler(spec, store, out=lambda m: None)
        self.assertEqual(scheduler.run(), 0)
        row = store.get("keep")
        self.assertEqual((row["status"], row["run_id"]), ("completed", "t1-keep-r2"))
        self.assertEqual(json.loads(row["history"])[0]["error"], "INTERRUPTED_WITHOUT_SAFE_CHECKPOINT")
        totals = report.aggregate(report.collect(spec, store))
        self.assertEqual((totals["model_calls"], totals["user_agent_calls"], totals["total_tokens"]), (5, 1, 45))

    def test_template_drift_is_rejected_before_retry(self):
        env = Env(self.tmp.name, [stock("a")])
        spec = env.spec()
        store = storemod.open_store(spec)
        self.addCleanup(store.close)
        store.sync(spec)
        path = env.folder / "template.json"
        cfg = specmod.read_json(path)
        cfg["model"] = "different-model"
        specmod.write_json(path, cfg)
        with self.assertRaisesRegex(storemod.StoreError, "batch_id"):
            store.sync(env.spec())
        self.assertEqual(store.get("a")["status"], "pending")

    def test_changed_definition_is_rejected(self):
        env = Env(self.tmp.name, [stock("a")])
        spec, store, _ = env.run()
        store.close()
        env.config["jobs"] = [{"id": "a", "task": {"kind": "synthetic_demo", "asset": "FUND"}}]
        env.path.write_text(json.dumps(env.config), encoding="utf-8")
        spec2 = env.spec()
        store2 = storemod.open_store(spec2)
        self.addCleanup(store2.close)
        with self.assertRaises(storemod.StoreError):
            store2.sync(spec2)

    def test_requeue_failed_job(self):
        jobs = [{"id": "fund", "task": {"kind": "synthetic_demo", "asset": "FUND"}}]
        spec, store, _ = Env(self.tmp.name, jobs).run()
        self.addCleanup(store.close)
        self.assertEqual(store.requeue(spec, ["fund"]), ["fund"])
        row = store.get("fund")
        self.assertEqual((row["status"], row["attempt"], row["attempt_limit"], row["run_id"]),
                         ("pending", 3, 4, "t1-fund-r3"))
        self.assertEqual(store.requeue(spec, ["fund"]), [])   # 非 failed 不处理

    def test_spec_validation(self):
        bad = [
            {"batch_id": "Bad_ID"},
            {"jobs": [stock("a"), stock("a")]},
            {"jobs": [{"id": "a", "task": {"kind": "synthetic_demo", "asset": "BOND"}}]},
            {"jobs": [stock("a", overrides={"run_id": "hack"})]},
            {"jobs": [stock("a", overrides={"runs_root": "/tmp"})]},
            {"jobs": [{"id": "a", "task": {"kind": "bound_task", "binding": "missing.json"}}]},
            {"jobs": [stock("x" * 25)]},
            {"concurrency": 0},
            {"max_attempts": True},
            {"unknown_field": 1},
        ]
        for patch in bad:
            with self.subTest(patch=patch):
                folder = tempfile.mkdtemp(dir=self.tmp.name)
                env = Env(folder, [stock("ok")])
                env.config.update(patch)
                env.path.write_text(json.dumps(env.config), encoding="utf-8")
                with self.assertRaises(specmod.SpecError):
                    env.spec()

    def test_bound_task_binding_is_resolved(self):
        folder = pathlib.Path(self.tmp.name)
        env = Env(folder, [{"id": "b", "task": {"kind": "bound_task", "binding": "inputs/binding.private.json"}}])
        (folder / "inputs").mkdir()
        (folder / "inputs" / "binding.private.json").write_text("{}", encoding="utf-8")
        job = env.spec()["jobs"][0]
        self.assertEqual(job["task"]["binding"], str(folder / "inputs" / "binding.private.json"))

    def test_report_files(self):
        jobs = [stock("ok"), {"id": "fund", "task": {"kind": "synthetic_demo", "asset": "FUND"}}]
        spec, store, _ = Env(self.tmp.name, jobs).run()
        self.addCleanup(store.close)
        document, json_path, csv_path = report.write_report(spec, store)
        self.assertEqual(document["summary"]["by_status"], {"completed": 1, "failed": 1})
        done = next(j for j in document["jobs"] if j["job_id"] == "ok")
        self.assertEqual((done["segments"], done["total_return"]), (7, 0.0635))
        self.assertEqual(done["usage"]["total_tokens"], 30)
        self.assertTrue(os.path.isfile(json_path) and os.path.isfile(csv_path))
        self.assertIn("ok", pathlib.Path(csv_path).read_text(encoding="utf-8-sig"))

    def test_second_controller_is_refused(self):
        batch_dir = os.path.join(self.tmp.name, "lock")
        with exclusive(batch_dir):
            with self.assertRaises(BatchBusy):
                with exclusive(batch_dir):
                    pass
        with exclusive(batch_dir):   # 释放后可再次获取
            pass

    def test_command_prefix_and_flags(self):
        env = Env(self.tmp.name, [stock("a")], command_prefix=["wrap", "--x"])
        spec = env.spec()
        store = storemod.open_store(spec)
        self.addCleanup(store.close)
        cmd = Scheduler(spec, store, out=lambda m: None).command("cfg.json")
        self.assertEqual(cmd[:2], ["wrap", "--x"])
        self.assertEqual(cmd[-5:], ["-B", FAKE, "start", "--config", "cfg.json"])

    def test_cli_run_status_report(self):
        env = Env(self.tmp.name, [stock("a")])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(batchctl.main(["run", str(env.path), "--no-validate"]), 0)
            self.assertTrue((pathlib.Path(env.spec()["batch_dir"]) / "report.json").is_file())
            self.assertTrue((pathlib.Path(env.spec()["batch_dir"]) / "report.csv").is_file())
            self.assertEqual(batchctl.main(["status", str(env.path)]), 0)
            self.assertEqual(batchctl.main(["report", str(env.path)]), 0)
        self.assertIn("completed", out.getvalue())
        # 已完成的批次再次运行不会重跑
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batchctl.main(["run", str(env.path), "--no-validate"]), 0)
        spec = env.spec()
        starts = (pathlib.Path(spec["batch_dir"]) / "runs" / "_events.log").read_text().count("start ")
        self.assertEqual(starts, 1)


if __name__ == "__main__":
    unittest.main()
