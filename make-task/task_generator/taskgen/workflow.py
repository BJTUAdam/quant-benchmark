from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from .dedup import structural_check
from .io_utils import catalog, digest, now, read_json, write_json, write_text
from .registry import Registry, RegistryError
from .validation import validate, validate_review


class WorkflowError(ValueError):
    pass


def identifier(prefix):
    return prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def archive_review(folder, review):
    """Short Windows-safe history name; compare full hashes before idempotent reuse."""
    path = Path(folder) / (digest(review)[:16] + ".json")
    if path.exists():
        if digest(read_json(path)) != digest(review):
            raise WorkflowError("REVIEW_HISTORY_FILENAME_COLLISION")
        return path
    write_json(path, review)
    return path


@contextmanager
def batch_lock(batch):
    path = batch / ".api.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise WorkflowError("该批次已有API操作或上次异常退出留下锁；确认没有运行进程后再移除该批次.api.lock") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "created_at": now()}))
        yield
    finally:
        path.unlink(missing_ok=True)


class Production:
    def __init__(self, root, config=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config).resolve() if config else self.root / "config" / "default.json"

    @property
    def registry(self):
        registry = Registry(self.root / "registry" / "tasks.sqlite")
        from .acceptance_snapshot import bootstrap
        bootstrap(self.root,registry)
        return registry

    def init(self):
        for folder in ("registry", "runs", "exports", "docs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        added = self.registry.import_history(catalog(self.root, "historical_tasks"))
        return {"historical_records_added": added, "registry": str(self.registry.path),
                "data_status": "not_bound", "pipeline_status": "not_connected"}

    def snapshot_acceptances(self, output=None):
        from .acceptance_snapshot import snapshot
        return snapshot(self.root,output)

    def batch_path(self, batch_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
            raise WorkflowError("无效批次ID")
        path = self.root / "runs" / batch_id
        if not path.is_dir():
            raise WorkflowError("批次不存在：" + batch_id)
        return path

    def find(self, candidate_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate_id):
            raise WorkflowError("无效候选ID")
        paths = list((self.root / "runs").glob("*/candidates/" + candidate_id))
        if len(paths) != 1:
            raise WorkflowError("候选不存在或ID冲突：" + candidate_id)
        return paths[0]

    def records(self, exclude=None):
        from .batch_snapshot import SnapshotError, distributed_records
        try:
            snapshots = distributed_records(self.root)
        except SnapshotError as error:
            raise WorkflowError("分发候选快照损坏或无效，停止去重：" + str(error)) from None
        records = {x["task_id"]: x for x in self.registry.records() if x["task_id"] != exclude}
        active = {}
        for path in sorted((self.root / "runs").glob("*/candidates/*/spec.json")):
            cid = path.parent.name
            spec = read_json(path)
            if cid in active and active[cid] != digest(spec):
                raise WorkflowError("活动候选ID冲突，停止去重：" + cid)
            active[cid] = digest(spec)
            if cid == exclude or cid in records:
                continue
            state = read_json(path.parent / "state.json")
            if state.get("status") in {"rejected", "invalid", "variant"}:
                continue
            historical_only = (isinstance(spec, dict) and spec.get("schema_version") == "finance_task_spec.v1"
                               and all(k in spec for k in ("core_mechanism", "decision_object", "required_decisions", "decision_dependencies", "maintenance_targets")))
            if historical_only or validate(spec, self.root)["passed"]:
                records[cid] = {"task_id": cid, "spec": spec, "status": "historical_read_only" if historical_only else state.get("status", "candidate")}
        for row in snapshots:
            cid, spec_hash = row["task_id"], digest(row["spec"])
            if cid in active:
                if active[cid] != spec_hash:
                    raise WorkflowError("分发快照与活动候选规格冲突，停止去重：" + cid)
                continue  # Even a rejected/edited activity state must never be revived by a snapshot.
            if cid == exclude:
                continue
            if cid in records:
                if digest(records[cid]["spec"]) != spec_hash:
                    raise WorkflowError("分发快照与登记规格冲突，停止去重：" + cid)
                continue
            records[cid] = row
        return sorted(records.values(), key=lambda x: x["task_id"])

    @staticmethod
    def context_hash(records):
        return digest([{ "task_id": x["task_id"], "spec_hash": digest(x["spec"])} for x in records])

    def new_batch(self, provenance, batch_config=None):
        batch_id = identifier("B")
        path = self.root / "runs" / batch_id
        (path / "candidates").mkdir(parents=True)
        meta = {"id": batch_id, "created_at": now(), "provenance": provenance,
                "config": batch_config or {}, "api_calls_used": 0, "status": "in_progress"}
        if provenance == "model_api":
            config = read_json(self.config_path)
            meta["api_profile"] = {"provider_name": config.get("provider_name", "configured_service"),
                "generation_model": config.get("generation_model"),
                "review_model": config.get("review_model") or config.get("generation_model")}
        write_json(path / "batch.json", meta)
        return path

    @staticmethod
    def task_summary(record):
        spec = record.get("spec", record)
        fields = ("title", "family_id", "core_mechanism", "decision_object", "required_decisions",
                  "decision_dependencies", "maintenance_targets", "research_question", "novelty_claim", "lineage")
        return {"task_id": record.get("task_id", record.get("id")), "status": record.get("status"),
                "spec": {k: spec.get(k) for k in fields}}

    def generation_schema(self, family):
        from .model_schema import for_model
        schema = for_model(read_json(self.root / "schemas" / "task_spec.v2.schema.json"))
        codes = catalog(self.root, "decisions")
        props = schema["properties"]
        props["family_id"]["enum"] = [family]
        props["source_ids"]["items"]["enum"] = [x["id"] for x in catalog(self.root, "sources")]
        for key, group in (("core_mechanism", "mechanisms"), ("decision_object", "objects")):
            props[key]["enum"] = sorted({x[key] for x in catalog(self.root, "seeds") if x["family_id"] == family})
        props["required_decisions"]["items"]["enum"] = list(codes["decisions"])
        props["research_modules"]["items"]["properties"]["decision_id"]["enum"] = list(codes["decisions"])
        props["maintenance_targets"]["items"]["enum"] = list(codes["maintenance"])
        return schema

    def generation_context(self, family, feedback):
        profiles = read_json(self.root / "schemas/data_api_profiles.v2.json")["profiles"]
        data_profiles = {key: {**p, "allowed_fields": sorted({f for ep in p["endpoints"] for f in ep["fields"]})}
                         for key, p in profiles.items()}
        context = {"family_id": family, "requested_family_id": family,
            "family": next(x for x in catalog(self.root, "families") if x["id"] == family),
            "sources": catalog(self.root, "sources"), "decisions": catalog(self.root, "decisions"),
            "environments": catalog(self.root, "environments"), "data_profiles": data_profiles,
            "requested_data_scope": {"futures_products": ["B", "M", "Y", "SC", "FU", "LU", "FG", "SA"],
                "calendar_spread_preferred_products": ["FG", "M"],
                "exchange_suffix_by_product": {"B": "DCE", "M": "DCE", "Y": "DCE", "FU": "SHF", "SC": "INE", "LU": "INE", "FG": "ZCE", "SA": "ZCE"},
                "symbol_rule": "品种池可填FG/M等无后缀代码待绑定；若填真实合约或交易所后缀必须匹配登记交易所，不虚构FG.SHF等标识。交割月份在数据绑定时确认。",
                "stock_pool": "国内股票或ETF的真实代码；空池或缺失成分资料必须标记待绑定，不使用匿名代号",
                "note": "期货标准目前只覆盖CNY/吨和CNY/桶；AU/EC等不同计价单位需另行扩展，本轮不生成"},
            "seeds": [x for x in catalog(self.root, "seeds") if x.get("family_id") == family],
            "existing_tasks": [self.task_summary(x) for x in self.records()],
            "recent_feedback": feedback[-3:], "schema": self.generation_schema(family)}
        from finlab.tasks import draft_contracts
        context["v2_pipeline"] = {"schema_version": "finance_task_spec.v2",
            "data_access": "Tushare Pro bridge; no market-data file package",
            "identity_policy": "real_names_and_codes", "date_domain": "virtual",
            "virtual_date_policy": "All event dates are virtual sequence labels. Years do not identify historical years; do not infer crashes, policy events, holidays or future events from them.",
            "runtime": "AKQuant 0.3.76 in Docker; platform finlab.strategy.v2 API; NextOpen",
            "resource_policy": "resources=null until human runs finlab setup; AI must not select CPU/memory/time limits",
            "submission": ["strategy.py", "backtest.py", "params.json", "RESEARCH.md", "CHANGELOG.md"],
            "multi_leg": "per-leg execution with partial fills; no atomic guarantee",
            "contract_defaults_by_environment": {
                e["id"]: draft_contracts(e["id"], {"minimum_years": 2, "symbols": []})
                for e in catalog(self.root, "environments") if family in e["supported_families"]}}
        return context

    def add_candidate(self, batch, spec, provenance):
        if not isinstance(spec, dict) or spec.get("schema_version") != "finance_task_spec.v2":
            raise WorkflowError("仅接受原生finance_task_spec.v2；旧任务只用于历史对照，不进行转换或入库")
        cid = identifier("TG")
        path = batch / "candidates" / cid
        state = {"id": cid, "batch_id": batch.name, "status": "pending_automatic_review",
                 "data_status": "not_bound", "pipeline_status": "not_connected", "version": 0,
                 "provenance": provenance, "model_review_status": "not_run", "created_at": now()}
        write_json(path / "spec.json", spec)
        write_json(path / "state.json", state)
        write_json(path / "operator" / "review.json", {"status": "not_run", "classification": "not_run",
                   "reason": "尚未调用模型进行独立初筛；不允许据此正式入库"})
        self.check_candidate(path)
        return path

    def check_candidate(self, path):
        from .render import build_package
        path = Path(path)
        spec, state = read_json(path / "spec.json"), read_json(path / "state.json")
        result = validate(spec, self.root)
        records = self.records(exclude=path.name)
        result["context_hash"] = self.context_hash(records)
        result["checked_at"] = now()
        if result["passed"]:
            result["dedup"] = structural_check(spec, records)
            try:
                build_package(self.root, path, spec, state)
                result["content_hash"] = self.content_hash(path)
            except (ValueError, TypeError, KeyError) as exc:
                result["passed"] = False
                result["errors"].append("数据规格或正文渲染失败：" + str(exc))
        review_path = path / "operator" / "review.json"
        if review_path.exists():
            review = read_json(review_path)
            if (review.get("spec_hash") != result.get("spec_hash") or review.get("context_hash") != result["context_hash"] or
                    review.get("content_hash") != result.get("content_hash")):
                state["model_review_status"] = "not_run" if review.get("status") == "not_run" else "stale"
        if state["status"] not in {"spec_approved", "spec_accepted", "spec_reused", "variant", "rejected"}:
            state["status"] = ("variant_candidate" if result.get("dedup", {}).get("exact_matches") else "pending_automatic_review") if result["passed"] else "invalid"
        write_json(path / "operator" / "validation.json", result)
        write_json(path / "state.json", state)
        return result

    @staticmethod
    def content_hash(path):
        from finlab.tasks import PUBLIC_FILES
        return digest({name: (path / "public" / name).read_text(encoding="utf-8")
                       for name in sorted(PUBLIC_FILES - {"task_input.draft.json"})})

    def import_examples(self):
        self.init()
        batch = self.new_batch("curated_example", {"family_quotas": {"F0" + str(i): 1 for i in range(1, 7)}})
        specs = read_json(self.root / "examples" / "first_batch.json")
        if isinstance(specs, dict):
            specs = specs.get("tasks", specs.get("candidates", specs.get("specs", [])))
        for spec in specs:
            self.add_candidate(batch, spec, "curated_example")
        for path in sorted((batch / "candidates").iterdir()):
            self.check_candidate(path)  # include later candidates in each comparison context
        meta = read_json(batch / "batch.json")
        meta["status"] = "examples_pending_api_review_and_automatic_acceptance"
        write_json(batch / "batch.json", meta)
        self.write_report(batch)
        return {"batch_id": batch.name, "candidates": len(specs), "api_calls_used": 0,
                "status": meta["status"]}

    def import_spec(self, filename):
        self.init()
        batch = self.new_batch("manual_import")
        path = self.add_candidate(batch, read_json(filename), "manual_import")
        self.write_report(batch)
        return {"batch_id": batch.name, "candidate_id": path.name}

    def model_review(self, path, client):
        from .model_schema import for_model
        check = self.check_candidate(path)
        if not check["passed"]:
            raise WorkflowError("静态检查未通过，不能执行模型初筛")
        spec = read_json(path / "spec.json")
        neighbors = check["dedup"]["nearest"]
        prompt = (self.root / "prompts" / "review.md").read_text(encoding="utf-8-sig")
        review = client.complete_json([
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"candidate": spec,
                "nearest": [{**self.task_summary(x), "score": x["score"], "exact_structure": x["exact_structure"]} for x in neighbors],
                "catalog": {"decisions": catalog(self.root, "decisions"), "environments": catalog(self.root, "environments")},
                "structural_check": {k: check["dedup"][k] for k in ("fingerprint", "classification", "exact_matches")},
                "public_task": (path / "public" / "TASK.md").read_text(encoding="utf-8")}, ensure_ascii=False)}
        ], stage="review", schema=for_model(read_json(self.root / "schemas" / "review.schema.json")))
        issues = validate_review(review, {x["task_id"] for x in neighbors})
        safe = review.get("public_information_safe") is True and review.get("long_horizon_valid") is True
        review.update({"spec_hash": digest(spec), "context_hash": check["context_hash"],
                       "content_hash": check["content_hash"],
                       "reviewed_at": now(), "mode": "model_api", "validation_errors": issues,
                       "status": "passed" if not issues and safe and not review.get("issues") and
                       review.get("classification") == "distinct" else "needs_attention"})
        previous = read_json(path / "operator" / "review.json")
        if previous.get("mode") == "model_api":
            archive_review(path / "operator" / "review_history", previous)
        archive_review(path / "operator" / "review_history", review)
        write_json(path / "operator" / "review.json", review)
        self.registry.event(path.name, "model_reviewed", "独立API初筛完成；并非人工批准", spec, review)
        state = read_json(path / "state.json")
        state["model_review_status"] = review["status"]
        write_json(path / "state.json", state)
        return review

    def _client(self, batch):
        from .api import APIClient
        config = read_json(self.config_path)
        consumed = read_json(batch / "batch.json").get("api_calls_used", 0)
        configured_limit = config.get("max_calls_per_batch", 60)
        if not isinstance(configured_limit, int) or isinstance(configured_limit, bool) or configured_limit < 1:
            raise WorkflowError("max_calls_per_batch必须为正整数")
        if not isinstance(consumed, int) or isinstance(consumed, bool) or consumed < 0:
            raise WorkflowError("批次累计调用记录无效")
        remaining = configured_limit - consumed
        if remaining <= 0:
            raise WorkflowError("该批次模型调用上限已用尽")
        config["max_calls_per_batch"] = remaining
        limit = consumed + remaining
        def reserve():
            meta = read_json(batch / "batch.json")
            used = meta.get("api_calls_used", 0)
            if used >= limit:
                raise WorkflowError("批次调用预算已用尽")
            meta["api_calls_used"] = used + 1
            write_json(batch / "batch.json", meta)
        return APIClient(config, log_path=batch / "api_calls.jsonl", reserve_attempt=reserve)

    def _save_calls(self, batch, client):
        # Attempts were reserved durably before dispatch, including attempts interrupted by a crash.
        return read_json(batch / "batch.json").get("api_calls_used", 0)

    def generate(self, batch_config):
        self.init()
        settings = read_json(batch_config)
        quotas = settings["family_quotas"]
        if not isinstance(quotas, dict) or set(quotas) - {x["id"] for x in catalog(self.root, "families")}:
            raise WorkflowError("批次family_quotas无效")
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in quotas.values()):
            raise WorkflowError("每类配额必须是非负整数")
        limit = settings.get("max_candidates", 18)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000 or sum(quotas.values()) < 1:
            raise WorkflowError("候选上限或配额无效")
        batch = self.new_batch("model_api", settings)
        lock = batch_lock(batch)
        lock.__enter__()
        try:
            client = self._client(batch)
        except Exception:
            meta = read_json(batch / "batch.json")
            meta["status"] = "configuration_error"
            write_json(batch / "batch.json", meta)
            self.write_report(batch)
            lock.__exit__(None, None, None)
            raise
        counts = {k: 0 for k in quotas}
        family_attempts = {k: 0 for k in quotas}
        feedback = {k: [] for k in quotas}
        attempted = 0
        failures = []
        interrupted = False
        prompt = (self.root / "prompts" / "generate.md").read_text(encoding="utf-8-sig")
        try:
            while attempted < limit and any(counts[k] < quotas[k] for k in quotas):
                family = min((k for k in quotas if counts[k] < quotas[k]),
                             key=lambda k: (family_attempts[k] / quotas[k], counts[k], k))
                family_attempts[family] += 1
                attempted += 1
                context = self.generation_context(family, feedback[family])
                schema = context["schema"]
                try:
                    response = client.complete_json([{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(context, ensure_ascii=False)}], stage="generate", schema=schema)
                    spec = response.get("spec", response)
                    path = self.add_candidate(batch, spec, "model_api")
                    check = read_json(path / "operator" / "validation.json")
                    if not check["passed"]:
                        feedback[family].append({"candidate_id": path.name, "kind": "static_invalid", "errors": check["errors"]})
                        self.write_report(batch)
                        continue
                    if spec["family_id"] != family:
                        state = read_json(path / "state.json")
                        state["status"] = "invalid"
                        write_json(path / "state.json", state)
                        check["passed"] = False
                        check["errors"].append("模型未遵循本次指定的family_id")
                        write_json(path / "operator" / "validation.json", check)
                        feedback[family].append({"candidate_id": path.name, "kind": "wrong_family", "errors": check["errors"]})
                        self.write_report(batch)
                        continue
                    review = self.model_review(path, client)
                    if review["status"] == "passed" and not check["dedup"]["exact_matches"]:
                        counts[family] += 1
                    else:
                        feedback[family].append({"candidate_id": path.name, "classification": review.get("classification"),
                            "closest_task_id": review.get("closest_task_id"), "rationale": review.get("rationale"),
                            "issues": review.get("issues"), "validation_errors": review.get("validation_errors")})
                    self.write_report(batch)
                except Exception as exc:
                    from .api import APIError, BudgetExceeded
                    # API exceptions are sanitized by the adapter. Never dump credentials/config.
                    failures.append({"attempt": attempted, "family_id": family,
                                     "error": str(exc) if isinstance(exc, (APIError, WorkflowError)) else type(exc).__name__})
                    if isinstance(exc, (APIError, BudgetExceeded)):
                        break
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            self._save_calls(batch, client)
            meta = read_json(batch / "batch.json")
            meta.update({"attempted_candidates": attempted, "preliminary_screened_counts": counts,
                         "family_attempts": family_attempts,
                         "recent_feedback": feedback,
                         "failures": failures, "status": "interrupted" if interrupted else
                         ("pending_automatic_acceptance" if not failures else "attention_required")})
            write_json(batch / "batch.json", meta)
            # A later candidate changes the comparison set. Mark earlier reviews stale until the final batch review.
            for path in sorted((batch / "candidates").iterdir()):
                self.check_candidate(path)
            # Complete the final comparison pass inside the same finite API
            # budget. No human approval command is needed after generation.
            if not interrupted:
                for path in sorted((batch/'candidates').iterdir()):
                    checked = read_json(path/'operator/validation.json')
                    review = read_json(path/'operator/review.json')
                    if not checked['passed'] or all(review.get(k) == checked.get(k) for k in ('spec_hash','context_hash','content_hash')):
                        continue
                    try:
                        self.model_review(path,client)
                    except Exception as exc:
                        from .api import APIError
                        failures.append({'candidate_id':path.name,'stage':'final_review',
                                         'error':str(exc) if isinstance(exc,(APIError,WorkflowError)) else type(exc).__name__})
                        break
                meta=read_json(batch/'batch.json')
                meta['failures']=failures
                write_json(batch/'batch.json',meta)
            self.write_report(batch)
            lock.__exit__(None, None, None)
        result = read_json(batch / "batch.json")
        result['automatic_acceptance'] = self.accept_batch(batch.name)
        result['status'] = 'automatic_acceptance_completed' if not result['automatic_acceptance']['blockers'] else 'attention_required'
        write_json(batch/'batch.json',result)
        return result

    def check_batch(self, batch_id):
        batch = self.batch_path(batch_id)
        results = {p.name: self.check_candidate(p) for p in sorted((batch / "candidates").iterdir())}
        self.write_report(batch)
        return {"batch_id": batch_id, "passed": sum(x["passed"] for x in results.values()), "total": len(results)}

    def review_batch(self, batch_id, use_api=False, candidate_ids=None):
        batch = self.batch_path(batch_id)
        errors = []
        selected = set(candidate_ids or [])
        known_ids = {p.name for p in (batch / "candidates").iterdir() if p.is_dir()}
        if selected - known_ids:
            raise WorkflowError("指定候选不属于该批次：" + ",".join(sorted(selected - known_ids)))
        if use_api:
            with batch_lock(batch):
                client = self._client(batch)
                try:
                    for path in sorted((batch / "candidates").iterdir()):
                        if selected and path.name not in selected:
                            continue
                        state = read_json(path / "state.json")
                        if state["status"] in {"spec_approved", "spec_accepted", "spec_reused", "variant", "rejected"}:
                            continue
                        try:
                            self.model_review(path, client)
                        except Exception as exc:
                            from .api import APIError
                            errors.append({"candidate_id": path.name, "error": str(exc) if isinstance(exc, (APIError, WorkflowError)) else type(exc).__name__})
                            if isinstance(exc, APIError):
                                break
                finally:
                    self._save_calls(batch, client)
        self.write_report(batch)
        result = read_json(batch / "coverage_report.json")
        result["errors"] = errors
        result["review_selection"] = sorted(selected) if selected else "all"
        if use_api:
            result['automatic_acceptance'] = self.accept_batch(batch_id, selected or None)
        return result

    def accept(self, candidate_id):
        """Automatic specification admission, including non-new executable variants.

        Admission does not assert data readiness or strategy performance. Only
        model evidence actually recorded for this exact content is eligible.
        """
        path = self.find(candidate_id)
        state = read_json(path / 'state.json')
        spec = read_json(path / 'spec.json')
        prior = self.registry.approval(candidate_id)
        if prior:
            record = self.registry.resolve_candidate(candidate_id)
            if record['spec_hash'] != digest(spec): raise WorkflowError('ACCEPTED_SPEC_CHANGED')
            return {**record, 'status':'spec_accepted' if record.get('approval_method') == 'automated' else 'spec_approved',
                    'pipeline_candidate_id':record['candidate_id'], 'reused':True}
        if state.get('status') == 'rejected': raise WorkflowError('REJECTED_CANDIDATE_NOT_ELIGIBLE')
        check = self.check_candidate(path)
        review = read_json(path / 'operator/review.json')
        if not check['passed']: raise WorkflowError('SPECIFICATION_CHECK_FAILED')
        public = None
        content_source = 'current_reviewed_render'
        if (review.get('mode') == 'model_api' and all(review.get(k)==check.get(k) for k in ('spec_hash','context_hash'))
                and review.get('content_hash') != check.get('content_hash')):
            # A later platform renderer may add documentation. Reuse the
            # original, fully verified reviewed bytes rather than claiming the
            # new render was reviewed. Arbitrary local files are never fallback.
            from .batch_snapshot import verify_snapshot, _review
            from .render import PUBLIC_FILES
            for folder in sorted((self.root/'examples/generated').glob('*')):
                if not folder.is_dir() or folder.name.startswith('.'): continue
                rows = verify_snapshot(folder,self.root)
                row = next((r for r in rows if r['task_id']==candidate_id),None)
                if not row or digest(row['spec'])!=check['spec_hash']:continue
                original = folder/'candidates'/candidate_id
                if _review(review)!=read_json(original/'operator/review.json'):continue
                files={n:(original/'public'/n).read_text(encoding='utf-8') for n in sorted(PUBLIC_FILES)}
                if digest({n:files[n] for n in sorted(files) if n!='task_input.draft.json'})!=review['content_hash']:continue
                public=files;content_source='verified_reviewed_snapshot';break
        if (review.get('mode') != 'model_api' or any(review.get(k) != check.get(k) for k in ('spec_hash','context_hash'))
                or review.get('content_hash') != check.get('content_hash') and public is None):
            raise WorkflowError('MODEL_REVIEW_MISSING_OR_STALE: review --batch BATCH --api')
        issues = validate_review(review, {x['task_id'] for x in check['dedup']['nearest']})
        if (issues or review.get('validation_errors') or review.get('issues')
                or review.get('public_information_safe') is not True or review.get('long_horizon_valid') is not True
                or review.get('classification') not in {'distinct','variant','duplicate'}):
            raise WorkflowError('MODEL_REVIEW_UNRESOLVED_ISSUES')
        novelty = review['classification']
        if novelty == 'distinct' and check['dedup']['exact_matches']: novelty = 'duplicate'
        from .render import PUBLIC_FILES
        public = public or {name:(path/'public'/name).read_text(encoding='utf-8') for name in sorted(PUBLIC_FILES)}
        record = self.registry.accept(candidate_id, spec, review, public, novelty=novelty,
                                      related_task_id=review.get('closest_task_id'),admission_content_source=content_source)
        reused = record['candidate_id'] != candidate_id
        state.update({'status':'spec_reused' if reused else 'spec_accepted', 'approval_method':'automated',
                      'task_id':record['task_id'],'version':record['version'],'accepted_at':record['created_at'],
                      'pipeline_candidate_id':record['candidate_id'],'novelty':record['novelty'],
                      'counts_new_question':record['counts_new_question'],'human_approved':False})
        write_json(path/'state.json',state)
        # Only bookkeeping is updated; the content reviewed by the model remains byte-identical.
        write_text(path/'public/task_input.draft.json',record['artifacts']['public_files']['task_input.draft.json'])
        self.write_report(path.parent.parent)
        return {**record,'status':state['status'],'pipeline_candidate_id':record['candidate_id'],'reused':reused}

    def accept_batch(self, batch_id, candidate_ids=None):
        batch = self.batch_path(batch_id)
        selected = set(candidate_ids or [])
        results, blockers = [], []
        paths = sorted(p for p in (batch/'candidates').iterdir() if p.is_dir())
        known = {p.name for p in paths}
        if selected-known: raise WorkflowError('CANDIDATE_NOT_IN_BATCH')
        for path in paths:
            if selected and path.name not in selected: continue
            try:
                record = self.accept(path.name)
                results.append({k:record.get(k) for k in ('candidate_id','task_id','version','status','approval_method',
                                'novelty','counts_new_question','pipeline_candidate_id','reused')})
            except (WorkflowError, RegistryError, OSError, ValueError, KeyError) as error:
                blockers.append({'candidate_id':path.name,'error':str(error)})
        return {'batch_id':batch_id,'mode':'automatic','human_approval_required':False,
                'accepted':results,'accepted_count':len(results),'blockers':blockers,
                'new_independent_questions':sum(r['counts_new_question'] is True and not r['reused'] for r in results),
                'data_status':'not_bound','pipeline_status':'not_connected'}

    def approve(self, candidate_id, note, as_variant=None, update_task=None):
        path = self.find(candidate_id)
        check = self.check_candidate(path)
        review = read_json(path / "operator" / "review.json")
        if not check["passed"]:
            raise WorkflowError("规格检查未通过")
        if (review.get("mode") != "model_api" or review.get("spec_hash") != check["spec_hash"] or
                review.get("context_hash") != check["context_hash"] or review.get("content_hash") != check.get("content_hash")):
            raise WorkflowError("模型初筛缺失或已过期；请运行 review --batch <ID> --api")
        fresh_issues = validate_review(review, {x["task_id"] for x in check["dedup"]["nearest"]})
        if fresh_issues:
            raise WorkflowError("初筛结构或比较依据无效：" + ";".join(fresh_issues))
        if (review.get("validation_errors") or review.get("issues") or review.get("public_information_safe") is not True or
                review.get("long_horizon_valid") is not True or review.get("classification") == "uncertain"):
            raise WorkflowError("初筛存在未解决问题，需修改规格后重新检查")
        if not as_variant and not update_task and (review.get("classification") != "distinct" or check["dedup"]["exact_matches"]):
            raise WorkflowError("重复或变体不能作为独立新题入库")
        if as_variant and review.get("classification") not in {"variant", "distinct"}:
            raise WorkflowError("原样重复不可用变体登记绕过；需保留实质参数或数据变化")
        if as_variant and update_task:
            raise WorkflowError("as-variant与update-task不能同时使用")
        spec = read_json(path / "spec.json")
        from .render import PUBLIC_FILES
        public_files = {name: (path / "public" / name).read_text(encoding="utf-8") for name in sorted(PUBLIC_FILES)}
        result = self.registry.approve(candidate_id, spec, review, note, as_variant, update_task, public_files=public_files)
        state = read_json(path / "state.json")
        state.update(result)
        state.update({"status": "variant" if as_variant else "spec_approved", "approved_at": now(), "approval_note": note})
        write_json(path / "state.json", state)
        from .render import build_package
        build_package(self.root, path, spec, state)
        self.write_report(path.parent.parent)
        return state

    def approved_catalog(self):
        rows = self.registry.approved_versions()
        return {"approved_versions": rows, "count": len(rows),
                "scope": "immutable_human_approvals_and_automated_acceptances", "runnable_count": None,
                "note": "绑定登记只是位置索引；可批跑状态须通过交付验收，不按候选或历史题ID推断。"}

    def reuse_options(self, candidate_id):
        """Turn dedup IDs into actionable inventory without rerunning a model."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate_id):
            raise WorkflowError("无效候选ID")
        active = list((self.root / "runs").glob("*/candidates/" + candidate_id))
        if len(active) > 1:
            raise WorkflowError("候选ID冲突：" + candidate_id)
        source = "active_candidate"
        if active:
            path = active[0]
        else:
            row = next((r for r in self.records() if r["task_id"] == candidate_id and r.get("source") == "distributed_snapshot"), None)
            if not row:
                raise WorkflowError("候选不存在：" + candidate_id)
            path = self.root / "examples/generated" / row["snapshot_batch_id"] / "candidates" / candidate_id
            source = "distributed_snapshot_read_only"
        spec = read_json(path / "spec.json")
        records = self.records(exclude=candidate_id)
        check = structural_check(spec, records)
        review = read_json(path / "operator/review.json")
        hashes = {"spec_hash": digest(spec), "context_hash": self.context_hash(records), "content_hash": self.content_hash(path)}
        changed = [key for key, value in hashes.items() if review.get(key) != value]
        options = []
        for neighbor in check["nearest"]:
            row = {k: neighbor[k] for k in ("task_id", "score", "exact_structure", "status")}
            try:
                approved = self.registry.resolve_approved(neighbor["task_id"])
                row.update(reuse_status="approved_spec_available", candidate_id=approved["candidate_id"],
                           version=approved["version"], spec_hash=approved["spec_hash"],
                           artifact_status=approved["artifacts"]["status"],
                           bindings=self.registry.bindings(approved["candidate_id"]),
                           next_action="复用该批准版本并核验数据绑定；无需为重复题再生成或修改批准状态")
            except RegistryError as error:
                row.update(reuse_status="unresolved_reference", reason=str(error), bindings=[],
                           next_action="仅为历史/候选对照；需要原生v2规格和有效模型复核/接纳记录，不能只凭历史ID转正")
            options.append(row)
        return {"candidate_id": candidate_id, "spec_hash": digest(spec), "source": source, "options": options,
                "approved_options": sum(r["reuse_status"] == "approved_spec_available" for r in options),
                "review_freshness": {"fresh": not changed and review.get("mode") == "model_api", "changed": changed,
                                     "classification": review.get("classification"),
                                     "next_action": "先确认可复用的批准版本；只在选择修订草案后做最终整批复核，不反复改写重复题"},
                "automatic_approval": False}

    def reuse_approved(self, reference, output, version=None):
        """Materialize a platform-private immutable approval; no new task or approval."""
        resolved = self.registry.resolve_approved(reference, version)
        if not validate(resolved["spec"], self.root)["passed"]:
            raise WorkflowError("APPROVED_SPEC_VALIDATION_FAILED")
        dest = Path(output).resolve()
        if dest.exists():
            raise WorkflowError("REUSE_DESTINATION_EXISTS")
        dest.mkdir(parents=True)
        write_json(dest / "approved_record.private.json", resolved)
        write_json(dest / "spec.json", resolved["spec"])
        public = resolved["artifacts"]["public_files"]
        entry_normalization = None
        if public is not None:
            for name, content in sorted(public.items()):
                write_text(dest / "public" / name, content)
            from finlab.agent_reference import normalize_public_entry
            entry_normalization = normalize_public_entry(dest/'public')
        return {"directory": str(dest), "candidate_id": resolved["candidate_id"],
                "task_id": resolved["task_id"], "version": resolved["version"],
                "spec_hash": resolved["spec_hash"], "new_approval_created": False,
                "artifact_status": resolved["artifacts"]["status"],
                "data_status": "requires_binding_validation", "pipeline_status": "not_verified",
                "entry_path_normalization": entry_normalization,
                "bindings": self.registry.bindings(resolved["candidate_id"])}

    def reject(self, candidate_id, note):
        if not note.strip():
            raise WorkflowError("驳回说明不能为空")
        path = self.find(candidate_id)
        if self.registry.approval(candidate_id):
            raise WorkflowError("已批准的版本不能用候选驳回覆盖；请创建修订版本")
        state, spec = read_json(path / "state.json"), read_json(path / "spec.json")
        self.registry.event(candidate_id, "rejected", note, spec)
        state.update({"status": "rejected", "rejection_note": note, "rejected_at": now()})
        write_json(path / "state.json", state)
        self.write_report(path.parent.parent)
        return state

    def export(self, candidate_id):
        from .render import PUBLIC_FILES, build_package
        accepted = self.registry.approval(candidate_id)
        if accepted and accepted.get('action')=='auto_accepted':
            return self._export_automatic(candidate_id)
        path = self.find(candidate_id)
        spec, state = read_json(path / "spec.json"), read_json(path / "state.json")
        approval = self.registry.approval(candidate_id)
        review = read_json(path / "operator" / "review.json")
        if not approval or approval["spec_hash"] != digest(spec) or approval["review_hash"] != digest(review):
            raise WorkflowError("缺少有效人工批准记录，或批准后内容已改变；禁止导出正式规格包")
        if state["status"] not in {"spec_approved", "spec_accepted", "variant"}:
            raise WorkflowError("候选未处于已批准状态")
        if state.get("id") != candidate_id or state.get("task_id") != approval["task_id"] or state.get("version") != approval["version"]:
            raise WorkflowError("状态文件与已批准任务身份或版本不一致")
        if not validate(spec, self.root)["passed"]:
            raise WorkflowError("当前规格未通过静态检查")
        existing = {p.name for p in (path / "public").iterdir()}
        if existing != PUBLIC_FILES or any(p.is_symlink() or not p.is_file() for p in (path / "public").iterdir()):
            raise WorkflowError("公共目录出现白名单外文件或符号链接，禁止导出")
        build_package(self.root, path, spec, state)
        if review.get("content_hash") != self.content_hash(path):
            raise WorkflowError("公开任务或数据标准在批准后变化，需重新初筛及人工审核")
        stem = candidate_id + "_v" + str(state["version"]) + "_spec"
        dest = self.root / "exports" / stem
        if dest.exists():
            raise WorkflowError("该版本已导出；保留原导出，不覆盖")
        dest.mkdir(parents=True)
        shutil.copytree(path / "public", dest / "public")
        shutil.copytree(path / "operator", dest / "operator")
        write_json(dest / "spec.json", spec)
        write_json(dest / "state.json", state)
        manifest = {"status": "specification_only_data_pending", "files": {
            "public/" + name: hashlib.sha256((dest / "public" / name).read_bytes()).hexdigest()
            for name in sorted(PUBLIC_FILES)}}
        write_json(dest / "public_manifest.json", manifest)
        archives = []
        for scope in ("public", "operator"):
            archive = self.root / "exports" / (stem + "_" + scope + ".zip")
            with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
                for item in sorted((dest / scope).rglob("*")):
                    if item.is_file():
                        z.write(item, item.relative_to(dest).as_posix())
                if scope == "public":
                    z.write(dest / "public_manifest.json", "public_manifest.json")
            archives.append(str(archive))
        self.registry.event(candidate_id, "exported", "公开/内部材料分离；规格包非可运行包", spec, review)
        return {"directory": str(dest), "archives": archives}

    def _export_automatic(self, candidate_id):
        """Export admitted immutable bytes; do not silently render unreviewed content."""
        from .render import PUBLIC_FILES
        record=self.registry.resolve_candidate(candidate_id)
        if record.get('approval_method')!='automated': raise WorkflowError('AUTOMATIC_ACCEPTANCE_REQUIRED')
        active=list((self.root/'runs').glob('*/candidates/'+candidate_id))
        if active and digest(read_json(active[0]/'spec.json'))!=record['spec_hash']:
            raise WorkflowError('ACCEPTED_SPEC_CHANGED')
        files=record['artifacts']['public_files']
        stem=candidate_id+'_v'+str(record['version'])+'_spec'
        dest=self.root/'exports'/stem
        if dest.exists():raise WorkflowError('该版本已导出；保留原导出，不覆盖')
        dest.mkdir(parents=True)
        for name,body in files.items():write_text(dest/'public'/name,body)
        from finlab.agent_reference import normalize_public_entry
        entry_normalization = normalize_public_entry(dest/'public')
        write_json(dest/'operator/acceptance.private.json',record)
        write_json(dest/'spec.json',record['spec'])
        write_json(dest/'state.json',{'id':candidate_id,'task_id':record['task_id'],'version':record['version'],
                                    'status':'spec_accepted','approval_method':'automated','novelty':record['novelty'],
                                    'counts_new_question':record['counts_new_question'],'human_approved':False})
        manifest={'status':'specification_only_data_pending','approval_method':'automated',
                  'novelty':record['novelty'],'counts_new_question':record['counts_new_question'],
                  'source_public_snapshot_hash':record['artifacts']['public_hash'],
                  'entry_path_normalization':entry_normalization,
                  'files':{'public/'+name:hashlib.sha256((dest/'public'/name).read_bytes()).hexdigest() for name in sorted(PUBLIC_FILES)}}
        write_json(dest/'public_manifest.json',manifest)
        archives=[]
        for scope in ('public','operator'):
            archive=self.root/'exports'/(stem+'_'+scope+'.zip')
            with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
                for item in sorted((dest/scope).rglob('*')):
                    if item.is_file():z.write(item,item.relative_to(dest).as_posix())
                if scope=='public':z.write(dest/'public_manifest.json','public_manifest.json')
            archives.append(str(archive))
        self.registry.event(candidate_id,'exported','Immutable automated specification export; data pending',record['spec'],record['artifacts']['review'])
        return {'directory':str(dest),'archives':archives,'approval_method':'automated','novelty':record['novelty']}

    def write_report(self, batch):
        quotas = read_json(batch / "batch.json").get("config", {}).get("family_quotas", {})
        coverage = {x["id"]: {"target": quotas.get(x["id"], 0), "candidates": 0, "spec_approved": 0,
                              "spec_accepted":0,"automatic_non_new":0,
                              "variants": 0, "static_passed": 0, "model_review_passed": 0,
                              "model_review_pending": 0} for x in catalog(self.root, "families")}
        rows = []
        for path in sorted((batch / "candidates").iterdir()):
            state, spec = read_json(path / "state.json"), read_json(path / "spec.json")
            review = read_json(path / "operator" / "review.json")
            validation = read_json(path / "operator" / "validation.json")
            family = spec.get("family_id") if isinstance(spec, dict) else None
            if not isinstance(family, str):
                family = None
            if family in coverage:
                coverage[family]["candidates"] += 1
                coverage[family]["spec_approved"] += int(state["status"] == "spec_approved")
                coverage[family]['spec_accepted'] += int(state['status'] == 'spec_accepted')
                coverage[family]['automatic_non_new'] += int(state.get('approval_method') == 'automated' and not state.get('counts_new_question',False))
                coverage[family]["variants"] += int(state["status"] == "variant")
                coverage[family]["static_passed"] += int(validation["passed"])
                coverage[family]["model_review_passed"] += int(validation["passed"] and
                    state["model_review_status"] == "passed" and review.get("classification") == "distinct")
                coverage[family]["model_review_pending"] += int(state["model_review_status"] in {"not_run", "stale"})
            rows.append({"id": path.name, "title": spec.get("title", "无效规格") if isinstance(spec, dict) else "无效规格",
                         "family_id": family, "status": state["status"], "static_passed": validation["passed"],
                         "model_review_status": state["model_review_status"], "classification": review.get("classification", "not_run"),
                         "data_status": "not_bound", "pipeline_status": "not_connected"})
        for values in coverage.values():
            values["model_review_gap"] = max(0, values["target"] - values["model_review_passed"])
        report = {"batch_id": batch.name, "updated_at": now(), "coverage": coverage, "candidates": rows,
                  "api_calls_used": read_json(batch / "batch.json").get("api_calls_used", 0),
                  "scope": "specification_only", "semantic_uniqueness_guaranteed": False}
        write_json(batch / "coverage_report.json", report)
        lines = ["# 批次检查报告", "", "仅验收任务规格；数据、管线与实际维护效果未验证。", "",
                 "|候选|方向|静态检查|模型初筛|接纳状态|", "|---|---|---|---|---|"]
        lines += [f"|{r['id']}|{r['family_id']}|{'通过' if r['static_passed'] else '失败'}|{r['model_review_status']}|{r['status']}|" for r in rows]
        lines += ["", "API HTTP尝试次数：" + str(report["api_calls_used"]), "", "重复和变体不计新增母题；待审不表示已批准。"]
        lines += ["", "|方向|目标|候选|静态通过|当前独立初筛通过|初筛缺口|人工批准|", "|---|---|---|---|---|---|---|"]
        lines += [f"|{key}|{v['target']}|{v['candidates']}|{v['static_passed']}|{v['model_review_passed']}|{v['model_review_gap']}|{v['spec_approved']}|" for key, v in coverage.items()]
        write_text(batch / "REPORT.md", "\n".join(lines) + "\n")
        return report
