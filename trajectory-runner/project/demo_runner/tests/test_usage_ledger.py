"""Offline budget gates exercise real transport retries and durable accounting."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model_client import ModelClient, ModelError, prepare_request
from usage_ledger import BudgetError, UsageLedger, safe_usage, totals, validate_policy

POLICY={'budget_cny':2,'input_cny_per_million':0.8,'output_cny_per_million':2.7,
        'input_token_ceiling':1000000,'price_reference':'https://help.aliyun.com/zh/model-studio/model-pricing'}
CFG={'provider':'openai_compatible','base_url':'https://example.test','model':'fake-flash',
     'api_key_env':'UNUSED_TEST_KEY','max_tokens':32768,'output_token_parameter':'max_completion_tokens',
     'timeout_seconds':30,'cost_control':POLICY}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'model_usage.jsonl'
        self.cfg={**CFG,'_usage_ledger_path':str(self.path),'_usage_turn':'review1'}
        self.ledger=UsageLedger(self.path,self.cfg)

    def tearDown(self):self.temp.cleanup()

    def records(self):return [json.loads(x) for x in self.path.read_text().splitlines()]

    def test_thinking_and_cached_input_are_not_double_counted(self):
        request=self.ledger.reserve('review1',1)
        self.ledger.settle(request,{'prompt_tokens':1000,'completion_tokens':100,
            'completion_tokens_details':{'reasoning_tokens':80,'text_tokens':100},
            'prompt_tokens_details':{'cached_tokens':900}})
        summary=totals(self.records())
        self.assertEqual(summary['estimated_cny'],'0.00107')
        self.assertEqual(summary['completion_tokens'],100)
        self.assertEqual(summary['reasoning_tokens'],80)
        self.assertEqual(summary['unconfirmed_reservations_cny'],'0')
        self.assertNotIn('Authorization',self.path.read_text())

    def test_pending_reservations_survive_restart_and_block_before_network(self):
        self.ledger.reserve('review1',1)
        UsageLedger(self.path,self.cfg).reserve('review1',2)
        with self.assertRaisesRegex(BudgetError,'未发送请求'), \
             patch('model_client._key',return_value='placeholder'), \
             patch('model_client.urllib.request.build_opener') as build:
            ModelClient(self.cfg).complete([{'role':'user','content':'hello'}],[])
        build.return_value.open.assert_not_called()
        self.assertEqual(totals(self.records())['requests'],2)

    def test_no_usage_retains_reservation_and_stops(self):
        request=self.ledger.reserve('review1',1)
        with self.assertRaisesRegex(BudgetError,'usage'):
            self.ledger.settle(request,None)
        self.assertEqual(totals(self.records())['settled_requests'],0)
        self.assertGreater(float(totals(self.records())['unconfirmed_reservations_cny']),0)

    def test_policy_cannot_be_changed_mid_run(self):
        self.ledger.reserve('review1',1)
        other=UsageLedger(self.path,{**self.cfg,'cost_control':{**POLICY,'budget_cny':3}})
        with self.assertRaisesRegex(BudgetError,'已固定'):other.reserve('review2',1)

    def test_invalid_and_partial_usage_is_not_trusted(self):
        for value in (None,{}, {'prompt_tokens':True,'completion_tokens':1},
                      {'prompt_tokens':100,'completion_tokens':-1},
                      {'prompt_tokens':1,'completion_tokens':1,'completion_tokens_details':{'reasoning_tokens':2}}):
            self.assertIsNone(safe_usage(value))
        for amount in (True,0,-1,'NaN','Infinity'):
            with self.assertRaises(BudgetError):validate_policy({**POLICY,'budget_cny':amount})

    def test_partial_ledger_fails_closed(self):
        self.ledger.reserve('review1',1)
        with self.path.open('a') as output:output.write('{')
        with self.assertRaises(BudgetError):UsageLedger(self.path,self.cfg).reserve('review1',2)

    def test_duplicate_settlement_is_rejected(self):
        request=self.ledger.reserve('review1',1)
        usage={'prompt_tokens':1,'completion_tokens':1}
        self.ledger.settle(request,usage)
        with self.assertRaises(BudgetError):self.ledger.settle(request,usage)

    def test_usage_over_provider_ceiling_is_recorded_then_stops(self):
        request=self.ledger.reserve('review1',1)
        with self.assertRaisesRegex(BudgetError,'上限'):
            self.ledger.settle(request,{'prompt_tokens':1000001,'completion_tokens':1})
        self.assertEqual(totals(self.records())['settled_requests'],1)

    def test_output_limit_includes_reasoning_in_wire_request(self):
        _,headers,payload=prepare_request(self.cfg,[{'role':'user','content':'hello'}],[])
        self.assertEqual(payload['max_completion_tokens'],32768)
        self.assertNotIn('max_tokens',payload)
        self.assertNotIn('cost_control',payload)
        self.assertNotIn('_usage_ledger_path',payload)
        self.assertNotIn('Authorization',headers)

    def test_each_retry_is_reserved_and_success_usage_is_settled(self):
        healthy=MagicMock()
        healthy.__enter__.return_value=healthy
        healthy.read.return_value=json.dumps({'choices':[{'message':{'content':'done'}}],
                    'usage':{'prompt_tokens':1000,'completion_tokens':100}}).encode()
        opener=MagicMock()
        opener.open.side_effect=[http.client.RemoteDisconnected('do not log provider text'),healthy]
        with patch('model_client._key',return_value='placeholder'), \
             patch('model_client.urllib.request.build_opener',return_value=opener), \
             patch('model_client.time.sleep'):
            result=ModelClient(self.cfg).complete([{'role':'user','content':'hello'}],[])
        self.assertEqual(result['content'],'done')
        summary=totals(self.records())
        self.assertEqual(summary['requests'],2)
        self.assertEqual(summary['settled_requests'],1)
        self.assertGreater(float(summary['unconfirmed_reservations_cny']),0)
        self.assertNotIn('placeholder',self.path.read_text())
        self.assertNotIn('provider text',self.path.read_text())

    def test_truncation_stops_but_usage_is_not_lost(self):
        response=MagicMock()
        response.__enter__.return_value=response
        response.read.return_value=json.dumps({'choices':[{'finish_reason':'length','message':{'content':'partial'}}],
                     'usage':{'prompt_tokens':10,'completion_tokens':32768}}).encode()
        opener=MagicMock();opener.open.return_value=response
        with patch('model_client._key',return_value='placeholder'), \
             patch('model_client.urllib.request.build_opener',return_value=opener), \
             self.assertRaisesRegex(ModelError,'截断'):
            ModelClient(self.cfg).complete([{'role':'user','content':'hello'}],[])
        self.assertEqual(totals(self.records())['completion_tokens'],32768)


if __name__=='__main__':unittest.main()
