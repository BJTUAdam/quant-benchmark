"""Operator-only bridge from accepted specifications to validated batch inputs.

Public task packages and private platform handoffs are deliberately distinct.
This module consumes recorded acceptance; it never substitutes for its checks.
"""
from __future__ import annotations
from contextlib import closing
import copy
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import zipfile

from .common import ROOT, LabError, digest, file_hash, now, read, write

_RESEARCH_REQUIRED_FILES = {'checkpoint.pkl', 'memory.json', 'account.private.json', 'bridge_audit.private.sqlite',
                            'public/equity.csv', 'public/orders.csv', 'public/trades.csv',
                            'public/metrics.json', 'public/run_manifest.json'}
_PORTABLE_BINDING_FIELDS = {'schema_version', 'frozen_dir', 'candidate_id', 'approved_spec_hash',
                            'contract_review_confirmed', 'research_fraction', 'feeds', 'runtime', 'stage_windows'}
_EPISODE_BINDING_FIELDS = {'approved_spec', 'images', 'windows', 'dataset_id', 'freeze_hash',
                           'synthetic', 'disclosure_policy_hash', 'resource_setup',
                           'acceptance_method', 'acceptance_novelty'}


def _validate_handoff_binding_fields(binding, *, source=False):
    """Do not carry arbitrary operator notes, credentials or path fields."""
    allowed = _PORTABLE_BINDING_FIELDS | (_EPISODE_BINDING_FIELDS if source else set())
    required = {'schema_version', 'frozen_dir', 'candidate_id', 'approved_spec_hash',
                'contract_review_confirmed', 'feeds', 'runtime'}
    if (not isinstance(binding, dict) or set(binding) - allowed or not required <= set(binding)
            or binding.get('schema_version') != 'finlab.binding.v2'
            or not isinstance(binding.get('runtime'), dict)):
        raise LabError('HANDOFF_BINDING_FIELDS', '运行绑定含未知字段或缺少明确的v2契约；不能自动透传非规范信息')
    from .preparation import _reject_credentials
    try:
        _reject_credentials(binding)
    except LabError:
        raise LabError('HANDOFF_CREDENTIAL_FORBIDDEN', '交接绑定不得包含凭证，使用平台环境变量配置') from None


def approved(binding, registry=None):
    """Compatibility name for exact, immutable machine or historical acceptance."""
    from taskgen.registry import Registry, RegistryError
    registry = registry or Registry(ROOT / 'registry/tasks.sqlite')
    try:
        reference = binding.get('candidate_id', '')
        record = registry.resolve_candidate(reference)
    except RegistryError:
        raise LabError('TASK_ACCEPTANCE_REQUIRED', '未找到通过程序检查和模型复核的登记版本；先运行 task accept，历史ID或草稿不能替代检查') from None
    if record['spec_hash'] != binding.get('approved_spec_hash'):
        raise LabError('APPROVAL_SPEC_HASH_MISMATCH')
    return record


def validate_approved_contract(binding, frozen, registry=None):
    record = approved(binding, registry)
    spec = record['spec']
    from .symbol_scope import validate_symbol_selection
    validate_symbol_selection(spec, [i['symbol'] for i in (binding.get('runtime',{}).get('instruments') or [])])
    allowed = {e['api_name']: set(e['fields']) for e in spec['bridge_contract']['endpoints']}
    actual = frozen.manifest['apis']
    if set(actual) != set(allowed) or any(set(actual[name]['fields']) != fields for name, fields in allowed.items()):
        raise LabError('FROZEN_CONTRACT_DIFFERS_FROM_APPROVED_SPEC')
    if binding.get('runtime', {}).get('frequency') != spec['runtime_profile']['frequency']:
        raise LabError('RUNTIME_DIFFERS_FROM_APPROVED_SPEC')
    if binding.get('contract_review_confirmed') is not True:
        raise LabError('BOUND_CONTRACT_REVIEW_REQUIRED')
    return record


