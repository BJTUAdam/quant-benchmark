"""Native v2 production gates; fixtures never alter the operator registry."""
from __future__ import annotations
import copy
import io
from contextlib import redirect_stderr
import shutil
import tempfile
from pathlib import Path
import unittest

from taskgen.cli import parser
from taskgen.io_utils import read_json, write_json
from taskgen.registry import RegistryError
from taskgen.validation import validate, shape_errors
from taskgen.workflow import Production, WorkflowError
from finlab.tasks import PUBLIC_FILES

ROOT = Path(__file__).resolve().parents[1]


class NativeV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('catalog','schemas','examples','prompts','config'):
            shutil.copytree(ROOT/name, self.root/name)
        self.app = Production(self.root)
        self.app.init()
        self.spec = read_json(self.root/'examples/first_batch.json')[0]

    def test_legacy_spec_rejected_before_candidate_or_registry_write(self):
        legacy = copy.deepcopy(self.spec)
        legacy['schema_version'] = 'finance_task_spec.v1'
        batch = self.app.new_batch('unit-native-gate')
        before = self.app.registry.records()
        self.assertFalse(validate(legacy, self.root)['passed'])
        with self.assertRaisesRegex(WorkflowError, '仅接受原生'):
            self.app.add_candidate(batch, legacy, 'unit')
        self.assertEqual(list((batch/'candidates').iterdir()), [])
        with self.assertRaisesRegex(RegistryError, '仅允许原生'):
            self.app.registry.approve('unit-v1', legacy, {}, 'unit')
        self.assertEqual(self.app.registry.records(), before)

    def test_legacy_history_still_compares_and_is_not_rewritten(self):
        history = read_json(self.root/'catalog/historical_tasks.json')
        before = (self.root/'catalog/historical_tasks.json').read_bytes()
        records = self.app.records()
        self.assertTrue(set(x['id'] for x in history) <= set(x['task_id'] for x in records))
        self.assertTrue(any(x['spec']['schema_version']=='finance_task_spec.v1' for x in records))
        self.app.generation_context('F01', [])
        self.assertEqual((self.root/'catalog/historical_tasks.json').read_bytes(), before)
        batch = self.app.new_batch('unit-readonly-history')
        path = batch/'candidates/unit-history'
        write_json(path/'spec.json', history[0])
        write_json(path/'state.json', {'status':'pending_human_review'})
        item = next(x for x in self.app.records() if x['task_id']=='unit-history')
        self.assertEqual(item['status'],'historical_read_only')

    def test_model_schema_is_native_and_contract_objects_are_complete(self):
        schema = self.app.generation_schema('F01')
        self.assertEqual(schema['properties']['schema_version']['enum'],['finance_task_spec.v2'])
        self.assertNotIn('migration',schema['properties'])
        def check(node):
            if isinstance(node,dict):
                if node.get('type')=='object':
                    self.assertEqual(set(node['required']),set(node.get('properties',{})))
                    self.assertFalse(node['additionalProperties'])
                for value in node.values():check(value)
            elif isinstance(node,list):
                for value in node:check(value)
        check(schema)
        example=copy.deepcopy(self.spec)
        example['parameter_space']=['descriptive research choice']
        self.assertEqual(shape_errors(example,schema),[])
        self.assertTrue({'bridge_contract','runtime_profile','submission_contract','protocol'} <= set(schema['properties']))

    def test_cli_no_longer_selects_v1_or_legacy_guide(self):
        with redirect_stderr(io.StringIO()):
            for argv in (['--spec-version','v1','init'],['--spec-version','v2','init'],['build-guide']):
                with self.assertRaises(SystemExit):parser().parse_args(argv)
        self.assertEqual(parser().parse_args(['init']).command,'init')

    def test_public_package_declares_virtual_years_and_unselected_budget(self):
        batch = self.app.new_batch('unit-virtual-policy')
        path = self.app.add_candidate(batch,self.spec,'unit')
        self.assertEqual(set(p.name for p in (path/'public').iterdir()),PUBLIC_FILES)
        for name in ('TASK.md','DATA_API.md','SUBMISSION.md'):
            text=(path/'public'/name).read_text(encoding='utf-8')
            self.assertIn('虚拟',text)
            self.assertIn('股灾',text)
            self.assertNotIn('year_offset',text)
        contract=read_json(path/'public/bridge_contract.json')
        self.assertEqual(contract['calendar_semantics'],'virtual_sequence_only_not_historical_years')
        runtime=read_json(path/'public/runtime_profile.json')
        self.assertIsNone(runtime['resources'])
        self.assertEqual(runtime['resource_status'],'requires_human_setup')
        self.assertNotIn('timeout_seconds',runtime['resource_recommendations'])
        self.assertIn('finlab setup',(path/'public/TASK.md').read_text(encoding='utf-8'))

    def test_migration_or_model_selected_resources_are_rejected(self):
        for key,value in [('migration',{'from_schema':'finance_task_spec.v1'}),('resources',{'cpus':2,'memory_mb':4096,'timeout_seconds':1800})]:
            spec=copy.deepcopy(self.spec)
            if key=='migration':spec[key]=value
            else:spec['runtime_profile'][key]=value
            self.assertFalse(validate(spec,self.root)['passed'])

    def test_contract_membership_lists_accept_equivalent_order(self):
        spec = copy.deepcopy(self.spec)
        for group, fields in {
            'runtime_profile': ('asset_types', 'state_continuity'),
            'submission_contract': ('required_files', 'dependencies'),
            'protocol': ('maintenance',),
        }.items():
            for field in fields:
                spec[group][field].reverse()
        result = validate(spec, self.root)
        self.assertTrue(result['passed'], result['errors'])

    def test_membership_order_fix_does_not_allow_missing_or_extra_members(self):
        for field, values in (
            ('required_files', ['strategy.py', 'backtest.py', 'params.json', 'RESEARCH.md']),
            ('required_files', list(self.spec['submission_contract']['required_files']) + ['strategy.py']),
            ('dependencies', ['finlab', 'pandas', 'numpy', 'requests']),
        ):
            spec = copy.deepcopy(self.spec)
            spec['submission_contract'][field] = values
            result = validate(spec, self.root)
            self.assertFalse(result['passed'])


if __name__=='__main__':unittest.main()
