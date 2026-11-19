"""Registered, sequential multi-model experiment scheduler. No credentials in files.

init/plan/status/report need no API key. run invokes worker.py under this Python.
The worker dispatches the current environment controller or historical harness
in private run roots.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import importlib.metadata
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench

SOURCES = ("autonomous_harness.py", "environment_controller.py", "agent_view.py", "user_agent.py", "research_evidence.py", "market_view.py", "model_client.py", "agent_tools.py", "strategy_guard.py", "skill_registry.py", "mcp_client.py", "mcp_requirements.txt", "skills/__init__.py", "skills/_workflow.py", "skills/mcp.py", "skills/data_access/__init__.py", "skills/data_access/SKILL.md", "skills/data_access/scripts/checklist.py", "skills/backtesting/__init__.py", "skills/backtesting/SKILL.md", "skills/backtesting/scripts/checklist.py", "skills/strategy_evaluation/__init__.py", "skills/strategy_evaluation/SKILL.md", "skills/strategy_evaluation/scripts/checklist.py", "skills/news_sentiment/__init__.py", "skills/news_sentiment/SKILL.md", "skills/news_sentiment/scripts/checklist.py",
           "research_runner.py", "runtime_environment.py", "conversation.py", "usage_ledger.py", "docker_sandbox.py",
           "simulator_host.py", "strategy_service.py",
           "audit_formal_run.py", "runtime_requirements.txt")
SHARED = ("strategy.py", "RESEARCH.md", "CHANGELOG.md")
MODEL_KEYS = {"id", "provider", "base_url", "model", "api_key_env", "thinking", "reasoning_effort",
              "output_token_parameter", "context_window_tokens", "context_margin_tokens", "cost_control"}
POLICY_KEYS = {"context_mode", "max_model_calls", "max_tokens", "timeout_seconds"}
SANDBOX_KEYS = {"execution_backend", "docker_image"}
SKILL_KEYS = {"skills", "mcp_servers"}
AGENT_LIMIT_KEYS = {"stage_warning_seconds", "stage_urgent_seconds", "stage_hard_seconds",
                    "max_research_calls", "max_strategy_evaluations", "max_sweep_evaluations",
                    "research_timeout_seconds", "reserved_final_evaluations"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with scratch.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(scratch, path)
    finally:
        if scratch.exists():
            scratch.unlink()


@contextlib.contextmanager
def exclusive(folder):
    """OS releases this advisory lock after crashes; never delete a lock to resume."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ".scheduler.lock"
    with path.open("a+b") as f:
        if path.stat().st_size == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise bench.Invalid("此批次已有进程运行；不要删除锁文件") from None
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def validate_spec(spec):
    allowed = {"schema_version", "batch_id", "mode", "project_root", "package_root", "task", "case_id",
               "track", "seed_run_id", "repeats", "policy", "models"}
    bench.require(not set(spec) - allowed and spec.get("schema_version") == 1, "批次配置存在未知字段或版本错误")
    bench.require(re.fullmatch(r"[a-z][a-z0-9-]{0,11}", spec.get("batch_id", "")), "batch_id 须为字母开头的 1—12 位小写字母/数字/连字符")
    bench.require(spec.get("mode") in ("fake", "real"), "必须明确 mode=fake 或 real")
    bench.require(type(spec.get("repeats")) is int and 1 <= spec["repeats"] <= 100, "repeats 须为 1—100")
    bench.require(spec.get("track") in ("maintenance_shared_v1", "end_to_end"), "未知赛道")
    bench.require(bool(spec.get("seed_run_id")) == (spec["track"] == "maintenance_shared_v1"), "共用 v1 赛道须指定 seed_run_id；独立研发赛道不能指定")
    if spec.get("seed_run_id"):
        bench.identifier(spec["seed_run_id"])
    policy = spec.get("policy", {})
    bench.require(POLICY_KEYS <= set(policy) and
                  not set(policy) - POLICY_KEYS - {"interaction_protocol", "user_agent"} - SANDBOX_KEYS - AGENT_LIMIT_KEYS - SKILL_KEYS,
                  "policy 须填写四项公共运行设置，可额外登记交互协议和执行后端")
    if SKILL_KEYS & set(policy):
        sys.path.insert(0, str(HERE.parent / "project/demo_runner"))
        from skill_registry import normalize_skills
        try:
            policy["skills"] = normalize_skills(policy.get("skills"), policy.get("mcp_servers"))
        except ValueError as exc:
            raise bench.Invalid(str(exc)) from None
        policy.pop("mcp_servers", None)
    bench.require(policy.get("interaction_protocol", "legacy") in ("legacy", "bundled_review_v1", "guided_delivery_v1", "guided_delivery_v2", "environment_feedback_v1"),
                  "未知复盘交互协议")
    bench.require(policy.get("interaction_protocol") != "environment_feedback_v1" or spec["track"] == "end_to_end",
                  "环境反馈协议须由模型独立研发初始策略")
    if "user_agent" in policy:
        ua = policy["user_agent"]
        bench.require(policy.get("interaction_protocol") == "environment_feedback_v1",
                      "User Agent 仅支持环境反馈协议")
        sys.path.insert(0, str(HERE.parent / "project/demo_runner"))
        from user_agent import validate_config, validate_model_budget
        try:
            validate_config(ua)
        except ValueError as exc:
            raise bench.Invalid(str(exc)) from None
    backend = policy.get("execution_backend", "bubblewrap")
    bench.require(backend in ("bubblewrap", "docker"), "未知执行后端")
    bench.require(policy.get("interaction_protocol") != "environment_feedback_v1" or backend == "docker",
                  "环境反馈协议要求 Docker 执行后端")
    if policy.get("interaction_protocol") == "environment_feedback_v1":
        policy.setdefault("reserved_final_evaluations", 2)
        bench.require(AGENT_LIMIT_KEYS <= set(policy), "环境反馈协议须登记研发时间和工具预算")
        warning, urgent, hard = (policy.get("stage_warning_seconds"), policy.get("stage_urgent_seconds"),
                                 policy.get("stage_hard_seconds"))
        bench.require(all(type(v) is int for v in (warning, urgent, hard)) and
                      60 <= warning < urgent < hard <= 14400, "研发时间提醒和硬上限无效")
        for key, lower, upper in (("max_research_calls", 1, 100),
                                  ("max_strategy_evaluations", 1, 200),
                                  ("max_sweep_evaluations", 1, 50),
                                  ("research_timeout_seconds", 10, 300)):
            bench.require(type(policy.get(key)) is int and lower <= policy[key] <= upper,
                          "非法研发工具预算：" + key)
        bench.require(policy["max_sweep_evaluations"] <= policy["max_strategy_evaluations"],
                      "单次 sweep 上限不能超过总策略评估额度")
        bench.require(type(policy["reserved_final_evaluations"]) is int and
                      1 <= policy["reserved_final_evaluations"] <= policy["max_strategy_evaluations"],
                      "最终验证预留须为 1 到总评估额度之间的整数")
        bench.require(policy["research_timeout_seconds"] < policy["stage_hard_seconds"],
                      "单次研究超时须小于研发硬时间上限")
    if backend == "docker":
        image = policy.get("docker_image")
        bench.require(isinstance(image, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image),
                      "Docker 后端须登记不可变的 sha256 镜像 ID")
    else:
        bench.require("docker_image" not in policy, "bubblewrap 后端不能登记 Docker 镜像")
    bench.require(policy["context_mode"] == "fresh", "当前批量适配器支持 fresh 文件交接模式；continuous 须接入专用审计后开放")
    bench.require(spec["track"] != "maintenance_shared_v1" or policy["context_mode"] == "fresh", "原 Harness 共用 v1 只支持 fresh")
    for key, lower, upper in (("max_model_calls", 1, 200), ("max_tokens", 128, 131072), ("timeout_seconds", 1, 600)):
        bench.require(type(policy[key]) is int and lower <= policy[key] <= upper, "非法公共设置：" + key)
    models = spec.get("models", [])
    bench.require(isinstance(models, list) and models, "至少登记一个模型")
    ids = []
    for model in models:
        if "user_agent" in policy:
            try:
                validate_model_budget({**model, "user_agent": policy["user_agent"]})
            except ValueError as exc:
                raise bench.Invalid(str(exc)) from None
        bench.require(not set(model) - MODEL_KEYS, "模型配置存在未知字段；禁止明文密钥或 key_file")
        mid = model.get("id", "")
        bench.require(re.fullmatch(r"[a-z][a-z0-9-]{0,11}", mid), "model id 须为 1—12 位小写标识符")
        ids.append(mid)
        bench.require(model.get("provider") in ("openai_compatible", "anthropic"), "未支持的模型协议")
        url = urlparse(model.get("base_url", ""))
        bench.require(url.hostname and not (url.username or url.password or url.query or url.fragment), "API 地址不能携带凭据或查询参数")
        bench.require(url.scheme == "https" or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")), "API 地址须为 HTTPS，本地服务可用 HTTP")
        bench.require(isinstance(model.get("model"), str) and model["model"].strip(), "缺少 model")
        bench.require(re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", model.get("api_key_env", "")), "仅支持 api_key_env 环境变量名，不接收密钥值")
        bench.require(model.get("thinking") in (None, "enabled", "disabled"), "未知 thinking 设置")
        bench.require(model.get("reasoning_effort") in (None, "none", "low", "high", "max"), "未知 reasoning_effort 设置")
        bench.require(model.get("output_token_parameter") in (None, "max_tokens", "max_completion_tokens"), "未知输出上限参数")
        if model.get("output_token_parameter"):
            bench.require(model["provider"] == "openai_compatible", "输出上限参数只用于兼容接口")
        if model.get("thinking") or model.get("reasoning_effort"):
            bench.require(model["provider"] == "openai_compatible", "当前 Anthropic 适配层不支持此推理参数")
        if policy["context_mode"] == "continuous":
            window = model.get("context_window_tokens")
            margin = model.get("context_margin_tokens", 4096)
            bench.require(type(window) is int and 1024 <= window <= 2000000 and type(margin) is int and margin >= 0 and window > margin + policy["max_tokens"], "持续上下文窗口或余量不合法")
        if model.get("cost_control") is not None:
            cost = model["cost_control"]
            bench.require(isinstance(cost, dict) and set(cost) == {"budget_cny", "input_cny_per_million", "output_cny_per_million", "input_token_ceiling", "price_reference"}, "费用控制须显式填写原适配层所需五项配置")
            bench.require(model["provider"] == "openai_compatible" and model.get("output_token_parameter") == "max_completion_tokens", "费用控制须使用包含思考的 max_completion_tokens")
            for key in ("budget_cny", "input_cny_per_million", "output_cny_per_million", "input_token_ceiling"):
                bench.require(bench.number(cost[key], key) > 0, "费用规则数值须为正")
        if spec["mode"] == "fake":
            bench.require(url.hostname == "example.test", "假模型只能使用 example.test 占位地址")
    bench.require(len(ids) == len(set(ids)), "模型 id 重复")
    return spec


def resolve_spec(path):
    spec = validate_spec(bench.read(path))
    for key in ("project_root", "package_root", "task"):
        bench.require(isinstance(spec.get(key), str), "缺少路径：" + key)
        spec[key] = str((path.parent / spec[key]).resolve())
    task, case = bench.load_task(pathlib.Path(spec["task"]), spec["case_id"])
    splits = bench.read(pathlib.Path(spec["package_root"]) / "data/splits.json")
    bench.require(all(splits.get(k) == case[k] for k in ("num_days", "research", "segments")), "任务登记与原包分段不一致")
    bench.require(case["scored_days"] == case["num_days"] - case["research"][1], "计分天数与题目切分不一致")
    return spec, task, case


def snapshot(spec):
    project, package = pathlib.Path(spec["project_root"]), pathlib.Path(spec["package_root"])
    manifest = bench.read(package / "MANIFEST.json")
    sources = [project / "demo_runner" / name for name in SOURCES]
    if spec["policy"].get("skills"):
        from skills.mcp import configured_docs
        sources += configured_docs(spec["policy"]["skills"], project / "demo_runner")
    sources += [HERE / name for name in ("batch.py", "worker.py", "bench.py")]
    if spec["policy"].get("execution_backend") == "docker":
        sources.append(project / "demo_runner/docker/Dockerfile")
    sources += [pathlib.Path(spec["task"]), package / "MANIFEST.json"]
    if spec["mode"] == "fake":
        sources.append(project / "demo_runner/tests/test_autonomous.py")
    data = {}
    receipt_path = package.parent / "stage.json"
    if receipt_path.is_file():
        receipt = bench.read(receipt_path)
        source_name = receipt["source_zip"]
        if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", source_name):
            source_zip = pathlib.Path("/mnt/" + source_name[0].lower() + "/" + source_name[3:].replace("\\", "/"))
        else:
            source_zip = pathlib.Path(source_name)
        bench.require(bench.digest(source_zip) == receipt["source_sha256"], "题目源 ZIP 已改变")
        data[str(source_zip)] = receipt["source_sha256"]
        for relative, expected in receipt["staged_sha256"].items():
            path = bench.contained(package, relative)
            bench.require(not path.name.startswith(".env") and bench.digest(path) == expected,
                          "已暂存题目文件哈希不符：" + relative)
            data[str(path)] = expected
        sources += [receipt_path, HERE / "package_stage.py", HERE / "audit_new.py"]
        if spec["mode"] == "fake":
            sources.append(HERE / "fake_fixture.py")
    else:
        for relative, expected in manifest["sha256"].items():
            path = bench.contained(package, relative)
            bench.require(not path.name.startswith(".env"), "数据清单含密钥路径")
            bench.require(bench.digest(path) == expected, "任务包哈希不符：" + relative)
            data[str(path)] = expected
    seed = None
    if spec.get("seed_run_id"):
        root = project / "autonomous_runs" / spec["seed_run_id"]
        events = [(p, bench.read(p)) for p in sorted((root / "events").glob("*.json"))]
        matches = [(p, e) for p, e in events if e.get("phase") == "initial_done"]
        bench.require(len(matches) == 1, "共享 v1 来源必须有唯一 initial_done")
        event_path, event = matches[0]
        cp = bench.contained(root, event["checkpoint"])
        marker = bench.read(cp / "_COMPLETE.json")
        bench.require(event["visible_part"] == 0 and event["segments"] == 0 and marker["strategy_sha256"] == event["strategy_sha256"], "共享初始材料不是仅研发期交付")
        hashes = {name: bench.digest(cp / "workspace" / name) for name in SHARED}
        bench.require(hashes["strategy.py"] == event["strategy_sha256"], "初始策略哈希不符")
        sources += [event_path, cp / "_COMPLETE.json", *(cp / "workspace" / name for name in SHARED)]
        seed = {"source_run_id": spec["seed_run_id"], "source_checkpoint": event["checkpoint"], "files_sha256": hashes}
    return {"source_sha256": {str(p): bench.digest(p) for p in sources}, "package_files_sha256": data, "seed": seed}


def initialize(config, folder):
    bench.require(not folder.exists(), "批次目录已存在；用 run 续跑，不能重新 init")
    spec, task, case = resolve_spec(config)
    frozen = snapshot(spec)
    jobs = []
    for repeat in range(1, spec["repeats"] + 1):
        for model in spec["models"]:
            run_id = f"b-{spec['batch_id']}-{model['id']}-r{repeat:02d}"
            bench.require(len(run_id) <= 41, "run-id 超过原 Harness 上限")
            jobs.append({"run_id": run_id, "model_id": model["id"], "repeat": repeat,
                         "config": {**spec["policy"], **{k: v for k, v in model.items() if k != "id"}}})
    lock = {"schema_version": 1, "registered_utc": now(), "spec": spec, "task": task, "case": case, "runtime": runtime_identity(),
            "jobs": jobs, **frozen, "execution_policy": {
                "scheduler": "sequential", "phase_failure_retry": "next_run_command_only",
                "on_job_failure": "continue_other_jobs", "transport_attempts_per_request": 3,
                "transport_backoff_seconds": [2, 5], "http_error_retry": False,
                "research_backtest_count_limit": None,
                "initial_or_changed_strategy_requires_full_default_backtest": True,
                "isolation": ("docker_agent_code_trusted_host_evaluator" if spec["policy"].get("execution_backend") == "docker"
                              else "bubblewrap_agent_tools_and_evaluator"),
                "audit": "full_offline_replay_and_independent_metrics_per_completed_run"}}
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".register-", dir=folder.parent) as tmp:
        stage = pathlib.Path(tmp) / "batch"
        stage.mkdir()
        bench.write_json(stage / "protocol.lock.json", lock)
        bench.write_json(stage / "_REGISTERED.json", {"protocol_sha256": bench.digest(stage / "protocol.lock.json")})
        stage.rename(folder)
    return lock


def read_registration(folder):
    lock_path = folder / "protocol.lock.json"
    bench.require(bench.digest(lock_path) == bench.read(folder / "_REGISTERED.json")["protocol_sha256"], "已登记协议被改动；须新建批次")
    return bench.read(lock_path)


def load_lock(folder, full=False):
    lock = read_registration(folder)
    bench.require(lock["runtime"] == runtime_identity(), "Python/依赖版本与登记时不同；使用登记时的解释器")
    paths = dict(lock["source_sha256"])
    if full:
        paths.update(lock["package_files_sha256"])
    for name, expected in paths.items():
        bench.require(bench.digest(pathlib.Path(name)) == expected, "锁定文件已改变：" + name)
    return lock


def runtime_identity():
    versions = {}
    for name in ("numpy", "pandas"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": sys.version.split()[0], "platform": sys.platform, **versions}


def journal(folder):
    events = []
    for seq, path in enumerate(sorted((folder / "events").glob("*.json"))):
        event = bench.read(path)
        bench.require(event["seq"] == seq, "批次事件序号不连续")
        events.append(event)
    return events


def emit(folder, run_id, status, **values):
    seq = len(journal(folder))
    event = {"seq": seq, "utc": now(), "run_id": run_id, "status": status, **values}
    atomic(folder / "events" / f"{seq:06d}.json", event)
    return event


def invoke(folder, job, action):
    command = [sys.executable, "-I", str(HERE / "worker.py"), "--batch", str(folder),
               "--run-id", job["run_id"], "--action", action]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    try:
        reply = json.loads(result.stdout)
    except (ValueError, TypeError):
        # Never copy arbitrary subprocess output into the public event log.
        raise bench.Invalid("worker 未返回有效状态；exit=" + str(result.returncode)) from None
    if result.returncode or not reply.get("ok"):
        error = bench.Invalid(reply.get("error", "worker 执行失败"))
        error.state = reply.get("state")
        raise error
    return reply


def run_batch(folder, call=invoke):
    with exclusive(folder):
        lock = load_lock(folder, full=True)
        bench.require(lock["spec"]["mode"] == "fake" or sys.platform.startswith("linux"), "真实实验必须在 Linux / WSL 中运行")
        call(folder, lock["jobs"][0], "doctor")
        for job in lock["jobs"]:
            run_id = job["run_id"]
            last = next((e for e in reversed(journal(folder)) if e["run_id"] == run_id), {})
            if last.get("status") == "running":
                emit(folder, run_id, "interrupted", reason="上次调度未正常结束；从已提交 checkpoint 检查恢复")
            state = None
            try:
                state = call(folder, job, "status").get("state")
                if state is not None and state["phase"] == "summary_done":
                    call(folder, job, "finalize")
                    if last.get("status") != "completed":
                        emit(folder, run_id, "completed", state=state)
                    print(f"{run_id}: completed 7/7", flush=True)
                    continue
                if last.get("status") == "completed":
                    raise bench.Invalid("已完成实验的 checkpoint 缺失或回退，停止该实验")
                emit(folder, run_id, "running", state=state)
                if lock["spec"]["mode"] == "real":
                    bench.require(bool(os.environ.get(job["config"]["api_key_env"], "").strip()),
                                  "缺少环境变量 " + job["config"]["api_key_env"] + "；未发起模型调用")
                if state is None:
                    state = call(folder, job, "prepare")["state"]
                for _ in range(15):
                    if state["phase"] == "summary_done":
                        break
                    previous = state["phase"]
                    state = call(folder, job, "step")["state"]
                    bench.require(state["phase"] != previous, "阶段未推进，停止自动重试")
                    emit(folder, run_id, "checkpoint", state=state)
                    print(f"{run_id}: {state['phase']} {state['segments']}/7", flush=True)
                bench.require(state["phase"] == "summary_done", "阶段数超出七段实验预期")
                call(folder, job, "finalize")
                emit(folder, run_id, "completed", state=state)
            except (bench.Invalid, OSError) as error:
                state = getattr(error, "state", None) or state
                emit(folder, run_id, "failed", state=state, error=str(error)[:500])
                print(f"{run_id}: failed; checkpoint preserved", flush=True)
        return batch_report(folder)


def read_status(folder):
    """Read atomic events without taking the writer lock or running any experiment.

    Old registered results remain readable after a code upgrade; execution still
    requires load_lock() to verify the original runtime and source hashes.
    """
    lock = read_registration(folder)
    latest = {e["run_id"]: e for e in journal(folder)}
    rows = []
    for job in lock["jobs"]:
        run_id = job["run_id"]
        live_folder = folder / "jobs" / run_id / "autonomous_runs" / run_id / "live"
        candidates = sorted(live_folder.glob("*.json"), key=lambda p: p.stat().st_mtime) if live_folder.is_dir() else []
        live = None
        if candidates:
            try:
                value = bench.read(candidates[-1])
                allowed = ("turn", "status", "completed_model_calls", "active_model_call", "active_tools",
                           "active_tool", "backtests", "research_calls", "strategy_evaluations", "delivery",
                           "started_utc", "updated_utc", "elapsed_seconds", "user_agent_model_calls")
                live = {key: value[key] for key in allowed if key in value}
            except (OSError, ValueError, TypeError):
                live = {"status": "unreadable"}
        last = latest.get(run_id, {})
        rows.append({"run_id": run_id, "model_id": job["model_id"], "repeat": job["repeat"],
                     "status": last.get("status", "pending"), "state": last.get("state"),
                     "error": last.get("error"), "live_progress": live})
    return {"batch_id": lock["spec"]["batch_id"], "mode": lock["spec"]["mode"],
            "read_only": True, "execution_sources_checked": False,
            "expected_runs": len(rows), "completed": sum(r["status"] == "completed" for r in rows),
            "failed": sum(r["status"] == "failed" for r in rows), "runs": rows}


def batch_report(folder, verify_execution=True):
    lock = load_lock(folder) if verify_execution else read_registration(folder)
    events = journal(folder)
    imported = {r["run_id"]: r for r in bench.verified_records(folder / "results")}
    rows = []
    for job in lock["jobs"]:
        history = [e for e in events if e["run_id"] == job["run_id"]]
        last = history[-1] if history else {}
        result = imported.get(job["run_id"])
        rows.append({"run_id": job["run_id"], "model_id": job["model_id"], "repeat": job["repeat"],
            "status": last.get("status", "pending"), "attempts": sum(e["status"] == "running" for e in history),
            "failure_count": sum(e["status"] == "failed" for e in history), "last_state": last.get("state"),
            "last_error": last.get("error"), "metrics": result["metrics"] if result else None,
            "formal_errors": sum(result["num_errors"]) if result else None,
            "harness_fallback_count": result.get("harness_fallback_count") if result else None,
            "fully_agent_delivered": result.get("fully_agent_delivered") if result else None,
            "user_agent": result.get("user_agent") if result else None,
            "trajectory": f"results/{job['run_id']}/evidence/stages" if result else None})
    summary = {"schema_version": 1, "batch_id": lock["spec"]["batch_id"], "mode": lock["spec"]["mode"],
        "registered_protocol_sha256": bench.read(folder / "_REGISTERED.json")["protocol_sha256"],
        "expected_runs": len(rows), "completed": sum(r["status"] == "completed" for r in rows),
        "failed": sum(r["status"] == "failed" for r in rows), "runs": rows}
    atomic(folder / "batch-results.json", summary)
    lines = ["# 批次结果", "", f"模式：**{summary['mode']}**；完成 {summary['completed']}/{len(rows)}；失败 {summary['failed']}。", ""]
    if summary["mode"] == "fake":
        lines += ["假模型离线测试：数字用于验证程序，不代表 DeepSeek、Qwen 或任何真实模型的能力。", ""]
    lines += ["| 实验 | 重复 | 状态 | 累计收益 | 夏普 | 最大回撤 | 正式错误 | 历史失败次数 | 环境兜底次数 |", "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in rows:
        m = r["metrics"]
        values = f"{m['total_return']:+.2%} | {m['sharpe']:.3f} | {m['max_drawdown']:.2%}" if m else "— | — | —"
        lines.append(f"| {r['run_id']} | {r['repeat']} | {r['status']} | {values} | {r['formal_errors'] if m else '—'} | {r['failure_count']} | {r['harness_fallback_count'] if r['harness_fallback_count'] is not None else '—'} |")
    lines += ["", "相同批次锁定同一公共协议；模型特有设置在 protocol.lock.json 中逐项登记。失败不作为 0 收益，也不从预定运行数中删除。", ""]
    (folder / "batch-results.md").write_text("\n".join(lines), encoding="utf-8")
    if imported:
        bench.report(folder / "results", folder / "reports")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "init", "start", "plan", "run", "status", "report"))
    parser.add_argument("--config", type=pathlib.Path)
    parser.add_argument("--batch", type=pathlib.Path)
    args = parser.parse_args()
    try:
        if args.action == "start":
            bench.require(args.config and args.batch, "start 需要 --config 和 --batch")
            folder = args.batch.resolve()
            if folder.exists():
                lock = load_lock(folder)
                spec, _, _ = resolve_spec(args.config.resolve())
                bench.require(spec == lock["spec"], "配置已改变；原批次只能按已登记协议续跑")
            else:
                initialize(args.config.resolve(), folder)
            result = run_batch(folder)
            print(f"BATCH: {result['completed']}/{result['expected_runs']} completed; failed={result['failed']}")
            if result["failed"]:
                raise SystemExit(1)
        elif args.action in ("validate", "init"):
            bench.require(args.config, "需要 --config")
            if args.action == "validate":
                spec = validate_spec(bench.read(args.config))
                print(f"VALID: {len(spec['models'])} models x {spec['repeats']} repeats; mode={spec['mode']}")
                return
            bench.require(args.batch, "需要 --batch")
            lock = initialize(args.config.resolve(), args.batch.resolve())
            print(f"REGISTERED: {len(lock['jobs'])} runs; no model calls")
        else:
            bench.require(args.batch and args.batch.is_dir(), "需要已有 --batch 目录")
            folder = args.batch.resolve()
            if args.action == "plan":
                lock = load_lock(folder)
                for job in lock["jobs"]:
                    print(job["run_id"], job["config"]["model"])
            elif args.action == "run":
                result = run_batch(folder)
                print(f"BATCH: {result['completed']}/{result['expected_runs']} completed; failed={result['failed']}")
                if result["failed"]:
                    raise SystemExit(1)
            elif args.action == "status":
                print(json.dumps(read_status(folder), ensure_ascii=False, indent=2))
            else:
                with exclusive(folder):
                    result = batch_report(folder, verify_execution=False)
                print(json.dumps({k: result[k] for k in ("batch_id", "mode", "expected_runs", "completed", "failed")}, ensure_ascii=False))
    except (bench.Invalid, OSError, ValueError, KeyError) as error:
        print("FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
