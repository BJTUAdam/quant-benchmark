from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .dedup import fingerprint
from .io_utils import digest, now


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks(
                    task_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                    spec_json TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
                    origin_id TEXT UNIQUE, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(
                    task_id TEXT NOT NULL, version INTEGER NOT NULL, spec_json TEXT NOT NULL,
                    spec_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task_id,version));
                CREATE TABLE IF NOT EXISTS variants(
                    variant_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES tasks(task_id),
                    spec_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, candidate_id TEXT, action TEXT NOT NULL,
                    note TEXT NOT NULL, spec_hash TEXT, review_hash TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, task_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals(
                    candidate_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, version INTEGER NOT NULL,
                    action TEXT NOT NULL, spec_hash TEXT NOT NULL, review_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approval_evidence(
                    candidate_id TEXT PRIMARY KEY REFERENCES approvals(candidate_id),
                    review_json TEXT NOT NULL, public_json TEXT, public_hash TEXT,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approved_bindings(
                    binding_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES approvals(candidate_id),
                    record_json TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trusted_approval_imports(
                    candidate_id TEXT PRIMARY KEY REFERENCES approvals(candidate_id),
                    source_json TEXT NOT NULL, source_hash TEXT NOT NULL,
                    trust_note TEXT NOT NULL, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS machine_acceptances(
                    candidate_id TEXT PRIMARY KEY, spec_hash TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL, record_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS machine_bindings(
                    binding_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES machine_acceptances(candidate_id),
                    record_json TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def records(self):
        with self.connect() as db:
            rows = [{"task_id": x["task_id"], "status": x["status"], "version": x["version"],
                     "spec": json.loads(x["spec_json"])} for x in db.execute("SELECT * FROM tasks ORDER BY task_id")]
            for saved in db.execute('SELECT record_json,record_hash FROM machine_acceptances ORDER BY candidate_id'):
                record = json.loads(saved[0])
                if digest(record) != saved[1]: raise RegistryError('ACCEPTANCE_RECORD_HASH_MISMATCH')
                rows.append({'task_id': record['task_id'], 'status': 'spec_accepted', 'version': record['version'],
                             'spec': record['spec'], 'approval_method': 'automated', 'novelty': record['novelty']})
            return rows

    def import_history(self, specs):
        added = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for spec in specs:
                task_id = spec.get("id", spec.get("task_id"))
                fp = fingerprint(spec)
                existing = db.execute("SELECT task_id FROM tasks WHERE fingerprint=?", (fp,)).fetchone()
                if existing:
                    db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?)", (task_id, existing[0]))
                    continue
                payload = json.dumps(spec, ensure_ascii=False, sort_keys=True)
                cur = db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?)",
                                 (task_id, fp, payload, spec.get("status", "historical"), 1, task_id, now()))
                if cur.rowcount:
                    db.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (task_id, 1, payload, digest(spec), now()))
                    added += 1
        return added

    def event(self, candidate_id, action, note, spec, review=None):
        with self.connect() as db:
            db.execute("INSERT INTO events(candidate_id,action,note,spec_hash,review_hash,created_at) VALUES(?,?,?,?,?,?)",
                       (candidate_id, action, note, digest(spec), digest(review) if review is not None else None, now()))

    def approve(self, candidate_id, spec, review, note, as_variant=None, update_task=None, *, public_files=None):
        if not isinstance(spec, dict) or spec.get("schema_version") != "finance_task_spec.v2":
            raise RegistryError("仅允许原生v2规格入库；旧任务仅用于历史对照")
        if not note.strip():
            raise RegistryError("人工审核说明不能为空")
        if public_files is not None:
            from finlab.tasks import PUBLIC_FILES
            if (not isinstance(public_files, dict) or set(public_files) != PUBLIC_FILES or
                    any(not isinstance(x, str) for x in public_files.values())):
                raise RegistryError("APPROVAL_PUBLIC_ALLOWLIST_MISMATCH")
            if digest({k: public_files[k] for k in sorted(public_files) if k != "task_input.draft.json"}) != review.get("content_hash"):
                raise RegistryError("APPROVAL_PUBLIC_NOT_REVIEWED")
            public_files = dict(public_files)
        payload = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        fp = fingerprint(spec)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute('SELECT 1 FROM machine_acceptances WHERE candidate_id=?',(candidate_id,)).fetchone():
                raise RegistryError('CANDIDATE_ALREADY_AUTOMATICALLY_ACCEPTED')
            evidence = db.execute("SELECT 1 FROM events WHERE candidate_id=? AND action='model_reviewed' AND spec_hash=? AND review_hash=?",
                                  (candidate_id, digest(spec), digest(review))).fetchone()
            if not evidence:
                raise RegistryError("缺少匹配的模型初筛调用记录，不能仅修改review.json完成批准")
            if db.execute("SELECT 1 FROM events WHERE candidate_id=? AND action IN ('approved','variant','updated')", (candidate_id,)).fetchone():
                raise RegistryError("该候选已经提交；重复批准被拒绝")
            if as_variant:
                parent = db.execute("SELECT * FROM tasks WHERE task_id=?", (as_variant,)).fetchone()
                if not parent:
                    raise RegistryError("变体的母题不存在")
                if parent["status"] != "spec_approved" or not db.execute(
                        "SELECT 1 FROM approvals WHERE task_id=? AND version=? AND action IN ('approved','updated')",
                        (as_variant, parent["version"])).fetchone():
                    raise RegistryError("变体的母题缺少真实人工批准版本；历史对照或候选不能直接复用为母题")
                if parent["fingerprint"] != fp:
                    raise RegistryError("变体必须保持母题核心结构；不同核心决策应单独审核")
                parent_spec = json.loads(parent["spec_json"])
                variant_fields = ("parameter_space", "data_requirements", "environment_id")
                if all(spec.get(k) == parent_spec.get(k) for k in variant_fields):
                    raise RegistryError("仅题名或措辞变化不构成可登记的参数/数据变体")
                db.execute("INSERT INTO variants VALUES(?,?,?,?,?)", (candidate_id, as_variant, payload, note, now()))
                result = {"task_id": as_variant, "variant_id": candidate_id, "version": 1, "action": "variant"}
            elif update_task:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (update_task,)).fetchone()
                if not row or row["status"] != "spec_approved" or row["fingerprint"] != fp:
                    raise RegistryError("仅可更新同一结构且已经人工批准的任务；历史草稿不自动转正")
                if json.loads(row["spec_json"]) == spec:
                    raise RegistryError("规格没有变化，无需新增版本")
                version = row["version"] + 1
                db.execute("UPDATE tasks SET spec_json=?,version=? WHERE task_id=?", (payload, version, update_task))
                db.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (update_task, version, payload, digest(spec), now()))
                result = {"task_id": update_task, "version": version, "action": "updated"}
            else:
                row = db.execute("SELECT task_id FROM tasks WHERE fingerprint=?", (fp,)).fetchone()
                if row:
                    raise RegistryError("同一规范结构已存在：" + row[0] + "；请登记变体或复用原任务")
                db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)", (candidate_id, fp, payload, "spec_approved", 1, candidate_id, now()))
                db.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (candidate_id, 1, payload, digest(spec), now()))
                result = {"task_id": candidate_id, "version": 1, "action": "approved"}
            db.execute("INSERT INTO events(candidate_id,action,note,spec_hash,review_hash,created_at) VALUES(?,?,?,?,?,?)",
                       (candidate_id, result["action"], note, digest(spec), digest(review), now()))
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?)", (candidate_id, result["task_id"], result["version"],
                       result["action"], digest(spec), digest(review), now()))
            if public_files is not None:
                from finlab.tasks import SUBMISSION_FILES
                # Only approval-state bookkeeping is outside the reviewed-content
                # hash. Construct it here using the actual transaction result.
                entry = {"schema_version": "finance_task_input.draft.v2", "task_id": result["task_id"],
                         "candidate_id": candidate_id, "task_version": result["version"],
                         "initial_message": {"role": "user", "content_file": "TASK.md"},
                         "expected_outputs": sorted(SUBMISSION_FILES), "dataset_id": None, "formal_intervals": None,
                         "spec_status": "variant" if as_variant else "spec_approved", "data_status": "not_bound",
                         "runtime_status": "not_connected", "binding_contract": "bridge_contract.json",
                         "runtime_profile": "runtime_profile.json"}
                public_files["task_input.draft.json"] = json.dumps(entry, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            db.execute("INSERT INTO approval_evidence VALUES(?,?,?,?,?)",
                       (candidate_id, json.dumps(review, ensure_ascii=False, sort_keys=True),
                        json.dumps(public_files, ensure_ascii=False, sort_keys=True) if public_files is not None else None,
                        digest(public_files) if public_files is not None else None, now()))
        return result

    def approval(self, candidate_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row: return dict(row)
            automatic = db.execute('SELECT record_json,record_hash FROM machine_acceptances WHERE candidate_id=?', (candidate_id,)).fetchone()
            if automatic:
                record = json.loads(automatic[0])
                if digest(record) != automatic[1]: raise RegistryError('ACCEPTANCE_RECORD_HASH_MISMATCH')
                return record['approval']
            return None

    def resolve_approved(self, reference, version=None):
        """Resolve an actual approval against its immutable spec, never current draft files.

        task IDs and aliases select mother-task versions; a variant's candidate ID
        selects only that variant. Historical metadata is not an approval.
        """
        if not isinstance(reference, str) or not reference.strip():
            raise RegistryError("APPROVED_REFERENCE_REQUIRED")
        if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
            raise RegistryError("APPROVED_VERSION_INVALID")
        with self.connect() as db:
            automatic = db.execute('SELECT record_json,record_hash FROM machine_acceptances WHERE candidate_id=?', (reference,)).fetchone()
            if automatic:
                if version not in {None, 1}: raise RegistryError('APPROVED_VERSION_NOT_FOUND: ' + reference)
                record = json.loads(automatic[0])
                if digest(record) != automatic[1]: raise RegistryError('ACCEPTANCE_RECORD_HASH_MISMATCH')
                self._validate_automatic_record(record)
                event = db.execute("SELECT 1 FROM events WHERE candidate_id=? AND action IN ('auto_accepted','trusted_auto_import') AND spec_hash=? AND review_hash=?",
                                   (reference, record['spec_hash'], record['review_hash'])).fetchone()
                if not event: raise RegistryError('ACCEPTANCE_EVENT_MISSING')
                return record
        with self.connect() as db:
            alias = db.execute("SELECT task_id FROM aliases WHERE alias=?", (reference,)).fetchone()
            canonical = alias[0] if alias else reference
            # A mother ID also identifies its first candidate; prefer the version selector.
            mother = db.execute("SELECT task_id FROM tasks WHERE task_id=?", (canonical,)).fetchone()
            if mother:
                clauses = "task_id=? AND action IN ('approved','updated')"
                params = [canonical]
                if version is not None:
                    clauses += " AND version=?"; params.append(version)
                row = db.execute("SELECT * FROM approvals WHERE " + clauses + " ORDER BY version DESC LIMIT 1", params).fetchone()
            else:
                row = db.execute("SELECT * FROM approvals WHERE candidate_id=?", (canonical,)).fetchone()
                if row and version is not None and row["version"] != version:
                    row = None
            if not row:
                raise RegistryError("APPROVED_VERSION_NOT_FOUND: " + reference)
            approval = dict(row)
            if approval["action"] == "variant":
                saved = db.execute("SELECT spec_json FROM variants WHERE variant_id=? AND parent_id=?",
                                   (approval["candidate_id"], approval["task_id"])).fetchone()
            else:
                saved = db.execute("SELECT spec_json FROM versions WHERE task_id=? AND version=?",
                                   (approval["task_id"], approval["version"])).fetchone()
            if not saved:
                raise RegistryError("APPROVAL_SPEC_MISSING")
            spec = json.loads(saved[0])
            if digest(spec) != approval["spec_hash"] or spec.get("schema_version") != "finance_task_spec.v2":
                raise RegistryError("APPROVAL_INTEGRITY_ERROR")
            event = db.execute("SELECT * FROM events WHERE candidate_id=? AND action=? AND spec_hash=? AND review_hash=?",
                               (approval["candidate_id"], approval["action"], approval["spec_hash"], approval["review_hash"])).fetchone()
            model = db.execute("SELECT * FROM events WHERE candidate_id=? AND action='model_reviewed' AND spec_hash=? AND review_hash=?",
                               (approval["candidate_id"], approval["spec_hash"], approval["review_hash"])).fetchone()
            provenance = {"approval_event": dict(event) if event else None, "model_event": dict(model) if model else None,
                          "origin": "local_registry", "trusted_import": False}
            if not event or not model:
                trusted = db.execute("SELECT * FROM trusted_approval_imports WHERE candidate_id=?", (approval["candidate_id"],)).fetchone()
                if not trusted:
                    raise RegistryError("APPROVAL_EVIDENCE_MISSING")
                original = json.loads(trusted["source_json"])
                if digest(original) != trusted["source_hash"] or original.get("approval") != approval:
                    raise RegistryError("TRUSTED_IMPORT_SOURCE_CHANGED")
                transfer_event = db.execute("SELECT 1 FROM events WHERE candidate_id=? AND action='trusted_import' AND spec_hash=? AND review_hash=?",
                                            (approval["candidate_id"], approval["spec_hash"], approval["review_hash"])).fetchone()
                if not transfer_event:
                    raise RegistryError("TRUSTED_IMPORT_EVENT_MISSING")
                provenance = {**original["provenance"], "origin": "operator_trusted_import",
                              "trusted_import": True, "imported_at": trusted["imported_at"], "source_hash": trusted["source_hash"]}
            evidence = db.execute("SELECT * FROM approval_evidence WHERE candidate_id=?", (approval["candidate_id"],)).fetchone()
            artifacts = {"status": "legacy_missing", "review": None, "public_files": None, "public_hash": None}
            if evidence:
                review = json.loads(evidence["review_json"])
                if digest(review) != approval["review_hash"]:
                    raise RegistryError("APPROVAL_REVIEW_HASH_MISMATCH")
                public = json.loads(evidence["public_json"]) if evidence["public_json"] else None
                if public is not None and digest(public) != evidence["public_hash"]:
                    raise RegistryError("APPROVAL_PUBLIC_HASH_MISMATCH")
                artifacts = {"status": "captured" if public is not None else "spec_and_review_only",
                             "review": review, "public_files": public, "public_hash": evidence["public_hash"]}
        return {"schema_version": "taskgen.approved.v1", **approval, "spec": spec,
                "approval_method": "human", "novelty": "variant" if approval['action'] == 'variant' else 'distinct',
                "approval": approval, "artifacts": artifacts, "provenance": provenance}

    @staticmethod
    def _validate_automatic_record(record):
        from finlab.tasks import PUBLIC_FILES
        try:
            approval, spec, artifacts, provenance = (record[k] for k in ('approval', 'spec', 'artifacts', 'provenance'))
            review, files = artifacts['review'], artifacts['public_files']
            if (record.get('schema_version') != 'taskgen.approved.v1' or record.get('approval_method') != 'automated'
                    or approval['action'] != 'auto_accepted' or approval['task_id'] != approval['candidate_id']
                    or approval['version'] != 1 or record.get('novelty') not in {'distinct', 'variant', 'duplicate'}
                    or any(record.get(k) != approval[k] for k in ('candidate_id','task_id','version','action','spec_hash','review_hash','created_at'))
                    or spec.get('schema_version') != 'finance_task_spec.v2' or digest(spec) != approval['spec_hash']
                    or digest(review) != approval['review_hash'] or review.get('spec_hash') != approval['spec_hash']
                    or review.get('mode') != 'model_api' or review.get('public_information_safe') is not True
                    or review.get('long_horizon_valid') is not True or review.get('issues') or review.get('validation_errors')
                    or review.get('classification') not in {'distinct','variant','duplicate'}
                    or review['classification'] != record['novelty'] and not (review['classification']=='distinct' and record['novelty']=='duplicate')
                    or artifacts.get('status') != 'captured' or set(files) != PUBLIC_FILES
                    or any(not isinstance(x, str) for x in files.values()) or digest(files) != artifacts['public_hash']
                    or digest({k: files[k] for k in sorted(files) if k != 'task_input.draft.json'}) != review.get('content_hash')
                    or record.get('counts_new_question') is not (record['novelty'] == 'distinct')):
                raise ValueError()
            if provenance.get('human_approved') is not False: raise ValueError()
            for key, action in (('approval_event','auto_accepted'), ('model_event','model_reviewed')):
                event = provenance[key]
                if event['candidate_id'] != approval['candidate_id'] or event['action'] != action or any(
                        event[k] != approval[k] for k in ('spec_hash','review_hash')): raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError):
            raise RegistryError('AUTOMATIC_ACCEPTANCE_HASH_OR_EVIDENCE_INVALID') from None

    def accept(self, candidate_id, spec, review, public_files, *, novelty, related_task_id=None,
               admission_content_source='current_reviewed_render'):
        """Register a runnable specification after machine checks, never a human verdict.

        Duplicate/variant research can be runnable without a fictional approved
        historical mother. It is labelled non-new and retained separately from
        the independent question identity table.
        """
        from finlab.tasks import PUBLIC_FILES, SUBMISSION_FILES
        if (not isinstance(candidate_id, str) or not candidate_id or novelty not in {'distinct','variant','duplicate'}
                or not isinstance(public_files, dict) or set(public_files) != PUBLIC_FILES):
            raise RegistryError('AUTOMATIC_ACCEPTANCE_INPUT_INVALID')
        spec_hash, review_hash = digest(spec), digest(review)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT record_json,record_hash FROM machine_acceptances WHERE spec_hash=? OR candidate_id=?', (spec_hash,candidate_id)).fetchall()
            for item in prior:
                saved = json.loads(item[0])
                if digest(saved) != item[1]: raise RegistryError('ACCEPTANCE_RECORD_HASH_MISMATCH')
                self._validate_automatic_record(saved)
                if saved['spec_hash'] != spec_hash: raise RegistryError('ACCEPTANCE_CANDIDATE_CHANGED')
                return {**saved, 'reused': True}
            if db.execute('SELECT 1 FROM approvals WHERE candidate_id=?', (candidate_id,)).fetchone():
                raise RegistryError('CANDIDATE_ALREADY_HUMAN_APPROVED')
            if novelty == 'distinct' and (db.execute('SELECT 1 FROM tasks WHERE fingerprint=?',(fingerprint(spec),)).fetchone()
                    or db.execute('SELECT 1 FROM machine_acceptances WHERE fingerprint=?',(fingerprint(spec),)).fetchone()):
                novelty = 'duplicate'  # transactionally protect independent-question counts
            event = db.execute("SELECT * FROM events WHERE candidate_id=? AND action='model_reviewed' AND spec_hash=? AND review_hash=? ORDER BY id DESC LIMIT 1",
                               (candidate_id,spec_hash,review_hash)).fetchone()
            if not event: raise RegistryError('MATCHING_MODEL_REVIEW_REQUIRED')
            stamp = now()
            approval = {'candidate_id':candidate_id,'task_id':candidate_id,'version':1,'action':'auto_accepted',
                        'spec_hash':spec_hash,'review_hash':review_hash,'created_at':stamp}
            files = dict(public_files)
            entry = {'schema_version':'finance_task_input.draft.v2','task_id':candidate_id,'candidate_id':candidate_id,
                     'task_version':1,'initial_message':{'role':'user','content_file':'TASK.md'},
                     'expected_outputs':sorted(SUBMISSION_FILES),'dataset_id':None,'formal_intervals':None,
                     'spec_status':'spec_accepted','approval_method':'automated','novelty':novelty,
                     'counts_new_question':novelty == 'distinct','data_status':'not_bound','runtime_status':'not_connected',
                     'binding_contract':'bridge_contract.json','runtime_profile':'runtime_profile.json'}
            files['task_input.draft.json'] = json.dumps(entry,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
            accepted_event = {**approval,'action':'auto_accepted','note':'Program checks + current model review; no human approval'}
            record = {'schema_version':'taskgen.approved.v1',**approval,'approval':approval,'spec':spec,
                      'approval_method':'automated','novelty':novelty,'counts_new_question':novelty == 'distinct',
                      'related_task_id':related_task_id,'related_reference_status':'comparison_reference_only_not_inherited_approval',
                      'admission_content_source':admission_content_source,
                      'artifacts':{'status':'captured','review':review,'public_files':files,'public_hash':digest(files)},
                      'provenance':{'approval_event':accepted_event,'model_event':dict(event),
                                    'origin':'local_automated_acceptance','trusted_import':False,'human_approved':False}}
            self._validate_automatic_record(record)
            db.execute('INSERT INTO machine_acceptances VALUES(?,?,?,?,?,?)',
                       (candidate_id,spec_hash,fingerprint(spec),json.dumps(record,ensure_ascii=False,sort_keys=True),digest(record),stamp))
            db.execute('INSERT INTO events(candidate_id,action,note,spec_hash,review_hash,created_at) VALUES(?,?,?,?,?,?)',
                       (candidate_id,'auto_accepted',accepted_event['note'],spec_hash,review_hash,stamp))
        return record

    def _import_automatic(self, record, note):
        self._validate_automatic_record(record)
        cid = record['candidate_id']
        imported = {**record, 'provenance':{**record['provenance'],'origin':'operator_trusted_machine_transfer',
                                          'trusted_import':True,'imported_at':now(),'source_hash':digest(record)}}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT record_json,record_hash FROM machine_acceptances WHERE candidate_id=? OR spec_hash=?', (cid, record['spec_hash'])).fetchall()
            for item in prior:
                saved = json.loads(item[0])
                if digest(saved) != item[1]: raise RegistryError('ACCEPTANCE_RECORD_HASH_MISMATCH')
                if (saved['candidate_id'] != cid or saved['spec_hash'] != record['spec_hash']
                        or saved['review_hash'] != record['review_hash'] or saved['artifacts'] != record['artifacts']):
                    raise RegistryError('TRUSTED_IMPORT_APPROVAL_CONFLICT')
                return {'candidate_id':cid,'status':'already_registered','new_approval_created':False,'approval_method':'automated'}
            if db.execute('SELECT 1 FROM approvals WHERE candidate_id=?',(cid,)).fetchone():
                raise RegistryError('TRUSTED_IMPORT_APPROVAL_CONFLICT')
            db.execute('INSERT INTO machine_acceptances VALUES(?,?,?,?,?,?)',(cid,record['spec_hash'],fingerprint(record['spec']),
                       json.dumps(imported,ensure_ascii=False,sort_keys=True),digest(imported),now()))
            db.execute('INSERT INTO events(candidate_id,action,note,spec_hash,review_hash,created_at) VALUES(?,?,?,?,?,?)',
                       (cid,'trusted_auto_import',note,record['spec_hash'],record['review_hash'],now()))
        return {'candidate_id':cid,'task_id':cid,'version':1,'status':'trusted_automated_acceptance_import',
                'approval_method':'automated','new_approval_created':False,'human_approved':False}

    def resolve_candidate(self, candidate_id):
        """Exact original approval reference for a binding, including old mother v1."""
        approval = self.approval(candidate_id)
        if not approval:
            raise RegistryError("APPROVED_VERSION_NOT_FOUND: " + str(candidate_id))
        reference = candidate_id if approval["action"] == "variant" else approval["task_id"]
        record = self.resolve_approved(reference, approval["version"])
        if record["candidate_id"] != candidate_id:
            raise RegistryError("APPROVAL_CANDIDATE_MISMATCH")
        return record

    def import_approved(self, record, note):
        """Explicit operator-trusted transfer, never a new model or human verdict.

        Hashes detect changed members, not the identity of an external approver.
        The caller must authenticate the platform-private source out of band.
        """
        from finlab.tasks import PUBLIC_FILES
        if not isinstance(note, str) or not note.strip():
            raise RegistryError("TRUSTED_IMPORT_NOTE_REQUIRED")
        if not isinstance(record, dict) or record.get("schema_version") != "taskgen.approved.v1":
            raise RegistryError("TRUSTED_IMPORT_RECORD_INVALID")
        if record.get('approval_method') == 'automated':
            return self._import_automatic(record, note)
        try:
            approval, spec, artifacts, provenance = (record[k] for k in ("approval", "spec", "artifacts", "provenance"))
            if set(approval) != {"candidate_id", "task_id", "version", "action", "spec_hash", "review_hash", "created_at"}:
                raise ValueError()
            cid, task_id, version, action = (approval[k] for k in ("candidate_id", "task_id", "version", "action"))
            if (not all(isinstance(x, str) and x for x in (cid, task_id)) or
                    not isinstance(version, int) or isinstance(version, bool) or version < 1 or
                    action not in {"approved", "updated", "variant"} or
                    spec.get("schema_version") != "finance_task_spec.v2" or digest(spec) != approval["spec_hash"]):
                raise ValueError()
            if any(record.get(k) != approval[k] for k in ("candidate_id", "task_id", "version", "action", "spec_hash", "review_hash", "created_at")):
                raise ValueError()
            review, files = artifacts["review"], artifacts["public_files"]
            if (artifacts.get("status") != "captured" or digest(review) != approval["review_hash"] or
                    review.get("mode") != "model_api" or review.get("public_information_safe") is not True or
                    review.get("long_horizon_valid") is not True or review.get("issues") or review.get("validation_errors") or
                    review.get("classification") not in ({"distinct"} if action == "approved" else
                        {"variant", "distinct"} if action == "variant" else {"duplicate", "variant", "distinct"}) or
                    not isinstance(files, dict) or set(files) != PUBLIC_FILES or
                    any(not isinstance(x, str) for x in files.values()) or digest(files) != artifacts["public_hash"] or
                    digest({k: files[k] for k in sorted(files) if k != "task_input.draft.json"}) != review.get("content_hash")):
                raise ValueError()
            for key, expected in (("approval_event", action), ("model_event", "model_reviewed")):
                event = provenance[key]
                if event["candidate_id"] != cid or event["action"] != expected or any(
                        event[k] != approval[k] for k in ("spec_hash", "review_hash")):
                    raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise RegistryError("TRUSTED_IMPORT_HASH_OR_EVIDENCE_INVALID") from None
        payload = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        fp = fingerprint(spec)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM approvals WHERE candidate_id=?", (cid,)).fetchone()
            if prior:
                if dict(prior) != approval:
                    raise RegistryError("TRUSTED_IMPORT_APPROVAL_CONFLICT")
                evidence = db.execute("SELECT review_json,public_hash FROM approval_evidence WHERE candidate_id=?", (cid,)).fetchone()
                if not evidence or digest(json.loads(evidence[0])) != approval["review_hash"] or evidence[1] != artifacts["public_hash"]:
                    raise RegistryError("TRUSTED_IMPORT_EVIDENCE_CONFLICT")
                return {"candidate_id": cid, "status": "already_registered", "new_approval_created": False}
            mother = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if action == "variant":
                parent_approval = db.execute("SELECT 1 FROM approvals WHERE task_id=? AND action IN ('approved','updated')", (task_id,)).fetchone()
                if not mother or mother["status"] != "spec_approved" or mother["fingerprint"] != fp or not parent_approval:
                    raise RegistryError("TRUSTED_IMPORT_VARIANT_PARENT_REQUIRED")
                db.execute("INSERT INTO variants VALUES(?,?,?,?,?)", (cid, task_id, payload, "operator trusted source transfer", approval["created_at"]))
            else:
                duplicate = db.execute("SELECT task_id FROM tasks WHERE fingerprint=?", (fp,)).fetchone()
                if duplicate and duplicate[0] != task_id:
                    raise RegistryError("TRUSTED_IMPORT_IDENTITY_CONFLICT")
                if mother:
                    if mother["status"] != "spec_approved" or mother["fingerprint"] != fp:
                        raise RegistryError("TRUSTED_IMPORT_TASK_CONFLICT")
                    saved = db.execute("SELECT spec_hash FROM versions WHERE task_id=? AND version=?", (task_id, version)).fetchone()
                    if saved and saved[0] != approval["spec_hash"]:
                        raise RegistryError("TRUSTED_IMPORT_VERSION_CONFLICT")
                    if version > mother["version"]:
                        db.execute("UPDATE tasks SET spec_json=?,version=? WHERE task_id=?", (payload, version, task_id))
                else:
                    db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)", (task_id, fp, payload, "spec_approved", version, task_id, approval["created_at"]))
                db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)", (task_id, version, payload, approval["spec_hash"], approval["created_at"]))
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?)", tuple(approval[k] for k in
                       ("candidate_id", "task_id", "version", "action", "spec_hash", "review_hash", "created_at")))
            db.execute("INSERT INTO approval_evidence VALUES(?,?,?,?,?)", (cid, json.dumps(review, ensure_ascii=False, sort_keys=True),
                       json.dumps(files, ensure_ascii=False, sort_keys=True), artifacts["public_hash"], approval["created_at"]))
            db.execute("INSERT INTO trusted_approval_imports VALUES(?,?,?,?,?)", (cid, json.dumps(record, ensure_ascii=False, sort_keys=True), digest(record), note, now()))
            db.execute("INSERT INTO events(candidate_id,action,note,spec_hash,review_hash,created_at) VALUES(?,?,?,?,?,?)",
                       (cid, "trusted_import", note, approval["spec_hash"], approval["review_hash"], now()))
        return {"candidate_id": cid, "task_id": task_id, "version": version, "status": "operator_trusted_import",
                "new_approval_created": False, "source_hash": digest(record)}

    def capture_public(self, candidate_id, files):
        """Persist reviewed public bytes once; later renderer upgrades cannot rewrite them."""
        from finlab.tasks import PUBLIC_FILES
        resolved = self.resolve_candidate(candidate_id)
        if set(files) != PUBLIC_FILES or any(not isinstance(x, str) for x in files.values()):
            raise RegistryError("APPROVAL_PUBLIC_ALLOWLIST_MISMATCH")
        review = resolved["artifacts"]["review"]
        if review is None or digest({k: files[k] for k in sorted(files) if k != "task_input.draft.json"}) != review.get("content_hash"):
            raise RegistryError("APPROVAL_PUBLIC_NOT_REVIEWED")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT public_hash FROM approval_evidence WHERE candidate_id=?", (candidate_id,)).fetchone()
            if existing and existing[0] is not None and existing[0] != digest(files):
                raise RegistryError("APPROVAL_PUBLIC_ALREADY_FROZEN")
            db.execute("UPDATE approval_evidence SET public_json=?,public_hash=? WHERE candidate_id=?",
                       (json.dumps(files, ensure_ascii=False, sort_keys=True), digest(files), candidate_id))
        return {"candidate_id": candidate_id, "public_hash": digest(files), "status": "captured"}

    def approved_versions(self):
        with self.connect() as db:
            refs = [dict(row) for row in db.execute("SELECT * FROM approvals ORDER BY task_id,version,candidate_id")]
        result = []
        for approval in refs:
            reference = approval["candidate_id"] if approval["action"] == "variant" else approval["task_id"]
            resolved = self.resolve_approved(reference, approval["version"])
            spec = resolved["spec"]
            result.append({**approval, "title": spec.get("title"), "family_id": spec.get("family_id"),
                           "reusable_spec": True, "artifact_status": resolved["artifacts"]["status"],
                           "data_status": "binding_not_registered" if not self.bindings(approval["candidate_id"]) else "binding_registered_unverified"})
        with self.connect() as db:
            automatic_ids = [r[0] for r in db.execute('SELECT candidate_id FROM machine_acceptances ORDER BY candidate_id')]
        for cid in automatic_ids:
            accepted = self.resolve_approved(cid)
            result.append({**accepted['approval'],'title':accepted['spec'].get('title'),'family_id':accepted['spec'].get('family_id'),
                           'reusable_spec':True,'artifact_status':'captured','approval_method':'automated',
                           'novelty':accepted['novelty'],'counts_new_question':accepted['counts_new_question'],
                           'data_status':'binding_registered_unverified' if self.bindings(cid) else 'binding_not_registered'})
        return result

    def register_binding(self, candidate_id, record):
        """Register private manifest pointers, not proof that data or Docker works."""
        approval = self.approval(candidate_id)
        if not approval:
            raise RegistryError("APPROVED_VERSION_NOT_FOUND")
        reference = candidate_id if approval["action"] == "variant" else approval["task_id"]
        self.resolve_approved(reference, approval["version"])
        required = {"binding_id", "dataset_id", "freeze_hash", "binding_hash", "runtime_hash", "manifest_path"}
        if not isinstance(record, dict) or not required <= set(record) or any(not isinstance(record[k], str) or not record[k] for k in required):
            raise RegistryError("BINDING_RECORD_INCOMPLETE")
        if record.get("approved_spec_hash") != approval["spec_hash"]:
            raise RegistryError("BINDING_APPROVAL_HASH_MISMATCH")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            table = 'machine_bindings' if approval['action'] == 'auto_accepted' else 'approved_bindings'
            existing = db.execute("SELECT candidate_id,record_hash FROM " + table + " WHERE binding_id=?", (record["binding_id"],)).fetchone()
            if existing:
                if existing[0] != candidate_id or existing[1] != digest(record):
                    raise RegistryError("BINDING_ID_CONFLICT")
            else:
                db.execute("INSERT INTO " + table + " VALUES(?,?,?,?,?)",
                           (record["binding_id"], candidate_id, json.dumps(record, ensure_ascii=False, sort_keys=True), digest(record), now()))
        return {"binding_id": record["binding_id"], "status": "registered_unverified"}

    def bindings(self, candidate_id):
        with self.connect() as db:
            rows = list(db.execute("SELECT record_json,record_hash FROM approved_bindings WHERE candidate_id=? ORDER BY binding_id", (candidate_id,)))
            rows += list(db.execute("SELECT record_json,record_hash FROM machine_bindings WHERE candidate_id=? ORDER BY binding_id", (candidate_id,)))
        result = []
        for row in rows:
            record = json.loads(row[0])
            if digest(record) != row[1]:
                raise RegistryError("BINDING_RECORD_HASH_MISMATCH")
            result.append(record)
        return result
