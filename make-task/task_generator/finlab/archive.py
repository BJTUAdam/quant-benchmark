"""Explicit public episode exports. Private snapshots/checkpoints stay local."""
from __future__ import annotations
from pathlib import Path
import copy
import shutil
import tempfile
import zipfile
from .common import LabError, canonical, file_hash, read, write
from .runtime import load_episode

RESULT_FILES = {"equity.csv", "orders.csv", "trades.csv", "benchmark.csv", "metrics.json", "run_manifest.json"}


def bound_package(folder, destination):
    from .tasks import build, PUBLIC_FILES
    from .common import ROOT
    folder, binding, state, view = load_episode(folder)
    spec = binding.get("approved_spec")
    if not spec or binding["synthetic"]: raise LabError("ACCEPTED_REAL_TASK_REQUIRED")
    dest = Path(destination).resolve()
    if dest.exists(): raise LabError("ALREADY_EXISTS")
    spec = copy.deepcopy(spec)
    contract = spec["bridge_contract"]
    contract.update(virtual_start=view.mapping.shift(view.manifest["real_start"], "datetime"),
                    virtual_stop=view.mapping.shift(view.manifest["real_stop"], "datetime"),
                    symbols=view.manifest["symbols"], dataset_id=binding["dataset_id"], binding_status="frozen")
    runtime = spec["runtime_profile"]
    profile = binding["runtime"]
    for key in ("initial_cash", "frequency", "fill_policy", "resources", "resource_status", "benchmark", "settlement_mode"):
        if key in profile: runtime[key] = profile[key]
    runtime["instruments"] = copy.deepcopy(profile["instruments"])
    for item in runtime["instruments"]:
        if item.get("expiry_date"): item["expiry_date"] = view.mapping.shift(item["expiry_date"], "date")
    runtime.update(market_rules_status="bound", execution_rules="platform_private_per_event",
                   corporate_actions="platform_applied_when_due")
    with tempfile.TemporaryDirectory(prefix="finlab-public-") as tmp:
        temp = Path(tmp)
        build(ROOT, temp, spec, {"id": binding["candidate_id"], "status": "spec_approved"})
        shutil.copytree(temp / "public", dest)
    task = dest / "TASK.md"
    text = task.read_text(encoding="utf-8")
    acceptance_method = binding.get('acceptance_method', 'human')
    acceptance_novelty = binding.get('acceptance_novelty', 'approved')
    accepted_status = 'spec_accepted' if acceptance_method == 'automated' else 'spec_approved'
    acceptance_label = ('v2规格已通过程序检查和模型复核自动接纳；未经过人工审批'
                        if acceptance_method == 'automated' else 'v2规格沿用已有人工批准记录')
    text = "\n".join('> ' + acceptance_label + '；接口与规则已绑定到冻结版本，真实时间映射仅保存在平台侧。'
                       if line.startswith('> v2任务规格') else line for line in text.splitlines())
    task.write_text(text + "\n", encoding="utf-8")
    entry = read(dest / "task_input.draft.json")
    entry["initial_message"]["content_file"] = "TASK.md"
    entry.update(dataset_id=binding["dataset_id"], data_status="frozen", spec_status=accepted_status,
                 acceptance_method=acceptance_method, acceptance_novelty=acceptance_novelty,
                 human_approved=acceptance_method == 'human',
                 formal_intervals=[{k: view.mapping.shift(v, "date") if k in {"start", "stop"} else v for k,v in w.items()} for w in binding["windows"][1:]],
                 runtime_status="verified" if state["commits"] else "not_connected")
    write(dest / "task_input.draft.json", entry)
    if {p.name for p in dest.iterdir()} != PUBLIC_FILES: raise LabError("PUBLIC_ALLOWLIST_ERROR")
    return {"public_package": str(dest), "files": {p.name: file_hash(p) for p in dest.iterdir()}, "private_data_included": False}


def archive_episode(folder, destination):
    folder, binding, state, view = load_episode(folder)
    if not state["commits"]: raise LabError("NO_COMMITTED_RESULTS")
    dest = Path(destination).resolve()
    if dest.exists(): raise LabError("ALREADY_EXISTS")
    dest.parent.mkdir(parents=True, exist_ok=True)
    entries, receipts = {}, []
    for commit in state["commits"]:
        job = folder / "jobs" / commit["job_id"]
        for name in RESULT_FILES:
            rel = "public/" + name
            if rel not in commit["hashes"]: continue
            p = job / "artifacts" / rel
            if not p.is_file() or file_hash(p) != commit["hashes"][rel]: raise LabError("ARTIFACT_CHANGED")
            entries[f"segment_{commit['segment']}/{name}"] = p
        receipts.append({k: commit[k] for k in ("job_id", "segment", "submission_hash", "dataset_id", "images")})
    manifest = {"schema_version": "finlab.public_archive.v2", "episode_id": state["episode_id"],
                "completed_segments": state["completed_segment"], "status": state["status"],
                "synthetic": binding["synthetic"], "time_domain": "virtual", "receipts": receipts,
                "files": {name: file_hash(p) for name, p in entries.items()},
                "private_checkpoint": "kept_on_platform_not_included"}
    with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED) as z:
        for name, p in sorted(entries.items()): z.write(p, name)
        z.writestr("MANIFEST.json", canonical(manifest))
    return {"archive": str(dest), "sha256": file_hash(dest), "segments": state["completed_segment"], "private_data_included": False}
