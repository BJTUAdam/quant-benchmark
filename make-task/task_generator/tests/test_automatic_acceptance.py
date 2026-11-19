"""Automatic admission fixtures, no real model/human judgments or market data."""
import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from taskgen.io_utils import digest, read_json, write_json
from taskgen.registry import Registry, RegistryError
from taskgen.workflow import Production, WorkflowError

ROOT=Path(__file__).resolve().parents[1]


class AutomaticAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for folder in ('catalog','schemas','config','prompts'):
            shutil.copytree(ROOT/folder,self.root/folder)
        self.app=Production(self.root)
        self.spec=read_json(ROOT/'examples/first_batch.json')[0]

    def candidate(self,classification='duplicate',issues=None,recorded=True):
        batch=self.app.new_batch('synthetic_automatic_test')
        path=self.app.add_candidate(batch,copy.deepcopy(self.spec),'synthetic_automatic_test')
        checked=self.app.check_candidate(path)
        neighbors=checked['dedup']['nearest']
        review={'classification':classification,'closest_task_id':neighbors[0]['task_id'] if neighbors else None,
                'comparisons':[{'task_id':r['task_id'],'similarities':['unit fixture only'],'differences':['unit fixture only']} for r in neighbors],
                'rationale':'PROGRAM-FABRICATED unit test review, no actual API verdict',
                'issues':issues or [],'public_information_safe':True,'long_horizon_valid':True,
                'mode':'model_api','status':'passed' if classification=='distinct' else 'needs_attention','validation_errors':[],
                **{k:checked[k] for k in ('spec_hash','context_hash','content_hash')}}
        write_json(path/'operator/review.json',review)
        if recorded:self.app.registry.event(path.name,'model_reviewed','Synthetic fixture only',self.spec,review)
        return path,review

    def test_duplicate_historical_reference_accepts_runnable_spec_without_fake_mother(self):
        self.app.init()
        path,_=self.candidate()
        historical_ids={r['task_id'] for r in self.app.registry.records()}
        record=self.app.accept(path.name)
        self.assertEqual(record['approval_method'],'automated')
        self.assertEqual(record['novelty'],'duplicate');self.assertFalse(record['counts_new_question'])
        self.assertEqual(record['task_id'],path.name)
        self.assertIn(record['related_task_id'],historical_ids)
        with self.app.registry.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM approvals').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM machine_acceptances').fetchone()[0],1)
        self.assertFalse(record['provenance']['human_approved'])

    def test_variant_without_approved_parent_is_non_new_execution_spec(self):
        self.app.init();path,_=self.candidate('variant')
        record=self.app.accept(path.name)
        self.assertEqual(record['novelty'],'variant');self.assertFalse(record['counts_new_question'])
        self.assertEqual(self.app.registry.resolve_candidate(path.name)['spec_hash'],digest(self.spec))

    def test_registry_itself_does_not_count_existing_structure_as_new(self):
        self.app.registry.import_history([{**self.spec,'id':'synthetic-history','status':'historical'}])
        path,review=self.candidate('distinct')
        from finlab.tasks import PUBLIC_FILES
        files={n:(path/'public'/n).read_text(encoding='utf-8') for n in PUBLIC_FILES}
        record=self.app.registry.accept(path.name,self.spec,review,files,novelty='distinct')
        self.assertEqual(record['novelty'],'duplicate');self.assertFalse(record['counts_new_question'])

    def test_missing_api_evidence_is_not_replaced_by_edited_review_json(self):
        path,_=self.candidate(recorded=False)
        with self.assertRaisesRegex(RegistryError,'MATCHING_MODEL_REVIEW_REQUIRED'):self.app.accept(path.name)
        self.assertIsNone(self.app.registry.approval(path.name))

    def test_current_safety_and_uncertainty_issues_still_block(self):
        for classification,issues in [('uncertain',[]),('duplicate',['缺少必要接口'])]:
            path,_=self.candidate(classification,issues)
            with self.assertRaisesRegex(WorkflowError,'UNRESOLVED'):self.app.accept(path.name)
            self.assertIsNone(self.app.registry.approval(path.name))

    def test_stale_comparison_context_is_blocked(self):
        path,_=self.candidate()
        changed=copy.deepcopy(self.spec);changed['title']+=' separate candidate'
        self.app.add_candidate(self.app.new_batch('synthetic_test'),changed,'synthetic_test')
        with self.assertRaisesRegex(WorkflowError,'MISSING_OR_STALE'):self.app.accept(path.name)

    def test_exact_machine_spec_reuses_accepted_record_not_new_question(self):
        first,_=self.candidate('distinct');accepted=self.app.accept(first.name)
        second,_=self.candidate('duplicate');reused=self.app.accept(second.name)
        self.assertEqual(reused['candidate_id'],accepted['candidate_id'])
        self.assertEqual(reused['pipeline_candidate_id'],accepted['candidate_id'])
        self.assertTrue(reused['reused'])
        self.assertEqual(len(self.app.registry.approved_versions()),1)

    def test_machine_record_transfer_keeps_machine_provenance_and_tamper_fails(self):
        path,_=self.candidate('variant');record=self.app.accept(path.name)
        dest=Registry(self.root/'target/tasks.sqlite')
        transferred=dest.import_approved(record,'Explicit test trusted transfer')
        self.assertEqual(transferred['approval_method'],'automated')
        result=dest.resolve_candidate(path.name)
        self.assertFalse(result['provenance']['human_approved']);self.assertEqual(result['novelty'],'variant')
        bad=copy.deepcopy(record);bad['counts_new_question']=True
        with self.assertRaisesRegex(RegistryError,'HASH_OR_EVIDENCE_INVALID'):dest.import_approved(bad,'Test transfer')
        bad=copy.deepcopy(record);bad['provenance']['human_approved']=True
        with self.assertRaisesRegex(RegistryError,'HASH_OR_EVIDENCE_INVALID'):dest.import_approved(bad,'Test transfer')
        with dest.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM approvals').fetchone()[0],0)

    def test_machine_snapshot_bootstraps_new_platform_without_approving_drafts(self):
        path,_=self.candidate('variant');record=self.app.accept(path.name)
        self.app.snapshot_acceptances()
        target=self.root/'target';target.mkdir()
        for folder in ('catalog','schemas','prompts','config'):shutil.copytree(ROOT/folder,target/folder)
        shutil.copytree(self.root/'examples',target/'examples')
        app=Production(target);app.init()
        self.assertEqual(app.registry.resolve_candidate(path.name)['spec_hash'],record['spec_hash'])
        with app.registry.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM approvals').fetchone()[0],0)

    def test_machine_snapshot_detects_hash_change_before_import(self):
        path,_=self.candidate();self.app.accept(path.name);self.app.snapshot_acceptances()
        file=next((self.root/'examples/accepted').glob('????????????????.json'))
        file.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(RegistryError,'SNAPSHOT_HASH_CHANGED'):self.app.approved_catalog()

    def publish_reviewed_snapshot(self,path,review):
        from taskgen.batch_snapshot import snapshot
        state=read_json(path/'state.json');state['model_review_status']=review['status'];write_json(path/'state.json',state)
        return Path(snapshot(self.root,path.parent.parent.name)['path'])

    def test_original_verified_reviewed_bytes_survive_platform_renderer_upgrade(self):
        path,review=self.candidate('variant');snapshot=self.publish_reviewed_snapshot(path,review)
        original=(snapshot/'candidates'/path.name/'public/TASK.md').read_text(encoding='utf-8')
        from taskgen.render import build_package
        def updated_renderer(root,folder,spec,state):
            result=build_package(root,folder,spec,state)
            task=Path(folder)/'public/TASK.md'
            task.write_text(task.read_text(encoding='utf-8')+'\nPlatform renderer upgrade after original review\n',encoding='utf-8')
            return result
        with patch('taskgen.render.build_package',side_effect=updated_renderer):
            result=self.app.accept(path.name)
        self.assertEqual(result['admission_content_source'],'verified_reviewed_snapshot')
        self.assertEqual(result['artifacts']['public_files']['TASK.md'],original)
        with patch('taskgen.render.build_package',side_effect=AssertionError('immutable export must not render')):
            exported=self.app.export(path.name)
        self.assertEqual((Path(exported['directory'])/'public/TASK.md').read_text(encoding='utf-8'),original)

    def test_snapshot_fallback_never_accepts_rewritten_model_evidence(self):
        path,review=self.candidate('variant');self.publish_reviewed_snapshot(path,review)
        changed=copy.deepcopy(review);changed['rationale']='tampered';write_json(path/'operator/review.json',changed)
        with self.assertRaisesRegex(RegistryError,'MATCHING_MODEL_REVIEW_REQUIRED'):
            self.app.accept(path.name)

    def test_machine_snapshot_export_works_without_active_candidates(self):
        path,_=self.candidate('variant');self.app.accept(path.name);self.app.snapshot_acceptances()
        shutil.rmtree(self.root/'runs')
        result=self.app.export(path.name)
        self.assertEqual(result['approval_method'],'automated')
        self.assertTrue((Path(result['directory'])/'public/TASK.md').is_file())


if __name__=='__main__':unittest.main()
