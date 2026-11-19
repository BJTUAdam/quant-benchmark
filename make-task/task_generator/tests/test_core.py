from __future__ import annotations
import copy
import json
import shutil
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from taskgen.dedup import fingerprint, structural_check
from taskgen.io_utils import read_json, write_json, digest
from taskgen.registry import Registry, RegistryError
from taskgen.validation import validate
from taskgen.workflow import Production, WorkflowError

PROJECT = Path(__file__).resolve().parent.parent


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for folder in ("catalog", "schemas", "examples", "prompts", "config"):
            shutil.copytree(PROJECT / folder, self.root / folder)
        self.app = Production(self.root)
        self.app.init()
        samples = read_json(self.root / "examples" / "first_batch.json")
        if isinstance(samples, dict):
            samples = samples.get("tasks", samples.get("candidates", samples.get("specs", [])))
        self.spec = copy.deepcopy(samples[0])

    def tearDown(self):
        self.temp.cleanup()

    def new_spec(self):
        spec = copy.deepcopy(self.spec)
        # A composite fixture is not a claimed real new financial task.
        from taskgen.io_utils import catalog
        all_codes = catalog(self.root, "decisions")["decisions"]
        code = next(c for c in all_codes if c not in spec["required_decisions"])
        spec["required_decisions"].append(code)
        spec["decision_dependencies"].append([spec["required_decisions"][0], code])
        spec["research_modules"].append({"decision_id": code, "description": "仅测试用结构变化", "interaction": "测试依赖说明"})
        return spec

    def stage(self, spec=None):
        batch = self.app.new_batch("test_fixture")
        path = self.app.add_candidate(batch, spec or self.new_spec(), "test_fixture")
        return batch, path

    def reviewed(self, path):
        # Unit fixture: no API call or human approval is represented in delivered examples.
        check = self.app.check_candidate(path)
        neighbors = check["dedup"]["nearest"]
        review = {"classification": "distinct", "closest_task_id": neighbors[0]["task_id"] if neighbors else None,
                  "comparisons": [{"task_id": n["task_id"], "similarities": ["fixture"], "differences": ["fixture"]} for n in neighbors],
                  "rationale": "unit fixture", "issues": [], "public_information_safe": True,
                  "long_horizon_valid": True, "mode": "model_api", "status": "passed",
                  "spec_hash": check["spec_hash"], "context_hash": check["context_hash"], "validation_errors": []}
        review["content_hash"] = check["content_hash"]
        write_json(path / "operator" / "review.json", review)
        self.app.registry.event(path.name, "model_reviewed", "unit test fixture, not a real API call", read_json(path / "spec.json"), review)
        return review

    def test_examples_static_and_no_auto_approval(self):
        imported = self.app.import_examples()
        paths = list((self.root / "runs" / imported["batch_id"] / "candidates").iterdir())
        self.assertEqual(len(paths), 6)
        for path in paths:
            self.assertTrue(read_json(path / "operator" / "validation.json")["passed"])
            self.assertEqual(read_json(path / "state.json")["model_review_status"], "not_run")
            with self.assertRaises(WorkflowError):
                self.app.approve(path.name, "不能用缺失的模型审核入库")
        self.assertFalse(any(r["status"] == "spec_approved" for r in self.app.registry.records()))

    def test_identity_ignores_wording_parameters_samples_family(self):
        other = copy.deepcopy(self.spec)
        other.update(title="同义改写的新标题", research_question="不同文字的相同题", family_id="F06")
        other["required_decisions"].reverse()
        other["decision_dependencies"].reverse()
        other["decision_dependencies"] = [list(reversed(edge)) for edge in other["decision_dependencies"]]
        other["data_requirements"]["symbols"] = ["另一国内标的"]
        other["data_requirements"]["date_start"] = "2023-01-01"
        other["parameter_space"] = [{"name": "lookback", "value": 900}]
        self.assertEqual(fingerprint(self.spec), fingerprint(other))
        result = structural_check(other, [{"task_id": "old", "spec": self.spec}])
        self.assertEqual(result["exact_matches"], ["old"])

    def test_same_family_new_decision_not_exact_duplicate(self):
        self.assertNotEqual(fingerprint(self.spec), fingerprint(self.new_spec()))

    def test_decorative_disconnected_module_rejected(self):
        other = self.new_spec()
        other["decision_dependencies"].pop()
        result = validate(other, self.root)
        self.assertFalse(result["passed"])
        self.assertTrue(any("装饰性" in x for x in result["errors"]))

    def test_edit_after_review_requires_new_review(self):
        _, path = self.stage()
        self.reviewed(path)
        changed = read_json(path / "spec.json")
        changed["title"] += "已修改"
        write_json(path / "spec.json", changed)
        with self.assertRaisesRegex(WorkflowError, "过期"):
            self.app.approve(path.name, "人工意见")

    def test_comparison_pool_change_invalidates_review(self):
        _, path = self.stage()
        self.reviewed(path)
        changed = self.new_spec()
        changed["title"] += "另一个候选"
        from taskgen.io_utils import catalog
        next_target = next(k for k in catalog(self.root, "decisions")["maintenance"] if k not in changed["maintenance_targets"])
        changed["maintenance_targets"].append(next_target)
        self.stage(changed)
        with self.assertRaisesRegex(WorkflowError, "过期"):
            self.app.approve(path.name, "人工意见")

    def test_uncertain_review_cannot_be_approved(self):
        _, path = self.stage()
        review = self.reviewed(path)
        review["classification"] = "uncertain"
        write_json(path / "operator" / "review.json", review)
        with self.assertRaises(WorkflowError):
            self.app.approve(path.name, "意见")

    def test_concurrent_atomic_uniqueness(self):
        spec = self.new_spec()
        registry = Registry(self.root / "concurrency.sqlite")
        def commit(index):
            try:
                registry.event("c" + str(index), "model_reviewed", "unit fixture", spec, {})
                return registry.approve("c" + str(index), spec, {}, "人工测试")
            except RegistryError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(commit, [1, 2]))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(len(registry.records()), 1)

    def test_export_allowlist_and_tampering(self):
        _, path = self.stage()
        self.reviewed(path)
        self.app.approve(path.name, "测试人工批准")
        (path / "public" / "future_quotes.csv").write_text("not real data", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "白名单"):
            self.app.export(path.name)
        (path / "public" / "future_quotes.csv").unlink()
        result = self.app.export(path.name)
        with zipfile.ZipFile(result["archives"][0]) as archive:
            names = set(archive.namelist())
            from finlab.tasks import PUBLIC_FILES
            self.assertEqual(names, {"public/" + f for f in PUBLIC_FILES} | {"public_manifest.json"})
            entry = json.loads(archive.read("public/task_input.draft.json"))
            self.assertIsNone(entry["dataset_id"])
            self.assertIsNone(entry["formal_intervals"])
        spec = read_json(path / "spec.json")
        spec["title"] += "tampered"
        write_json(path / "spec.json", spec)
        with self.assertRaisesRegex(WorkflowError, "改变"):
            self.app.export(path.name)

    def test_unapproved_export_forbidden_and_rejection_logged(self):
        _, path = self.stage()
        with self.assertRaises(WorkflowError):
            self.app.export(path.name)
        state = self.app.reject(path.name, "题目需重写")
        self.assertEqual(state["status"], "rejected")
        with self.app.registry.connect() as db:
            self.assertEqual(db.execute("SELECT action FROM events ORDER BY id DESC").fetchone()[0], "rejected")

    def test_variant_preserves_mother_count_and_rejects_wording_only(self):
        _, parent = self.stage()
        self.reviewed(parent)
        self.app.approve(parent.name, "母题人工批准测试")
        spec = read_json(parent / "spec.json")
        spec["data_requirements"]["date_start"] = "2023-01-01"
        _, child = self.stage(spec)
        self.reviewed(child)
        state = self.app.approve(child.name, "仅数据需求期变化", as_variant=parent.name)
        self.assertEqual(state["status"], "variant")
        self.assertEqual(sum(x["status"] == "spec_approved" for x in self.app.registry.records()), 1)
        same = read_json(parent / "spec.json")
        same["title"] += "换标题"
        _, duplicate = self.stage(same)
        self.reviewed(duplicate)
        with self.assertRaisesRegex(RegistryError, "措辞"):
            self.app.approve(duplicate.name, "仅改题名", as_variant=parent.name)

    def test_revision_uses_canonical_task_identity_and_preserves_versions(self):
        _, parent = self.stage()
        self.reviewed(parent)
        self.app.approve(parent.name, "首版测试批准")
        spec = read_json(parent / "spec.json")
        spec["title"] += "修订版"
        _, child = self.stage(spec)
        self.reviewed(child)
        state = self.app.approve(child.name, "修订说明", update_task=parent.name)
        self.assertEqual(state["version"], 2)
        entry = read_json(child / "public" / "task_input.draft.json")
        self.assertEqual(entry["task_id"], parent.name)
        self.assertEqual(entry["task_version"], 2)
        with self.app.registry.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM versions WHERE task_id=?", (parent.name,)).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
