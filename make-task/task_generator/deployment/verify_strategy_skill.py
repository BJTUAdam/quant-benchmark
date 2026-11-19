"""Verify the portable strategy skill without network or submitted-code execution."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finlab.common import now, read, write
from finlab.submission import check as platform_check
from finlab.temporal import DEFAULT_YEAR_OFFSET

SKILL = ROOT / 'skills/finlab-strategy'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_links(document):
    """Ignore fenced source examples; only authored document links are followed."""
    text = re.sub(r'```[^\n]*\n.*?```', '', document, flags=re.S)
    return re.findall(r'(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+"[^"\n]*")?\)', text)


def verify():
    errors, hashes = [], 0
    snapshot = read(SKILL / 'references/tushare/snapshot.json')
    catalog = read(ROOT / 'catalog/tushare_pro.v2.json')
    documented = {d['api_name'] for d in snapshot['documents'] if d.get('api_name')}
    if documented != set(catalog['endpoints']):
        errors.append('Pro API documentation does not match the versioned catalog')
    if snapshot['failed']:
        errors.append('official documentation has failed pages')
    for item in snapshot['documents']:
        target = SKILL / 'references/tushare' / item['path']
        body = target.read_bytes()
        if hashlib.sha256(body).hexdigest() != item['sha256']:
            errors.append('official document hash changed: ' + item['path'])
        hashes += 1
        if item.get('api_name'):
            if item.get('content_status') != 'full_article':
                errors.append('API article incomplete: ' + item['api_name'])
            text = body.decode('utf-8')
            for field in item['official_params'] + item['official_fields']:
                if not re.search(r'(?<!\w)' + re.escape(field) + r'(?!\w)', text):
                    errors.append(item['api_name'] + ': documented parameter/field absent: ' + field)
    ak = read(SKILL / 'references/akquant/snapshot.json')
    for item in [*ak['documents'], ak['license']]:
        body = (SKILL / 'references/akquant' / item['path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != item['sha256']:
            errors.append('AKQuant reference hash changed: ' + item['path'])
        hashes += 1

    checked_links, files = 0, 0
    credential = re.compile(rb'sk-[A-Za-z0-9]{24,}')
    for path in SKILL.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        files += 1
        if path.is_symlink(): errors.append('symlink in portable skill')
        if credential.search(path.read_bytes()): errors.append('credential-like content in skill: ' + path.name)
        if path.suffix == '.py': ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        if path.suffix != '.md': continue
        for link in local_links(path.read_text(encoding='utf-8')):
            if urlsplit(link).scheme or link.startswith(('#', '//')):
                continue
            relative = unquote(link.split('#', 1)[0].split('?', 1)[0])
            target = (path.parent / relative).resolve()
            checked_links += 1
            if not target.is_relative_to(SKILL) or not target.exists():
                errors.append('broken/nonportable link: ' + str(path.relative_to(SKILL)) + ' -> ' + link)

    preflight = load_module('finlab_skill_preflight', SKILL / 'scripts/preflight.py')
    maker = load_module('finlab_skill_maker', SKILL / 'scripts/make_submission.py')
    lookup = load_module('finlab_skill_lookup', SKILL / 'scripts/api_docs.py')
    with tempfile.TemporaryDirectory(prefix='finlab-skill-verify-') as tmp:
        path = Path(tmp) / 'submission'
        maker.create(path)
        template = preflight.check(path)
        formal = platform_check(path)
        if not template['passed'] or not formal['passed']:
            errors.append('empty-position template failed actual preflight/platform static check')
    if lookup.document(api='daily')['api_name'] != 'daily': errors.append('exact API lookup failed')
    if not lookup.search('trade_date')['results']: errors.append('offline full-text lookup failed')
    # The entrypoint and handwritten rules must not expose the private mapping.
    authored = [SKILL / 'SKILL.md', *list((SKILL / 'references').glob('*.md'))]
    for path in authored:
        body = path.read_text(encoding='utf-8')
        if re.search(r'(?:偏移|平移|offset)\s*(?:为|=|：|:)?\s*[+＋−-]?' + str(abs(DEFAULT_YEAR_OFFSET)), body, re.I):
            errors.append('private mapping present in participant skill: ' + path.name)

    acceptance = read(ROOT / 'docs/acceptance_v2.json') if (ROOT / 'docs/acceptance_v2.json').exists() else {}
    report = {'schema_version': 'finlab.skill.verification.v1', 'checked_at': now(),
              'passed': not errors, 'offline': True, 'skill_files': files,
              'tushare_pro_full_articles': len(documented), 'tushare_page_records': snapshot['document_count'],
              'navigation_or_empty_pages': len(snapshot['navigation_or_empty_pages']),
              'akquant_official_documents': ak['official_documents'], 'akquant_version': ak['engine_version'],
              'source_hashes_verified': hashes, 'local_links_checked': checked_links,
              'template_preflight': template['passed'], 'template_platform_check': formal['passed'],
              'query_scripts_checked': True, 'real_tushare_account_tested': False,
              'new_docker_acceptance_run': acceptance.get('status') == 'passed' and acceptance.get('image_ids') == read(ROOT / 'deployment/images.lock.json')['images'],
              'runtime_changed': True,
              'credential_scan': 'no matches' if not any('credential' in e for e in errors) else 'failed',
              'errors': errors}
    write(ROOT / 'docs/skills_report_v2.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    raise SystemExit(0 if verify()['passed'] else 2)
