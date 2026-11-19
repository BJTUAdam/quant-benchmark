"""Read-only validation of bundled task specifications and their individual ZIPs."""
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

from .common import ROOT, LabError, digest, file_hash, read
from .submission import check as check_submission
from .tasks import PUBLIC_FILES

TASK_PACKAGE_PATH = 'task_packages/20261110'
RELEASE = 'v2-integrated-task12-20261110'


def _member_path(root, value):
    if not isinstance(value,str): raise LabError('TASK_PACKAGE_UNSAFE_PATH')
    relative=PurePosixPath(value)
    if relative.is_absolute() or '..' in relative.parts or '\\' in value or ':' in value:
        raise LabError('TASK_PACKAGE_UNSAFE_PATH')
    path=(root/value).resolve()
    if not path.is_relative_to(root.resolve()): raise LabError('TASK_PACKAGE_UNSAFE_PATH')
    return path


def verify(root=None):
    root=Path(root or ROOT/TASK_PACKAGE_PATH).resolve()
    index=read(root/'任务清单.json')
    rows=index.get('tasks')
    if (not isinstance(rows,list) or len(rows)!=12 or index.get('count')!=12
            or Counter(x.get('family_id') for x in rows)!=Counter({f'F{i:02d}':2 for i in range(1,7)})):
        raise LabError('TASK_PACKAGE_COVERAGE_INVALID')
    code_pattern=re.compile(rb'(?:sk-[A-Za-z0-9]{24,}|qdl_[A-Za-z0-9_-]{24,})')
    packages=[]
    from taskgen.validation import validate
    top={'任务清单.json','题包索引.md','可用性检查报告.json','RELEASE.json','zips'}|{x['directory'] for x in rows}
    if {p.name for p in root.iterdir()}!=top:raise LabError('TASK_PACKAGE_ROOT_ALLOWLIST_CHANGED')
    for row in rows:
        folder=_member_path(root,row['directory'])
        archive=_member_path(root,row['archive'])
        if not folder.is_dir() or folder.is_symlink() or not archive.is_file() or archive.is_symlink():
            raise LabError('TASK_PACKAGE_MISSING')
        if file_hash(archive)!=row['sha256']: raise LabError('TASK_PACKAGE_ARCHIVE_HASH_CHANGED')
        manifest=read(folder/'MANIFEST.json')
        if manifest.get('task_id')!=row['task_id'] or manifest.get('spec_hash')!=row['spec_hash']:
            raise LabError('TASK_PACKAGE_IDENTITY_CHANGED')
        actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
        if actual!=set(manifest['files'])|{'MANIFEST.json'}:
            raise LabError('TASK_PACKAGE_ALLOWLIST_CHANGED')
        for rel,sha in manifest['files'].items():
            path=_member_path(folder,rel)
            if path.is_symlink() or file_hash(path)!=sha:raise LabError('TASK_PACKAGE_FILE_HASH_CHANGED')
            if path.suffix in {'.sqlite','.pkl','.log','.pyc'} or path.name.startswith('.env') or code_pattern.search(path.read_bytes()):
                raise LabError('TASK_PACKAGE_PRIVATE_CONTENT')
        public=folder/'public'
        if {p.name for p in public.iterdir()}!=PUBLIC_FILES:raise LabError('PUBLIC_ALLOWLIST_ERROR')
        operator={'spec.json','task_card.json','validation.json','peer_review.json','dedup.json','provenance.json',
                  'source_cards.json','DATA_BINDING_PROMPT.md','RUN_READINESS.json','RUN_CHECKLIST.md',
                  'binding.selection.draft.private.json'}
        if {p.name for p in (folder/'operator').iterdir()}!=operator:
            raise LabError('TASK_PACKAGE_OPERATOR_ALLOWLIST_CHANGED')
        entry=read(public/'task_input.draft.json')
        if (entry['initial_message'].get('content_file')!='TASK.md' or entry.get('path_base')!='entry_directory'
                or entry.get('human_approval_required') is not False or entry.get('human_approved') is not False
                or entry.get('data_status')!='not_bound' or entry.get('runtime_status')!='not_connected'
                or entry.get('external_api_review_status')!='not_run' or entry.get('automatic_acceptance_status')!='not_registered'):
            raise LabError('TASK_PACKAGE_STATUS_INVALID')
        for target in ('TASK.md',entry['binding_contract'],entry['runtime_profile']):
            if not _member_path(public,target).is_file():raise LabError('PUBLIC_ENTRY_REFERENCE_MISSING')
        spec=read(folder/'operator/spec.json')
        if digest(spec)!=row['spec_hash'] or not validate(spec,ROOT)['passed']:raise LabError('TASK_PACKAGE_SPEC_INVALID')
        if not check_submission(public)['passed']:raise LabError('TASK_PACKAGE_TEMPLATE_INVALID')
        runtime=read(public/'runtime_profile.json');contract=read(public/'bridge_contract.json')
        if (runtime.get('resources') is not None or runtime.get('resource_status')!='requires_human_setup'
                or contract.get('virtual_start') is not None or contract.get('virtual_stop') is not None
                or contract.get('dataset_id') is not None):
            raise LabError('TASK_PACKAGE_UNCONFIRMED_BINDING')
        for name in ('DATA_API.md','SUBMISSION.md'):
            for target in re.findall(r'\[[^\]\n]+\]\(([^)\s]+)\)',(public/name).read_text(encoding='utf-8')):
                if ':' not in target and not target.startswith('#'):
                    if not _member_path(public,target.split('#')[0]).is_file():raise LabError('TASK_PACKAGE_REFERENCE_MISSING')
        with zipfile.ZipFile(archive) as z:
            names=z.namelist();prefix=folder.name+'/'
            if (len(names)!=len(set(names)) or set(names)!={prefix+p for p in actual}
                    or any(i.is_dir() or (i.external_attr>>16)&0o170000==0o120000 for i in z.infolist())):
                raise LabError('TASK_PACKAGE_ZIP_ALLOWLIST_CHANGED')
            for rel,sha in manifest['files'].items():
                if hashlib.sha256(z.read(prefix+rel)).hexdigest()!=sha:raise LabError('TASK_PACKAGE_ZIP_MEMBER_CHANGED')
        packages.append({'task_id':row['task_id'],'family_id':row['family_id'],'archive':row['archive'],
                         'sha256':row['sha256'],'spec_hash':row['spec_hash'],'public_entry_valid':True})
    if len({r['task_id'] for r in packages})!=12 or len({r['spec_hash'] for r in packages})!=12:
        raise LabError('TASK_PACKAGE_DUPLICATE_IDENTITY')
    if any(p.suffix.lower()=='.zip' for p in root.iterdir()):
        raise LabError('INTEGRATED_RELEASE_MUST_NOT_REQUIRE_PATCH_ARCHIVE')
    return {'passed':True,'count':12,'per_family':2,'public_files_per_task':11,
            'patch_application_required':False,'human_approval_required':False,
            'real_task_data_bound':False,'formal_backtest_completed':False,'packages':packages}