def check_binding(binding, submission=None, *, registry=None, environment=True):
    """Accumulate blockers; approval, data and environment fail independently."""
    from .data import FrozenView
    from . import runtime
    from .setup import load_settings
    result = {'schema_version': 'finlab.delivery_check.v1', 'checked_at': now(),
              'passed': False, 'checks': {}, 'blockers': [], 'scope': 'platform_private'}

    def check(name, action):
        try:
            value = action()
            result['checks'][name] = {'status': 'passed'}
            return value
        except (LabError, OSError, ValueError, KeyError, TypeError) as error:
            code = error.code if isinstance(error, LabError) else 'INVALID_OR_MISSING_ARTIFACT'
            message = error.message if isinstance(error, LabError) else '检查材料缺失、格式无效或版本不一致'
            result['checks'][name] = {'status': 'blocked', 'code': code}
            result['blockers'].append({'check': name, 'code': code, 'message': message})

    record = check('approval', lambda: approved(binding, registry))
    frozen = check('frozen_dataset', lambda: FrozenView(binding['frozen_dir']))
    settings = check('operator_setup', load_settings) if environment else None
    selected = copy.deepcopy(binding)
    if settings:
        supplied = selected.get('runtime', {}).get('resources')
        if supplied is not None and supplied != settings['resources']:
            check('resource_consistency', lambda: (_ for _ in ()).throw(LabError('RESOURCE_SELECTION_CONFLICT')))
        else:
            selected['runtime']['resources'] = settings['resources']
            selected['runtime']['resource_status'] = 'human_selected'
    if frozen:
        result['synthetic'] = frozen.manifest.get('synthetic') is True
        check('runtime_binding', lambda: runtime.validate_binding(selected, frozen))
        check('stage_windows', lambda: runtime.derive_windows(selected, frozen))
        if record:
            check('approved_data_contract', lambda: validate_approved_contract(binding, frozen, registry))
        if frozen.manifest.get('synthetic'):
            check('real_data_required', lambda: (_ for _ in ()).throw(LabError('REAL_DATA_REQUIRED')))
    if record:
        result['approval'] = {k: record[k] for k in ('candidate_id', 'task_id', 'version', 'spec_hash')}
        result['approval']['acceptance_method'] = record.get('approval_method', 'human')
        result['approval']['novelty'] = record.get('novelty', record.get('action'))
    if submission is not None:
        from .submission import check as submission_check
        def verify_submission():
            report = submission_check(submission)
            if not report['passed']:
                raise LabError('SUBMISSION_INVALID', '；'.join(report['errors']))
            result['submission_hash'] = report['submission_hash']
            return report
        check('submission', verify_submission)
    if environment:
        check('docker_daemon', lambda: runtime.docker(['version', '--format', '{{.Server.Version}}'], timeout=15))
        def images():
            lock = read(ROOT / 'deployment/images.lock.json')['images']
            if set(lock) != {'engine', 'worker'}: raise LabError('IMAGES_NOT_BUILT')
            for image in lock.values(): runtime.docker(['image', 'inspect', image], timeout=15)
            result['image_ids'] = lock
        check('locked_images', images)
    else:
        result['checks']['environment'] = {'status': 'not_checked'}
    result['passed'] = not result['blockers']
    result['status'] = 'ready_for_research' if result['passed'] and environment else 'static_valid' if result['passed'] else 'blocked'
    result['execution_verified'] = False
    return result


def prepare(reference, selection, output, *, version=None, frozen_dir=None):
    from taskgen.registry import Registry, RegistryError
    from .preparation import prepare as build_plan, write_preparation
    registry = Registry(ROOT / 'registry/tasks.sqlite')
    try:
        record = registry.resolve_approved(reference, version=version)
    except RegistryError:
        record = None
    if not record:
        raise LabError('TASK_ACCEPTANCE_REQUIRED', '先运行 task accept 或检索已接纳版本；不要反复生成同义候选或把历史草稿当作母题')
    from .setup import load_settings
    try:
        resources = load_settings()['resources']
    except LabError:
        resources = None
    report = build_plan(record['spec'], selection, approval=record, frozen_dir=frozen_dir, operator_resources=resources)
    return write_preparation(output, report)


