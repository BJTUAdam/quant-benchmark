"""Portable, read-only candidate evidence snapshots; no approvals or private stores.

Only public files use their original bytes. Structured operator/audit records are
allowlisted projections. Spec identity and public-content hashes are verified
before publication and whenever a distributed snapshot enters comparisons.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from .io_utils import digest, now, read_json
from .validation import validate


class SnapshotError(ValueError):
    pass


SCHEMA = "taskgen.batch_snapshot.v1"
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}")
CREDENTIAL = re.compile(r"(?:sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._~-]{16,})", re.I)
FORBIDDEN_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "tushare_token",
                  "market_bridge_token", "year_offset", "validation_year_offset", "real_start",
                  "real_stop", "real_end", "private_path", "cache_path", "raw_cache", "checkpoint"}
STATE_KEYS = {"id", "batch_id", "status", "data_status", "pipeline_status", "version", "provenance",
              "model_review_status", "created_at", "rejection_note", "rejected_at"}
REVIEW_KEYS = {"status", "classification", "closest_task_id", "rationale", "issues", "pending_bindings",
               "public_information_safe", "long_horizon_valid", "spec_hash", "context_hash", "content_hash",
               "reviewed_at", "mode", "validation_errors", "reason"}
VALIDATION_KEYS = {"passed", "errors", "warnings", "spec_hash", "scope", "context_hash", "checked_at", "content_hash"}
SUMMARY_KEYS = {"title", "family_id", "core_mechanism", "decision_object", "required_decisions",
                "decision_dependencies", "maintenance_targets", "research_question", "novelty_claim", "lineage"}
API_KEYS = {"stage", "model", "status", "duration_seconds", "ended_at_utc", "response_id", "response_model",
            "finish_reason", "completion_status"}
USAGE_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
ALLOWED_STATES = {"pending_human_review", "pending_automatic_review", "variant_candidate"}
ROOT_FILES = {"batch.json", "summary.json", "excluded_candidates.json", "sources.json", "api_calls.jsonl", "REPORT.md"}
CANDIDATE_FILES = {"spec.json", "state.json", "operator/task_card.json", "operator/validation.json", "operator/review.json", "operator/DATA_BINDING_PROMPT.md"}


def _schema_keys(spec, root):
    allowed = set(read_json(Path(root) / "schemas/task_spec.v2.schema.json")["properties"])
    # Existing curated examples may carry these documented, non-operative labels.
    allowed.update({"sample_id", "seed_id", "status", "provenance", "validation_notes"})
    if set(spec) - allowed:
        raise SnapshotError("SNAPSHOT_UNKNOWN_SPEC_FIELDS")
    return _safe(spec)


def _allowed_name(name):
    from finlab.tasks import PUBLIC_FILES
    if name in ROOT_FILES:
        return True
    parts = PurePosixPath(name).parts
    if len(parts) < 3 or parts[0] != "candidates" or not IDENTIFIER.fullmatch(parts[1]):
        return False
    rest = "/".join(parts[2:])
    return (rest in CANDIDATE_FILES or (len(parts) == 4 and parts[2] == "public" and parts[3] in PUBLIC_FILES)
            or (len(parts) == 5 and parts[2:4] == ("operator", "review_history")
                and re.fullmatch(r"(?:[a-f0-9]{16}|[a-f0-9]{64})\.json", parts[4]) is not None))


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise SnapshotError("SNAPSHOT_INVALID_IDENTIFIER")
    return value


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _safe(value):
    """Reject accidental secrets/mapping metadata without reading any environment."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in FORBIDDEN_KEYS:
                raise SnapshotError("SNAPSHOT_PRIVATE_FIELD")
            _safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child)
    elif isinstance(value, str):
        if CREDENTIAL.search(value):
            raise SnapshotError("SNAPSHOT_CREDENTIAL_LIKE_CONTENT")
        if re.search(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|file://|(?:^|[\s\"'])/(?:home|Users|mnt|tmp)/)", value):
            raise SnapshotError("SNAPSHOT_LOCAL_PATH")
    return value


def _project(value, keys):
    if not isinstance(value, dict):
        raise SnapshotError("SNAPSHOT_EXPECTED_OBJECT")
    return {key: value[key] for key in keys if key in value}


def _review(value):
    output = _project(value, REVIEW_KEYS)
    if "comparisons" in value:
        output["comparisons"] = [_project(row, {"task_id", "similarities", "differences"})
                                 for row in value["comparisons"]]
    return _safe(output)


def _validation(value):
    output = _project(value, VALIDATION_KEYS)
    dedup = value.get("dedup")
    if isinstance(dedup, dict):
        clean = _project(dedup, {"fingerprint", "classification", "exact_matches", "guarantee"})
        clean["nearest"] = [{**_project(row, {"task_id", "score", "exact_structure", "status"}),
                             "spec": _project(row.get("spec", {}), SUMMARY_KEYS)} for row in dedup.get("nearest", [])]
        output["dedup"] = clean
    return _safe(output)


def _api_record(value):
    output = _project(value, API_KEYS)
    for key, item in list(output.items()):
        if key == "status":
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise SnapshotError("SNAPSHOT_INVALID_API_STATUS")
        elif key == "duration_seconds":
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item < 0:
                raise SnapshotError("SNAPSHOT_INVALID_API_DURATION")
        elif not isinstance(item, str) or len(item) > 512 or "\n" in item or "\r" in item:
            raise SnapshotError("SNAPSHOT_INVALID_API_METADATA")
    usage = value.get("usage")
    if isinstance(usage, dict):
        output["usage"] = {k: v for k, v in usage.items() if k in USAGE_KEYS and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        for group in ("prompt_tokens_details", "completion_tokens_details"):
            if isinstance(usage.get(group), dict):
                output["usage"][group] = {k: v for k, v in usage[group].items() if k in {
                    "cached_tokens", "audio_tokens", "reasoning_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens"}
                    and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
    return _safe(output)


def _content_hash(public):
    from finlab.tasks import PUBLIC_FILES
    return digest({name: public[name].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                   for name in sorted(PUBLIC_FILES - {"task_input.draft.json"})})


def _regular(path, base):
    path, base = Path(path), Path(base).resolve()
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base):
        raise SnapshotError("SNAPSHOT_UNSAFE_FILE")
    parent = path.parent
    while parent != base:
        if parent.is_symlink():
            raise SnapshotError("SNAPSHOT_UNSAFE_FILE")
        parent = parent.parent
    return path.read_bytes()


def _review_current(state, check, review):
    return (state.get("model_review_status") not in {"not_run", "stale"}
            and review.get("mode") == "model_api" and state.get("model_review_status") == review.get("status")
            and all(review.get(k) == check.get(k) and isinstance(check.get(k), str)
                    for k in ("spec_hash", "context_hash", "content_hash")))


def snapshot(root, batch_id, destination=None):
    """Publish a candidate/evidence copy without touching registry or approvals."""
    root = Path(root).resolve()
    batch_id = _id(batch_id)
    batch = root / "runs" / batch_id
    if not batch.is_dir() or batch.is_symlink():
        raise SnapshotError("SNAPSHOT_BATCH_MISSING")
    if (batch / ".api.lock").exists():
        raise SnapshotError("SNAPSHOT_BATCH_ACTIVE")
    dest = Path(destination).resolve() if destination is not None else root / "examples/generated" / batch_id
    if dest.exists():
        raise SnapshotError("SNAPSHOT_DESTINATION_EXISTS")
    if dest == root or dest.is_relative_to(batch) or batch.is_relative_to(dest):
        raise SnapshotError("SNAPSHOT_INVALID_DESTINATION")
    metadata = read_json(batch / "batch.json")
    if metadata.get("id") != batch_id:
        raise SnapshotError("SNAPSHOT_BATCH_ID_MISMATCH")
    batch_before = (batch / "batch.json").read_bytes()
    from finlab.tasks import PUBLIC_FILES
    files, retained, excluded = {}, [], []
    status_counts, review_counts = Counter(), Counter()
    all_static_passed, current_reviews, current_distinct = 0, 0, 0
    source_ids = set()
    for folder in sorted((batch / "candidates").iterdir()):
        cid = _id(folder.name)
        reason = "snapshot_candidate_invalid"
        try:
            if folder.is_symlink() or not folder.is_dir():
                raise SnapshotError("SNAPSHOT_UNSAFE_CANDIDATE")
            spec = json.loads(_regular(folder / "spec.json", folder).decode("utf-8-sig"))
            state = json.loads(_regular(folder / "state.json", folder).decode("utf-8-sig"))
            check = json.loads(_regular(folder / "operator/validation.json", folder).decode("utf-8-sig"))
            review = json.loads(_regular(folder / "operator/review.json", folder).decode("utf-8-sig"))
            if state.get("id") != cid or state.get("batch_id") != batch_id:
                raise SnapshotError("SNAPSHOT_CANDIDATE_ID_MISMATCH")
            status_counts[str(state.get("status", "unknown"))] += 1
            review_counts[str(state.get("model_review_status", "unknown"))] += 1
            fresh = validate(spec, root)
            if not fresh["passed"] or check.get("passed") is not True or check.get("spec_hash") != digest(spec):
                reason = "static_validation_failed_or_stale"
                raise SnapshotError("SNAPSHOT_STATIC_VALIDATION_FAILED")
            all_static_passed += 1
            if state.get("status") not in ALLOWED_STATES:
                reason = "not_an_unapproved_candidate"
                raise SnapshotError("SNAPSHOT_CANDIDATE_STATE_EXCLUDED")
            public = folder / "public"
            if public.is_symlink() or not public.is_dir() or {p.name for p in public.iterdir()} != PUBLIC_FILES:
                reason = "public_allowlist_mismatch"
                raise SnapshotError("SNAPSHOT_PUBLIC_ALLOWLIST_MISMATCH")
            public_files = {name: _regular(public / name, folder) for name in sorted(PUBLIC_FILES)}
            content_hash = _content_hash(public_files)
            if check.get("content_hash") != content_hash:
                reason = "public_content_changed_after_check"
                raise SnapshotError("SNAPSHOT_CONTENT_HASH_MISMATCH")
            _schema_keys(spec, root)
            clean_state = _safe(_project(state, STATE_KEYS))
            clean_review, clean_check = _review(review), _validation(check)
            candidate = {"spec.json": _json(spec), "state.json": _json(clean_state),
                         "operator/task_card.json": _json({**spec, "production_state": clean_state}),
                         "operator/validation.json": _json(clean_check), "operator/review.json": _json(clean_review)}
            for name, body in public_files.items():
                _safe(body.decode("utf-8"))
                if name.endswith(".json"):
                    _safe(json.loads(body.decode("utf-8-sig")))
                candidate["public/" + name] = body
            prompt = _regular(folder / "operator/DATA_BINDING_PROMPT.md", folder)
            _safe(prompt.decode("utf-8"))
            candidate["operator/DATA_BINDING_PROMPT.md"] = prompt
            history = folder / "operator/review_history"
            if history.exists():
                if history.is_symlink() or not history.is_dir():
                    raise SnapshotError("SNAPSHOT_UNSAFE_REVIEW_HISTORY")
                for item in sorted(history.iterdir()):
                    if not re.fullmatch(r"(?:[a-f0-9]{16}|[a-f0-9]{64})\.json", item.name):
                        raise SnapshotError("SNAPSHOT_REVIEW_HISTORY_FILENAME")
                    original = json.loads(_regular(item, folder).decode("utf-8-sig"))
                    # Keep portable Windows extraction paths short. The manifest
                    # still authenticates every member with its complete SHA-256.
                    portable = "operator/review_history/" + item.stem[:16] + ".json"
                    if portable in candidate:
                        raise SnapshotError("SNAPSHOT_REVIEW_FILENAME_COLLISION")
                    candidate[portable] = _json(_review(original))
            current = _review_current(clean_state, clean_check, clean_review)
            current_reviews += int(current)
            distinct = (current and clean_review.get("status") == "passed" and clean_review.get("classification") == "distinct"
                        and clean_review.get("public_information_safe") is True and clean_review.get("long_horizon_valid") is True
                        and not clean_review.get("issues") and not clean_review.get("validation_errors"))
            current_distinct += int(distinct)
            prefix = "candidates/" + cid + "/"
            files.update({prefix + name: body for name, body in candidate.items()})
            source_ids.update(spec["source_ids"])
            retained.append({"candidate_id": cid, "family_id": spec["family_id"], "title": spec["title"],
                             "path": "candidates/" + cid, "spec_hash": digest(spec), "content_hash": content_hash,
                             "status": clean_state["status"], "model_review_status": clean_state.get("model_review_status"),
                             "classification": clean_review.get("classification", "not_run"), "review_current": current,
                             "current_independent_review_passed": distinct, "human_approved": False})
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
            # Never leak raw provider/model exception text, spec content, paths or credentials.
            excluded.append({"candidate_id": cid, "reason": reason,
                             "error_code": str(error) if isinstance(error, SnapshotError) else type(error).__name__})
    calls = []
    log = batch / "api_calls.jsonl"
    if log.exists():
        for line in _regular(log, batch).decode("utf-8-sig").splitlines():
            if line.strip():
                calls.append(_api_record(json.loads(line)))
    reserved = metadata.get("api_calls_used", 0)
    if not isinstance(reserved, int) or isinstance(reserved, bool) or reserved < len(calls):
        raise SnapshotError("SNAPSHOT_API_ACCOUNTING_MISMATCH")
    success = sum(isinstance(c.get("status"), int) and 200 <= c["status"] < 300 for c in calls)
    counts = {"source_candidates": len(retained) + len(excluded), "retained_candidates": len(retained), "excluded_candidates": len(excluded),
              "static_passed_at_snapshot": all_static_passed, "current_reviews_retained": current_reviews,
              "current_independent_reviews_passed_retained": current_distinct,
              "human_approved": 0, "candidate_state_counts": dict(status_counts), "model_review_state_counts": dict(review_counts),
              "http_attempts_reserved": reserved, "http_attempt_records": len(calls), "http_success_responses": success,
              "http_non_success_records": len(calls) - success, "unobserved_reserved_attempts": reserved - len(calls),
              "total_tokens_reported": sum(c.get("usage", {}).get("total_tokens", 0) for c in calls),
              "http_stage_counts": dict(Counter(c.get("stage", "unknown") for c in calls))}
    clean_meta = _project(metadata, {"id", "created_at", "provenance", "status", "attempted_candidates", "family_attempts"})
    clean_meta["api_profile"] = _project(metadata.get("api_profile", {}), {"provider_name", "generation_model", "review_model"})
    config = metadata.get("config", {})
    clean_meta["generation_targets"] = _project(config, {"family_quotas", "max_candidates"}) if isinstance(config, dict) else {}
    clean_meta.update({"scope": "specification_only", "human_approved": False, "data_status": "not_bound", "runtime_status": "not_connected"})
    _safe(clean_meta)
    source_cards = read_json(root / "catalog/sources.json")
    cards = [_project(card, {"id", "title", "status", "url", "verified_scope"}) for card in source_cards if card.get("id") in source_ids]
    if {c["id"] for c in cards} != source_ids:
        raise SnapshotError("SNAPSHOT_SOURCE_CARD_MISSING")
    files["batch.json"] = _json(clean_meta)
    files["summary.json"] = _json(counts)
    files["excluded_candidates.json"] = _json(excluded)
    files["sources.json"] = _json(_safe(cards))
    files["api_calls.jsonl"] = b"".join(_json(c).replace(b"\n", b" ").rstrip() + b"\n" for c in calls)
    report = ["# 模型候选批次快照", "", "本快照只供规格审阅和历史去重；候选未人工批准，数据与运行均未绑定。", "",
              "API HTTP预留尝试：" + str(reserved) + "；已记录尝试：" + str(len(calls)) + "；成功响应：" + str(success) + "。", "",
              "保留候选：" + str(len(retained)) + "；仅保留排除摘要：" + str(len(excluded)) + "；当前独立初筛通过：" + str(current_distinct) + "。", "",
              "|候选|方向|静态|模型状态|分类|当前审核|人工批准|", "|---|---|---|---|---|---|---|"]
    report += ["|{candidate_id}|{family_id}|通过|{model_review_status}|{classification}|{review_current}|否|".format(**row) for row in retained]
    report += ["", "静态通过或HTTP成功不表示独立新题、人工批准、数据可得或回测完成。原无效候选不进入本快照。"]
    files["REPORT.md"] = ("\n".join(report) + "\n").encode("utf-8")
    manifest = {"schema_version": SCHEMA, "batch_id": batch_id, "created_at": now(),
                "source": "model_candidate_batch" if metadata.get("provenance") == "model_api" else "candidate_batch",
                "human_approved": False, "private_data_included": False,
                "retained_candidates": retained, "excluded_candidates": excluded, "summary": counts,
                "files": {name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)} for name, body in sorted(files.items())}}
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".candidate-snapshot-", dir=dest.parent))
    try:
        for name, body in files.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        (temporary / "manifest.json").write_bytes(_json(manifest))
        verify_snapshot(temporary, root)
        if (batch / ".api.lock").exists() or (batch / "batch.json").read_bytes() != batch_before:
            raise SnapshotError("SNAPSHOT_SOURCE_CHANGED")
        if dest.exists():
            raise SnapshotError("SNAPSHOT_DESTINATION_EXISTS")
        os.rename(temporary, dest)
    finally:
        if temporary.exists() and temporary.parent.resolve() == dest.parent.resolve() and temporary.name.startswith(".candidate-snapshot-"):
            shutil.rmtree(temporary)
    return {"path": str(dest), "batch_id": batch_id, "integrity_verified": True, **counts}


