"""Automated admission across runtime/handoff, with disposable fabricated inputs.

All model events, prices and receipts here are PROGRAM-FABRICATED test evidence.
No live model, upstream, Docker or human approval happens in these tests. The
fixture flag is altered only to cover the real-delivery branch, never reported
as a real market-data acceptance.
"""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from finlab.common import ROOT, LabError, digest, file_hash, read, write
from finlab.archive import bound_package
from finlab.delivery import approved, export_handoff, import_handoff, inspect_handoff
from finlab.demo import build as build_demo
from finlab.runtime import create_episode
from finlab.submission import check as check_submission
from taskgen.registry import Registry
from taskgen.workflow import Production
import test_delivery_roundtrip as fixtures


class MachineDeliveryTests(unittest.TestCase):
    environment = fixtures.DeliveryRoundtripTests.environment
    fabricated_research_evidence = fixtures.DeliveryRoundtripTests.fabricated_research_evidence

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='machine-delivery-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin, self.receiver = self.root / 'origin', self.root / 'receiver'
        for target in (self.origin, self.receiver):
            target.mkdir()
            for name in ('catalog', 'schemas', 'prompts', 'config'):
                shutil.copytree(ROOT / name, target / name)
            write(target / 'deployment/images.lock.json', {'schema_version': 'finlab.images.v2', 'images': fixtures.TEST_IMAGES})
        self.app = Production(self.origin)
        self.app.init()  # Historical comparison identities are not human approvals.
        self.spec = read(ROOT / 'examples/first_batch.json')[0]
        self.spec['title'] = 'PROGRAM-FABRICATED automatic duplicate delivery only'
        self.spec['data_requirements']['symbols'] = ['000001.SZ']
        self.spec['bridge_contract']['symbols'] = ['000001.SZ']
        self.spec['bridge_contract']['endpoints'] = [
            {'api_name': 'daily', 'fields': ['ts_code','trade_date','open','high','low','close','vol'],
             'frequency': 'daily', 'visibility': 'available_at_lte_controller_clock'},
            {'api_name': 'index_daily', 'fields': ['ts_code','trade_date','close'],
             'frequency': 'daily', 'visibility': 'available_at_lte_controller_clock'}]
        batch = self.app.new_batch('fabricated_machine_test_only')
        candidate = self.app.add_candidate(batch, copy.deepcopy(self.spec), 'fabricated_machine_test_only')
        checked = self.app.check_candidate(candidate)
        self.assertTrue(checked['passed'], checked)
        neighbors = checked['dedup']['nearest']
        self.assertTrue(neighbors)
        review = {'classification': 'duplicate', 'closest_task_id': neighbors[0]['task_id'],
                  'comparisons': [{'task_id': x['task_id'], 'similarities': ['same synthetic mechanism'],
                                   'differences': ['PROGRAM-FABRICATED no novel question claim']} for x in neighbors],
                  'rationale': 'PROGRAM-FABRICATED event only; no live model, market or human claim.',
                  'issues': [], 'public_information_safe': True, 'long_horizon_valid': True,
                  'mode': 'model_api', 'status': 'needs_attention', 'validation_errors': [],
                  **{k: checked[k] for k in ('spec_hash','context_hash','content_hash')}}
        write(candidate / 'operator/review.json', review)
        self.app.registry.event(candidate.name, 'model_reviewed', 'PROGRAM-FABRICATED unit evidence', self.spec, review)
        self.accepted = self.app.accept(candidate.name)
        self.record = self.app.registry.resolve_candidate(self.accepted['candidate_id'])
        self.fixture = self.origin / 'fixture'
        build_demo(self.fixture, resources=fixtures.TEST_RESOURCES)
        manifest = read(self.fixture / 'frozen/manifest.private.json')
        manifest['synthetic'] = False  # Branch coverage only, these are fabricated prices.
        manifest.pop('manifest_hash'); manifest['manifest_hash'] = digest(manifest)
        write(self.fixture / 'frozen/manifest.private.json', manifest)
        binding = read(self.fixture / 'binding.private.json')
        binding.update(candidate_id=self.record['candidate_id'], approved_spec_hash=self.record['spec_hash'],
                       contract_review_confirmed=True)
        self.episode = self.origin / 'episode'
        with self.environment(self.origin): create_episode(binding, self.episode)
        self.binding = read(self.episode / 'binding.private.json')
        self.submission = self.fixture / 'submission'
        self.submission_hash = check_submission(self.submission)['submission_hash']
        self.receipt = self.fabricated_research_evidence()
        self.archive = self.origin / 'FABRICATED_MACHINE_HANDOFF.zip'

    def test_automatic_duplicate_runtime_and_public_do_not_claim_human_approval(self):
        self.assertEqual(self.record['approval_method'], 'automated')
        self.assertFalse(self.record['counts_new_question'])
        self.assertEqual(self.binding['acceptance_method'], 'automated')
        self.assertEqual(self.binding['acceptance_novelty'], 'duplicate')
        with self.app.registry.connect() as db:
            self.assertEqual(db.execute('select count(*) from approvals').fetchone()[0], 0)
        with self.environment(self.origin):
            resolved = approved(self.binding)
            bound_package(self.episode, self.origin / 'public')
        self.assertEqual(resolved['spec_hash'], self.record['spec_hash'])
        text = (self.origin / 'public/TASK.md').read_text(encoding='utf-8')
        self.assertIn('自动接纳', text)
        self.assertIn('未经过人工审批', text)
        self.assertNotIn('已人工批准', text)
        entry = read(self.origin / 'public/task_input.draft.json')
        self.assertEqual(entry['spec_status'], 'spec_accepted')
        self.assertEqual(entry['acceptance_method'], 'automated')
        self.assertFalse(entry['human_approved'])

    def test_automatic_duplicate_handoff_roundtrip_without_fabricated_mother(self):
        with self.environment(self.origin):
            exported = export_handoff(self.episode, self.submission, self.archive)
        self.assertTrue(exported['passed'])
        manifest = inspect_handoff(self.archive)
        self.assertEqual(manifest['acceptance_method'], 'automated')
        self.assertEqual(manifest['acceptance_novelty'], 'duplicate')
        self.assertNotIn('parent_approval_hash', manifest)
        with zipfile.ZipFile(self.archive) as z:
            self.assertNotIn('platform_private/parent_approval.json', z.namelist())
        destination = self.receiver / 'imported'
        with self.environment(self.receiver):
            result = import_handoff(self.archive, destination, 'Explicit trust of fabricated unit transfer')
        self.assertTrue(result['passed'])
        registry = Registry(self.receiver / 'registry/tasks.sqlite')
        transferred = registry.resolve_candidate(self.record['candidate_id'])
        self.assertEqual(transferred['spec_hash'], self.record['spec_hash'])
        self.assertEqual(transferred['approval_method'], 'automated')
        self.assertEqual(transferred['novelty'], 'duplicate')
        self.assertFalse(transferred['counts_new_question'])
        with registry.connect() as db:
            self.assertEqual(db.execute('select count(*) from approvals').fetchone()[0], 0)
        self.assertIsNone(read(destination / 'binding.imported.private.json')['runtime']['resources'])

    def test_machine_manifest_cannot_be_relabelled_human(self):
        with self.environment(self.origin): export_handoff(self.episode, self.submission, self.archive)
        with zipfile.ZipFile(self.archive) as z: members = {n: z.read(n) for n in z.namelist()}
        manifest = json.loads(members['HANDOFF_MANIFEST.json'])
        manifest['acceptance_method'] = 'human'
        members['HANDOFF_MANIFEST.json'] = json.dumps(manifest).encode()
        changed = self.origin / 'tampered-method.zip'
        with zipfile.ZipFile(changed, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, body in members.items(): z.writestr(name, body)
        with self.assertRaisesRegex(LabError, 'HANDOFF_ACCEPTANCE_MISMATCH'):
            inspect_handoff(changed)


if __name__ == '__main__': unittest.main()
