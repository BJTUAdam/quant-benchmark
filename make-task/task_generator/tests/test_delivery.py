"""Delivery gates: synthetic inputs only, no real approvals or external calls."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from finlab.common import LabError, read, write
from finlab.demo import build
from finlab.delivery import approved, batch, check_binding
from finlab.upstream import TushareUpstream, probe

RESOURCES = {'cpus': 2, 'memory_mb': 4096, 'timeout_seconds': 120}


class DeliveryChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        build(self.root / 'fixture', resources=RESOURCES)
        self.binding = read(self.root / 'fixture/binding.private.json')

    def test_preflight_accumulates_approval_setup_and_docker_blockers(self):
        with patch('finlab.delivery.approved', side_effect=LabError('TASK_ACCEPTANCE_REQUIRED')), \
             patch('finlab.setup.load_settings', side_effect=LabError('SETUP_REQUIRED')), \
             patch('finlab.runtime.docker', side_effect=LabError('DOCKER_UNAVAILABLE_OR_TIMEOUT')):
            report = check_binding(self.binding, self.root / 'fixture/submission')
        codes = {b['code'] for b in report['blockers']}
        self.assertTrue({'TASK_ACCEPTANCE_REQUIRED','SETUP_REQUIRED','REAL_DATA_REQUIRED','DOCKER_UNAVAILABLE_OR_TIMEOUT'} <= codes)
        self.assertEqual(report['checks']['submission']['status'], 'passed')
        self.assertFalse(report['passed'])
        self.assertFalse(report['execution_verified'])

    def test_preflight_windows_must_match_create_episode(self):
        from finlab.runtime import derive_windows
        from finlab.data import FrozenView
        frozen = FrozenView(self.binding['frozen_dir'])
        windows = derive_windows(self.binding, frozen)
        windows[1]['start'] = windows[0]['start']
        self.binding['stage_windows'] = windows
        with patch('finlab.delivery.approved', side_effect=LabError('TASK_ACCEPTANCE_REQUIRED')):
            report = check_binding(self.binding, environment=False)
        self.assertEqual(report['checks']['stage_windows']['status'], 'blocked')
        with self.assertRaises(LabError): derive_windows(self.binding, frozen)

    def test_exact_candidate_approval_resolves_old_version_not_latest(self):
        from unittest.mock import Mock
        registry = Mock()
        registry.resolve_candidate.return_value = {'spec_hash':'old'}
        self.assertEqual(approved({'candidate_id':'mother','approved_spec_hash':'old'}, registry)['spec_hash'],'old')
        registry.resolve_candidate.assert_called_once_with('mother')

    def test_batch_invalid_item_does_not_hide_remaining_items_or_execute(self):
        valid = self.root / 'binding.json'; write(valid, self.binding)
        plan = {'items':[{'binding':str(self.root/'missing.json'),'episode':str(self.root/'one'),'submission':str(self.root/'fixture/submission')},
                         {'binding':str(valid),'episode':str(self.root/'two'),'submission':str(self.root/'fixture/submission')}]}
        with patch('finlab.delivery.check_binding', return_value={'passed':True,'checks':{},'blockers':[]}), patch('finlab.runtime.create_episode') as create:
            report = batch(plan, execute=True)
        self.assertEqual(len(report['items']),2)
        self.assertFalse(report['passed']); self.assertFalse(report['executed']); create.assert_not_called()
        self.assertTrue(all(x['execution']['status']=='not_started' for x in report['items']))

    def test_batch_defaults_to_preflight_only(self):
        file=self.root/'binding.json'; write(file,self.binding)
        plan={'items':[{'binding':str(file),'episode':str(self.root/'ep'),'submission':str(self.root/'fixture/submission')}]}
        with patch('finlab.delivery.check_binding',return_value={'passed':True,'checks':{},'blockers':[]}),patch('finlab.runtime.create_episode') as create:
            report=batch(plan)
        self.assertTrue(report['passed']); self.assertFalse(report['executed']); create.assert_not_called()


class UpstreamErrors(unittest.TestCase):
    def client(self, reply):
        from unittest.mock import Mock
        client=TushareUpstream('http://localhost:1234','unit-secret-token',retries=0)
        response=Mock(); response.read.return_value=json.dumps(reply).encode()
        response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        client.opener=Mock();client.opener.open.return_value=response
        return client

    def test_rejection_code_and_message_survive_valueerror_handler(self):
        client=self.client({'code':4502,'msg':'日期不符合请求区间 unit-secret-token','data':None})
        with self.assertRaises(LabError) as caught: client.query('dividend')
        self.assertEqual(caught.exception.code,'UPSTREAM_REJECTED')
        self.assertEqual(caught.exception.upstream_code,4502)
        self.assertIn('日期不符合请求区间',caught.exception.message)
        self.assertNotIn('unit-secret-token',caught.exception.message)

    def test_http_auth_failure_is_not_empty_success(self):
        client=self.client({})
        client.opener.open.side_effect=HTTPError(client.url,401,'Unauthorized',{},io.BytesIO(b'{"msg":"bad unit-secret-token"}'))
        report=probe({'queries':[{'api_name':'daily'}]},client)
        row=report['results'][0]
        self.assertFalse(report['passed']);self.assertEqual(row['http_status'],401)
        self.assertEqual(row['error_code'],'UPSTREAM_HTTP_FAILED');self.assertNotIn('unit-secret-token',row['message'])

    def test_empty_code_zero_report_does_not_certify_coverage(self):
        client=self.client({'code':0,'data':{'fields':['ts_code'],'items':[]}})
        report=probe({'queries':[{'api_name':'daily'}]},client)
        self.assertFalse(report['passed']);self.assertEqual(report['results'][0]['status'],'empty_success')
        self.assertFalse(report['results'][0]['coverage_proven'])


if __name__ == '__main__': unittest.main()
