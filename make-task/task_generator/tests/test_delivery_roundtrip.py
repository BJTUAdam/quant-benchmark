"""Cross-module handoff tests using PROGRAM-FABRICATED evidence only.

No live API, Docker, human approval or market acceptance occurs here. A local
synthetic fixture's flag is changed solely to exercise the real-delivery branch;
all directories/registries/receipts are disposable test-only material. These
tests must never be cited as real-data readiness or a real Docker acceptance.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from finlab.common import ROOT, LabError, digest, file_hash, read, write
from finlab.data import FrozenView
from finlab.demo import build as build_synthetic_demo
from finlab.delivery import export_handoff, import_handoff, inspect_handoff
from finlab.runtime import create_episode, load_episode
from finlab.submission import check as check_submission
from finlab.tasks import PUBLIC_FILES
from taskgen.registry import Registry
from taskgen.workflow import Production


TEST_RESOURCES = {"cpus": 2.0, "memory_mb": 4096, "timeout_seconds": 120}
TEST_IMAGES = {"engine": "sha256:" + "a" * 64, "worker": "sha256:" + "b" * 64}


class DeliveryRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-handoff-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin, self.receiver = self.root / "origin", self.root / "receiver"
        self.real_registry_files = {str(p): file_hash(p) for p in (ROOT / "registry").glob("*.sqlite") if p.is_file()}
        for target in (self.origin, self.receiver):
            target.mkdir()
            for name in ("catalog", "schemas", "prompts", "config"):
                shutil.copytree(ROOT / name, target / name)
            write(target / "deployment/images.lock.json", {"schema_version": "finlab.images.v2", "images": TEST_IMAGES})
        self.app = Production(self.origin)
        self.spec = read(ROOT / "examples/first_batch.json")[0]
        self.spec["title"] = "PROGRAM-FABRICATED roundtrip test only"
        self.spec["data_requirements"]["symbols"] = ["000001.SZ"]
        self.spec["bridge_contract"]["symbols"] = ["000001.SZ"]
        self.spec["bridge_contract"]["endpoints"] = [
            {"api_name": "daily", "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol"],
             "frequency": "daily", "visibility": "available_at_lte_controller_clock"},
            {"api_name": "index_daily", "fields": ["ts_code", "trade_date", "close"],
             "frequency": "daily", "visibility": "available_at_lte_controller_clock"}]
        batch = self.app.new_batch("synthetic_roundtrip_test_only")
        candidate = self.app.add_candidate(batch, copy.deepcopy(self.spec), "synthetic_roundtrip_test_only")
        checked = self.app.check_candidate(candidate)
        self.assertTrue(checked["passed"], checked)
        neighbors = checked["dedup"]["nearest"]
        review = {"classification": "distinct", "closest_task_id": neighbors[0]["task_id"] if neighbors else None,
                  "comparisons": [{"task_id": x["task_id"], "similarities": ["synthetic only"],
                                   "differences": ["synthetic only"]} for x in neighbors],
                  "rationale": "PROGRAM-FABRICATED unit fixture; no model API or human verdict occurred.",
                  "issues": [], "public_information_safe": True, "long_horizon_valid": True,
                  "mode": "model_api", "status": "passed", "validation_errors": [],
                  **{k: checked[k] for k in ("spec_hash", "context_hash", "content_hash")}}
        write(candidate / "operator/review.json", review)
        self.app.registry.event(candidate.name, "model_reviewed", "PROGRAM-FABRICATED test evidence", self.spec, review)
        self.app.approve(candidate.name, "PROGRAM-FABRICATED approval fixture, disposable registry only")
        self.approval = self.app.registry.resolve_approved(candidate.name)
        self.fixture = self.origin / "fabricated-fixture"
        build_synthetic_demo(self.fixture, resources=TEST_RESOURCES)
        manifest = read(self.fixture / "frozen/manifest.private.json")
        # Deliberate branch coverage ONLY. Prices remain fabricated and no
        # acceptance report escapes this TemporaryDirectory.
        manifest["synthetic"] = False
        manifest.pop("manifest_hash")
        manifest["manifest_hash"] = digest(manifest)
        write(self.fixture / "frozen/manifest.private.json", manifest)
        binding = read(self.fixture / "binding.private.json")
        binding.update(candidate_id=candidate.name, approved_spec_hash=self.approval["spec_hash"], contract_review_confirmed=True)
        self.episode = self.origin / "fabricated-episode"
        with self.environment(self.origin):
            create_episode(binding, self.episode)
        self.binding = read(self.episode / "binding.private.json")
        self.submission = self.fixture / "submission"
        self.submission_hash = check_submission(self.submission)["submission_hash"]
        self.receipt = self.fabricated_research_evidence()
        self.archive = self.origin / "PROGRAM_FABRICATED_TEST_ONLY.zip"

    def tearDown(self):
        self.assertEqual({str(p): file_hash(p) for p in (ROOT / "registry").glob("*.sqlite") if p.is_file()},
                         self.real_registry_files, "test must not modify the real project registry")

    def environment(self, target):
        stack = ExitStack()
        for module in ("finlab.delivery", "finlab.runtime", "finlab.common"):
            stack.enter_context(patch(module + ".ROOT", target))
        settings = {"schema_version": "finlab.operator_setup.v1", "method": "human_interactive",
                    "resources": copy.deepcopy(TEST_RESOURCES), "configured_at": "2024-01-01T00:00:00+00:00"}
        stack.enter_context(patch("finlab.runtime.load_settings", return_value=settings))
        stack.enter_context(patch("finlab.setup.load_settings", return_value=settings))
        # Docker is not run. This exercises orchestration and artifact contracts
        # rather than claiming code execution in an actual engine container.
        stack.enter_context(patch("finlab.runtime.docker", return_value=SimpleNamespace(stdout="synthetic-daemon", returncode=0)))
        original_registry = Registry
        def isolated_registry(path):
            self.assertTrue(Path(path).resolve().is_relative_to(self.root.resolve()))
            return original_registry(path)
        stack.enter_context(patch("taskgen.registry.Registry", side_effect=isolated_registry))
        return stack

    def fabricated_research_evidence(self):
        job_id = "PROGRAM_FABRICATED_JOB"
        job = self.episode / "jobs" / job_id
        artifacts = job / "artifacts"
        artifacts.mkdir(parents=True)
        # The checkpoint is never deserialized. It is merely an intentionally
        # inert byte fixture whose hash is checked by the handoff validator.
        (artifacts / "checkpoint.pkl").write_bytes(b"NOT_A_PICKLE_PROGRAM_FABRICATED_TEST_ONLY")
        (artifacts / "bridge_audit.private.sqlite").write_bytes(b"PROGRAM_FABRICATED_AUDIT_BYTES_TEST_ONLY")
        write(artifacts / "memory.json", {"synthetic_test_only": True})
        write(artifacts / "account.private.json", {"synthetic_test_only": True, "cash": 1000000})
        for name in ("equity.csv", "orders.csv", "trades.csv"):
            path = artifacts / "public" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PROGRAM_FABRICATED_TEST_ONLY\n", encoding="utf-8")
        write(artifacts / "public/metrics.json", {"synthetic_test_only": True})
        write(artifacts / "public/run_manifest.json", {
            "research": True, "segment": 0, "job_id": job_id, "submission_hash": self.submission_hash,
            "dataset_id": self.binding["dataset_id"], "synthetic_test_only": True})
        hashes = {p.relative_to(artifacts).as_posix(): file_hash(p) for p in artifacts.rglob("*") if p.is_file()}
        write(artifacts / "success.private.json", {"status": "ready_to_commit", "hashes": hashes,
                                                   "synthetic_test_only": True})
        write(job / "input/job.private.json", {"research": True, "segment": 0, "job_id": job_id,
                                                "submission_hash": self.submission_hash, "binding": self.binding,
                                                "synthetic_test_only": True})
        receipt = {"research": True, "segment": 0, "job_id": job_id, "submission_hash": self.submission_hash,
                   "dataset_id": self.binding["dataset_id"], "images": TEST_IMAGES, "hashes": hashes,
                   "committed_at": "2024-01-01T00:00:00+00:00", "synthetic_test_only": True}
        write(job / "receipt.private.json", receipt)
        return receipt

    def export(self):
        with self.environment(self.origin):
            return export_handoff(self.episode, self.submission, self.archive)

    def make_variant_episode(self):
        """Register a real parent/child relationship only in disposable test DB."""
        self.mother_approval = copy.deepcopy(self.approval)
        variant = copy.deepcopy(self.spec)
        variant["data_requirements"]["date_start"] = "2023-01-01"
        batch = self.app.new_batch("synthetic_variant_roundtrip_test_only")
        candidate = self.app.add_candidate(batch, variant, "synthetic_variant_roundtrip_test_only")
        checked = self.app.check_candidate(candidate)
        self.assertTrue(checked["passed"], checked)
        neighbors = checked["dedup"]["nearest"]
        review = {"classification": "variant", "closest_task_id": self.mother_approval["task_id"],
                  "comparisons": [{"task_id": x["task_id"], "similarities": ["same synthetic mechanism"],
                                   "differences": ["synthetic requested data window changed"]} for x in neighbors],
                  "rationale": "PROGRAM-FABRICATED variant evidence; disposable registry only.",
                  "issues": [], "public_information_safe": True, "long_horizon_valid": True,
                  "mode": "model_api", "status": "passed", "validation_errors": [],
                  **{k: checked[k] for k in ("spec_hash", "context_hash", "content_hash")}}
        write(candidate / "operator/review.json", review)
        self.app.registry.event(candidate.name, "model_reviewed", "PROGRAM-FABRICATED variant review", variant, review)
        self.app.approve(candidate.name, "PROGRAM-FABRICATED data variant, disposable registry only",
                         as_variant=self.mother_approval["task_id"])
        self.approval = self.app.registry.resolve_candidate(candidate.name)
        self.assertEqual(self.approval["action"], "variant")
        binding = read(self.fixture / "binding.private.json")
        binding.update(candidate_id=candidate.name, approved_spec_hash=self.approval["spec_hash"], contract_review_confirmed=True)
        self.episode = self.origin / "fabricated-variant-episode"
        with self.environment(self.origin):
            create_episode(binding, self.episode)
        self.binding = read(self.episode / "binding.private.json")
        self.receipt = self.fabricated_research_evidence()
        self.archive = self.origin / "PROGRAM_FABRICATED_VARIANT_ONLY.zip"

    def rewrite_archive(self, destination, *, member=None, manifest_change=None, rehash=False):
        with zipfile.ZipFile(self.archive) as original:
            members = {n: original.read(n) for n in original.namelist()}
        manifest = json.loads(members["HANDOFF_MANIFEST.json"])
        if member:
            name, value = member
            members[name] = value
            if rehash:
                manifest["files"][name] = hashlib.sha256(value).hexdigest()
        if manifest_change:
            manifest.update(manifest_change)
        members["HANDOFF_MANIFEST.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as output:
            for name, value in members.items():
                output.writestr(name, value)

    def test_export_inspect_import_preserves_version_hashes_and_resets_resources(self):
        exported = self.export()
        self.assertTrue(exported["passed"])
        self.assertEqual(exported["sha256"], file_hash(self.archive))
        manifest = inspect_handoff(self.archive)
        self.assertFalse(manifest["destination_environment_verified"])
        self.assertEqual(manifest["approved_spec_hash"], self.approval["spec_hash"])
        with zipfile.ZipFile(self.archive) as package:
            self.assertEqual({n.removeprefix("participant_public/") for n in package.namelist()
                              if n.startswith("participant_public/")}, PUBLIC_FILES)
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256(package.read(name)).hexdigest(), expected)
            portable_binding = json.loads(package.read("platform_private/binding.private.json"))
            self.assertEqual(portable_binding["frozen_dir"], "platform_private/frozen")
            self.assertIsNone(portable_binding["runtime"]["resources"])
            self.assertNotIn("resource_setup", portable_binding)
            self.assertFalse(any("checkpoint" in n or "bridge_audit" in n for n in package.namelist()))
        destination = self.receiver / "imported"
        with self.environment(self.receiver):
            imported = import_handoff(self.archive, destination, "Explicit trusted PROGRAM-FABRICATED test source")
        self.assertTrue(imported["passed"])
        self.assertFalse(imported["destination_environment_verified"])
        registry = Registry(self.receiver / "registry/tasks.sqlite")
        record = registry.resolve_approved(self.approval["task_id"], self.approval["version"])
        for name in ("candidate_id", "task_id", "version", "spec_hash", "review_hash", "spec", "artifacts"):
            self.assertEqual(record[name], self.approval[name])
        self.assertTrue(record["provenance"]["trusted_import"])
        relocated = read(destination / "binding.imported.private.json")
        self.assertEqual(relocated["frozen_dir"], str(destination / "platform_private/frozen"))
        self.assertIsNone(relocated["runtime"]["resources"])
        self.assertEqual(relocated["runtime"]["resource_status"], "requires_human_setup")
        self.assertFalse((self.receiver / ".finlab/operator.json").exists())
        frozen = FrozenView(destination / "platform_private/frozen")
        self.assertEqual(frozen.manifest["manifest_hash"], self.binding["freeze_hash"])
        self.assertEqual(frozen.manifest["dataset_id"], self.binding["dataset_id"])
        self.assertEqual(file_hash(frozen.folder / "snapshot.sqlite"),
                         file_hash(self.fixture / "frozen/snapshot.sqlite"))
        self.assertEqual(check_submission(destination / "submission")["submission_hash"], self.submission_hash)
        self.assertEqual(self.app.registry.resolve_approved(self.approval["task_id"]), self.approval)
        self.assertEqual(len(self.app.registry.bindings(self.approval["candidate_id"])), 1)
        with registry.connect() as db:
            self.assertEqual([r[0] for r in db.execute("select action from events")], ["trusted_import"])

    def test_changed_member_fails_before_any_destination_approval_or_extraction(self):
        self.export()
        changed = self.root / "changed-byte.zip"
        self.rewrite_archive(changed, member=("participant_public/TASK.md", b"tampered"))
        destination = self.receiver / "should-not-exist"
        with self.environment(self.receiver), self.assertRaisesRegex(LabError, "HANDOFF_HASH_CHANGED"):
            import_handoff(changed, destination, "Trust does not authorize corrupted bytes")
        self.assertFalse(destination.exists())
        self.assertFalse((self.receiver / "registry/tasks.sqlite").exists())

    def test_self_consistent_member_hash_does_not_hide_dataset_linkage_tamper(self):
        self.export()
        changed = self.root / "changed-dataset.zip"
        self.rewrite_archive(changed, manifest_change={"dataset_id": "forged-dataset"})
        inspect_handoff(changed)  # Per-file checksums alone cannot prove linkage.
        destination = self.receiver / "should-not-exist"
        with self.environment(self.receiver), self.assertRaisesRegex(LabError, "HANDOFF_BINDING_MISMATCH"):
            import_handoff(changed, destination, "Trust does not authorize inconsistent dataset identity")
        self.assertFalse(destination.exists())
        self.assertFalse((self.receiver / "registry/tasks.sqlite").exists())

    def test_changed_research_artifact_prevents_export(self):
        path = self.episode / "jobs" / self.receipt["job_id"] / "artifacts/public/equity.csv"
        path.write_text("changed after acceptance", encoding="utf-8")
        with self.environment(self.origin), self.assertRaisesRegex(LabError, "MATCHING_RESEARCH_ACCEPTANCE_REQUIRED"):
            export_handoff(self.episode, self.submission, self.archive)
        self.assertFalse(self.archive.exists())

    def test_variant_roundtrip_imports_approved_mother_then_child_with_exact_lineage(self):
        self.make_variant_episode()
        self.export()
        manifest = inspect_handoff(self.archive)
        self.assertEqual(manifest["parent_approval_hash"], digest(self.mother_approval))
        with zipfile.ZipFile(self.archive) as package:
            parent = json.loads(package.read("platform_private/parent_approval.json"))
            child = json.loads(package.read("platform_private/approval.json"))
            self.assertEqual(parent, self.mother_approval)
            self.assertEqual(child["task_id"], parent["task_id"])
            self.assertNotEqual(child["candidate_id"], parent["candidate_id"])
            self.assertEqual(child["action"], "variant")
        destination = self.receiver / "imported-variant"
        with self.environment(self.receiver):
            result = import_handoff(self.archive, destination, "Explicit trust of PROGRAM-FABRICATED parent and child")
        self.assertTrue(result["passed"])
        registry = Registry(self.receiver / "registry/tasks.sqlite")
        imported_parent = registry.resolve_candidate(self.mother_approval["candidate_id"])
        imported_child = registry.resolve_candidate(self.approval["candidate_id"])
        for field in ("candidate_id", "task_id", "version", "spec_hash", "review_hash", "spec", "artifacts"):
            self.assertEqual(imported_parent[field], self.mother_approval[field])
            self.assertEqual(imported_child[field], self.approval[field])
        self.assertTrue(imported_parent["provenance"]["trusted_import"])
        self.assertTrue(imported_child["provenance"]["trusted_import"])
        with registry.connect() as db:
            actions = [(r["candidate_id"], r["action"]) for r in db.execute("select * from events order by id")]
            self.assertEqual(actions, [(self.mother_approval["candidate_id"], "trusted_import"),
                                       (self.approval["candidate_id"], "trusted_import")])
            self.assertEqual(db.execute("select parent_id from variants where variant_id=?",
                                        (self.approval["candidate_id"],)).fetchone()[0], self.mother_approval["task_id"])
        relocated = read(destination / "binding.imported.private.json")
        self.assertIsNone(relocated["runtime"]["resources"])
        self.assertEqual(relocated["approved_spec_hash"], self.approval["spec_hash"])
        self.assertEqual(FrozenView(destination / "platform_private/frozen").manifest["manifest_hash"], self.binding["freeze_hash"])
        self.assertEqual(self.app.registry.resolve_candidate(self.approval["candidate_id"]), self.approval)
        self.assertEqual(self.app.registry.resolve_candidate(self.mother_approval["candidate_id"]), self.mother_approval)

    def test_variant_parent_hash_and_identity_tamper_refused_before_import(self):
        self.make_variant_episode()
        self.export()
        parent = copy.deepcopy(self.mother_approval)
        parent["task_id"] = "forged-parent-task"
        parent["approval"]["task_id"] = "forged-parent-task"
        attacks = (
            ("parent-hash", None, {"parent_approval_hash": "0" * 64}, False),
            ("parent-identity", ("platform_private/parent_approval.json", json.dumps(parent).encode()),
             {"parent_approval_hash": digest(parent)}, True))
        for name, member, change, rehash in attacks:
            with self.subTest(attack=name):
                damaged = self.root / (name + ".zip")
                self.rewrite_archive(damaged, member=member, manifest_change=change, rehash=rehash)
                destination = self.receiver / ("rejected-" + name)
                with self.environment(self.receiver), self.assertRaisesRegex(LabError, "HANDOFF_PARENT_MISMATCH"):
                    import_handoff(damaged, destination, "Source trust cannot repair inconsistent mother evidence")
                self.assertFalse(destination.exists())
                self.assertFalse((self.receiver / "registry/tasks.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
