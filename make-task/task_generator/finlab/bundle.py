"""Allowlisted reproducible toolkits. No work directories, tokens or responses."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import zipfile
from .common import ROOT, LabError, file_hash, read, write, now
from .task_packages import RELEASE, TASK_PACKAGE_PATH, verify as verify_task_packages


def build(destination):
    package_check = verify_task_packages(ROOT / TASK_PACKAGE_PATH)
    dest = Path(destination).resolve(); dest.mkdir(parents=True, exist_ok=True)
    if dest == ROOT: raise LabError("INVALID_BUNDLE_DESTINATION")
    selections = {
        "finlab_v2_complete": ["finlab", "taskgen", "catalog", "schemas", "prompts", "config", "examples/v2", "examples/first_batch.json", "examples/generated", "examples/accepted", "deployment", "docs/v2", "tests", "pyproject.toml", "README.md", ".dockerignore", ".gitignore"],
        "task_production_v2": ["finlab", "taskgen", "catalog", "schemas", "prompts", "config", "examples/v2", "examples/first_batch.json", "examples/generated", "examples/accepted", "pyproject.toml", "docs/v2"],
        "tushare_bridge_v2": ["finlab", "catalog/tushare_pro.v2.json", "catalog/tushare_overrides.v2.json", "config/v2", "schemas", "pyproject.toml", "taskgen", "docs/v2"],
        "akquant_runtime_v2": ["finlab", "taskgen", "catalog", "schemas", "deployment", "pyproject.toml", ".dockerignore", "docs/v2"]}
    excluded = {"taskgen/guide.py", "taskgen/data_specs.py", "schemas/task_spec.schema.json",
                "schemas/data_schema_profiles.json", "catalog/build_data_profiles.py",
                "deployment/data_bridge.compose.example.yml", "tests/test_market_bridge.py"}
    result = []
    docs = ["docs/index.html", "docs/system_v2.html", "docs/tushare_api_offline.html", "docs/acceptance_v2.json", "docs/unit_tests_v2.json", "docs/release_smoke_v2.json", "docs/catalog_coverage_v2.json", "docs/browser_qa_v2.json", "docs/security_dns_v2.json", "docs/setup_preview_v2.png", "docs/disclosure_preview_v2.jpg", "docs/delivery_preview_v2.jpg", "docs/skills_report_v2.json", "docs/skills_changes_v2.md", "docs/skills_browser_qa_v2.json", "docs/live_generation_v2.json", "docs/disclosure_validation_v2.json", "docs/upstream_probe_v2.json", "docs/delivery_readiness_v2.json", "docs/delivery_validation_v2.json", "docs/automatic_acceptance_retry_v2.json", "docs/automatic_upstream_retry_v2.json", "docs/automatic_retry_data_v2.json", "docs/automatic_validation_v2.json", "docs/automatic_docker_smoke_v2.json", "docs/automatic_preview_v2.jpg"]
    for stem, targets in selections.items():
        files = {}
        for target in targets + docs + ["docs/portable_snapshot_tests_v2.json", "skills/finlab-strategy", TASK_PACKAGE_PATH,
                                        "docs/integrated_release_v2.json", "docs/task12_usability_v2.json", "docs/integrated_preview_v2.jpg"]:
            p = ROOT / target
            for f in (p.rglob("*") if p.is_dir() else [p]):
                if not f.is_file() or f.is_symlink() or "__pycache__" in f.parts or f.suffix == ".pyc": continue
                rel = f.relative_to(ROOT).as_posix()
                if rel in excluded or rel.startswith("config/data_bridge_templates/"): continue
                # Configuration files are shipped templates, not environment snapshots.
                if f.name.startswith(".env") or rel.startswith(("work_v2/", "runs/", "registry/")): raise LabError("BUNDLE_PRIVATE_FILE")
                files[rel] = file_hash(f)
        manifest = {"schema_version": "finlab.bundle.v2", "version": "2.0.0", "toolkit": stem,
                    "files": files, "private_data_included": False, "images_included": False,
                    "registry_bootstrap": "finlab task init restores verified machine records; new twelve specs remain unregistered",
                    "release": RELEASE, "task_packages": {"directory": TASK_PACKAGE_PATH, "count": package_check['count']}}
        filename = dest / (stem + ".zip")
        with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in sorted(files): z.write(ROOT / rel, "task_generator/" + rel)
            z.writestr("task_generator/TOOLKIT_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        result.append({"toolkit": stem, "path": str(filename), "sha256": file_hash(filename), "files": len(files)})
    skill = ROOT / 'skills/finlab-strategy'
    if not (skill / 'SKILL.md').is_file():
        raise LabError('STRATEGY_SKILL_MISSING')
    skill_files = {p.relative_to(skill).as_posix(): file_hash(p) for p in skill.rglob('*')
                   if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    skill_archive = dest / 'finlab_strategy_skill.zip'
    with zipfile.ZipFile(skill_archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(skill_files): z.write(skill / rel, 'finlab-strategy/' + rel)
        z.writestr('finlab-strategy/SKILL_MANIFEST.json', json.dumps({
            'schema_version': 'finlab.skill.bundle.v1', 'skill': 'finlab-strategy', 'files': skill_files,
            'private_data_included': False, 'standalone': True}, ensure_ascii=False, indent=2))
    manifest = {"version": "2.0.0", "release": RELEASE, "archives": result,
                "strategy_skill": {"file": skill_archive.name, "sha256": file_hash(skill_archive), "files": len(skill_files)},
                "image_ids": read(ROOT / "deployment/images.lock.json")["images"],
                "task_packages": {"directory": TASK_PACKAGE_PATH, "count": package_check['count'],
                                  "index_sha256": file_hash(ROOT/TASK_PACKAGE_PATH/'任务清单.json'),
                                  "individual_archives": 12, "expanded_packages": 12,
                                  "patch_application_required": False}}
    image_archive = dest / "finlab_images_v2.tar"
    if image_archive.is_file(): manifest["image_archive"] = {"file": image_archive.name, "sha256": file_hash(image_archive), "bytes": image_archive.stat().st_size}
    write(dest / "manifest.json", manifest)
    return {"archives": result, "strategy_skill": manifest['strategy_skill'], "manifest": str(dest / "manifest.json")}


def build_all_in_one(destination, source_dir=None):
    """One portable delivery, without nesting four duplicate source toolkits."""
    dest = Path(destination).resolve()
    source = Path(source_dir or ROOT / 'dist').resolve()
    if dest.exists():
        raise LabError('DELIVERY_ALREADY_EXISTS')
    if not dest.parent.is_dir():
        raise LabError('DELIVERY_PARENT_MISSING')
    partial = dest.with_suffix(dest.suffix + '.partial')
    if partial.exists():
        raise LabError('DELIVERY_PARTIAL_EXISTS')
    versions = read(source / 'manifest.json')
    complete = next((a for a in versions['archives'] if a['toolkit'] == 'finlab_v2_complete'), None)
    if not complete or file_hash(source / 'finlab_v2_complete.zip') != complete['sha256']:
        raise LabError('DELIVERY_SOURCE_HASH_CHANGED')
    image_info = versions.get('image_archive')
    if not image_info:
        raise LabError('DELIVERY_IMAGES_MISSING')
    image = source / image_info['file']
    if not image.is_file() or file_hash(image) != image_info['sha256']:
        raise LabError('DELIVERY_IMAGE_HASH_CHANGED')
    members = {}

    def add_bytes(z, name, body):
        if name in members:
            raise LabError('DELIVERY_DUPLICATE_MEMBER')
        z.writestr(name, body)
        members[name] = {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}

    with zipfile.ZipFile(source / 'finlab_v2_complete.zip') as source_archive:
        readme = source_archive.read('task_generator/docs/v2/START_HERE.md').decode('utf-8')

    with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        with zipfile.ZipFile(source / 'finlab_v2_complete.zip') as original:
            manifest = json.loads(original.read('task_generator/TOOLKIT_MANIFEST.json'))
            expected = {'task_generator/' + rel for rel in manifest['files']} | {'task_generator/TOOLKIT_MANIFEST.json'}
            if set(original.namelist()) != expected:
                raise LabError('DELIVERY_SOURCE_ALLOWLIST_CHANGED')
            for name in sorted(expected):
                body = original.read(name)
                rel = name.removeprefix('task_generator/')
                if rel != 'TOOLKIT_MANIFEST.json' and hashlib.sha256(body).hexdigest() != manifest['files'][rel]:
                    raise LabError('DELIVERY_MEMBER_HASH_CHANGED')
                add_bytes(z, name, body)
        report = ROOT / 'docs/distribution_audit_v2.json'
        if report.is_file(): add_bytes(z, 'task_generator/docs/distribution_audit_v2.json', report.read_bytes())
        add_bytes(z, '开始阅读.md', readme.encode('utf-8'))
        public_version = {'version': versions['version'], 'release': versions.get('release'),
                          'image_ids': versions['image_ids'],
                          'image_archive': {**image_info, 'file': 'images/' + image.name},
                          'source_toolkit_sha256': complete['sha256'],
                          'strategy_skill': {**versions.get('strategy_skill', {}), 'standalone_archive_included': False,
                                             'directory': 'task_generator/skills/finlab-strategy'},
                          'nested_source_toolkits': False}
        public_version['task_packages'] = {**versions['task_packages'],
                                           'directory':'task_generator/'+versions['task_packages']['directory']}
        public_version['fixes_merged'] = True
        public_version['patch_application_required'] = False
        add_bytes(z, 'VERSION_MANIFEST.json', (json.dumps(public_version, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        image_name = 'images/' + image.name
        z.write(image, image_name)
        members[image_name] = {'sha256': image_info['sha256'], 'bytes': image.stat().st_size}
        z.writestr('DELIVERY_MANIFEST.json', json.dumps({'schema_version': 'finlab.delivery.v2',
                      'version': versions['version'], 'release': versions.get('release'), 'created_at': now(),
                      'manifest_self_excluded': True, 'private_data_included': False, 'files': members}, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(partial) as z:
        if len(z.namelist()) != len(members) + 1 or set(z.namelist()) != set(members) | {'DELIVERY_MANIFEST.json'}:
            raise LabError('DELIVERY_MEMBER_LIST_CHANGED')
        for name, record in members.items():
            digest, length = hashlib.sha256(), 0
            with z.open(name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk); length += len(chunk)
            if digest.hexdigest() != record['sha256'] or length != record['bytes']:
                raise LabError('DELIVERY_VERIFY_FAILED')
    partial.rename(dest)
    return {'path': str(dest), 'bytes': dest.stat().st_size, 'files': len(members) + 1,
            'sha256': file_hash(dest), 'integrity_verified': True, 'nested_source_toolkits': False}