def _smoke_receipt(folder, binding, submission_hash):
    required = _RESEARCH_REQUIRED_FILES
    candidates = []
    for path in (folder / 'jobs').glob('*/receipt.private.json'):
        try:
            receipt = read(path)
            hashes = receipt.get('hashes')
            if (receipt.get('research') is not True or receipt.get('segment') != 0
                    or receipt.get('job_id') != path.parent.name
                    or receipt.get('submission_hash') != submission_hash
                    or receipt.get('dataset_id') != binding['dataset_id']
                    or receipt.get('images') != binding['images']
                    or not isinstance(hashes, dict) or not required <= set(hashes)):
                continue
            base = path.parent / 'artifacts'
            success = read(base / 'success.private.json')
            config = read(path.parent / 'input/job.private.json')
            public = read(base / 'public/run_manifest.json')
            if (success.get('status') != 'ready_to_commit' or success.get('hashes') != hashes
                    or config.get('research') is not True or config.get('segment') != 0
                    or config.get('job_id') != receipt['job_id']
                    or config.get('submission_hash') != submission_hash
                    or digest(config.get('binding')) != digest(binding)
                    or public.get('research') is not True or public.get('segment') != 0
                    or public.get('job_id') != receipt['job_id']
                    or public.get('submission_hash') != submission_hash
                    or public.get('dataset_id') != binding['dataset_id']):
                continue
            for rel, sha in hashes.items():
                if (not isinstance(rel, str) or not isinstance(sha, str)
                        or not re.fullmatch(r'[0-9a-f]{64}', sha)
                        or PurePosixPath(rel).is_absolute() or '..' in PurePosixPath(rel).parts
                        or '\\' in rel or ':' in rel):
                    raise ValueError()
                target = (base / rel).resolve()
                if (not target.is_relative_to(base.resolve()) or not target.is_file()
                        or (base / rel).is_symlink() or file_hash(target) != sha):
                    raise ValueError()
            if not isinstance(receipt.get('committed_at'), str) or not receipt['committed_at']:
                continue
            candidates.append(receipt)
        except (OSError, KeyError, TypeError, ValueError):
            # An incomplete orphan job cannot supply acceptance; retain it for
            # diagnosis and continue looking for a complete matching run.
            continue
    if not candidates: raise LabError('MATCHING_RESEARCH_ACCEPTANCE_REQUIRED', '须先使用同一提交、冻结版本和镜像成功完成研发回测')
    return sorted(candidates, key=lambda r: r['committed_at'])[-1]


def _validate_parent_approval(record, parent):
    """A portable variant names a real mother approval with the same mechanism."""
    from taskgen.dedup import fingerprint
    try:
        if (record.get('action') != 'variant' or not isinstance(parent, dict)
                or parent.get('action') not in {'approved', 'updated'}
                or parent['task_id'] != record['task_id']
                or parent['candidate_id'] == record['candidate_id']
                or parent.get('artifacts', {}).get('status') != 'captured'
                or digest(parent['spec']) != parent['spec_hash']
                or fingerprint(parent['spec']) != fingerprint(record['spec'])):
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise LabError('HANDOFF_PARENT_MISMATCH', '变体须引用同一研究机制、同一母题ID及完整的真实批准证据') from None


