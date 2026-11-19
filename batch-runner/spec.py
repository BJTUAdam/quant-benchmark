"""批次配置：校验、展开重复任务，并为每次尝试生成 finlab v2 轨迹配置。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import uuid

BATCH_ID = re.compile(r"[a-z][a-z0-9-]{0,7}")
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
RUN_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}")
SCHEMA = "trajectory.finlab.v2"
ASSETS = {"STOCK", "FUND", "FUTURES"}

TOP_KEYS = {
    "batch_id", "runner_root", "runner_script", "template", "out_dir", "python", "command_prefix",
    "env", "concurrency", "max_attempts", "retry_backoff_seconds", "job_timeout_seconds",
    "launch_interval_seconds", "poll_seconds", "grace_seconds", "per_job_memory_mb",
    "export_html", "jobs",
}
JOB_KEYS = {"id", "repeat", "task", "overrides"}
# 任务可以覆盖的只有模型与预算；运行目录、数据源和任务本身由批跑系统固定。
OVERRIDABLE = {
    "provider", "base_url", "model", "api_key_env", "api_key_file", "thinking", "reasoning_effort",
    "output_token_parameter", "max_tokens", "timeout_seconds", "max_model_calls",
    "stage_warning_seconds", "stage_urgent_seconds", "stage_hard_seconds",
    "max_research_calls", "max_data_queries", "max_strategy_evaluations", "max_sweep_evaluations",
    "reserved_final_evaluations", "research_timeout_seconds", "user_agent", "evidence_driven",
    "cost_control",
}


class SpecError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise SpecError(message)


def absolute(base, value):
    """相对路径以 base 为基准；不解析符号链接（venv 的 python 本身就是链接）。"""
    return os.path.abspath(os.path.join(str(base), str(value)))


def whole(value, low, high):
    return type(value) is int and low <= value <= high


def number(value, low, allow_zero=True):
    return type(value) in (int, float) and math.isfinite(value) and (value >= low if allow_zero else value > low)


def read_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SpecError(f"无法读取 {path}：{exc}") from None


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def binding_digest(task):
    if task["kind"] == "bound_task":
        try:
            return hashlib.sha256(pathlib.Path(task["binding"]).read_bytes()).hexdigest()
        except OSError:
            raise SpecError("无法读取正式题绑定文件") from None
    return None


def normalize_task(task, base, where):
    need(isinstance(task, dict), f"{where}: task 须为对象")
    kind = task.get("kind")
    if kind == "synthetic_demo":
        need(set(task) == {"kind", "asset"} and task["asset"] in ASSETS,
             f"{where}: synthetic_demo 须为 {{kind, asset}}，asset 取 STOCK/FUND/FUTURES")
        return dict(task)
    if kind == "bound_task":
        need(set(task) == {"kind", "binding"} and isinstance(task["binding"], str) and task["binding"],
             f"{where}: bound_task 须为 {{kind, binding}}")
        binding = absolute(base, task["binding"])
        need(os.path.isfile(binding), f"{where}: 找不到 binding 文件 {binding}；正式题须先完成数据冻结与绑定")
        return {"kind": kind, "binding": binding}
    raise SpecError(f"{where}: task.kind 须为 synthetic_demo 或 bound_task")


def expand_jobs(raw_jobs, base):
    need(isinstance(raw_jobs, list) and raw_jobs, "jobs 须为非空列表")
    jobs, seen = [], set()
    for position, item in enumerate(raw_jobs):
        where = f"jobs[{position}]"
        need(isinstance(item, dict) and set(item) <= JOB_KEYS and "id" in item and "task" in item,
             f"{where}: 须含 id、task，且只允许 {sorted(JOB_KEYS)}")
        repeat = item.get("repeat", 1)
        need(whole(repeat, 1, 1000), f"{where}: repeat 须为 1—1000 的整数")
        task = normalize_task(item["task"], base, where)
        digest = binding_digest(task)
        overrides = item.get("overrides", {})
        need(isinstance(overrides, dict) and set(overrides) <= OVERRIDABLE,
             f"{where}: overrides 只允许 {sorted(OVERRIDABLE)}")
        overrides = copy.deepcopy(overrides)
        if "api_key_file" in overrides:
            need(isinstance(overrides["api_key_file"], str), f"{where}: api_key_file 须为字符串")
            overrides["api_key_file"] = absolute(base, overrides["api_key_file"])
        for n in range(1, repeat + 1):
            job_id = item["id"] if repeat == 1 else f"{item['id']}-{n}"
            need(isinstance(job_id, str) and JOB_ID.fullmatch(job_id),
                 f"{where}: 任务编号 {job_id!r} 须为小写字母/数字/连字符，且不超过 20 位（含重复序号）")
            need(job_id not in seen, f"任务编号重复：{job_id}")
            need(not re.search(r"-r[0-9]+$", job_id), f"{where}: -r数字 后缀保留给重试")
            seen.add(job_id)
            core = {"task": task, "overrides": overrides}
            if digest is not None:
                core["binding_digest"] = digest
            jobs.append({"job_id": job_id, "task": task, "overrides": overrides, "binding_digest": digest,
                         "spec_hash": hashlib.sha256(canonical(core).encode("utf-8")).hexdigest()})
    return jobs


def load_template(path):
    template_dir = os.path.dirname(path)
    template = read_json(path)
    need(isinstance(template, dict) and template.get("schema_version") == SCHEMA,
         f"模板 schema_version 须为 {SCHEMA}")
    need(template.get("finlab_root") and template.get("finlab_python"), "模板须含 finlab_root 与 finlab_python")
    for key in ("run_id", "runs_root", "task"):
        template.pop(key, None)
    for key in ("finlab_root", "finlab_python", "api_key_file"):
        if template.get(key):
            template[key] = absolute(template_dir, template[key])
    return template


def load_spec(path):
    path = pathlib.Path(path).resolve()
    raw = read_json(path)
    base = str(path.parent)
    need(isinstance(raw, dict), "批次配置须为 JSON 对象")
    unknown = set(raw) - TOP_KEYS
    need(not unknown, f"批次配置含未知字段：{sorted(unknown)}")
    batch_id = raw.get("batch_id")
    need(isinstance(batch_id, str) and BATCH_ID.fullmatch(batch_id),
         "batch_id 须为小写字母开头、1—8 位的小写字母/数字/连字符")
    need(isinstance(raw.get("runner_root"), str) and isinstance(raw.get("template"), str),
         "须填写 runner_root（轨迹运行器根目录）与 template（轨迹配置模板）")
    runner_root = absolute(base, raw["runner_root"])
    script = absolute(base, raw["runner_script"]) if raw.get("runner_script") else \
        os.path.join(runner_root, "benchmark", "finlab_v2.py")
    need(os.path.isfile(script), f"找不到轨迹入口脚本：{script}")
    python = raw.get("python") or sys.executable
    need(isinstance(python, str), "python 须为字符串")
    if os.sep in python or (os.altsep and os.altsep in python):
        python = absolute(base, python)
    prefix = raw.get("command_prefix", [])
    need(isinstance(prefix, list) and all(isinstance(x, str) for x in prefix), "command_prefix 须为字符串列表")
    env = raw.get("env", {})
    need(isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()),
         "env 须为字符串键值对象")
    concurrency = raw.get("concurrency", 1)
    need(whole(concurrency, 1, 32), "concurrency 须为 1—32 的整数")
    attempts = raw.get("max_attempts", 2)
    need(whole(attempts, 1, 5), "max_attempts 须为 1—5 的整数")
    backoff = raw.get("retry_backoff_seconds", 60)
    need(number(backoff, 0), "retry_backoff_seconds 不能为负")
    timeout = raw.get("job_timeout_seconds")
    need(timeout is None or number(timeout, 0, allow_zero=False), "job_timeout_seconds 须为正数或 null")
    interval = raw.get("launch_interval_seconds", 20)
    need(number(interval, 0), "launch_interval_seconds 不能为负")
    poll = raw.get("poll_seconds", 1)
    need(number(poll, 0, allow_zero=False), "poll_seconds 须为正数")
    grace = raw.get("grace_seconds", 30)
    need(number(grace, 0), "grace_seconds 不能为负")
    memory = raw.get("per_job_memory_mb")
    need(memory is None or whole(memory, 1, 10_000_000), "per_job_memory_mb 须为正整数或 null")
    export_html = raw.get("export_html", True)
    need(type(export_html) is bool, "export_html 须为布尔值")
    out_dir = absolute(base, raw.get("out_dir", "out"))
    return {
        "path": str(path), "base": base, "batch_id": batch_id,
        "runner_root": runner_root, "runner_script": script, "python": python,
        "command_prefix": list(prefix), "env": dict(env),
        "concurrency": concurrency, "max_attempts": attempts, "retry_backoff_seconds": backoff,
        "job_timeout_seconds": timeout, "launch_interval_seconds": interval, "poll_seconds": poll,
        "grace_seconds": grace, "per_job_memory_mb": memory, "export_html": export_html,
        "out_dir": out_dir, "batch_dir": os.path.join(out_dir, batch_id),
        "template": load_template(absolute(base, raw["template"])),
        "jobs": expand_jobs(raw.get("jobs"), base),
    }


def run_id_for(spec, job_id, attempt):
    base = f"{spec['batch_id']}-{job_id}"
    run_id = base if attempt == 1 else f"{base}-r{attempt}"
    need(RUN_ID.fullmatch(run_id), f"run_id 无效：{run_id}")
    return run_id


def build_config(spec, job, attempt):
    """job 可以是展开后的任务，也可以是数据库行（只需 job_id/task/overrides）。"""
    cfg = copy.deepcopy(spec["template"])
    cfg.update(copy.deepcopy(job["overrides"]))
    cfg["run_id"] = run_id_for(spec, job["job_id"], attempt)
    cfg["runs_root"] = os.path.join(spec["batch_dir"], "runs")
    cfg["task"] = copy.deepcopy(job["task"])
    return cfg


def config_path(spec, run_id):
    return os.path.join(spec["batch_dir"], "configs", run_id + ".json")


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with scratch.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(scratch, path)
    finally:
        if scratch.exists():
            scratch.unlink()


def write_config(spec, job, attempt):
    cfg = build_config(spec, job, attempt)
    path = config_path(spec, cfg["run_id"])
    if os.path.exists(path):
        need(read_json(path) == cfg, f"运行配置已冻结：{cfg['run_id']}，请使用新批次")
    else:
        write_json(path, cfg)
    return cfg["run_id"], path
