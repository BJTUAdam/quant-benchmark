from __future__ import annotations
import copy
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from finlab.common import ROOT, LabError, read, write, digest
from finlab.temporal import TimeMap, parse_time
from finlab.catalog import load_catalog, parse_doc, check_catalog
from finlab.data import init, ingest, freeze, FrozenView as PrivateFrozenView
from finlab.disclosure import AuditLog, DisclosureScope
from finlab.demo import build
from finlab.runtime import create_episode, load_episode, EpisodeResearchView, status
from finlab.server import query_server
from finlab.tushare import pro_api
from finlab.tasks import PUBLIC_FILES
from finlab.submission import check
from finlab.upstream import TushareUpstream
from taskgen.validation import validate
from taskgen.dedup import fingerprint
from taskgen.workflow import Production


def FrozenView(folder, **kwargs):
    """Explicit synthetic query capability; private readers alone have no query grant."""
    folder = Path(folder)
    manifest = read(folder / 'manifest.private.json')
    scope = DisclosureScope(manifest['real_start'], manifest['real_stop'], 'synthetic-test', 0,
                            digest({'test_fixture_only': True, 'manifest_hash': manifest['manifest_hash']}))
    return PrivateFrozenView(folder, scope=scope,
                             audit=AuditLog(folder.parent / (folder.name + '_audit.private.sqlite')), **kwargs)


class TemporalTests(unittest.TestCase):
    def setUp(self): self.m = TimeMap()
    def test_dates_and_leap_roundtrip(self):
        for value in ['20240229', '2024-02-29', 20240229, '2025-12-31']:
            self.assertEqual(self.m.shift(self.m.shift(value), inverse=True), value)
    def test_invalid_century_mapping_rejected(self):
        with self.assertRaises(LabError): TimeMap(100).shift('20000229')
    def test_datetime_precision(self):
        t='2024-02-29T12:13:14.123456789+08:00'
        self.assertEqual(self.m.shift(self.m.shift(t,'datetime'),'datetime',inverse=True),t)
    def test_year_month_quarter(self):
        for value,kind in [(2024,'year'),('202401','month'),('2024-12','month'),('2024Q2','quarter'),('2024年3季度','quarter')]:
            self.assertEqual(self.m.shift(self.m.shift(value,kind),kind,inverse=True),value)
        self.assertEqual(self.m.shift('09','month'),'09')
    def test_epoch_units_preserve_low_digits(self):
        for kind,scale in [('epoch_s',1),('epoch_ms',1000),('epoch_us',10**6),('epoch_ns',10**9)]:
            t=1709180000*scale + (123 if scale>123 else 0)
            self.assertEqual(self.m.shift(self.m.shift(t,kind),kind,inverse=True),t)
    def test_identity_exceptions(self):
        row={'ts_code':'M2509.DCE','name':'2025年9月豆粕','list_date':'20240101'}
        out=self.m.record(row,{'ts_code':'identity','name':'identity','list_date':'date'})
        self.assertEqual(out['ts_code'],row['ts_code']);self.assertEqual(out['name'],row['name'])
        self.assertEqual(out['list_date'],'24000101')
    def test_text_all_formats(self):
        s='2024-02-29 / 20240229 / 2024年2月29日 / 2024Q1 / 2024 / 202402 / 20240229120102'
        out=self.m.text(s)
        self.assertNotIn('2024',out); self.assertEqual(self.m.text(out,inverse=True),s)
    def test_unknown_field_and_ambiguous_text_block(self):
        with self.assertRaises(LabError): self.m.record({'new_time':'2024-01-01'}, {})
        with self.assertRaises(LabError): self.m.text('timestamp=1709180000123')
    def test_nested(self):
        rule={'rows':{'type':'array','fields':{'at':'datetime','duration':'duration'}}}
        row={'rows':[{'at':'2024-01-01T01:00:00+08:00','duration':2024}]}
        out=self.m.record(row,rule);self.assertEqual(out['rows'][0]['duration'],2024)
        self.assertEqual(self.m.record(out,rule,inverse=True),row)
    def test_unregistered_nested_value_blocked(self):
        for kind in ['text','identity','number']:
            with self.assertRaisesRegex(LabError,'NESTED'): self.m.record({'x':{'at':'20240229'}},{'x':kind})
    def test_utc_year_boundary(self):
        t='2023-12-31T20:00:00Z'
        self.assertEqual(self.m.shift(t,'datetime'),'2399-12-31T20:00:00Z')
        self.assertEqual(self.m.shift(self.m.shift(t,'datetime'),'datetime',inverse=True),t)
    def test_order_identifier_dates_and_code_exceptions(self):
        self.assertEqual(self.m.identifier('expiry:M2509.DCE:2025-09-15'),'expiry:M2509.DCE:2401-09-15')
        uuid='aa20240229'+'f'*22
        self.assertEqual(self.m.identifier(uuid),uuid)
        with self.assertRaisesRegex(LabError,'IDENTIFIER'): self.m.identifier('unregistered-2024-02-29')