def export_handoff(episode, submission, destination):
    from .runtime import load_episode
    from .archive import bound_package
    from .submission import check as submission_check
    folder, binding, state, frozen = load_episode(episode)
    _validate_handoff_binding_fields(binding, source=True)
    record = validate_approved_contract(binding, frozen)
    parent = None
    if record.get('action') == 'variant':
        from taskgen.registry import Registry, RegistryError
        try:
            parent = Registry(ROOT / 'registry/tasks.sqlite').resolve_approved(record['task_id'])
        except RegistryError:
            raise LabError('PARENT_HANDOFF_REQUIRED', '未找到可交接的真实获批母题；保留变体待处理，不自动生成母题批准') from None
        _validate_parent_approval(record, parent)
    report = check_binding(binding, submission)
    if not report['passed']: raise LabError('DELIVERY_BLOCKED', '；'.join(b['code'] for b in report['blockers']))
    if record.get('artifacts', {}).get('status') != 'captured':
        raise LabError('APPROVAL_ARTIFACTS_REQUIRED', '旧审批缺少可核验审查快照；须运营方恢复原证据，不能伪造')
    submission_report = submission_check(submission)
    receipt = _smoke_receipt(folder, binding, submission_report['submission_hash'])
    dest = Path(destination).resolve()
    if dest.exists(): raise LabError('ALREADY_EXISTS')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='finlab-handoff-', dir=dest.parent) as tmp:
        base = Path(tmp)
        private = base / 'platform_private'; private.mkdir()
        # Only the immutable DB and manifest are needed, never arbitrary private files.
        if ({p.name for p in frozen.folder.iterdir()} != {'snapshot.sqlite', 'manifest.private.json'}
                or any(p.is_symlink() or not p.is_file() for p in frozen.folder.iterdir())):
            raise LabError('FROZEN_HANDOFF_ALLOWLIST')
        shutil.copytree(frozen.folder, private / 'frozen')
        write(private / 'approval.json', record)
        if parent is not None:
            write(private / 'parent_approval.json', parent)
        draft = {k: copy.deepcopy(v) for k, v in binding.items() if k in _PORTABLE_BINDING_FIELDS}
        draft['frozen_dir'] = 'platform_private/frozen'
        draft['runtime']['resources'] = None
        draft['runtime']['resource_status'] = 'requires_human_setup'
        _validate_handoff_binding_fields(draft)
        write(private / 'binding.private.json', draft)
        write(private / 'research_acceptance.json', receipt)
        write(private / 'preflight.private.json', report)
        bound_package(folder, base / 'participant_public')
        sub = base / 'submission'; sub.mkdir()
        for name in submission_report['files']: shutil.copyfile(Path(submission) / name, sub / name)
        if submission_check(sub)['submission_hash'] != submission_report['submission_hash']:
            raise LabError('SUBMISSION_CHANGED_DURING_COPY')
        files = {p.relative_to(base).as_posix(): file_hash(p) for p in base.rglob('*') if p.is_file()}
        manifest = {'schema_version': 'finlab.private_handoff.v1', 'created_at': now(), 'audience': 'trusted_platform_operator_only',
                    'contains_real_market_data_and_time_mapping': True, 'contains_credentials': False,
                    'task_id': record['task_id'], 'version': record['version'], 'approved_spec_hash': record['spec_hash'],
                    'dataset_id': binding['dataset_id'], 'freeze_hash': binding['freeze_hash'],
                    'image_ids': binding['images'], 'submission_hash': submission_report['submission_hash'],
                    'origin_research_verified': True, 'destination_environment_verified': False, 'files': files}
        manifest['acceptance_method'] = record.get('approval_method', 'human')
        manifest['acceptance_novelty'] = record.get('novelty', record.get('action'))
        if parent is not None:
            manifest['parent_approval_hash'] = digest(parent)
        partial = base / 'delivery.zip'
        with zipfile.ZipFile(partial, 'x', zipfile.ZIP_DEFLATED) as archive:
            for rel in sorted(files): archive.write(base / rel, rel)
            archive.writestr('HANDOFF_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        inspect_handoff(partial)
        partial.rename(dest)
    from taskgen.registry import Registry
    Registry(ROOT / 'registry/tasks.sqlite').register_binding(record['candidate_id'], {
        'binding_id': file_hash(dest),
        'binding_hash': digest(binding), 'runtime_hash': digest(binding['runtime']), 'manifest_path': str(dest),
        'handoff_path': str(dest), 'handoff_hash': file_hash(dest), 'approved_spec_hash': record['spec_hash'],
        'dataset_id': binding['dataset_id'], 'freeze_hash': binding['freeze_hash'],
        'status': 'origin_research_verified', 'episode_path': str(folder)})
    return {'passed': True, 'path': str(dest), 'sha256': file_hash(dest), 'audience': 'trusted_platform_operator_only',
            'participant_directory': 'participant_public', 'destination_requires_setup_and_preflight': True}


def inspect_handoff(path):
    """Check all bytes before extraction; reject extra files and unsafe paths."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist(); names = [i.filename for i in infos]
        if len(names) != len(set(names)) or len(names) > 1000 or sum(i.file_size for i in infos) > 2_000_000_000:
            raise LabError('HANDOFF_INVALID_ARCHIVE')
        for info in infos:
            p = PurePosixPath(info.filename)
            if (info.is_dir() or p.is_absolute() or '..' in p.parts or '\\' in info.filename or ':' in info.filename
                    or any(part.endswith((' ', '.')) or part.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for part in p.parts)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise LabError('HANDOFF_UNSAFE_PATH')
        try:
            manifest = json.loads(archive.read('HANDOFF_MANIFEST.json'))
        except (KeyError, ValueError):
            raise LabError('HANDOFF_SCHEMA_REQUIRED') from None
        if (not isinstance(manifest, dict) or manifest.get('schema_version') != 'finlab.private_handoff.v1'
                or manifest.get('audience') != 'trusted_platform_operator_only'):
            raise LabError('HANDOFF_SCHEMA_REQUIRED')
        if (not isinstance(manifest.get('files'), dict)
                or not all(isinstance(n, str) and isinstance(h, str) and re.fullmatch(r'[0-9a-f]{64}', h)
                           for n, h in manifest['files'].items())
                or manifest.get('origin_research_verified') is not True
                or manifest.get('destination_environment_verified') is not False
                or not isinstance(manifest.get('version'), int) or isinstance(manifest.get('version'), bool)
                or manifest['version'] < 1
                or not all(isinstance(manifest.get(k), str) and manifest[k]
                           for k in ('task_id', 'dataset_id', 'approved_spec_hash', 'freeze_hash', 'submission_hash'))
                or not isinstance(manifest.get('image_ids'), dict)
                or set(manifest['image_ids']) != {'engine', 'worker'}):
            raise LabError('HANDOFF_SCHEMA_REQUIRED')
        expected = set(manifest['files']) | {'HANDOFF_MANIFEST.json'}
        if set(names) != expected: raise LabError('HANDOFF_ALLOWLIST_CHANGED')
        from .tasks import PUBLIC_FILES
        from .tasks import SUBMISSION_FILES
        allowed = {'platform_private/approval.json', 'platform_private/binding.private.json',
                   'platform_private/research_acceptance.json', 'platform_private/preflight.private.json',
                   'platform_private/frozen/manifest.private.json', 'platform_private/frozen/snapshot.sqlite'}
        allowed |= {'participant_public/' + n for n in PUBLIC_FILES}
        allowed |= {'submission/' + n for n in SUBMISSION_FILES}
        try:
            record = json.loads(archive.read('platform_private/approval.json'))
        except (KeyError, ValueError):
            raise LabError('HANDOFF_APPROVAL_REQUIRED') from None
        is_variant = isinstance(record, dict) and record.get('action') == 'variant'
        if not isinstance(record, dict):
            raise LabError('HANDOFF_ACCEPTANCE_REQUIRED')
        # Old human-approved handoffs did not have these descriptive keys.
        # Machine handoffs must carry them, preventing a public human claim.
        method = record.get('approval_method', 'human')
        if ((method == 'automated' and manifest.get('acceptance_method') != 'automated')
                or ('acceptance_method' in manifest and manifest['acceptance_method'] != method)
                or ('acceptance_novelty' in manifest and manifest['acceptance_novelty'] != record.get('novelty', record.get('action')))):
            raise LabError('HANDOFF_ACCEPTANCE_MISMATCH')
        if is_variant:
            parent_hash = manifest.get('parent_approval_hash')
            if not isinstance(parent_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', parent_hash):
                raise LabError('PARENT_HANDOFF_REQUIRED', '变体交接包必须携带真实母题批准及其哈希')
            allowed.add('platform_private/parent_approval.json')
        elif 'parent_approval_hash' in manifest:
            raise LabError('HANDOFF_PARENT_MISMATCH')
        for name in expected - {'HANDOFF_MANIFEST.json'}:
            if name not in allowed:
                raise LabError('HANDOFF_UNEXPECTED_FILE')
            if file_hash_bytes(archive, name) != manifest['files'][name]: raise LabError('HANDOFF_HASH_CHANGED')
        if not allowed <= set(names): raise LabError('HANDOFF_REQUIRED_FILE_MISSING')
        if is_variant:
            try:
                parent = json.loads(archive.read('platform_private/parent_approval.json'))
            except (KeyError, ValueError):
                raise LabError('PARENT_HANDOFF_REQUIRED') from None
            _validate_parent_approval(record, parent)
            if digest(parent) != manifest['parent_approval_hash']:
                raise LabError('HANDOFF_PARENT_MISMATCH')
    return manifest


def file_hash_bytes(archive, name):
    import hashlib
    h = hashlib.sha256()
    with archive.open(name) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def import_handoff(archive, output, trust_note):
    if not isinstance(trust_note, str) or not trust_note.strip(): raise LabError('EXPLICIT_APPROVAL_TRANSFER_TRUST_REQUIRED')
    manifest = inspect_handoff(archive)
    dest = Path(output).resolve()
    if dest.exists(): raise LabError('ALREADY_EXISTS')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='finlab-import-', dir=dest.parent) as tmp:
        base = Path(tmp) / 'unpacked'; base.mkdir()
        # Extract only already-inspected fixed member names, checking the copied
        # bytes. Reopening a replaced ZIP must not introduce a traversal or a
        # changed member between validation and extraction.
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if (len(infos) != len(manifest['files']) + 1
                    or set(z.namelist()) != set(manifest['files']) | {'HANDOFF_MANIFEST.json'}
                    or sum(i.file_size for i in infos) > 2_000_000_000):
                raise LabError('HANDOFF_ALLOWLIST_CHANGED')
            for rel, sha in manifest['files'].items():
                target = base / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(rel) as source, target.open('xb') as stream:
                    shutil.copyfileobj(source, stream)
                if file_hash(target) != sha:
                    raise LabError('HANDOFF_HASH_CHANGED')
            write(base / 'HANDOFF_MANIFEST.json', manifest)
        record = read(base / 'platform_private/approval.json')
        binding = read(base / 'platform_private/binding.private.json')
        _validate_handoff_binding_fields(binding)
        from .data import FrozenView
        frozen = FrozenView(base / 'platform_private/frozen')
        parent = None
        if record.get('action') == 'variant':
            parent = read(base / 'platform_private/parent_approval.json')
            _validate_parent_approval(record, parent)
            if digest(parent) != manifest.get('parent_approval_hash'):
                raise LabError('HANDOFF_PARENT_MISMATCH')
        if (record['task_id'] != manifest['task_id'] or record['version'] != manifest['version']
                or record['spec_hash'] != manifest['approved_spec_hash'] or digest(record['spec']) != record['spec_hash']
                or binding['candidate_id'] != record['candidate_id'] or binding['approved_spec_hash'] != record['spec_hash']
                or binding.get('frozen_dir') != 'platform_private/frozen'
                or frozen.manifest['dataset_id'] != manifest['dataset_id']
                or frozen.manifest['manifest_hash'] != manifest['freeze_hash'] or frozen.manifest.get('synthetic')):
            raise LabError('HANDOFF_BINDING_MISMATCH')
        from .tasks import validate as validate_spec
        allowed = {e['api_name']: set(e['fields']) for e in record['spec']['bridge_contract']['endpoints']}
        actual = frozen.manifest['apis']
        if (validate_spec(record['spec']) or set(actual) != set(allowed)
                or any(set(actual[name]['fields']) != fields for name, fields in allowed.items())
                or binding.get('runtime', {}).get('frequency') != record['spec']['runtime_profile']['frequency']
                or binding.get('contract_review_confirmed') is not True):
            raise LabError('HANDOFF_CONTRACT_MISMATCH')
        from .submission import check as submission_check
        sub = submission_check(base / 'submission')
        if not sub['passed'] or sub['submission_hash'] != manifest['submission_hash']: raise LabError('HANDOFF_SUBMISSION_MISMATCH')
        receipt = read(base / 'platform_private/research_acceptance.json')
        if (receipt.get('research') is not True or receipt.get('segment') != 0
                or not isinstance(receipt.get('hashes'), dict) or not _RESEARCH_REQUIRED_FILES <= set(receipt['hashes'])
                or not all(isinstance(n, str) and isinstance(h, str) and re.fullmatch(r'[0-9a-f]{64}', h)
                           for n, h in receipt['hashes'].items())
                or receipt.get('dataset_id') != manifest['dataset_id']
                or receipt.get('images') != manifest['image_ids'] or receipt.get('submission_hash') != sub['submission_hash']):
            raise LabError('HANDOFF_ACCEPTANCE_MISMATCH')
        preflight = read(base / 'platform_private/preflight.private.json')
        proof = preflight.get('approval', {})
        if (preflight.get('passed') is not True or preflight.get('submission_hash') != manifest['submission_hash']
                or preflight.get('image_ids') != manifest['image_ids']
                or any(proof.get(k) != record[k] for k in ('candidate_id', 'task_id', 'version', 'spec_hash'))):
            raise LabError('HANDOFF_PREFLIGHT_MISMATCH')
        binding['frozen_dir'] = str(dest / 'platform_private/frozen')
        binding['runtime']['resources'] = None
        binding['runtime']['resource_status'] = 'requires_human_setup'
        binding.pop('resource_setup', None)
        write(base / 'binding.imported.private.json', binding)
        write(base / 'IMPORT_STATUS.private.json', {
            'schema_version': 'finlab.handoff_import.v1', 'imported_at': now(),
            'status': 'imported_requires_local_preflight', 'source_research_verified': True,
            'destination_environment_verified': False, 'resources': 'requires_human_setup',
            'participant_public_status': 'origin_snapshot_do_not_dispatch',
            'next_action': '本人配置资源并完成本机研发后，使用新episode重新生成公开包；源public仅保留为交付证据。'})
        from taskgen.registry import Registry
        if parent is not None:
            # Validate both complete evidence records and their lineage before
            # touching the destination's authority. This disposable registry is
            # not the working approval database and never reaches the package.
            staging = Registry(Path(tmp) / 'lineage-validation.sqlite')
            staging.import_approved(parent, trust_note)
            staging.import_approved(record, trust_note)
        registry = Registry(ROOT / 'registry/tasks.sqlite')
        if parent is not None:
            registry.import_approved(parent, trust_note)
        registry.import_approved(record, trust_note)
        # Source manifest is retained as evidence. The relocated binding is separate.
        shutil.move(str(base), str(dest))
    return {'passed': True, 'path': str(dest), 'binding': str(dest / 'binding.imported.private.json'),
            'status': 'imported_requires_local_preflight', 'origin_research_verified': True,
            'destination_environment_verified': False, 'resources': 'requires_human_setup',
            'participant_public_status': 'origin_snapshot_do_not_dispatch'}


def batch(plan, *, execute=False):
    items = plan.get('items', [])
    if not isinstance(items, list) or not items: raise LabError('BATCH_ITEMS_REQUIRED')
    prepared, results = [], []
    for item in items:
        try:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), (str, Path)) and str(item[k]) for k in ('binding', 'episode', 'submission')):
                raise LabError('INVALID_BATCH_ITEM')
            binding = read(item['binding'])
            report = check_binding(binding, item['submission'])
        except (LabError, OSError, ValueError, KeyError, TypeError):
            binding = None
            report = {'passed': False, 'status': 'blocked', 'checks': {}, 'blockers': [
                {'check': 'batch_item', 'code': 'INVALID_OR_MISSING_ARTIFACT', 'message': '绑定或提交材料无效'}]}
        report['execution'] = {'status': 'not_started'}
        if isinstance(item, dict) and item.get('episode') and Path(item['episode']).exists():
            report['passed'] = False
            report['blockers'].append({'check': 'episode', 'code': 'ALREADY_EXISTS', 'message': '每个批跑条目须指定新的episode目录'})
        results.append(report); prepared.append((item, binding))
    paths = [str(Path(i['episode']).resolve()) for i, _ in prepared if isinstance(i, dict) and i.get('episode')]
    if len(set(paths)) != len(paths): raise LabError('DUPLICATE_BATCH_EPISODE')
    report = {'schema_version': 'finlab.batch_delivery.v1', 'checked_at': now(), 'passed': all(r['passed'] for r in results),
              'executed': False, 'mode': 'research_only', 'items': results}
    if not execute or not report['passed']: return report
    from . import runtime
    report['executed'] = True
    for result, (item, binding) in zip(results, prepared):
        try:
            runtime.create_episode(runtime.bind_operator_resources(binding), item['episode'])
            result['execution'] = runtime.run(item['episode'], item['submission'], research=True)
        except (LabError, OSError, ValueError) as error:
            result['execution'] = {'status': 'failed', 'code': error.code if isinstance(error, LabError) else 'RUN_FAILED'}
            report['passed'] = False
            break
    return report
