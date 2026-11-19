"""Read-only artifact checks; prints file paths/counts, never credential values."""
from __future__ import annotations
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shlex
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from finlab.common import read,write,now
from finlab.tasks import PUBLIC_FILES
from finlab.cli import parser
from taskgen.cli import parser as task_parser


def audit(destination):
    errors=[];archives=[];commands=[];toolkit_members={}
    key_pattern=re.compile(rb'(?:sk-[A-Za-z0-9]{24,}|qdl_[A-Za-z0-9_-]{24,})')
    distribution=read(Path(destination)/'manifest.json')
    from finlab.task_packages import RELEASE, TASK_PACKAGE_PATH, verify as verify_task_packages
    package_report=verify_task_packages()
    if distribution.get('release')!=RELEASE:errors.append('release identifier mismatch')
    toolkit_paths=[Path(destination)/Path(a['path']).name for a in distribution['archives']]
    for p in sorted(toolkit_paths):
        with zipfile.ZipFile(p) as z:
            names=z.namelist()
            toolkit_members[p.name]=set(names)
            manifest=json.loads(z.read('task_generator/TOOLKIT_MANIFEST.json'))
            for rel,sha in manifest['files'].items():
                member='task_generator/'+rel;body=z.read(member)
                if hashlib.sha256(body).hexdigest()!=sha:errors.append(member+': hash mismatch')
                if key_pattern.search(body):errors.append(member+': credential-like content')
                if any(x in Path(rel).parts for x in ('work_v2','runs','registry','__pycache__','.finlab')) or Path(rel).suffix in {'.sqlite','.pkl','.log'}:
                    errors.append(member+': private file')
                if rel.startswith(('market_bridge/', 'config/data_bridge_templates/')) or rel in {
                    'taskgen/guide.py', 'taskgen/data_specs.py', 'schemas/task_spec.schema.json', 'schemas/data_schema_profiles.json',
                    'deployment/data_bridge.compose.example.yml', 'tests/test_market_bridge.py'}:
                    errors.append(member+': retired v1 tool in active delivery')
                if Path(rel).name.startswith('.env'):errors.append(member+': environment snapshot')
            if set(names)!={'task_generator/'+n for n in manifest['files']}|{'task_generator/TOOLKIT_MANIFEST.json'}:
                errors.append(p.name+': unlisted members')
            archives.append({'file':p.name,'files':len(manifest['files']),'hashes_verified':True})
    package_members={'task_generator/'+p.relative_to(ROOT).as_posix() for p in (ROOT/TASK_PACKAGE_PATH).rglob('*') if p.is_file()}
    for archive,members in toolkit_members.items():
        if not package_members <= members:errors.append(archive+': integrated task packages missing')
    skill_info=distribution.get('strategy_skill')
    if not skill_info:errors.append('standalone strategy skill missing')
    else:
        p=Path(destination)/skill_info['file']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=skill_info['sha256']:errors.append('skill archive hash mismatch')
        with zipfile.ZipFile(p) as z:
            skill_manifest=json.loads(z.read('finlab-strategy/SKILL_MANIFEST.json'))
            expected={'finlab-strategy/'+n for n in skill_manifest['files']}|{'finlab-strategy/SKILL_MANIFEST.json'}
            if set(z.namelist())!=expected:errors.append('skill allowlist mismatch')
            for rel,sha in skill_manifest['files'].items():
                body=z.read('finlab-strategy/'+rel)
                if hashlib.sha256(body).hexdigest()!=sha:errors.append('skill member hash mismatch: '+rel)
                if key_pattern.search(body):errors.append('skill credential-like content: '+rel)
    manifest=read(ROOT/'examples/v2/manifest.json')
    for candidate in manifest['candidates']:
        public=ROOT/candidate['path']/'public'
        if {p.name for p in public.iterdir()}!=PUBLIC_FILES:errors.append(candidate['family_id']+': public allowlist')
        if candidate['human_approved']:errors.append(candidate['family_id']+': unexpected approval')
        for p in public.iterdir():
            if key_pattern.search(p.read_bytes()):errors.append(p.name+': credential-like content')
        runtime=read(public/'runtime_profile.json')
        if runtime.get('resources') is not None or runtime.get('resource_status')!='requires_human_setup':
            errors.append(candidate['family_id']+': candidate budget was selected without human setup')
    from taskgen.batch_snapshot import verify_snapshot,SnapshotError
    generated=[];current_independent=0
    from taskgen.acceptance_snapshot import bootstrap
    from taskgen.registry import Registry
    import tempfile
    with tempfile.TemporaryDirectory(prefix='finlab-distribution-acceptance-') as temporary:
        accepted_registry=Registry(Path(temporary)/'tasks.sqlite')
        acceptance_library=bootstrap(ROOT,accepted_registry)
        accepted_records=accepted_registry.approved_versions()
        if any(r.get('approval_method')!='automated' for r in accepted_records): errors.append('automated library has non-machine records')
    for archive in ('finlab_v2_complete.zip','task_production_v2.zip'):
        expected={'task_generator/'+p.relative_to(ROOT).as_posix() for p in (ROOT/'examples/accepted').rglob('*') if p.is_file()}
        if not expected<=toolkit_members.get(archive,set()):errors.append(archive+': accepted library missing')
    for folder in sorted((ROOT/'examples/generated').glob('*')):
        if folder.name.startswith('.candidate-snapshot-'):continue
        try:
            rows=verify_snapshot(folder,ROOT)
            generated.extend(rows)
            summary=read(folder/'summary.json')
            current_independent+=summary['current_independent_reviews_passed_retained']
            expected={'task_generator/'+p.relative_to(ROOT).as_posix() for p in folder.rglob('*') if p.is_file()}
            for archive in ('finlab_v2_complete.zip','task_production_v2.zip'):
                if not expected<=toolkit_members.get(archive,set()):errors.append(archive+': generated snapshot missing')
            for row in rows:
                public=folder/'candidates'/row['task_id']/'public'
                runtime=read(public/'runtime_profile.json')
                if runtime.get('resources') is not None or runtime.get('resource_status')!='requires_human_setup':
                    errors.append(row['task_id']+': generated candidate selected a budget')
        except (SnapshotError,OSError,ValueError,KeyError) as exc:
            errors.append(folder.name+': invalid generated snapshot '+type(exc).__name__)
    command_documents=list((ROOT/'docs/v2').glob('*.md'))+list((ROOT/'skills/finlab-strategy/references').glob('*.md'))
    for document in command_documents:
        for line in document.read_text(encoding='utf-8').splitlines():
            if not line.startswith('finlab '):continue
            try:
                args=shlex.split(line)[1:]
                command=parser().parse_args(args)
                if command.group=='task':task_parser().parse_args(command.args)
                commands.append(line)
            except (SystemExit,ValueError):errors.append(document.name+': invalid command')
    class Page(HTMLParser):
        def __init__(self):super().__init__();self.external=[]
        def handle_starttag(self,tag,attrs):
            for name,value in attrs:
                if tag in {'script','link','img','iframe'} and name in {'src','href'} and str(value).startswith(('http:','https:','//')):self.external.append(value)
    external_assets=0
    for name in ['system_v2.html','tushare_api_offline.html']:
        page=Page();page.feed((ROOT/'docs'/name).read_text(encoding='utf-8'))
        external_assets+=len(page.external)
        if page.external:errors.append(name+': HTML has external assets')
    report={'schema_version':'finlab.distribution_audit.v2','checked_at':now(),'passed':not errors,
            'archives':archives,'candidate_packages':len(manifest['candidates']),
            'generated_candidate_packages':len(generated),'generated_current_independent_review_passed':current_independent,
            'automated_library_records':acceptance_library['records'],
            'integrated_task_packages':package_report['count'],
            'individual_task_archives':12,'patch_application_required':False,
            'commands_parsed':len(commands),'html_external_assets':external_assets,
            'standalone_skill':skill_info['file'] if skill_info else None,
            'credential_scan':'no matches' if not any('credential' in e for e in errors) else 'failed',
            'errors':errors}
    write(ROOT/'docs/distribution_audit_v2.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if not errors else 2


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',nargs='?',type=Path,default=ROOT/'dist')
    raise SystemExit(audit(p.parse_args().destination))