class CatalogTests(unittest.TestCase):
    def test_catalog_complete_snapshot_and_status(self):
        c=load_catalog();self.assertFalse(c['failed_pages'])
        self.assertGreaterEqual(len(c['endpoints']),240)
        for name in ['daily','adj_factor','fund_daily','fut_daily','index_daily','index_weight','income','stk_mins']:
            self.assertIn(name,c['endpoints'])
        for ep in c['endpoints'].values(): self.assertFalse(ep.get('live_tested',False))
    def test_output_required_header_is_not_input(self):
        h='<div class="content"><h2>A</h2><p>接口：x_api</p><p>输入参数</p><table><tr><th>名称</th><th>类型</th><th>必选</th></tr><tr><td>ts_code</td><td>str</td><td>N</td></tr></table><p>输出参数</p><table><tr><th>名称</th><th>类型</th><th>必选</th></tr><tr><td>trade_date</td><td>str</td><td>Y</td></tr></table>'
        ep=parse_doc('1',h);self.assertEqual(ep['fields']['trade_date'],'date');self.assertIn('ts_code',ep['params'])
    def test_catalog_check(self):
        r=check_catalog();self.assertEqual(r['api_count'],len(load_catalog()['endpoints']))
        self.assertTrue(all(x['mock_schema_roundtrip']=='passed_synthetic_types_only' for x in r['endpoints']))


class DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/'fixture'
        build(self.p,'STOCK',resources={'cpus':2,'memory_mb':4096,'timeout_seconds':120}); self.view=FrozenView(self.p/'frozen',clock='2024-01-10T15:00:00+08:00')
    def tearDown(self): self.tmp.cleanup()
    def test_names_codes_and_dates(self):
        r=self.view.query('daily',{'ts_code':'000001.SZ','trade_date':'24000110'})
        self.assertEqual(len(r['data']['items']),1)
        self.assertEqual(r['data']['items'][0][0],'000001.SZ');self.assertEqual(r['data']['items'][0][1],'24000110')
    def test_future_scope_and_unknown(self):
        for api,params,fields in [('daily',{'trade_date':'24000226'},''),('daily',{'ts_code':'999999.SZ'},''),('income',{},''),('daily',{},'new_time')]:
            with self.assertRaises(LabError): self.view.query(api,params,fields)
    def test_pagination(self):
        a=self.view.query('daily',{'limit':2});b=self.view.query('daily',{'limit':2,'offset':2})
        self.assertTrue(a['meta']['has_more']); self.assertNotEqual(a['data']['items'],b['data']['items'])
    def test_frozen_isolation_from_later_source_edits(self):
        before=self.view.query('daily')
        import sqlite3
        with closing(sqlite3.connect(self.p/'source.private.sqlite')) as c, c:c.execute('delete from records')
        after=self.view.query('daily')
        self.assertEqual(after['data'],before['data'])
        self.assertEqual(after['meta']['dataset_id'],before['meta']['dataset_id'])
    def test_frozen_tampering_detected(self):
        with (self.p/'frozen/snapshot.sqlite').open('ab') as f:f.write(b'tampered')
        with self.assertRaises(LabError): FrozenView(self.p/'frozen')
    def test_revision_visibility(self):
        api='daily'; db=self.p/'source.private.sqlite'
        rows=[{'ts_code':'000001.SZ','trade_date':'20240102','close':999,'_available_at':'2024-01-15T15:00:00+08:00'}]
        ingest(db,api,rows,{'timing_basis':'operator_supplied','key_fields':['ts_code','trade_date'],'event_field':'trade_date'})
        plan={'synthetic':True,'symbols':['000001.SZ'],'real_start':'2024-01-02','real_stop':'2024-03-01',
              'apis':{api:{'fields':['ts_code','trade_date','close'],'coverage_confirmed':True,'replayability':'point_in_time'}}}
        freeze(db,plan,self.p/'rev')
        v=FrozenView(self.p/'rev',clock='2024-01-10T16:00:00+08:00')
        self.assertNotEqual(v.query('daily',{'trade_date':'24000102'})['data']['items'][0][-1],999)
        v.clock='2024-01-16T16:00:00.000000+08:00'
        self.assertEqual(v.query('daily',{'trade_date':'24000102'})['data']['items'][0][-1],999)
    def test_download_time_not_pit(self):
        row={'ts_code':'000001.SZ','trade_date':'20240102','close':10}
        db=self.p/'bad.sqlite';init(db)
        ingest(db,'daily',[row],{'timing_basis':'download_time','key_fields':['ts_code','trade_date'],'event_field':'trade_date'})
        with self.assertRaisesRegex(LabError,'DOWNLOAD_TIME'):
            freeze(db,{'real_start':'2024-01-01','real_stop':'2025-01-01','symbols':['000001.SZ'],
                       'apis':{'daily':{'fields':list(row),'coverage_confirmed':True,'replayability':'point_in_time'}}},self.p/'badfreeze')
    def test_episode_visibility_only_on_committed_state(self):
        create_episode(read(self.p/'binding.private.json'),self.p/'ep')
        v=EpisodeResearchView(self.p/'ep')
        with self.assertRaisesRegex(LabError,'TIME_RANGE_EXCEEDED'):v.query('daily',{'trade_date':'24000120'},'')
        self.assertEqual(status(self.p/'ep')['completed_segment'],0)
    def test_episode_pins_images_despite_global_rebuild(self):
        from finlab.runtime import episode_images
        pinned={'engine':'sha256:fixed_engine','worker':'sha256:fixed_worker'}
        changed={'engine':'sha256:other_engine','worker':'sha256:other_worker'}
        write(self.p/'config_root/deployment/images.lock.json',{'images':changed})
        with patch('finlab.runtime.ROOT',self.p/'config_root'):
            self.assertEqual(episode_images({'images':pinned},{'commits':[]}),pinned)
            self.assertEqual(episode_images({}, {'commits':[{'images':pinned}]}),pinned)
    def test_missing_rules_blocks_binding(self):
        b=read(self.p/'binding.private.json');del b['runtime']['instruments'][0]['lot_size']
        with self.assertRaises(LabError):create_episode(b,self.p/'bad_ep')
    def test_empty_or_invalid_rules_fail_before_containers(self):
        mutations=[('corporate_actions_verified',False),('execution_rules',None),('initial_cash',float('nan'))]
        for key,value in mutations:
            b=read(self.p/'binding.private.json');b['runtime'][key]=value
            with self.assertRaisesRegex(LabError,'BINDING'):create_episode(b,self.p/'bad_ep')
        b=read(self.p/'binding.private.json');b['runtime']['instruments'][0]['tick_size']=None
        with self.assertRaisesRegex(LabError,'BINDING'):create_episode(b,self.p/'bad_ep')
    def test_missing_price_limit_fails_before_episode(self):
        b=read(self.p/'binding.private.json');del b['runtime']['execution_rules'][0]['up_limit']
        with self.assertRaisesRegex(LabError,'BINDING'):create_episode(b,self.p/'bad_ep')
    def test_unimplemented_accounting_rules_are_not_silently_ignored(self):
        b=read(self.p/'binding.private.json');b['runtime']['instruments'][0]['fee_per_contract']=2
        with self.assertRaisesRegex(LabError,'BINDING'):create_episode(b,self.p/'bad_ep')
        b=read(self.p/'binding.private.json');b['runtime']['margin_schedule']=[{'ratio':.2}]
        with self.assertRaisesRegex(LabError,'BINDING'):create_episode(b,self.p/'bad_ep')
        b=read(self.p/'binding.private.json');b['runtime']['execution_rules'][0]['margin_ratio']=.2
        with self.assertRaisesRegex(LabError,'BINDING'):create_episode(b,self.p/'bad_ep')
    def test_default_range_is_not_full_source_history(self):
        db=self.p/'source.private.sqlite'
        row={'ts_code':'000001.SZ','trade_date':'20231229','close':10,'_available_at':'2023-12-29T15:00:00+08:00'}
        ingest(db,'daily',[row],{'timing_basis':'operator_supplied','key_fields':['ts_code','trade_date'],'event_field':'trade_date'})
        plan={'synthetic':True,'symbols':['000001.SZ'],'real_start':'2024-01-02','real_stop':'2024-03-01',
              'apis':{'daily':{'fields':['ts_code','trade_date','close'],'coverage_confirmed':True,'replayability':'point_in_time'}}}
        freeze(db,plan,self.p/'range')
        v=FrozenView(self.p/'range',clock='2024-01-10T16:00:00+08:00')
        self.assertTrue(all(row[1]>='24000102' for row in v.query('daily')['data']['items']))
    def test_published_future_maturity_and_historic_metadata(self):
        db=self.p/'meta.sqlite';init(db)
        row={'ts_code':'M2509.DCE','list_date':'20230101','delist_date':'20250915','name':'豆粕2025年9月',
             '_available_at':'2023-01-01T15:00:00+08:00'}
        ingest(db,'fut_basic',[row],{'timing_basis':'operator_supplied','key_fields':['ts_code'],'event_field':'list_date'})
        plan={'synthetic':True,'symbols':['M2509.DCE'],'real_start':'2024-01-02','real_stop':'2025-01-01',
              'apis':{'fut_basic':{'fields':['ts_code','list_date','delist_date','name'],'coverage_confirmed':True,
                                   'replayability':'point_in_time','event_scope':'visible_history'}}}
        freeze(db,plan,self.p/'meta')
        r=FrozenView(self.p/'meta',clock='2024-01-10').query('fut_basic')['data']['items'][0]
        self.assertEqual(r,['M2509.DCE','23990101','24010915','豆粕2025年9月'])
    def test_explicit_datetime_future_blocked(self):
        self.view.manifest['apis']['daily']['params']['end_date']='datetime'
        self.view.manifest['apis']['daily']['time_query_rules']['end_date']['kind']='datetime'
        with self.assertRaisesRegex(LabError,'TIME_RANGE_EXCEEDED'):
            self.view.query('daily',{'end_date':'2400-01-10T18:00:00+08:00'})
    def test_scope_cannot_expand_via_allowed_symbols(self):
        with self.assertRaisesRegex(LabError,'SYMBOL'):
            FrozenView(self.p/'frozen',allowed_symbols=['999999.SZ'])
    def test_partition_only_frequency_replays_minute_wrapper(self):
        db=self.p/'minutes.sqlite';init(db)
        rows=[{'ts_code':'000001.SZ','trade_time':'2024-01-02 09:31:00','open':10,'high':10.2,'low':9.8,'close':10.1,
               'vol':1000,'amount':10000,'_available_at':'2024-01-02T09:31:00+08:00'}]
        ingest(db,'stk_mins',rows,{'timing_basis':'operator_supplied','key_fields':['ts_code','trade_time'],'event_field':'trade_time'},
               partition={'api_name':'stk_mins','params':{'ts_code':'000001.SZ','freq':'1min'},'fields':list(rows[0])[:-1]})
        plan={'synthetic':True,'symbols':['000001.SZ'],'real_start':'2024-01-02','real_stop':'2024-01-03',
              'apis':{'stk_mins':{'fields':list(rows[0])[:-1],'coverage_confirmed':True,'replayability':'point_in_time'}}}
        freeze(db,plan,self.p/'minutes')
        v=FrozenView(self.p/'minutes',clock='2024-01-02T09:32:00+08:00')
        self.assertEqual(len(v.query('stk_mins',{'freq':'1min'})['data']['items']),1)
        with self.assertRaisesRegex(LabError,'PARAMETER_NOT_FROZEN'):v.query('stk_mins',{'freq':'5min'})
        s=query_server(v,'test-token',port=0);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        try:
            from finlab.tushare import pro_bar
            frame=pro_bar('000001.SZ',api=pro_api('test-token',url=f'http://127.0.0.1:{s.server_port}'),freq='1min')
            self.assertEqual(frame.iloc[0]['trade_time'],'2400-01-02 09:31:00')
        finally:s.shutdown();s.server_close();t.join()
    def test_real_episode_requires_current_approval_and_bound_export_is_private_safe(self):
        # Isolated synthetic test exercises the formal gate; it creates no
        # approvals or data bindings in the user's real registry.
        from taskgen.registry import Registry
        from finlab.archive import bound_package
        m=read(self.p/'frozen/manifest.private.json');m['synthetic']=False
        m.pop('manifest_hash');m['manifest_hash']=digest(m);write(self.p/'frozen/manifest.private.json',m)
        b=read(self.p/'binding.private.json');b.update(candidate_id='UNIT-V2',contract_review_confirmed=True)
        fixture_root=self.p/'operator';reg=Registry(fixture_root/'registry/tasks.sqlite')
        with patch('finlab.runtime.ROOT',fixture_root), patch('finlab.runtime.load_settings',return_value={'schema_version':'finlab.operator_setup.v1','method':'human_interactive','resources':{'cpus':2,'memory_mb':4096,'timeout_seconds':120},'configured_at':'2026-01-01T00:00:00Z'}):
            with self.assertRaisesRegex(LabError,'ACCEPTANCE'):create_episode(b,self.p/'denied')
        seeds=read(ROOT/'examples/first_batch.json');seed=seeds['tasks'][0] if isinstance(seeds,dict) else seeds[0]
        s=copy.deepcopy(seed)
        s['bridge_contract']['endpoints']=[{'api_name':name,'fields':list(ep['fields']),'frequency':'daily',
                                          'visibility':'available_at_lte_controller_clock'} for name,ep in m['apis'].items()]
        review={'verdict':'distinct','test_fixture_only':True}
        reg.event('UNIT-V2','model_reviewed','unit fixture',s,review)
        reg.approve('UNIT-V2',s,review,'unit fixture, not an operational approval')
        b['approved_spec_hash']=digest(s)
        with patch('finlab.runtime.ROOT',fixture_root), patch('finlab.runtime.load_settings',return_value={'schema_version':'finlab.operator_setup.v1','method':'human_interactive','resources':{'cpus':2,'memory_mb':4096,'timeout_seconds':120},'configured_at':'2026-01-01T00:00:00Z'}):
            create_episode(b,self.p/'approved')
        r=bound_package(self.p/'approved',self.p/'bound_public')
        self.assertEqual(set(r['files']),PUBLIC_FILES)
        contract=read(self.p/'bound_public/bridge_contract.json')
        self.assertTrue(contract['virtual_start'].startswith('2400-'))
        for f in (self.p/'bound_public').iterdir():
            text=f.read_text(encoding='utf-8')
            self.assertNotIn('year_offset',text);self.assertNotIn(str(self.p),text)
            self.assertNotIn('2024-01-02',text)
        bad=copy.deepcopy(b);bad['approved_spec_hash']='changed'
        with patch('finlab.runtime.ROOT',fixture_root), patch('finlab.runtime.load_settings',return_value={'schema_version':'finlab.operator_setup.v1','method':'human_interactive','resources':{'cpus':2,'memory_mb':4096,'timeout_seconds':120},'configured_at':'2026-01-01T00:00:00Z'}):
            with self.assertRaisesRegex(LabError,'APPROVAL'):create_episode(bad,self.p/'wrong_hash')
    def test_http_dataframe_wrapper(self):
        s=query_server(self.view,'test-token',port=0);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        try:
            c=pro_api('test-token',url=f'http://127.0.0.1:{s.server_port}')
            frame=c.daily(ts_code='000001.SZ',trade_date='24000110')
            self.assertEqual(frame.iloc[0]['ts_code'],'000001.SZ')
            with self.assertRaises(LabError):pro_api('wrong',url=c.url).daily()
            from finlab.tushare import pro_bar
            frame=pro_bar('000001.SZ',api=c,ma=[2])
            self.assertIn('ma2',frame)
        finally:s.shutdown();s.server_close();t.join()


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for d in ['catalog','schemas','examples','prompts','config']:shutil.copytree(ROOT/d,self.root/d)
        self.app=Production(self.root);self.app.init()
        self.examples=read(ROOT/'examples/first_batch.json')
        if isinstance(self.examples,dict):self.examples=self.examples['tasks']
    def tearDown(self):self.tmp.cleanup()
    def test_native_v2_examples_without_migration(self):
        for spec in self.examples:
            self.assertEqual(spec['schema_version'],'finance_task_spec.v2')
            self.assertNotIn('migration',spec)
            self.assertTrue(validate(spec,self.root)['passed'])
            self.assertIsNone(spec['runtime_profile']['resources'])
            self.assertEqual(spec['runtime_profile']['resource_status'],'requires_human_setup')
    def test_six_v2_packages_and_no_autoapproval(self):
        r=self.app.import_examples();batch=self.root/'runs'/r['batch_id']
        for path in (batch/'candidates').iterdir():
            self.assertTrue(read(path/'operator/validation.json')['passed'])
            self.assertEqual({p.name for p in (path/'public').iterdir()},PUBLIC_FILES)
            text=(path/'public/TASK.md').read_text(encoding='utf-8');self.assertNotIn('data/',text)
            self.assertIn(read(path/'public/task_input.draft.json')['spec_status'],{'pending_automatic_review','variant_candidate'})
            self.assertFalse((path/'public/data').exists())
    def test_submission_static_and_bad_code(self):
        from finlab.tasks import templates
        for name,value in templates().items(): (self.root/name).write_text(value,encoding='utf-8')
        self.assertTrue(check(self.root)['passed'])
        (self.root/'backtest.py').write_text('import forbidden_package',encoding='utf-8')
        self.assertFalse(check(self.root)['passed'])


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.mode='normal';self.calls=[]
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                q=json.loads(self.rfile.read(int(self.headers['Content-Length'])));outer.calls.append(q)
                if outer.mode=='redirect':
                    self.send_response(302);self.send_header('Location','http://127.0.0.1:1');self.end_headers();return
                if outer.mode=='limit':self.send_response(429);self.end_headers();return
                raw=(b'not json' if outer.mode=='format' else json.dumps({'code':0,'data':{'fields':['ts_code','trade_date'],
                    'items':[['000001.SZ','20240102']] if q['params'].get('offset',0)==0 or outer.mode=='stalled' else []}}).encode())
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.t=threading.Thread(target=self.server.serve_forever,daemon=True);self.t.start()
        self.client=TushareUpstream(f'http://127.0.0.1:{self.server.server_port}',token='synthetic-token',retries=1)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.t.join()
    def test_localhost_and_pagination(self):
        rows=self.client.pages('daily',{},'ts_code,trade_date',page_size=1)
        self.assertEqual(len(rows),1);self.assertEqual(len(self.calls),2)
    def test_malformed(self):
        self.mode='format'
        with self.assertRaisesRegex(LabError,'FORMAT'):self.client.query('daily')
    def test_redirect_never_forwarded(self):
        self.mode='redirect'
        with self.assertRaisesRegex(LabError,'HTTP'):self.client.query('daily')
        self.assertEqual(len(self.calls),1)
    def test_rate_limit_bounded(self):
        self.mode='limit'
        with patch('finlab.upstream.time.sleep'):
            with self.assertRaises(LabError):self.client.query('daily')
        self.assertEqual(len(self.calls),2)
    def test_stalled_offset(self):
        self.mode='stalled'
        with self.assertRaisesRegex(LabError,'STALLED'):self.client.pages('daily',{},'',page_size=1)
    def test_timeout_is_bounded(self):
        with patch.object(self.client.opener,'open',side_effect=TimeoutError),patch('finlab.upstream.time.sleep'):
            with self.assertRaisesRegex(LabError,'TIMEOUT'):self.client.query('daily')
    def test_new_upstream_field_does_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.sqlite';init(p)
            with self.assertRaisesRegex(LabError,'UNCLASSIFIED'):
                ingest(p,'daily',[{'ts_code':'000001.SZ','trade_date':'20240102','new_sensitive_field':'20240102'}],
                       {'timing_basis':'operator_supplied','key_fields':['ts_code','trade_date'],'event_field':'trade_date'})


if __name__=='__main__': unittest.main()
