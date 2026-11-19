"""Generation guidance regressions; all model responses are temporary local fakes."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskgen.io_utils import catalog, read_json
from taskgen.validation import validate
from taskgen.workflow import Production
import test_workflow_api as wf_fixtures


PROJECT = Path(__file__).resolve().parents[1]


class GenerationContextTests(unittest.TestCase):
    # Reuse fixture builders without inheriting/repeating the workflow test suite.
    fixture_spec = wf_fixtures.WorkflowAPITests.fixture_spec
    configure = wf_fixtures.WorkflowAPITests.configure
    generate = wf_fixtures.WorkflowAPITests.generate
    review_for = staticmethod(wf_fixtures.WorkflowAPITests.review_for)
    responder = wf_fixtures.WorkflowAPITests.responder

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ('catalog', 'schemas', 'examples', 'prompts', 'config'):
            shutil.copytree(PROJECT / folder, self.root / folder)
        self.app = Production(self.root)
        self.app.init()
        examples = read_json(self.root / 'examples' / 'first_batch.json')
        if isinstance(examples, dict):
            examples = examples.get('tasks', examples.get('candidates', examples.get('specs', [])))
        self.examples = {x['family_id']: x for x in examples}
        env = patch.dict(os.environ, {wf_fixtures.TEST_ENV: wf_fixtures.TEST_KEY})
        env.start()
        self.addCleanup(env.stop)

    def test_generation_schema_uses_requested_family_and_catalog_identifiers(self):
        source_path = self.root / 'schemas' / 'task_spec.v2.schema.json'
        before = source_path.read_bytes()
        codes = catalog(self.root, 'decisions')
        for family in ('F01', 'F06'):
            with self.subTest(family=family):
                schema = self.app.generation_schema(family)
                props = schema['properties']
                self.assertEqual(props['family_id']['enum'], [family])
                self.assertEqual(set(props['source_ids']['items']['enum']),
                                 {x['id'] for x in catalog(self.root, 'sources')})
                seeds = [x for x in catalog(self.root, 'seeds') if x['family_id'] == family]
                self.assertEqual(set(props['core_mechanism']['enum']), {x['core_mechanism'] for x in seeds})
                self.assertEqual(set(props['decision_object']['enum']), {x['decision_object'] for x in seeds})
                self.assertEqual(set(props['required_decisions']['items']['enum']), set(codes['decisions']))
                self.assertEqual(set(props['research_modules']['items']['properties']['decision_id']['enum']),
                                 set(codes['decisions']))
                self.assertEqual(set(props['maintenance_targets']['items']['enum']), set(codes['maintenance']))
                self.assertIs(schema['additionalProperties'], False)
        self.assertEqual(source_path.read_bytes(), before)

    def test_cross_product_family_rejects_term_curve_mechanism(self):
        spec = self.fixture_spec('F05')
        spec['core_mechanism'] = 'term_curve_rotation'
        spec['decision_object'] = 'term_curve_pair_selection'
        result = validate(spec, self.root)
        self.assertFalse(result['passed'])
        self.assertTrue(any('core_mechanism与family_id方向不匹配' in x for x in result['errors']))
        self.assertTrue(any('decision_object与family_id方向不匹配' in x for x in result['errors']))

    def test_futures_products_with_undefined_quotation_units_are_rejected(self):
        for symbol in ('AU', 'AU2512', 'EC', 'EC2512'):
            with self.subTest(symbol=symbol):
                spec = self.fixture_spec('F06')
                spec['data_requirements']['symbols'] = [symbol]
                result = validate(spec, self.root)
                self.assertFalse(result['passed'])
                self.assertTrue(any('超出现有报价单位/规格能力' in x and symbol in x for x in result['errors']))

    def test_supported_fg_and_m_requests_accept_product_and_month_codes(self):
        for symbols in (['FG', 'M'], ['FG509', 'M2509']):
            with self.subTest(symbols=symbols):
                spec = self.fixture_spec('F06')
                spec['data_requirements']['symbols'] = symbols
                spec['bridge_contract']['symbols'] = symbols
                result = validate(spec, self.root)
                self.assertTrue(result['passed'], result['errors'])

    def test_futures_exchange_suffix_must_match_known_product(self):
        correct = {'B':'DCE','M':'DCE','Y':'DCE','FU':'SHF','SC':'INE','LU':'INE','FG':'ZCE','SA':'ZCE'}
        for product, suffix in correct.items():
            for symbol in (product, product+'.'+suffix, product+'2509.'+suffix):
                with self.subTest(symbol=symbol):
                    spec = self.fixture_spec('F06')
                    spec['data_requirements']['symbols'] = [symbol]
                    spec['bridge_contract']['symbols'] = [symbol]
                    result = validate(spec,self.root)
                    self.assertTrue(result['passed'],result['errors'])
            wrong = 'SHF' if suffix != 'SHF' else 'DCE'
            for symbol in (product+'.'+wrong,product+'2509.'+wrong):
                with self.subTest(symbol=symbol):
                    spec = self.fixture_spec('F06')
                    spec['data_requirements']['symbols'] = [symbol]
                    spec['bridge_contract']['symbols'] = [symbol]
                    result = validate(spec,self.root)
                    self.assertFalse(result['passed'])
                    self.assertTrue(any('后缀与品种不匹配' in e for e in result['errors']))
        context = self.app.generation_context('F06',[])
        self.assertEqual(context['requested_data_scope']['exchange_suffix_by_product'],correct)
        spec=self.fixture_spec('F06')
        spec['data_requirements']['symbols']=[];spec['bridge_contract']['symbols']=[]
        self.assertTrue(validate(spec,self.root)['passed'])

    def test_generation_context_summarizes_history_and_bounds_recent_feedback(self):
        feedback = [{'candidate_id': 'fake-' + str(i), 'kind': 'static_invalid',
                     'errors': ['temporary fixture ' + str(i)]} for i in range(5)]
        context = self.app.generation_context('F02', feedback)
        self.assertEqual(context['family_id'], 'F02')
        self.assertEqual(context['requested_family_id'], 'F02')
        self.assertEqual(context['family']['id'], 'F02')
        self.assertTrue(all(seed['family_id'] == 'F02' for seed in context['seeds']))
        self.assertEqual(context['recent_feedback'], feedback[-3:])
        self.assertEqual(len(feedback), 5)
        full = {x['task_id']: x['spec'] for x in self.app.records()}
        self.assertEqual({x['task_id'] for x in context['existing_tasks']}, set(full))
        for summary in context['existing_tasks']:
            spec = summary['spec']
            for needed in ('core_mechanism', 'decision_object', 'required_decisions',
                           'decision_dependencies', 'maintenance_targets', 'research_question'):
                self.assertEqual(spec[needed], full[summary['task_id']].get(needed))
            for omitted in ('research_modules', 'parameter_space', 'data_requirements',
                            'maintenance_rationale', 'source_ids'):
                self.assertNotIn(omitted, spec)
        profiles = read_json(self.root / 'schemas' / 'data_api_profiles.v2.json')['profiles']
        for name, profile in profiles.items():
            self.assertEqual(context['data_profiles'][name]['allowed_fields'],
                             sorted({f for ep in profile['endpoints'] for f in ep['fields']}))

    def test_review_http_context_contains_one_summary_per_neighbor_without_full_repeat(self):
        with wf_fixtures.local_model(self.responder) as (url, calls):
            self.configure(url)
            _, _, _ = self.generate()
            review_call = next(x for x in calls if x['payload']['model'] == 'fake-workflow-review')
            context = review_call['context']
            self.assertEqual(set(context['structural_check']), {'fingerprint', 'classification', 'exact_matches'})
            neighbors = context['nearest']
            self.assertTrue(neighbors)
            self.assertEqual(len({x['task_id'] for x in neighbors}), len(neighbors))
            for neighbor in neighbors:
                self.assertIn('score', neighbor)
                self.assertIn('exact_structure', neighbor)
                self.assertNotIn('research_modules', neighbor['spec'])
                self.assertNotIn('data_requirements', neighbor['spec'])
                self.assertNotIn('parameter_space', neighbor['spec'])
            self.assertIn('research_modules', context['candidate'])
            self.assertIn('public_task', context)

    def test_static_failure_feedback_reaches_next_same_family_after_other_family_turn(self):
        generations = []

        def respond(payload, context, number):
            if payload['model'] != 'fake-workflow-generation':
                return self.responder(payload, context, number)
            generations.append(copy.deepcopy(context))
            family = context['family_id']
            spec = self.fixture_spec(family)
            if family == 'F01' and sum(x['family_id'] == 'F01' for x in generations) == 1:
                spec['core_mechanism'] = 'nonexistent_temporary_fixture_code'
            return spec

        with wf_fixtures.local_model(respond) as (url, calls):
            self.configure(url, budget=8)
            result, _, _ = self.generate({'F01': 1, 'F02': 1}, limit=3)
            self.assertEqual([x['family_id'] for x in generations], ['F01', 'F02', 'F01'])
            self.assertEqual(generations[0]['recent_feedback'], [])
            self.assertEqual(generations[1]['recent_feedback'], [])
            feedback = generations[2]['recent_feedback']
            self.assertEqual(len(feedback), 1)
            self.assertEqual(feedback[0]['kind'], 'static_invalid')
            self.assertTrue(any('core_mechanism' in x for x in feedback[0]['errors']))
            self.assertEqual(result['preliminary_screened_counts'], {'F01': 1, 'F02': 1})
            self.assertEqual(result['family_attempts'], {'F01': 2, 'F02': 1})
            self.assertEqual(len(calls), 6)  # final stale-context review before automatic acceptance

    def test_semantic_review_failure_is_available_to_next_generation(self):
        generations, reviews = [], []

        def respond(payload, context, number):
            if payload['model'] == 'fake-workflow-generation':
                generations.append(copy.deepcopy(context))
                spec = self.fixture_spec(context['family_id'])
                if len(generations) > 1:
                    code = next(k for k in catalog(self.root, 'decisions')['maintenance']
                                if k not in spec['maintenance_targets'])
                    spec['maintenance_targets'].append(code)
                return spec
            review = self.review_for(context)
            reviews.append(copy.deepcopy(context))
            if len(reviews) == 1:
                review.update({'classification': 'uncertain',
                               'rationale': 'temporary review fixture asks for a clearer mechanism distinction',
                               'issues': ['temporary fixture: unclear distinction']})
            return review

        with wf_fixtures.local_model(respond) as (url, calls):
            self.configure(url, budget=6)
            result, _, candidates = self.generate({'F01': 1}, limit=2)
            self.assertEqual(len(generations), 2)
            feedback = generations[1]['recent_feedback']
            self.assertEqual(len(feedback), 1)
            self.assertEqual(feedback[0]['classification'], 'uncertain')
            self.assertEqual(feedback[0]['issues'], ['temporary fixture: unclear distinction'])
            self.assertIn('clearer mechanism distinction', feedback[0]['rationale'])
            self.assertIn(feedback[0]['candidate_id'], {p.name for p in candidates})
            self.assertIn('closest_task_id', feedback[0])
            self.assertEqual(result['preliminary_screened_counts'], {'F01': 1})
            self.assertEqual(len(calls), 5)  # original uncertain review is refreshed after the candidate set changes


if __name__ == '__main__':
    unittest.main()
