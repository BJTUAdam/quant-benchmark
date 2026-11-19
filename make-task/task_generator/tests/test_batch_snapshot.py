"""Portable evidence snapshots use temporary, synthetic candidates only."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from taskgen.batch_snapshot import SnapshotError, snapshot, verify_snapshot, distributed_records
from taskgen.io_utils import digest, read_json, write_json
from taskgen.workflow import Production, WorkflowError
from finlab.tasks import PUBLIC_FILES

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "sk-" + "x" * 32


class BatchSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('catalog', 'schemas', 'prompts', 'config'):
            shutil.copytree(ROOT/name, self.root/name)
        (self.root/'examples').mkdir()
        shutil.copy2(ROOT/'examples/first_batch.json', self.root/'examples/first_batch.json')
        self.app = Production(self.root)
        self.app.init()
        self.batch = self.app.new_batch('model_api', {'family_quotas': {'F01': 1}, 'max_candidates': 3})
        spec = read_json(self.root/'examples/first_batch.json')[0]
        self.path = self.app.add_candidate(self.batch, spec, 'unit_model_fixture')
        self.cid = self.path.name
        self.check = read_json(self.path/'operator/validation.json')
        self.assertTrue(self.check['passed'])
        state = read_json(self.path/'state.json')
        state.update(model_review_status='passed', secret_config=FAKE_KEY)
        write_json(self.path/'state.json', state)
        self.review = {'classification': 'distinct', 'closest_task_id': None, 'comparisons': [],
            'rationale': 'Synthetic metadata fixture, not a real model judgment.', 'issues': [], 'pending_bindings': [],
            'public_information_safe': True, 'long_horizon_valid': True, 'mode': 'model_api', 'status': 'passed',
            'validation_errors': [], 'spec_hash': self.check['spec_hash'], 'context_hash': self.check['context_hash'],
            'content_hash': self.check['content_hash'], 'request': {'Authorization': FAKE_KEY}}
        write_json(self.path/'operator/review.json', self.review)
        write_json(self.path/'operator/review_history'/('a'*64+'.json'), self.review)
        logs = [{'stage':'generate','model':'synthetic-model','status':200,'duration_seconds':1,
                 'ended_at_utc':'2026-11-05T00:00:00Z','response_id':'fixture-id','response_model':'fixture-model',
                 'finish_reason':'stop','usage':{'prompt_tokens':5,'completion_tokens':7,'total_tokens':12,'private_note':FAKE_KEY},
                 'request':{'Authorization':FAKE_KEY},'response':{'secret':FAKE_KEY}},
                {'stage':'review','model':'synthetic-model','status':429,'duration_seconds':0.1},
                {'stage':'review','model':'synthetic-model','status':'timeout','duration_seconds':2}]
        (self.batch/'api_calls.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in logs), encoding='utf-8')
        meta = read_json(self.batch/'batch.json')
        meta.update(api_calls_used=4, status='pending_human_review', private_config={'token':FAKE_KEY})
        write_json(self.batch/'batch.json', meta)
        # Unselected files never enter the delivery.
        (self.path/'operator/raw_response.txt').write_text(FAKE_KEY, encoding='utf-8')

    def publish(self):
        return snapshot(self.root, self.batch.name)

    def test_snapshot_carries_evidence_without_secrets_and_separates_counts(self):
        source_hash = digest(read_json(self.path/'spec.json'))
        before_registry = (self.root/'registry/tasks.sqlite').read_bytes()
        result = self.publish()
        dest = Path(result['path'])
        self.assertTrue(result['integrity_verified'])
        self.assertEqual(result['http_attempts_reserved'],4)
        self.assertEqual(result['http_attempt_records'],3)
        self.assertEqual(result['http_success_responses'],1)
        self.assertEqual(result['unobserved_reserved_attempts'],1)
        self.assertEqual(result['current_independent_reviews_passed_retained'],1)
        self.assertEqual(result['human_approved'],0)
        manifest=read_json(dest/'manifest.json')
        self.assertEqual(manifest['retained_candidates'][0]['spec_hash'],source_hash)
        self.assertFalse(manifest['retained_candidates'][0]['human_approved'])
        self.assertEqual(set(p.name for p in (dest/'candidates'/self.cid/'public').iterdir()),PUBLIC_FILES)
        for file in dest.rglob('*'):
            if file.is_file():
                self.assertNotIn(FAKE_KEY.encode(),file.read_bytes())
                self.assertNotIn('raw_response',file.name)
                self.assertNotIn('registry',file.parts)
        calls=[json.loads(line) for line in (dest/'api_calls.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertNotIn('request',calls[0]);self.assertNotIn('response',calls[0])
        self.assertEqual(calls[0]['usage'],{'prompt_tokens':5,'completion_tokens':7,'total_tokens':12})
        state=read_json(dest/'candidates'/self.cid/'state.json')
        self.assertNotIn('secret_config',state)
        self.assertNotIn('private_config',read_json(dest/'batch.json'))
        self.assertEqual((self.root/'registry/tasks.sqlite').read_bytes(),before_registry)
        self.assertEqual(verify_snapshot(dest,self.root)[0]['task_id'],self.cid)

    def test_invalid_and_public_extra_file_candidates_have_only_failure_summary(self):
        bad=copy.deepcopy(read_json(self.path/'spec.json'))
        bad['research_modules']=[]
        invalid=self.app.add_candidate(self.batch,bad,'fixture')
        # Main candidate no longer satisfies its public allowlist.
        (self.path/'public/secret-market-data.csv').write_text('synthetic',encoding='utf-8')
        result=self.publish()
        self.assertEqual(result['retained_candidates'],0)
        self.assertEqual(result['excluded_candidates'],2)
        dest=Path(result['path'])
        self.assertFalse((dest/'candidates'/invalid.name).exists())
        self.assertFalse((dest/'candidates'/self.cid).exists())
        reasons={r['reason'] for r in read_json(dest/'excluded_candidates.json')}
        self.assertIn('static_validation_failed_or_stale',reasons)
        self.assertIn('public_allowlist_mismatch',reasons)
        self.assertEqual(distributed_records(self.root),[])

    def test_snapshot_refuses_cover_or_active_batch(self):
        (self.batch/'.api.lock').write_text('fixture',encoding='utf-8')
        with self.assertRaisesRegex(SnapshotError,'BATCH_ACTIVE'):self.publish()
        (self.batch/'.api.lock').unlink()
        self.publish()
        with self.assertRaisesRegex(SnapshotError,'DESTINATION_EXISTS'):self.publish()

    def test_source_public_tamper_is_excluded(self):
        (self.path/'public/TASK.md').write_text('changed after check',encoding='utf-8')
        result=self.publish()
        self.assertEqual(result['retained_candidates'],0)
        failure=read_json(Path(result['path'])/'excluded_candidates.json')[0]
        self.assertEqual(failure['reason'],'public_content_changed_after_check')

    def test_stale_review_is_preserved_and_not_counted_as_current_pass(self):
        state=read_json(self.path/'state.json');state['model_review_status']='stale'
        write_json(self.path/'state.json',state)
        result=self.publish()
        self.assertEqual(result['retained_candidates'],1)
        self.assertEqual(result['current_reviews_retained'],0)
        self.assertEqual(result['current_independent_reviews_passed_retained'],0)
        self.assertEqual(read_json(Path(result['path'])/'manifest.json')['retained_candidates'][0]['model_review_status'],'stale')

    def test_history_filenames_are_portable_and_keep_full_manifest_hashes(self):
        result = self.publish()
        manifest = read_json(Path(result['path'])/'manifest.json')
        history = {name: value for name, value in manifest['files'].items() if '/review_history/' in name}
        self.assertTrue(history)
        for name, value in history.items():
            self.assertEqual(len(Path(name).stem), 16)
            self.assertEqual(len(value['sha256']), 64)
        self.assertEqual(len(verify_snapshot(Path(result['path']), self.root)), 1)

    def test_distributed_candidates_enter_future_comparisons_once(self):
        self.publish()
        # Active source has the same ID/hash; its state remains authoritative.
        state=read_json(self.path/'state.json')
        state['status']='pending_human_review';write_json(self.path/'state.json',state)
        records=self.app.records()
        self.assertEqual(sum(r['task_id']==self.cid for r in records),1)
        self.assertNotIn('source',next(r for r in records if r['task_id']==self.cid))
        # Portable copy without source runs still supplies a read-only comparison.
        shutil.rmtree(self.root/'runs')
        row=next(r for r in self.app.records() if r['task_id']==self.cid)
        self.assertEqual(row['source'],'distributed_snapshot')
        self.assertFalse(row['human_approved'])
        self.assertNotIn(self.cid,{r['task_id'] for r in self.app.records(exclude=self.cid)})
        with self.app.registry.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM tasks WHERE task_id=?',(self.cid,)).fetchone())

    def test_active_rejection_or_variant_is_not_revived(self):
        self.publish()
        for status in ('rejected','invalid'):
            state=read_json(self.path/'state.json');state['status']=status;write_json(self.path/'state.json',state)
            self.assertNotIn(self.cid,{r['task_id'] for r in self.app.records()})
        state=read_json(self.path/'state.json');state['status']='variant_candidate'
        write_json(self.path/'state.json',state)
        row=next(r for r in self.app.records() if r['task_id']==self.cid)
        self.assertEqual(row['status'],'variant_candidate')
        self.assertNotIn('source',row)

    def test_duplicate_candidate_checks_keep_comparison_context_stable(self):
        # Same research structure remains visible for comparison even when the
        # duplicate judgment changes its review state to variant_candidate.
        spec=read_json(self.path/'spec.json')
        other=self.app.add_candidate(self.batch,copy.deepcopy(spec),'synthetic_duplicate_fixture')
        paths=[self.path,other]
        for path in paths:
            state=read_json(path/'state.json');state['status']='pending_human_review'
            write_json(path/'state.json',state)
        baseline={}
        for path in paths:
            check=self.app.check_candidate(path)
            baseline[path.name]=check['context_hash']
            expected_other=other.name if path==self.path else self.cid
            self.assertIn(expected_other,check['dedup']['exact_matches'])
            review={'status':'needs_attention','classification':'duplicate','mode':'model_api',
                    'spec_hash':check['spec_hash'],'context_hash':check['context_hash'],
                    'content_hash':check['content_hash']}
            write_json(path/'operator/review.json',review)
            state=read_json(path/'state.json');state['model_review_status']='needs_attention'
            write_json(path/'state.json',state)
        for _ in range(3):
            for path in paths:
                check=self.app.check_candidate(path)
                self.assertEqual(check['context_hash'],baseline[path.name])
                state=read_json(path/'state.json')
                self.assertEqual(state['status'],'variant_candidate')
                self.assertEqual(state['model_review_status'],'needs_attention')
        for path in paths:
            row=next(r for r in self.app.records() if r['task_id']==path.name)
            self.assertEqual(row['status'],'variant_candidate')

    def test_same_id_changed_spec_fails_closed(self):
        self.publish()
        spec=read_json(self.path/'spec.json');spec['title']+=' edited';write_json(self.path/'spec.json',spec)
        with self.assertRaisesRegex(WorkflowError,'规格冲突'):self.app.records()

    def test_corrupted_or_unlisted_snapshot_file_blocks_comparisons(self):
        result=self.publish();dest=Path(result['path'])
        task=dest/'candidates'/self.cid/'public/TASK.md'
        previous=task.read_bytes();task.write_bytes(previous+b'tampered')
        with self.assertRaisesRegex(WorkflowError,'FILE_HASH'):self.app.records()
        task.write_bytes(previous)
        (dest/'unlisted.json').write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(WorkflowError,'FILE_LIST'):self.app.records()

    def test_even_manifest_listed_market_data_is_blocked(self):
        result=self.publish();dest=Path(result['path'])
        raw=dest/'raw-cache.json';raw.write_bytes(b'{}')
        manifest=read_json(dest/'manifest.json')
        manifest['files']['raw-cache.json']={'sha256':hashlib.sha256(b'{}').hexdigest(),'bytes':2}
        write_json(dest/'manifest.json',manifest)
        with self.assertRaisesRegex(SnapshotError,'FILE_NOT_ALLOWLISTED'):verify_snapshot(dest,self.root)

    def test_unknown_sensitive_spec_fields_are_not_distributed(self):
        spec=read_json(self.path/'spec.json');spec['api_key']=FAKE_KEY
        write_json(self.path/'spec.json',spec)
        check=read_json(self.path/'operator/validation.json');check['spec_hash']=digest(spec)
        write_json(self.path/'operator/validation.json',check)
        result=self.publish()
        self.assertEqual(result['retained_candidates'],0)
        self.assertEqual(read_json(Path(result['path'])/'excluded_candidates.json')[0]['error_code'],'SNAPSHOT_UNKNOWN_SPEC_FIELDS')


if __name__=='__main__':unittest.main()
