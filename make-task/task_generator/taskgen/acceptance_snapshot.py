"""Portable immutable machine-admission library, separate from candidate drafts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .io_utils import digest, now, read_json, write_json
from .registry import Registry, RegistryError


def snapshot(root, output=None):
    root = Path(root).resolve()
    dest = Path(output or root/'examples/accepted').resolve()
    if dest.exists(): raise RegistryError('ACCEPTANCE_SNAPSHOT_EXISTS')
    registry = Registry(root/'registry/tasks.sqlite')
    records = [registry.resolve_candidate(row['candidate_id']) for row in registry.approved_versions()
               if row.get('approval_method') == 'automated']
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.acceptance-snapshot-',dir=dest.parent) as tmp:
        folder = Path(tmp)/'content'; folder.mkdir()
        files = {}
        for record in records:
            Registry._validate_automatic_record(record)
            if re.search(r'(?:sk-[A-Za-z0-9]{24,}|qdl_[A-Za-z0-9_-]{24,})',json.dumps(record,ensure_ascii=False)):
                raise RegistryError('ACCEPTANCE_SNAPSHOT_CREDENTIAL_FORBIDDEN')
            name = digest(record)[:16]+'.json'
            write_json(folder/name,record)
            files[name] = hashlib.sha256((folder/name).read_bytes()).hexdigest()
        manifest={'schema_version':'taskgen.automatic_snapshot.v1','created_at':now(),'approval_method':'automated',
                  'human_approved':False,'records':len(records),'files':files,
                  'candidate_ids':[r['candidate_id'] for r in records],
                  'data_status':'binding_not_included','contains_credentials':False}
        write_json(folder/'manifest.json',manifest)
        folder.rename(dest)
    return {'directory':str(dest),'records':len(records),'human_approvals_created':0,'data_included':False}


def bootstrap(root, registry):
    folder = Path(root)/'examples/accepted'
    if not folder.exists(): return {'records':0,'status':'no_snapshot'}
    if folder.is_symlink() or not folder.is_dir(): raise RegistryError('ACCEPTANCE_SNAPSHOT_INVALID')
    manifest = read_json(folder/'manifest.json')
    if (manifest.get('schema_version') != 'taskgen.automatic_snapshot.v1' or manifest.get('approval_method') != 'automated'
            or manifest.get('human_approved') is not False or not isinstance(manifest.get('files'),dict)):
        raise RegistryError('ACCEPTANCE_SNAPSHOT_INVALID')
    expected = set(manifest['files'])|{'manifest.json'}
    if {p.name for p in folder.iterdir()} != expected: raise RegistryError('ACCEPTANCE_SNAPSHOT_MEMBERS_CHANGED')
    records=[]
    for name,sha in manifest['files'].items():
        if not re.fullmatch(r'[a-f0-9]{16}\.json',name): raise RegistryError('ACCEPTANCE_SNAPSHOT_UNSAFE_PATH')
        file=folder/name
        if file.is_symlink() or not file.is_file() or file.stat().st_size>5_000_000:
            raise RegistryError('ACCEPTANCE_SNAPSHOT_INVALID')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=sha: raise RegistryError('ACCEPTANCE_SNAPSHOT_HASH_CHANGED')
        record=read_json(file);Registry._validate_automatic_record(record);records.append(record)
    if (len(records)!=manifest.get('records') or len({r['candidate_id'] for r in records})!=len(records)
            or manifest.get('candidate_ids')!=[r['candidate_id'] for r in records]):
        raise RegistryError('ACCEPTANCE_SNAPSHOT_INVALID')
    # Validate every record before any import; an edited draft cannot bootstrap.
    for record in records: registry.import_approved(record,'Verified bundled automatic acceptance library; no human verdict')
    return {'records':len(records),'status':'verified_automated_library','human_approvals_created':0}
