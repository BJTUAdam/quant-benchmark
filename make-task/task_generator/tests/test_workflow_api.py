"""End-to-end tests against a loopback fake model, entirely in temporary roots.

Fixtures deliberately alter example structures for test identity. They are not
real model generations, actual financial research, or delivered approvals.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskgen.dedup import fingerprint
from taskgen.io_utils import catalog, digest, read_json, write_json
from taskgen.registry import RegistryError
from taskgen.validation import validate
from taskgen.workflow import Production, WorkflowError


PROJECT = Path(__file__).resolve().parents[1]
TEST_ENV = 'TASKGEN_WORKFLOW_FAKE_KEY'
TEST_KEY = 'fake-loopback-workflow-key-not-a-provider-secret'


@contextmanager
def local_model(responder):
    calls, handler_errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                context = json.loads(payload['messages'][-1]['content'])
                calls.append({'path': self.path, 'payload': payload, 'context': context,
                              'authorization': self.headers.get('Authorization')})
                reply = responder(payload, context, len(calls))
                if isinstance(reply, tuple):
                    status, body = reply
                else:
                    status, body = 200, {'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)},
                                                     'finish_reason': 'stop'}]}
            except Exception as error:
                handler_errors.append(error)
                status, body = 500, {'error': 'Local test fixture failed.'}
            raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port) + '/v1', calls
        if handler_errors:
            raise AssertionError('Local fake-model handler failed.') from handler_errors[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class WorkflowAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ('catalog', 'schemas', 'examples', 'prompts', 'config'):
            shutil.copytree(PROJECT / folder, self.root / folder)
        self.app = Production(self.root)
        self.app.init()
        # Shipped machine-admission records bootstrap into a clean platform.
        # Generation adds its own candidates without relabelling that library.
        self.accepted_library_ids = {x['task_id'] for x in self.app.registry.records() if x['status']=='spec_accepted'}
        snapshot=self.root/'examples/accepted/manifest.json'
        if snapshot.is_file():
            self.assertEqual(self.accepted_library_ids,set(read_json(snapshot)['candidate_ids']))
            with self.app.registry.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM approvals').fetchone()[0],0)
        examples = read_json(self.root / 'examples' / 'first_batch.json')
        if isinstance(examples, dict):
            examples = examples.get('tasks', examples.get('candidates', examples.get('specs', [])))
        self.examples = {item['family_id']: item for item in examples}
        self.env = patch.dict(os.environ, {TEST_ENV: TEST_KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sleep = patch('taskgen.api.time.sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def fixture_spec(self, family):
        spec = copy.deepcopy(self.examples[family])
        known = {fingerprint(x['spec']) for x in self.app.registry.records()}
        known.update(fingerprint(x) for x in self.examples.values())
        for code in catalog(self.root, 'decisions')['decisions']:
            if code in spec['required_decisions']:
                continue
            spec['required_decisions'].append(code)
            spec['decision_dependencies'].append([spec['required_decisions'][0], code])
            spec['research_modules'].append({'decision_id': code,
                'description': '仅用于本机临时测试的结构变化，不作为真实金融题目。',
                'interaction': '测试旧决策与新增决策的关联。'})
            if fingerprint(spec) not in known:
                break
        spec['title'] = '临时本机HTTP测试夹具 ' + family
        self.assertTrue(validate(spec, self.root)['passed'])
        self.assertNotIn(fingerprint(spec), known)
        return spec

    @staticmethod
    def review_for(context):
        neighbors = context['nearest']
        return {'classification': 'distinct',
                'closest_task_id': neighbors[0]['task_id'] if neighbors else None,
                'comparisons': [{'task_id': item['task_id'],
                    'similarities': ['本机测试对照，非真实模型研究判断。'],
                    'differences': ['测试夹具增加规范决策及交互。']} for item in neighbors],
                'rationale': '仅验证生产工作流，不能作为真实模型质量证据。',
                'issues': [], 'public_information_safe': True, 'long_horizon_valid': True}

    def responder(self, payload, context, _number):
        if payload['model'] == 'fake-workflow-generation':
            return self.fixture_spec(context['family_id'])
        self.assertEqual(payload['model'], 'fake-workflow-review')
        return self.review_for(context)

    def configure(self, url, budget=12, retries=0):
        write_json(self.root / 'config' / 'default.json', {
            'adapter': 'chat_completions', 'base_url': url,
            'generation_model': 'fake-workflow-generation',
            'review_model': 'fake-workflow-review', 'api_key_env': TEST_ENV,
            'timeout_seconds': 2, 'max_retries': retries, 'format_retries': 0,
            'max_calls_per_batch': budget, 'json_mode': 'json_object'})

    def generate(self, quotas=None, limit=1):
        path = self.root / 'config' / 'test_batch.json'
        write_json(path, {'family_quotas': quotas or {'F01': 1}, 'max_candidates': limit})
        result = self.app.generate(path)
        batch = self.root / 'runs' / result['id']
        return result, batch, sorted((batch / 'candidates').iterdir())

    def test_real_client_generate_autoaccept_export_in_temporary_root(self):
        with local_model(self.responder) as (url, calls):
            self.configure(url)
            result, batch, candidates = self.generate()
            self.assertEqual(len(candidates), 1)
            path = candidates[0]
            self.assertEqual(result['api_calls_used'], 2)
            self.assertEqual(read_json(path / 'state.json')['status'], 'spec_accepted')
            self.assertEqual(self.app.registry.approval(path.name)['action'],'auto_accepted')
            reviewed = self.app.review_batch(batch.name, use_api=True)
            self.assertEqual(reviewed['errors'], [])
            self.assertEqual(reviewed['api_calls_used'], 2)
            self.assertTrue(all(call['path'] == '/v1/chat/completions' for call in calls))
            self.assertTrue(all(call['authorization'] == 'Bearer ' + TEST_KEY for call in calls))
            state = self.app.accept(path.name)
            self.assertEqual(state['status'], 'spec_accepted')
            self.assertEqual(state['approval_method'],'automated')
            exported = self.app.export(path.name)
            with zipfile.ZipFile(exported['archives'][0]) as archive:
                from finlab.tasks import PUBLIC_FILES
                self.assertEqual(set(archive.namelist()), {'public/' + f for f in PUBLIC_FILES} | {'public_manifest.json'})
                draft = json.loads(archive.read('public/task_input.draft.json'))
                self.assertEqual(draft['data_status'], 'not_bound')
                self.assertIsNone(draft['dataset_id'])
                self.assertIsNone(draft['formal_intervals'])
                self.assertNotIn(TEST_KEY, archive.read('public/TASK.md').decode('utf-8'))
            self.assertNotIn(TEST_KEY, (batch / 'api_calls.jsonl').read_text(encoding='utf-8'))
            self.assertEqual({x['task_id'] for x in self.app.registry.records() if x['status']=='spec_accepted'},
                             self.accepted_library_ids|{path.name})
            self.assertTrue(Path(exported['directory']).is_relative_to(self.root))

    def test_budget_accumulates_across_review_commands_and_reserves_before_http(self):
        reservations = []

        def respond(payload, context, number):
            batches = list((self.root / 'runs').glob('*/batch.json'))
            self.assertEqual(len(batches), 1)
            reservations.append(read_json(batches[0])['api_calls_used'])
            result = self.responder(payload, context, number)
            if payload['model']=='fake-workflow-review': result['classification']='uncertain'
            return result

        with local_model(respond) as (url, calls):
            self.configure(url, budget=4)
            result, batch, _ = self.generate()
            self.assertEqual(result['api_calls_used'], 2)
            self.assertEqual(self.app.review_batch(batch.name, use_api=True)['api_calls_used'], 3)
            self.assertEqual(self.app.review_batch(batch.name, use_api=True)['api_calls_used'], 4)
            with self.assertRaisesRegex(WorkflowError, '上限已用尽'):
                self.app.review_batch(batch.name, use_api=True)
            self.assertEqual(len(calls), 4)
            self.assertEqual(reservations, [1, 2, 3, 4])
            self.assertEqual(read_json(batch / 'batch.json')['api_calls_used'], 4)
            self.assertFalse((batch / '.api.lock').exists())

    def test_v2_generate_autoaccept_public_export(self):
        from finlab.tasks import PUBLIC_FILES
        self.app=Production(self.root)
        with local_model(self.responder) as (url,calls):
            self.configure(url)
            result,batch,candidates=self.generate()
            path=candidates[0]
            self.assertEqual(read_json(path/'spec.json')['schema_version'],'finance_task_spec.v2')
            self.assertEqual(self.app.registry.approval(path.name)['action'],'auto_accepted')
            self.app.review_batch(batch.name,use_api=True)
            self.assertEqual(self.app.accept(path.name)['approval_method'],'automated')
            exported=self.app.export(path.name)
            with zipfile.ZipFile(exported['archives'][0]) as z:
                self.assertEqual(set(z.namelist()),{'public/'+f for f in PUBLIC_FILES}|{'public_manifest.json'})
                entry=json.loads(z.read('public/task_input.draft.json'))
                self.assertEqual(entry['spec_status'],'spec_accepted')
                self.assertNotIn(TEST_KEY,z.read('public/TASK.md').decode('utf-8'))
                self.assertNotIn('未声明已通过人工批准',z.read('public/TASK.md').decode('utf-8'))

    def test_forged_review_without_database_evidence_cannot_be_approved(self):
        batch = self.app.new_batch('temporary_test_fixture')
        path = self.app.add_candidate(batch, self.fixture_spec('F01'), 'temporary_test_fixture')
        check = self.app.check_candidate(path)
        review = self.review_for({'nearest': check['dedup']['nearest']})
        review.update({'mode': 'model_api', 'status': 'passed', 'validation_errors': [],
                       'spec_hash': check['spec_hash'], 'context_hash': check['context_hash'],
                       'content_hash': check['content_hash']})
        write_json(path / 'operator' / 'review.json', review)
        with self.assertRaisesRegex(RegistryError, '模型初筛调用记录'):
            self.app.approve(path.name, '伪造JSON不应构成API初筛证据。')
        self.assertIsNone(self.app.registry.approval(path.name))
        self.assertEqual(read_json(batch / 'batch.json')['api_calls_used'], 0)

    def test_invalid_typed_generation_is_recorded_and_next_family_is_attempted(self):
        attempted_families = []

        def respond(payload, context, number):
            if payload['model'] == 'fake-workflow-generation':
                family = context['family_id']
                attempted_families.append(family)
                spec = self.fixture_spec(family)
                if family == 'F01':
                    spec['family_id'] = {'invalid_type': 'temporary fake model output'}
                    spec['parameter_space'] = [123]
                return spec
            return self.responder(payload, context, number)

        with local_model(respond) as (url, calls):
            self.configure(url)
            result, batch, candidates = self.generate({'F01': 1, 'F02': 1}, limit=2)
            self.assertEqual(attempted_families, ['F01', 'F02'])
            self.assertEqual(result['family_attempts'], {'F01': 1, 'F02': 1})
            self.assertEqual(result['preliminary_screened_counts'], {'F01': 0, 'F02': 1})
            states = [read_json(p / 'state.json')['status'] for p in candidates]
            self.assertEqual(states.count('invalid'), 1)
            self.assertEqual(states.count('spec_accepted'), 1)
            invalid = next(p for p in candidates if read_json(p / 'state.json')['status'] == 'invalid')
            self.assertFalse(read_json(invalid / 'operator' / 'validation.json')['passed'])
            self.assertTrue((batch / 'coverage_report.json').is_file())
            self.assertEqual(len(calls), 3)
            self.assertFalse((batch / '.api.lock').exists())

    def test_same_batch_reviews_are_refreshed_against_complete_context(self):
        with local_model(self.responder) as (url, calls):
            self.configure(url, budget=8)
            result, batch, candidates = self.generate({'F01': 1, 'F02': 1}, limit=2)
            by_family = {read_json(p / 'spec.json')['family_id']: p for p in candidates}
            first = by_family['F01']
            self.assertEqual(read_json(first / 'state.json')['model_review_status'], 'passed')
            self.assertEqual(read_json(first / 'state.json')['status'], 'spec_accepted')
            reviewed = self.app.review_batch(batch.name, use_api=True)
            self.assertEqual(reviewed['errors'], [])
            self.assertEqual(len(calls), 5)
            for path in candidates:
                review = read_json(path / 'operator' / 'review.json')
                state = read_json(path / 'state.json')
                others = {p.name for p in candidates if p != path}
                self.assertEqual(review['status'], 'passed')
                self.assertEqual(state['model_review_status'], 'passed')
                self.assertTrue(others.issubset({x['task_id'] for x in review['comparisons']}))
                self.assertEqual(review['context_hash'], self.app.context_hash(self.app.records(exclude=path.name)))
                self.assertEqual(review['spec_hash'], digest(read_json(path / 'spec.json')))
            for path in candidates:self.assertEqual(self.app.accept(path.name)['approval_method'],'automated')
            self.assertEqual({x['task_id'] for x in self.app.registry.records() if x['status']=='spec_accepted'},
                             self.accepted_library_ids|{p.name for p in candidates})
            self.assertEqual(result['preliminary_screened_counts'], {'F01': 1, 'F02': 1})

    def test_typed_malformed_review_is_recorded_without_approval(self):
        def respond(payload, context, number):
            if payload['model'] == 'fake-workflow-review':
                review = self.review_for(context)
                review['classification'] = {'invalid': 'typed fake model review'}
                return review
            return self.responder(payload, context, number)

        with local_model(respond) as (url, calls):
            self.configure(url)
            result, _, candidates = self.generate()
            path = candidates[0]
            review = read_json(path / 'operator' / 'review.json')
            self.assertEqual(review['status'], 'needs_attention')
            self.assertTrue(review['validation_errors'])
            self.assertEqual(result['preliminary_screened_counts'], {'F01': 0})
            with self.assertRaises(WorkflowError):
                self.app.approve(path.name, '坏初筛不可批准。')
            self.assertIsNone(self.app.registry.approval(path.name))
            self.assertEqual(len(calls), 2)

    def test_batch_lock_blocks_second_review_without_spending_budget(self):
        with local_model(self.responder) as (url, calls):
            self.configure(url)
            _, batch, _ = self.generate()
            lock = batch / '.api.lock'
            lock.write_text('temporary competing-operation fixture', encoding='utf-8')
            with self.assertRaisesRegex(WorkflowError, '已有API操作'):
                self.app.review_batch(batch.name, use_api=True)
            self.assertEqual(read_json(batch / 'batch.json')['api_calls_used'], 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue(lock.exists())

    def test_retried_http_attempts_are_persisted_in_batch_budget(self):
        def respond(payload, context, number):
            if number == 1:
                return 429, {'error': 'synthetic retryable fake response'}
            return self.responder(payload, context, number)

        with local_model(respond) as (url, calls):
            self.configure(url, budget=3, retries=1)
            result, batch, _ = self.generate()
            self.assertEqual(result['api_calls_used'], 3)
            self.assertEqual(len(calls), 3)
            with self.assertRaisesRegex(WorkflowError, '上限已用尽'):
                self.app.review_batch(batch.name, use_api=True)
            self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
