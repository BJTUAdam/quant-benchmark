"""Immutable approvals and explicit trusted transfers; all evidence is synthetic."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from taskgen.io_utils import digest, read_json, write_json, write_text
from taskgen.registry import Registry, RegistryError
from taskgen.workflow import Production, WorkflowError, archive_review

ROOT = Path(__file__).resolve().parents[1]


class ApprovedReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("catalog", "schemas", "prompts", "config"):
            shutil.copytree(ROOT / name, self.root / name)
        self.app = Production(self.root)
        self.spec = read_json(ROOT / "examples/first_batch.json")[0]

    def candidate(self, spec=None):
        batch = self.app.new_batch("synthetic_reuse_test")
        path = self.app.add_candidate(batch, copy.deepcopy(spec or self.spec), "synthetic_reuse_test")
        check = self.app.check_candidate(path)
        neighbors = check["dedup"]["nearest"]
        review = {"classification": "distinct", "closest_task_id": neighbors[0]["task_id"] if neighbors else None,
                  "comparisons": [{"task_id": row["task_id"], "similarities": ["synthetic"], "differences": ["synthetic"]} for row in neighbors],
                  "rationale": "Synthetic test only, no API or real human approval.", "issues": [],
                  "public_information_safe": True, "long_horizon_valid": True, "mode": "model_api", "status": "passed",
                  "validation_errors": [], **{k: check[k] for k in ("spec_hash", "context_hash", "content_hash")}}
        write_json(path / "operator/review.json", review)
        self.app.registry.event(path.name, "model_reviewed", "synthetic unit fixture", read_json(path / "spec.json"), review)
        return path

    def approved(self):
        path = self.candidate()
        self.app.approve(path.name, "synthetic explicit approval fixture")
        return path, self.app.registry.resolve_approved(path.name)

    def test_historical_reference_is_unresolved_and_no_approval_fabricated(self):
        self.app.init()
        history = self.app.registry.records()[0]["task_id"]
        with self.assertRaisesRegex(RegistryError, "APPROVED_VERSION_NOT_FOUND"):
            self.app.registry.resolve_approved(history)
        self.assertEqual(self.app.approved_catalog()["count"], 0)

    def test_approval_captures_review_and_public_immutable_content(self):
        path, record = self.approved()
        self.assertEqual(record["artifacts"]["status"], "captured")
        self.assertEqual(digest(record["spec"]), record["spec_hash"])
        self.assertEqual(digest(record["artifacts"]["review"]), record["review_hash"])
        self.assertEqual(record["provenance"]["model_event"]["action"], "model_reviewed")
        self.assertFalse(record["provenance"]["trusted_import"])
        (path / "public/TASK.md").write_text("tampered draft", encoding="utf-8")
        self.assertNotEqual(record["artifacts"]["public_files"]["TASK.md"], "tampered draft")
        self.assertEqual(self.app.registry.resolve_approved(path.name), record)

    def test_old_approved_version_survives_update_and_candidate_deletion(self):
        path, first = self.approved()
        changed = copy.deepcopy(self.spec); changed["title"] += " immutable revision"
        updated = self.candidate(changed)
        self.app.approve(updated.name, "synthetic version 2", update_task=path.name)
        shutil.rmtree(self.root / "runs")
        latest = self.app.registry.resolve_approved(path.name)
        old = self.app.registry.resolve_approved(path.name, 1)
        self.assertEqual(latest["version"], 2)
        self.assertEqual(self.app.registry.resolve_candidate(path.name)["version"], 1)
        self.assertEqual(old["spec_hash"], first["spec_hash"])
        self.assertEqual(old["artifacts"]["public_files"], first["artifacts"]["public_files"])
        self.assertEqual(len(self.app.registry.approved_versions()), 2)
        reused = self.app.reuse_approved(path.name, self.root / "reuse-private", 1)
        self.assertFalse(reused["new_approval_created"])
        self.assertEqual(read_json(self.root / "reuse-private/spec.json"), first["spec"])
        self.assertEqual(len(self.app.registry.approved_versions()), 2)

    def test_immutable_spec_or_review_tamper_fails_closed(self):
        path, record = self.approved()
        with self.app.registry.connect() as db:
            db.execute("UPDATE approval_evidence SET review_json='{}' WHERE candidate_id=?", (path.name,))
        with self.assertRaisesRegex(RegistryError, "REVIEW_HASH_MISMATCH"):
            self.app.registry.resolve_approved(path.name)

    def test_postcommit_renderer_failure_cannot_lose_approved_public_snapshot(self):
        path = self.candidate()
        from taskgen.render import build_package as original_render
        calls = 0
        def render(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise PermissionError("synthetic postcommit Windows failure")
            return original_render(*args)
        with patch("taskgen.render.build_package", side_effect=render):
            with self.assertRaises(PermissionError): self.app.approve(path.name, "synthetic explicit approval")
        approved = self.app.registry.resolve_candidate(path.name)
        self.assertEqual(approved["artifacts"]["status"], "captured")
        import json
        entry = json.loads(approved["artifacts"]["public_files"]["task_input.draft.json"])
        self.assertEqual(entry["spec_status"], "spec_approved")
        reused = self.app.reuse_approved(path.name, self.root / "recovery-private")
        self.assertFalse(reused["new_approval_created"])
        self.assertEqual(reused["artifact_status"], "captured")

    def test_unapproved_historical_mother_cannot_authorize_variant(self):
        registry = self.app.registry
        historical = {**self.spec, "id": "history", "status": "historical"}
        registry.import_history([historical])
        child = copy.deepcopy(self.spec)
        child["data_requirements"]["date_start"] = "2023-01-01"
        registry.event("child", "model_reviewed", "synthetic", child, {})
        with self.assertRaisesRegex(RegistryError, "真实人工批准"):
            registry.approve("child", child, {}, "synthetic explicit", as_variant="history")

    def test_reuse_options_resolves_approved_and_unresolved_ids(self):
        self.app.init()
        path = self.candidate()
        options = self.app.reuse_options(path.name)
        self.assertTrue(options["options"])
        self.assertEqual(options["approved_options"], 0)
        self.assertTrue(all(row["reuse_status"] == "unresolved_reference" for row in options["options"]))
        # A separate empty registry can approve the seed without historical identity collision.

    def test_trusted_import_is_explicit_and_preserves_original_provenance(self):
        path, record = self.approved()
        destination = Registry(self.root / "another-platform/tasks.sqlite")
        with self.assertRaisesRegex(RegistryError, "NOTE_REQUIRED"):
            destination.import_approved(record, "")
        result = destination.import_approved(record, "Operator verified source out of band; synthetic fixture")
        self.assertFalse(result["new_approval_created"])
        imported = destination.resolve_approved(path.name)
        self.assertEqual(imported["spec_hash"], record["spec_hash"])
        self.assertEqual(imported["artifacts"], record["artifacts"])
        self.assertTrue(imported["provenance"]["trusted_import"])
        with destination.connect() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT action FROM events")], ["trusted_import"])
        self.assertEqual(destination.import_approved(record, "same trusted transfer")["status"], "already_registered")

    def test_trusted_import_rejects_member_mutation_or_missing_evidence(self):
        _, record = self.approved()
        destination = Registry(self.root / "another-platform/tasks.sqlite")
        for mutation in ("spec", "review", "public", "evidence"):
            changed = copy.deepcopy(record)
            if mutation == "spec": changed["spec"]["title"] += "tamper"
            elif mutation == "review": changed["artifacts"]["review"]["rationale"] = "tamper"
            elif mutation == "public": changed["artifacts"]["public_files"]["TASK.md"] = "tamper"
            else: changed["provenance"]["model_event"]["action"] = "not_a_model_review"
            with self.assertRaisesRegex(RegistryError, "HASH_OR_EVIDENCE"):
                destination.import_approved(changed, "operator trust cannot fix corrupt hash")
        self.assertEqual(destination.approved_versions(), [])

    def test_trusted_import_versions_out_of_order_do_not_rewrite_latest(self):
        path, first = self.approved()
        changed = copy.deepcopy(self.spec); changed["title"] += " revision"
        updated = self.candidate(changed)
        self.app.approve(updated.name, "synthetic revised fixture", update_task=path.name)
        second = self.app.registry.resolve_candidate(updated.name)
        dest = Registry(self.root / "another-platform/tasks.sqlite")
        dest.import_approved(second, "operator verified source")
        self.assertEqual(dest.resolve_approved(path.name)["version"], 2)
        with self.assertRaisesRegex(RegistryError, "NOT_FOUND"):
            dest.resolve_approved(path.name, 1)
        dest.import_approved(first, "operator verified older source")
        self.assertEqual(dest.resolve_approved(path.name)["version"], 2)
        self.assertEqual(dest.resolve_candidate(path.name)["spec_hash"], first["spec_hash"])

    def test_variant_transfer_requires_actual_approved_parent(self):
        path, mother = self.approved()
        changed = copy.deepcopy(self.spec); changed["data_requirements"]["date_start"] = "2023-01-01"
        child = self.candidate(changed)
        self.app.approve(child.name, "synthetic variant fixture", as_variant=path.name)
        variant = self.app.registry.resolve_candidate(child.name)
        dest = Registry(self.root / "another-platform/tasks.sqlite")
        with self.assertRaisesRegex(RegistryError, "PARENT_REQUIRED"):
            dest.import_approved(variant, "operator verified source")
        dest.import_approved(mother, "operator verified source")
        dest.import_approved(variant, "operator verified source")
        self.assertEqual(dest.resolve_candidate(child.name)["spec_hash"], variant["spec_hash"])
        self.assertEqual(sum(row["status"] == "spec_approved" for row in dest.records()), 1)

    def test_binding_index_cannot_claim_another_spec_or_be_mutated(self):
        path, record = self.approved()
        binding = {"binding_id": "synthetic-binding", "dataset_id": "unit", "freeze_hash": "a" * 64,
                   "binding_hash": "b" * 64, "runtime_hash": "c" * 64, "manifest_path": "private/manifest.json",
                   "approved_spec_hash": record["spec_hash"]}
        self.assertEqual(self.app.registry.register_binding(path.name, binding)["status"], "registered_unverified")
        self.assertEqual(self.app.registry.bindings(path.name), [binding])
        changed = {**binding, "approved_spec_hash": "wrong"}
        with self.assertRaisesRegex(RegistryError, "APPROVAL_HASH_MISMATCH"):
            self.app.registry.register_binding(path.name, changed)
        changed = {**binding, "freeze_hash": "d" * 64}
        with self.assertRaisesRegex(RegistryError, "ID_CONFLICT"):
            self.app.registry.register_binding(path.name, changed)

    def test_reuse_refuses_overwrite(self):
        path, _ = self.approved()
        dest = self.root / "reuse-private"
        self.app.reuse_approved(path.name, dest)
        with self.assertRaisesRegex(WorkflowError, "DESTINATION_EXISTS"):
            self.app.reuse_approved(path.name, dest)


class WindowsWritesTests(unittest.TestCase):
    def test_sharing_violation_retries_atomic_replace_and_keeps_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "审查.json"
            write_text(path, "old")
            import os
            replace = os.replace
            error = PermissionError("synthetic sharing violation"); error.winerror = 32
            with patch("taskgen.io_utils.os.replace", side_effect=[error, None]) as mocked, patch("taskgen.io_utils.time.sleep"):
                def update(*args):
                    if mocked.call_count == 1: raise error
                    return replace(*args)
                mocked.side_effect = update
                write_text(path, "新内容")
                self.assertEqual(mocked.call_count, 2)
            self.assertEqual(path.read_text(encoding="utf-8"), "新内容")
            self.assertEqual(list(Path(folder).glob(".taskgen-*")), [])

    def test_nonsharing_failure_preserves_old_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "review.json"
            write_text(path, "old")
            with patch("taskgen.io_utils.os.replace", side_effect=PermissionError("synthetic permanent")):
                with self.assertRaises(PermissionError): write_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(folder).glob(".taskgen-*")), [])

    def test_history_uses_short_name_and_never_overwrites_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            data = {"rationale": "synthetic"}
            path = archive_review(folder, data)
            self.assertEqual(len(path.stem), 16)
            self.assertEqual(archive_review(folder, data), path)
            write_json(path, {"rationale": "different"})
            with self.assertRaisesRegex(WorkflowError, "COLLISION"):
                archive_review(folder, data)


if __name__ == "__main__": unittest.main()
