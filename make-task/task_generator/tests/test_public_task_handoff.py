"""A public task folder must work after it is detached from its operator folder."""
import copy
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from finlab.common import ROOT, read
from finlab.submission import check
from finlab.agent_reference import normalize_public_entry
from finlab.tasks import build, PUBLIC_FILES
from taskgen.io_utils import digest, write_json
from taskgen.registry import Registry


class PublicTaskHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.seeds = read(ROOT/'examples/first_batch.json')

    def render(self, family):
        spec = copy.deepcopy(next(s for s in self.seeds if s['family_id']==family))
        path = self.folder/family
        build(ROOT,path,spec,{'id':'test-'+family,'status':'spec_checked','version':1})
        return path,spec

    def test_detached_public_folder_resolves_every_machine_entry_reference(self):
        for family in ('F01','F05'):
            path,_ = self.render(family)
            detached = self.folder/('detached-'+family)
            shutil.copytree(path/'public',detached)
            entry=read(detached/'task_input.draft.json')
            self.assertTrue((detached/entry['initial_message']['content_file']).is_file())
            for key in ('binding_contract','runtime_profile'):
                self.assertTrue((detached/entry[key]).is_file())
            self.assertEqual({p.name for p in detached.iterdir()},PUBLIC_FILES)
            self.assertTrue(check(detached)['passed'])

    def test_embedded_sdk_describes_actual_callback_fields_and_query_types(self):
        path,_=self.render('F01')
        text=(path/'public/SUBMISSION.md').read_text(encoding='utf-8')
        for token in ('available_positions','filled_quantity','None','on_bars','order_id','finlab submission check'):
            self.assertIn(token,text)
        self.assertNotIn('python scripts/make_submission.py',text)
        self.assertNotIn('python scripts/preflight.py',text)
        bridge=(path/'public/DATA_API.md').read_text(encoding='utf-8')
        for token in ('DataFrame','.meta','next_offset','TIME_RANGE_EXCEEDED','BRIDGE_AUDIT_UNAVAILABLE','MARKET_BRIDGE_URL'):
            self.assertIn(token,bridge)

    def test_futures_first_query_uses_frozen_futures_endpoint(self):
        path,_=self.render('F05')
        text=(path/'public/DATA_API.md').read_text(encoding='utf-8')
        first_code=text.split('```python',1)[1].split('```',1)[0]
        self.assertIn('ctx.query("fut_daily"',first_code)
        self.assertNotIn('ctx.query("daily"',first_code)

    def test_public_markdown_does_not_link_to_unshipped_skill_files(self):
        path,_=self.render('F01')
        public=path/'public'
        for name in ('SUBMISSION.md','DATA_API.md'):
            for target in re.findall(r'\[[^\]\n]+\]\(([^)\s]+)\)',(public/name).read_text(encoding='utf-8')):
                if ':' in target or target.startswith('#'):continue
                self.assertTrue((public/target.split('#')[0]).is_file(),(name,target))

    def test_new_automatic_record_keeps_entry_relative_to_public_directory(self):
        path,spec=self.render('F01')
        public={p.name:p.read_text(encoding='utf-8') for p in (path/'public').iterdir()}
        content=digest({k:public[k] for k in sorted(public) if k!='task_input.draft.json'})
        review={'mode':'model_api','classification':'duplicate','spec_hash':digest(spec),'content_hash':content,
                'public_information_safe':True,'long_horizon_valid':True,'issues':[],'validation_errors':[],
                'note':'PROGRAM-FABRICATED test evidence only'}
        registry=Registry(self.folder/'registry.sqlite')
        registry.event('test-F01','model_reviewed','unit fixture only',spec,review)
        record=registry.accept('test-F01',spec,review,public,novelty='duplicate')
        entry=json.loads(record['artifacts']['public_files']['task_input.draft.json'])
        self.assertEqual(entry['initial_message']['content_file'],'TASK.md')
        self.assertTrue((path/'public'/entry['initial_message']['content_file']).is_file())

    def test_old_export_entry_is_repaired_without_rewriting_research_files(self):
        path,_=self.render('F01')
        public=path/'public'
        before={p.name:p.read_bytes() for p in public.iterdir() if p.name!='task_input.draft.json'}
        entry=read(public/'task_input.draft.json');entry['initial_message']['content_file']='public/TASK.md'
        write_json(public/'task_input.draft.json',entry)
        result=normalize_public_entry(public)
        self.assertFalse(result['reviewed_research_content_changed'])
        self.assertEqual(read(public/'task_input.draft.json')['initial_message']['content_file'],'TASK.md')
        self.assertEqual(before,{p.name:p.read_bytes() for p in public.iterdir() if p.name!='task_input.draft.json'})

    def test_entry_normalizer_refuses_unknown_locations(self):
        path,_=self.render('F01');public=path/'public'
        entry=read(public/'task_input.draft.json');entry['initial_message']['content_file']='../operator/spec.json'
        write_json(public/'task_input.draft.json',entry)
        with self.assertRaisesRegex(ValueError,'PUBLIC_ENTRY_PATH_UNSUPPORTED'):
            normalize_public_entry(public)


if __name__=='__main__':unittest.main()
