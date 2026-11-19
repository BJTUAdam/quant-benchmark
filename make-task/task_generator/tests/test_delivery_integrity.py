"""Private transfer integrity, using synthetic bytes and mocked source authority.

These tests do not create an approval in the working registry, make API calls,
or claim that synthetic receipt bytes are real Docker acceptance evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from finlab.common import LabError, digest, file_hash, read, write
from finlab.delivery import _smoke_receipt, import_handoff, inspect_handoff, _RESEARCH_REQUIRED_FILES
from finlab.tasks import PUBLIC_FILES, SUBMISSION_FILES

ROOT = Path(__file__).resolve().parents[1]


class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = read(ROOT / 'examples/first_batch.json')[0]
        self.record = {'task_id': 'test-approved', 'candidate_id': 'test-approved', 'version': 1,
                       'action': 'approved', 'spec': self.spec, 'spec_hash': digest(self.spec)}
        self.images = {'engine': 'sha256:' + 'e' * 64, 'worker': 'sha256:' + 'f' * 64}
        self.binding = {'schema_version': 'finlab.binding.v2', 'feeds': [],
                        'candidate_id': self.record['candidate_id'], 'approved_spec_hash': self.record['spec_hash'],
                        'frozen_dir': 'platform_private/frozen', 'contract_review_confirmed': True,
                        'runtime': {'frequency': self.spec['runtime_profile']['frequency'], 'resources': None}}
        self.freeze = {'dataset_id': 'test-dataset', 'manifest_hash': 'a' * 64, 'synthetic': False,
                       'apis': {e['api_name']: {'fields': {f: 'test' for f in e['fields']}}
                                for e in self.spec['bridge_contract']['endpoints']}}
        self.receipt = {'research': True, 'segment': 0, 'job_id': 'research_fixture',
                        'submission_hash': 'b' * 64, 'dataset_id': self.freeze['dataset_id'],
                        'images': self.images, 'hashes': {n: 'c' * 64 for n in _RESEARCH_REQUIRED_FILES},
                        'committed_at': '2026-01-01T00:00:00+00:00'}
        self.preflight = {'passed': True, 'submission_hash': self.receipt['submission_hash'],
                          'image_ids': self.images,
                          'approval': {k: self.record[k] for k in ('candidate_id', 'task_id', 'version', 'spec_hash')}}
        self.payload = {
            'platform_private/approval.json': self.record,
            'platform_private/binding.private.json': self.binding,
            'platform_private/research_acceptance.json': self.receipt,
            'platform_private/preflight.private.json': self.preflight,
            'platform_private/frozen/manifest.private.json': self.freeze,
            'platform_private/frozen/snapshot.sqlite': b'synthetic fixture only, not a real database',
            **{'participant_public/' + n: b'public synthetic fixture' for n in PUBLIC_FILES},
            **{'submission/' + n: b'submission synthetic fixture' for n in SUBMISSION_FILES},
        }
        self.manifest = {'schema_version': 'finlab.private_handoff.v1', 'audience': 'trusted_platform_operator_only',
                         'version': 1, 'task_id': self.record['task_id'], 'dataset_id': self.freeze['dataset_id'],
                         'approved_spec_hash': self.record['spec_hash'], 'freeze_hash': self.freeze['manifest_hash'],
                         'submission_hash': self.receipt['submission_hash'], 'image_ids': self.images,
                         'origin_research_verified': True, 'destination_environment_verified': False}

    def archive(self, *, payload=None, manifest=None):
        payload = copy.deepcopy(self.payload if payload is None else payload)
        manifest = copy.deepcopy(self.manifest if manifest is None else manifest)
        bodies = {n: v if isinstance(v, bytes) else json.dumps(v, ensure_ascii=False).encode('utf-8')
                  for n, v in payload.items()}
        manifest['files'] = {n: hashlib.sha256(v).hexdigest() for n, v in bodies.items()}
        archive = self.root / ('archive-' + str(len(list(self.root.glob('*.zip')))) + '.zip')
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
            for n, body in bodies.items(): z.writestr(n, body)
            z.writestr('HANDOFF_MANIFEST.json', json.dumps(manifest))
        return archive

    def test_exact_complete_allowlist_passes_without_extracting(self):
        report = inspect_handoff(self.archive())
        self.assertEqual(set(report['files']), set(self.payload))

    def test_missing_submission_is_rejected_even_with_correct_manifest_hashes(self):
        payload = copy.deepcopy(self.payload)
        payload.pop('submission/CHANGELOG.md')
        with self.assertRaisesRegex(LabError, 'HANDOFF_REQUIRED_FILE_MISSING'):
            inspect_handoff(self.archive(payload=payload))

    def test_unsafe_windows_and_traversal_paths_are_rejected(self):
        for name in ('../escape.txt', 'platform_private/NUL', 'submission/strategy.py:secret'):
            with self.subTest(name=name):
                payload = {**self.payload, name: b'unsafe'}
                with self.assertRaisesRegex(LabError, 'HANDOFF_UNSAFE_PATH'):
                    inspect_handoff(self.archive(payload=payload))

    def test_destination_verified_claim_and_boolean_version_are_rejected(self):
        for mutation in ({'version': True}, {'destination_environment_verified': True},
                         {'origin_research_verified': False}):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(LabError, 'HANDOFF_SCHEMA_REQUIRED'):
                    inspect_handoff(self.archive(manifest={**self.manifest, **mutation}))

    def test_variant_requires_complete_parent_link_and_matching_canonical_hash(self):
        payload = copy.deepcopy(self.payload)
        parent = {**copy.deepcopy(self.record), 'artifacts': {'status': 'captured'}}
        child = copy.deepcopy(self.record)
        child.update(candidate_id='test-variant', action='variant')
        payload['platform_private/approval.json'] = child
        with self.assertRaisesRegex(LabError, 'PARENT_HANDOFF_REQUIRED'):
            inspect_handoff(self.archive(payload=payload))
        payload['platform_private/parent_approval.json'] = parent
        manifest = {**self.manifest, 'parent_approval_hash': digest(parent)}
        inspect_handoff(self.archive(payload=payload, manifest=manifest))
        # Rehashing the member after a mutation cannot hide its pinned identity.
        payload['platform_private/parent_approval.json']['created_at'] = 'mutated'
        with self.assertRaisesRegex(LabError, 'HANDOFF_PARENT_MISMATCH'):
            inspect_handoff(self.archive(payload=payload, manifest=manifest))

    def test_regular_task_cannot_smuggle_parent_record(self):
        payload = copy.deepcopy(self.payload)
        payload['platform_private/parent_approval.json'] = self.record
        with self.assertRaisesRegex(LabError, 'HANDOFF_UNEXPECTED_FILE'):
            inspect_handoff(self.archive(payload=payload))
        with self.assertRaisesRegex(LabError, 'HANDOFF_PARENT_MISMATCH'):
            inspect_handoff(self.archive(payload=payload, manifest={**self.manifest, 'parent_approval_hash': digest(self.record)}))

    def import_fixture(self, archive, destination):
        # Trust provenance/market validation are separate units. Here the mocks
        # let metadata consistency be tested without importing any authority.
        with patch('finlab.data.FrozenView', return_value=SimpleNamespace(manifest=self.freeze)), \
                patch('finlab.tasks.validate', return_value=[]), \
                patch('finlab.submission.check', return_value={'passed': True, 'submission_hash': self.receipt['submission_hash']}), \
                patch('taskgen.registry.Registry') as registry:
            result = import_handoff(archive, destination, 'synthetic unit fixture; no actual approval transfer')
            registry.return_value.import_approved.assert_called_once()
            return result

    def test_import_rejects_dataset_task_version_and_locator_mismatch(self):
        mutations = ({'dataset_id': 'wrong-dataset'}, {'task_id': 'wrong-task'}, {'version': 2})
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                dest = self.root / f'invalid-{index}'
                with self.assertRaisesRegex(LabError, 'HANDOFF_BINDING_MISMATCH'):
                    self.import_fixture(self.archive(manifest={**self.manifest, **mutation}), dest)
                self.assertFalse(dest.exists())
        payload = copy.deepcopy(self.payload)
        payload['platform_private/binding.private.json']['frozen_dir'] = 'D:/source-machine/private-data'
        with self.assertRaisesRegex(LabError, 'HANDOFF_BINDING_MISMATCH'):
            self.import_fixture(self.archive(payload=payload), self.root / 'invalid-locator')

    def test_import_rejects_empty_acceptance_and_mismatched_preflight(self):
        for mutation in ('empty_hashes', 'mismatched_preflight'):
            payload = copy.deepcopy(self.payload)
            if mutation == 'empty_hashes':
                payload['platform_private/research_acceptance.json']['hashes'] = {}
                expected = 'HANDOFF_ACCEPTANCE_MISMATCH'
            else:
                payload['platform_private/preflight.private.json']['approval']['version'] = 2
                expected = 'HANDOFF_PREFLIGHT_MISMATCH'
            with self.assertRaisesRegex(LabError, expected):
                self.import_fixture(self.archive(payload=payload), self.root / mutation)

    def test_unknown_binding_fields_or_nested_credential_keys_cannot_cross_handoff(self):
        for mutation in ('unknown_top_level', 'nested_credential'):
            payload = copy.deepcopy(self.payload)
            binding = payload['platform_private/binding.private.json']
            if mutation == 'unknown_top_level':
                binding['extra_source_path'] = 'private/source-only-note'
                expected = 'HANDOFF_BINDING_FIELDS'
            else:
                binding['runtime']['api_token'] = 'unit-test-placeholder-not-a-credential'
                expected = 'HANDOFF_CREDENTIAL_FORBIDDEN'
            with self.assertRaisesRegex(LabError, expected):
                self.import_fixture(self.archive(payload=payload), self.root / mutation)

    def test_imported_public_stays_origin_snapshot_and_resources_are_cleared(self):
        dest = self.root / 'imported'
        result = self.import_fixture(self.archive(), dest)
        self.assertEqual(result['participant_public_status'], 'origin_snapshot_do_not_dispatch')
        self.assertIsNone(read(dest / 'binding.imported.private.json')['runtime']['resources'])
        self.assertEqual(read(dest / 'IMPORT_STATUS.private.json')['participant_public_status'],
                         'origin_snapshot_do_not_dispatch')
        self.assertEqual((dest / 'participant_public/TASK.md').read_bytes(), self.payload['participant_public/TASK.md'])

    def smoke_fixture(self):
        folder = self.root / 'episode'
        job = folder / 'jobs/research_fixture'
        binding = {'dataset_id': 'test-dataset', 'images': self.images,
                   'runtime': {'resources': {'cpus': 2, 'memory_mb': 4096, 'timeout_seconds': 120}}}
        for rel in _RESEARCH_REQUIRED_FILES:
            p = job / 'artifacts' / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'synthetic bytes; no actual engine acceptance')
        public = job / 'artifacts/public/run_manifest.json'
        write(public, {k: self.receipt[k] for k in ('research', 'segment', 'job_id', 'submission_hash', 'dataset_id')})
        receipt = copy.deepcopy(self.receipt)
        receipt['hashes'] = {n: file_hash(job / 'artifacts' / n) for n in _RESEARCH_REQUIRED_FILES}
        write(job / 'receipt.private.json', receipt)
        write(job / 'artifacts/success.private.json', {'status': 'ready_to_commit', 'hashes': receipt['hashes']})
        write(job / 'input/job.private.json', {**{k: receipt[k] for k in ('research', 'segment', 'job_id', 'submission_hash')},
                                              'binding': binding})
        return folder, job, binding, receipt

    def test_smoke_requires_actual_complete_hashes_success_and_job_binding(self):
        folder, job, binding, receipt = self.smoke_fixture()
        self.assertEqual(_smoke_receipt(folder, binding, receipt['submission_hash']), receipt)
        for mutation in ('empty_hashes', 'missing_success', 'changed_binding', 'changed_artifact'):
            with self.subTest(mutation=mutation):
                write(job / 'receipt.private.json', receipt)
                write(job / 'artifacts/success.private.json', {'status': 'ready_to_commit', 'hashes': receipt['hashes']})
                current = copy.deepcopy(binding)
                if mutation == 'empty_hashes':
                    write(job / 'receipt.private.json', {**receipt, 'hashes': {}})
                elif mutation == 'missing_success':
                    (job / 'artifacts/success.private.json').unlink()
                elif mutation == 'changed_binding':
                    current['runtime']['resources']['memory_mb'] = 8192
                else:
                    (job / 'artifacts/public/equity.csv').write_bytes(b'changed synthetic artifact')
                with self.assertRaisesRegex(LabError, 'MATCHING_RESEARCH_ACCEPTANCE_REQUIRED'):
                    _smoke_receipt(folder, current, receipt['submission_hash'])


if __name__ == '__main__':
    unittest.main()
