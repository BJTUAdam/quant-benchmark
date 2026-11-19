"""Contract tests: completion, evidence integrity, errors and comparability."""
import copy
import importlib.util
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench", HERE / "bench.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.project = self.base / "project"
        self.audit_dir = self.base / "audits"
        self.output = self.base / "results"
        self.task_path = HERE / "tasks/star50_trend.json"
        self.task, self.case = b.load_task(self.task_path, "star50_479_dev")
        self.run = "test-1"
        self.root = self.project / "autonomous_runs" / self.run
        (self.root / "events").mkdir(parents=True)
        self.audit_dir.mkdir()
        package = self.project / "star50_trend_task_v1/star50_trend_task_v1"
        (package / "data").mkdir(parents=True)
        b.write_json(package / "data/splits.json", {k: self.case[k] for k in ("num_days", "research", "segments")})
        b.write_json(package / "MANIFEST.json", {"fixture": True})
        model = {"provider": "test", "model": "fixture", "context_mode": "fresh", "max_model_calls": 80}
        self.paths = []
        for seq, (phase, segments, visible) in enumerate(b.expected_stages()):
            cp = self.root / "checkpoints" / f"{seq:02d}_{phase}"
            (cp / "workspace").mkdir(parents=True)
            self.paths.append(cp)
            if seq:
                for name in ("strategy.py", "RESEARCH.md", "CHANGELOG.md"):
                    (cp / "workspace" / name).write_text("fixture\n", encoding="utf-8")
            event = {"seq": seq, "phase": phase, "segments": segments, "visible_part": visible,
                "model": model, "strategy_sha256": b.digest(cp / "workspace/strategy.py") if seq else None,
                "checkpoint": str(cp.relative_to(self.root)).replace("\\", "/")}
            b.write_json(cp / "_COMPLETE.json", event)
            b.write_json(self.root / "events" / f"{seq:02d}.json", event)
            if phase == "initial_done" or phase.startswith("review"):
                b.write_json(cp / "transcript.json", {"status": "completed", "messages": []})
        (self.paths[-1] / "eval_run").mkdir()
        metrics = {"days": 335, "total_return": 0, "annual_return": 0, "annual_volatility": 0,
                   "sharpe": 0, "max_drawdown": 0, "final_equity": 1000000,
                   "benchmark_return": 0, "excess_return": 0}
        self.summary = {"agent": metrics, "v1_baseline": metrics, "log": []}
        for k, (start, end) in enumerate(self.case["segments"], 1):
            self.summary["log"].append({"segment": k, "t_range": [start, end - 1],
                "change": "新建" if k == 1 else "不改", "num_errors": 0, "metrics": {**metrics, "days": end-start}})
        (self.paths[-1] / "eval_run/summary.md").write_text("fixture\n", encoding="utf-8")
        self.sync_summary()
        b.write_json(self.audit_dir / "future_visibility_audit.json", {"run_id": self.run, "issues": [], "reviews": [{}] * 6})

    def sync_summary(self):
        b.write_json(self.paths[-1] / "eval_run/summary.json", self.summary)
        errors = [r.get("num_errors") for r in self.summary["log"]]
        b.write_json(self.audit_dir / "completion_checks.json", {"run_id": self.run, "phase": "summary_done",
            "checkpoint": str(self.paths[-1].relative_to(self.root)).replace("\\", "/"), "num_errors": errors})
        b.write_json(self.audit_dir / "audit_results.json", {"run_id": self.run,
            "agent": self.summary["agent"], "v1_baseline": self.summary["v1_baseline"], "num_errors": errors,
            "package_hashes_match": True, "independent_metrics_match": True,
            "original_checkpoints_unchanged": True, "strict_v1_baseline_has_no_errors": True,
            "replay": {"summary_matches": True, "segments": [{"segment": k,
                "equity_and_trades_numerically_match": True, "cash_positions_memory_match": True} for k in range(1, 8)]}})

    def run_import(self):
        return b.import_run(self.project, self.run, self.task_path, self.case["case_id"], self.audit_dir,
                            "maintenance_shared_v1", self.output)

    def rejected(self):
        with self.assertRaises((b.Invalid, FileNotFoundError)):
            self.run_import()
        self.assertFalse((self.output / self.run).exists())

    def test_complete_archive_and_report(self):
        row = self.run_import()
        self.assertTrue(row["formal_error_free"])
        self.assertEqual(len(row["stages"]), 16)
        self.assertEqual(row["stages"][7]["max_visible_t"], 286)
        self.assertEqual(len(b.verified_records(self.output)), 1)
        report_dir = self.base / "reports"
        self.assertEqual(b.report(self.output, report_dir), 1)
        report = b.read(report_dir / "results.json")
        self.assertIsNone(report["ranking"])
        self.assertIsNone(report["aggregates"][0]["sharpe_sample_std"])

    def test_partial_run_rejected(self):
        (self.root / "events/15.json").unlink()
        self.rejected()

    def test_missing_error_count_not_zero(self):
        del self.summary["log"][1]["num_errors"]
        self.sync_summary()
        self.rejected()

    def test_nonzero_formal_errors_preserved(self):
        self.summary["log"][1]["num_errors"] = 2
        self.sync_summary()
        row = self.run_import()
        self.assertFalse(row["formal_error_free"])
        self.assertEqual(row["num_errors"], [0, 2, 0, 0, 0, 0, 0])
        report_dir = self.base / "reports"
        b.report(self.output, report_dir)
        self.assertEqual(b.read(report_dir / "results.json")["aggregates"][0]["error_free_runs"], 0)

    def test_strategy_tamper_rejected(self):
        (self.paths[5] / "workspace/strategy.py").write_text("tampered", encoding="utf-8")
        self.rejected()

    def test_audit_from_other_run_rejected(self):
        path = self.audit_dir / "audit_results.json"
        data = b.read(path)
        data["run_id"] = "different-run"
        b.write_json(path, data)
        self.rejected()

    def test_missing_replay_rejected(self):
        path = self.audit_dir / "audit_results.json"
        data = b.read(path)
        data["replay"]["segments"].pop()
        b.write_json(path, data)
        self.rejected()

    def test_wrong_scored_days_rejected(self):
        self.summary["agent"]["days"] = 479
        self.sync_summary()
        self.rejected()

    def test_nonfinite_metrics_rejected(self):
        data = copy.deepcopy(self.summary)
        data["agent"]["sharpe"] = float("nan")
        with self.assertRaises(b.Invalid):
            b.validate_summary(data, self.case)

    def test_wrong_segment_boundary_rejected(self):
        self.summary["log"][2]["t_range"][1] = 287
        self.sync_summary()
        self.rejected()

    def test_duplicate_import_cannot_overwrite(self):
        self.run_import()
        before = b.digest(self.output / self.run / "result.json")
        with self.assertRaises(b.Invalid):
            self.run_import()
        self.assertEqual(before, b.digest(self.output / self.run / "result.json"))

    def test_archived_evidence_tamper_detected(self):
        self.run_import()
        (self.output / self.run / "evidence/final/summary.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(b.Invalid):
            b.verified_records(self.output)

    def test_checkpoint_escape_rejected(self):
        with self.assertRaises(b.Invalid):
            b.contained(self.root, "../outside")

    def test_secret_file_read_forbidden(self):
        with self.assertRaises(b.Invalid):
            b.read(self.base / ".env")

    def test_inherited_initialization_not_end_to_end(self):
        b.write_json(self.paths[1] / "transcript.json", {"kind": "shared_v1_import"})
        with self.assertRaises(b.Invalid):
            b.collect_run(self.project, self.run, self.task, self.case, self.audit_dir, "end_to_end")

    def test_different_budget_separates_comparison_group(self):
        first, _ = b.collect_run(self.project, self.run, self.task, self.case, self.audit_dir, "maintenance_shared_v1")
        for cp in self.paths:
            seq = self.paths.index(cp)
            path = self.root / "events" / f"{seq:02d}.json"
            event = b.read(path)
            event["model"]["cost_control"] = {"budget_cny": 20}
            b.write_json(path, event)
            b.write_json(cp / "_COMPLETE.json", event)
        second, _ = b.collect_run(self.project, self.run, self.task, self.case, self.audit_dir, "maintenance_shared_v1")
        self.assertNotEqual(first["comparison_group"], second["comparison_group"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