def verify_snapshot(folder, root):
    """Return valid records or fail closed on any changed/missing/unlisted file."""
    folder, root = Path(folder), Path(root).resolve()
    try:
        if folder.is_symlink() or not folder.is_dir():
            raise SnapshotError("SNAPSHOT_UNSAFE_DIRECTORY")
        manifest = json.loads(_regular(folder / "manifest.json", folder).decode("utf-8-sig"))
        if manifest.get("schema_version") != SCHEMA or manifest.get("human_approved") is not False or manifest.get("private_data_included") is not False:
            raise SnapshotError("SNAPSHOT_INVALID_MANIFEST")
        batch_id = _id(manifest.get("batch_id"))
        expected = manifest.get("files")
        if not isinstance(expected, dict):
            raise SnapshotError("SNAPSHOT_INVALID_FILE_LIST")
        actual = set()
        for file in folder.rglob("*"):
            if file.is_symlink():
                raise SnapshotError("SNAPSHOT_UNSAFE_FILE")
            if file.is_file():
                actual.add(file.relative_to(folder).as_posix())
        if actual != set(expected) | {"manifest.json"}:
            raise SnapshotError("SNAPSHOT_FILE_LIST_MISMATCH")
        for name, record in expected.items():
            rel = PurePosixPath(name)
            if rel.is_absolute() or ".." in rel.parts or "\\" in name or not rel.parts:
                raise SnapshotError("SNAPSHOT_PATH_TRAVERSAL")
            if not _allowed_name(name):
                raise SnapshotError("SNAPSHOT_FILE_NOT_ALLOWLISTED")
            body = _regular(folder / name, folder)
            if hashlib.sha256(body).hexdigest() != record.get("sha256") or len(body) != record.get("bytes"):
                raise SnapshotError("SNAPSHOT_FILE_HASH_MISMATCH")
            _safe(body.decode("utf-8"))
            if name.endswith(".json"):
                _safe(json.loads(body.decode("utf-8-sig")))
            elif name.endswith(".jsonl"):
                for line in body.decode("utf-8-sig").splitlines():
                    if line.strip():
                        record = json.loads(line)
                        if _api_record(record) != record:
                            raise SnapshotError("SNAPSHOT_API_LOG_NOT_ALLOWLISTED")
        summary = read_json(folder / "summary.json")
        exclusions = read_json(folder / "excluded_candidates.json")
        if summary != manifest.get("summary") or exclusions != manifest.get("excluded_candidates"):
            raise SnapshotError("SNAPSHOT_SUMMARY_CHANGED")
        candidate_rows = manifest.get("retained_candidates")
        if not isinstance(candidate_rows, list) or not isinstance(exclusions, list):
            raise SnapshotError("SNAPSHOT_INVALID_CANDIDATE_LIST")
        if (summary.get("retained_candidates") != len(candidate_rows)
                or summary.get("excluded_candidates") != len(exclusions)
                or summary.get("source_candidates") != len(candidate_rows) + len(exclusions)
                or summary.get("human_approved") != 0):
            raise SnapshotError("SNAPSHOT_SUMMARY_COUNTS_CHANGED")
        calls = [json.loads(line) for line in (folder / "api_calls.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        successful = sum(isinstance(c.get("status"), int) and 200 <= c["status"] < 300 for c in calls)
        reserved = summary.get("http_attempts_reserved")
        if (not isinstance(reserved, int) or isinstance(reserved, bool) or reserved < len(calls)
                or summary.get("http_attempt_records") != len(calls)
                or summary.get("http_success_responses") != successful
                or summary.get("http_non_success_records") != len(calls) - successful
                or summary.get("unobserved_reserved_attempts") != reserved - len(calls)
                or summary.get("total_tokens_reported") != sum(c.get("usage", {}).get("total_tokens", 0) for c in calls)):
            raise SnapshotError("SNAPSHOT_API_COUNTS_CHANGED")
        from finlab.tasks import PUBLIC_FILES
        records, ids = [], set()
        current_reviews = current_distinct = 0
        for row in candidate_rows:
            cid = _id(row.get("candidate_id"))
            if cid in ids or row.get("path") != "candidates/" + cid or row.get("human_approved") is not False:
                raise SnapshotError("SNAPSHOT_INVALID_CANDIDATE_LIST")
            ids.add(cid)
            path = folder / "candidates" / cid
            spec, state = read_json(path / "spec.json"), read_json(path / "state.json")
            check, review = read_json(path / "operator/validation.json"), read_json(path / "operator/review.json")
            _schema_keys(spec, root)
            if state != _project(state, STATE_KEYS) or review != _review(review) or check != _validation(check):
                raise SnapshotError("SNAPSHOT_OPERATOR_RECORD_NOT_ALLOWLISTED")
            if not validate(spec, root)["passed"] or digest(spec) != row.get("spec_hash") or digest(spec) != check.get("spec_hash"):
                raise SnapshotError("SNAPSHOT_SPEC_CHANGED")
            if state.get("id") != cid or state.get("batch_id") != batch_id or state.get("status") not in ALLOWED_STATES or state.get("status") != row.get("status"):
                raise SnapshotError("SNAPSHOT_STATE_CHANGED")
            public = path / "public"
            if {p.name for p in public.iterdir()} != PUBLIC_FILES:
                raise SnapshotError("SNAPSHOT_PUBLIC_ALLOWLIST_MISMATCH")
            content = _content_hash({name: _regular(public / name, folder) for name in PUBLIC_FILES})
            if content != row.get("content_hash") or content != check.get("content_hash") or check.get("passed") is not True:
                raise SnapshotError("SNAPSHOT_CONTENT_HASH_MISMATCH")
            current = _review_current(state, check, review)
            distinct = (current and review.get("status") == "passed" and review.get("classification") == "distinct"
                        and review.get("public_information_safe") is True and review.get("long_horizon_valid") is True
                        and not review.get("issues") and not review.get("validation_errors"))
            current_reviews += int(current)
            current_distinct += int(distinct)
            if (bool(row.get("review_current")) != current or bool(row.get("current_independent_review_passed")) != distinct
                    or row.get("model_review_status") != state.get("model_review_status")
                    or row.get("classification") != review.get("classification", "not_run")):
                raise SnapshotError("SNAPSHOT_REVIEW_STATUS_CHANGED")
            if read_json(path / "operator/task_card.json") != {**spec, "production_state": state}:
                raise SnapshotError("SNAPSHOT_TASK_CARD_CHANGED")
            records.append({"task_id": cid, "spec": spec, "status": state["status"],
                            "source": "distributed_snapshot", "snapshot_batch_id": batch_id,
                            "model_review_status": state.get("model_review_status"), "human_approved": False})
        candidate_dirs = {p.name for p in (folder / "candidates").iterdir()} if (folder / "candidates").exists() else set()
        if candidate_dirs != ids:
            raise SnapshotError("SNAPSHOT_CANDIDATE_LIST_MISMATCH")
        if (summary.get("current_reviews_retained") != current_reviews
                or summary.get("current_independent_reviews_passed_retained") != current_distinct):
            raise SnapshotError("SNAPSHOT_REVIEW_COUNTS_CHANGED")
        return records
    except SnapshotError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        raise SnapshotError("SNAPSHOT_INVALID_CONTENT:" + type(error).__name__) from None


def distributed_records(root):
    root = Path(root).resolve()
    base = root / "examples/generated"
    if not base.exists():
        return []
    if base.is_symlink() or not base.is_dir():
        raise SnapshotError("SNAPSHOT_UNSAFE_ROOT")
    records = {}
    for folder in sorted(base.iterdir()):
        if folder.name.startswith(".candidate-snapshot-"):
            continue
        if not folder.is_dir() or folder.is_symlink():
            raise SnapshotError("SNAPSHOT_UNEXPECTED_ENTRY")
        for row in verify_snapshot(folder, root):
            cid = row["task_id"]
            if cid in records and digest(records[cid]["spec"]) != digest(row["spec"]):
                raise SnapshotError("SNAPSHOT_CANDIDATE_ID_COLLISION")
            records.setdefault(cid, row)
    return list(records.values())
