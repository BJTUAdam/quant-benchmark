"""读取单条轨迹的结果文件并汇总整个批次。只读，不改动任何运行目录。"""
from __future__ import annotations

import csv
import datetime
import json
import os
import pathlib
import uuid

import spec as specmod


def load(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def iso(value):
    if value is None:
        return None
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat(timespec="seconds")


def run_dir_of(spec, run_id):
    return os.path.join(spec["batch_dir"], "runs", run_id)


def summarize(run_dir):
    """从 state.json / summary.json / stages 里取状态、最终指标和模型用量。"""
    run_dir = pathlib.Path(run_dir)
    state = load(run_dir / "state.json") or {}
    summary = load(run_dir / "summary.json") or {}
    final = summary.get("final_continuous_account_metrics") or {}
    usage = {"model_calls": 0, "research_agent_calls": 0, "user_agent_calls": 0,
             "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def add_tokens(item):
        if not isinstance(item, dict):
            return
        prompt = item.get("prompt_tokens")
        completion = item.get("completion_tokens")
        prompt = prompt if type(prompt) is int else 0
        completion = completion if type(completion) is int else 0
        total = item.get("total_tokens")
        usage["prompt_tokens"] += prompt
        usage["completion_tokens"] += completion
        usage["total_tokens"] += total if type(total) is int else prompt + completion

    stages = run_dir / "stages"
    if stages.is_dir():
        for path in sorted(stages.glob("*.json")):
            stage = load(path) or {}
            actor = stage.get("usage") or []
            user = stage.get("user_agent") or {}
            usage["research_agent_calls"] += stage.get("calls", len(actor))
            usage["user_agent_calls"] += user.get("model_calls", 0)
            for item in actor:
                add_tokens(item)
            for field in ("events", "submission_reviews"):
                for event in user.get(field, []):
                    add_tokens(event.get("usage"))
    usage["model_calls"] = usage["research_agent_calls"] + usage["user_agent_calls"]
    return {
        "run_state": state.get("status"),
        "segments": state.get("segments"),
        "synthetic": state.get("synthetic"),
        "run_error": state.get("last_error"),
        "total_return": final.get("total_return"),
        "excess_return": final.get("excess_return"),
        "max_drawdown": final.get("max_drawdown"),
        "final_equity": final.get("final_equity"),
        "usage": usage,
    }


def collect(spec, store):
    rows = []
    for row in store.all():
        run_dir = run_dir_of(spec, row["run_id"])
        info = summarize(run_dir) if os.path.isdir(run_dir) else {}
        html = os.path.join(run_dir, "trajectory.html")
        zipped = os.path.join(run_dir, "public_results.zip")
        task = json.loads(row["task"])
        history = json.loads(row["history"])
        attempts = {attempt["run_id"]: attempt for attempt in history}
        attempts.setdefault(row["run_id"], {"run_id": row["run_id"], "attempt": row["attempt"]})
        for run_id, attempt in attempts.items():
            attempt["usage"] = (info if run_id == row["run_id"] else summarize(run_dir_of(spec, run_id))).get("usage", {})
        rows.append({
            "job_id": row["job_id"], "status": row["status"], "attempt": row["attempt"],
            "attempt_limit": row["attempt_limit"], "run_id": row["run_id"],
            "task": task.get("asset") or os.path.basename(task.get("binding", "")) or task.get("kind"),
            "task_kind": task.get("kind"),
            "started_at": iso(row["started_at"]), "finished_at": iso(row["finished_at"]),
            "exit_code": row["exit_code"], "error": row["error"],
            "run_dir": run_dir if os.path.isdir(run_dir) else None,
            "trajectory_html": html if os.path.isfile(html) else None,
            "public_results": zipped if os.path.isfile(zipped) else None,
            "history": history,
            "attempts": list(attempts.values()),
            **info,
        })
    return rows


def aggregate(rows):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    done = [r for r in rows if r["status"] == "completed" and type(r.get("total_return")) in (int, float)]
    out = {"total": len(rows), "by_status": counts}
    if done:
        returns = [r["total_return"] for r in done]
        out["completed_mean_total_return"] = sum(returns) / len(returns)
        out["completed_min_total_return"] = min(returns)
        out["completed_max_total_return"] = max(returns)
    for key in ("total_tokens", "model_calls", "research_agent_calls", "user_agent_calls"):
        out[key] = sum(attempt["usage"].get(key, 0) for row in rows for attempt in row["attempts"])
    return out


CSV_FIELDS = ["job_id", "status", "attempt", "run_id", "task", "segments", "total_return", "excess_return",
              "max_drawdown", "final_equity", "model_calls", "total_tokens", "error", "run_dir",
              "trajectory_html"]


def write_report(spec, store):
    rows = collect(spec, store)
    document = {"batch_id": spec["batch_id"], "generated_at": iso(datetime.datetime.now().timestamp()),
                "summary": aggregate(rows), "jobs": rows}
    specmod.write_json(os.path.join(spec["batch_dir"], "report.json"), document)
    path = os.path.join(spec["batch_dir"], "report.csv")
    scratch = path + "." + uuid.uuid4().hex
    try:
        with open(scratch, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({**{k: row.get(k) for k in CSV_FIELDS},
                    **{key: sum(a["usage"].get(key, 0) for a in row["attempts"])
                       for key in ("model_calls", "total_tokens")}})
        os.replace(scratch, path)
    finally:
        pathlib.Path(scratch).unlink(missing_ok=True)
    return document, os.path.join(spec["batch_dir"], "report.json"), path
