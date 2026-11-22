"""Single-task finlab v2 trajectory entry point. Legacy Matrix runs are independent.

prepare stages an operator-configured episode without model calls; start drives
initial research, seven simulations and six reviews. No command builds images
or chooses operator resources. Unknown in-flight model/tool work fails closed.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import time
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
RUNNER = HERE.parent / "project" / "demo_runner"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(RUNNER))
from batch import exclusive
from model_client import load_config, ModelClient, MAX_REQUEST_ATTEMPTS
from skill_registry import SkillRegistry
from skills.mcp import configured_docs
from user_agent import UserAgent, context as user_context, render as render_user
from finlab_v2_adapter import Backend, BackendError, Tools, TOOLS, SUBMISSION, PUBLIC, read, save, digest, fingerprint, snapshot_submission

ROOT = HERE.parents[1]
PROFILE_SCRIPT = ROOT / "scripts/openai_profile.py"
MAX_FORMAL_RETRIES = 2
REPAIR_HINTS = {
    "ORDER_NOT_ACTIVE": "撤单前用当前 ctx.portfolio['open_orders'] 核对订单 ID。",
    "ORDER_LOT_SIZE": "按 runtime_profile.json 中标的的交易单位校验下单数量。",
    "STRATEGY_RPC_FAILED": "检查策略入口和回调在跨段恢复时能否处理已有账户、订单和 memory。",
}

SYSTEM = """你是金融长轨迹任务的研发 Agent。以工作区 TASK.md、DATA_API.md、SUBMISSION.md、
bridge_contract.json 和 runtime_profile.json 为准；先读取这些文件。
策略使用 finlab.sdk.Strategy；提交 strategy.py、backtest.py、params.json、RESEARCH.md，追加 CHANGELOG.md。
通过 query_data 查询当前允许的数据，通过 run_research 分析，并用
run_local_backtest 自己编写研究回测代码，在隔离环境中验证公开数据上的想法。
策略代码内使用 ctx.query；不访问上游或平台私有文件，不反推真实时间，不猜测未披露数据。
run_backtest 仅检验当前提交能否在平台执行，不返回平台研究期收益指标；
reports/segment_* 是外部正式运行的反馈。
每次修改 strategy.py、backtest.py 或 params.json 后，交付前执行一次不带参数的 run_backtest。
只更新研究记录无需重复验证执行代码。通过回测表示可运行，不保证收益最优。
每轮完成交付后，用不含工具调用的最终回复说明本轮决定。已有可用方案时及时收敛。
后续维护须兼容现有账户、未完成订单和 JSON memory。你不能推进正式段或扩大数据权限。
"""

EXPLORATION_PROMPT = """本轮可以持续探索。先从可见数据提出可检验的假设，建立基线，
再用独立的本地研究回测做对照，检查不同区间、交易成本或参数敏感性中与决策相关的风险。
复盘时结合历史研究记录，区分单段表现与可重复证据，允许有依据地保持策略。
将对照结果、限制、失败尝试和未解决问题写入 RESEARCH.md，方便后续复盘和上下文恢复。
有能够改变当前决策的疑问时继续研究；准备交付时说明证据、风险和停止探索的理由。
收到用户复核后，处理仍有决策价值的问题，再用不含工具调用的最终回复确认交付，
并在末尾单独一行写“确认交付”。"""


def config(path):
    raw = read(path)
    allowed = {"schema_version", "run_id", "runs_root", "finlab_root", "finlab_python", "task",
        "provider", "base_url", "model", "api_key_env", "api_key_file", "max_tokens", "timeout_seconds",
        "max_model_calls", "context_mode", "interaction_protocol", "execution_backend", "docker_image",
        "stage_warning_seconds", "stage_urgent_seconds", "stage_hard_seconds", "max_research_calls",
        "max_strategy_evaluations", "max_sweep_evaluations", "reserved_final_evaluations",
        "research_timeout_seconds", "max_data_queries", "user_agent", "output_token_parameter",
        "thinking", "reasoning_effort", "temperature", "cost_control", "evidence_driven", "mcp_servers", "skills"}
    if set(raw) - allowed:
        raise ValueError("v2 配置存在未知字段")
    if raw.get("schema_version") != "trajectory.finlab.v2" or "api_key" in raw:
        raise ValueError("需要 trajectory.finlab.v2 配置；密钥存放在 api_key_file 指向的私有配置")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", raw.get("run_id", "")):
        raise ValueError("run_id 无效")
    cfg = load_config(path)
    if type(cfg.get("evidence_driven", False)) is not bool:
        raise ValueError("evidence_driven 须为布尔值")
    if cfg["context_mode"] != "fresh" or cfg["interaction_protocol"] != "environment_feedback_v1" or not cfg.get("user_agent"):
        raise ValueError("v2 入口须配置 fresh、环境反馈预算和 User Agent")
    for field in ("finlab_root", "runs_root"):
        cfg[field] = str((path.parent / raw[field]).resolve())
    # A virtualenv's interpreter can be a symlink to the system binary.
    cfg["finlab_python"] = os.path.abspath(path.parent / raw["finlab_python"])
    task = cfg.get("task", {})
    if task.get("kind") == "synthetic_demo":
        if set(task) != {"kind", "asset"} or task["asset"] not in {"STOCK", "FUND", "FUTURES"}:
            raise ValueError("demo 资产须为 STOCK/FUND/FUTURES")
    elif task.get("kind") == "bound_task":
        if set(task) != {"kind", "binding"}:
            raise ValueError("真实任务须提供 binding")
        binding_path = (path.parent / task["binding"]).resolve()
        if not binding_path.is_file():
            raise ValueError("真实题尚缺少 binding.private.json；12 个草案题包不能直接正式跑轨迹")
        binding = read(binding_path)
        if binding.get("synthetic") or not binding.get("frozen_dir"):
            raise ValueError("binding.private.json 未绑定真实冻结数据")
        cfg["task"] = {**task, "binding": str(binding_path)}
    else:
        raise ValueError("task.kind 须为 synthetic_demo 或 bound_task")
    cfg["max_data_queries"] = raw.get("max_data_queries", 65)
    if type(cfg["max_data_queries"]) is not int or not 1 <= cfg["max_data_queries"] <= 200:
        raise ValueError("max_data_queries 无效")
    return cfg


def source_lock(cfg):
    own = [pathlib.Path(__file__), HERE / "batch.py", HERE / "bench.py"]
    if PROFILE_SCRIPT.is_file():
        own.append(PROFILE_SCRIPT)
    own += [RUNNER / name for name in ("finlab_v2_adapter.py", "finlab_v2_worker.py", "model_client.py",
        "user_agent.py", "agent_view.py", "usage_ledger.py", "agent_tools.py", "research_runner.py",
        "runtime_environment.py", "docker_sandbox.py", "mcp_client.py", "skill_registry.py", "mcp_requirements.txt")]
    # 锁定插件代码、离线检查脚本和中文操作规范。
    own += [p for p in (RUNNER / "skills").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".md"}]
    own += configured_docs(cfg.get("skills"), RUNNER)
    upstream = pathlib.Path(cfg["finlab_root"])
    for folder, pattern in (("finlab", "*.py"), ("taskgen", "*.py"), ("schemas", "*.json"),
                            ("catalog", "*.json")):
        own += [p for p in (upstream / folder).rglob(pattern) if p.is_file() and "__pycache__" not in p.parts]
    own += [upstream / "deployment" / name for name in
            ("Dockerfile.v2", "requirements.v2.lock", "images.lock.json")
            if (upstream / "deployment" / name).is_file()]
    if cfg["task"]["kind"] == "bound_task":
        own.append(pathlib.Path(cfg["task"]["binding"]))
    if not (upstream / "finlab/runtime.py").is_file():
        raise ValueError("finlab_root 未指向 v2 源码根目录")
    return {str(p.resolve()): digest(p) for p in sorted(set(own))}


def prepare(cfg, root):
    lock = {"protocol": "finlab_v2_agent_v1", "config": cfg, "sources": source_lock(cfg)}
    if (root / "lock.json").exists():
        if read(root / "lock.json") != lock:
            raise RuntimeError("源码或配置改变；请使用新 run_id")
        if not (root / "state.json").is_file():
            raise RuntimeError("准备过程曾中断；保留现场并使用新 run_id")
        return
    if any(p.name != ".scheduler.lock" for p in root.iterdir()):
        raise RuntimeError("运行目录非空，拒绝覆盖")
    if not os.access(cfg["finlab_python"], os.X_OK):
        raise RuntimeError("找不到可执行的 finlab_python；请先按 README 安装 FinLab 环境")
    save(root / "lock.json", lock)
    backend = Backend(cfg, root)
    status = backend.call("prepare", workspace=str(root / "workspace"), **cfg["task"])
    if status["completed_segment"] != 0:
        raise RuntimeError("新轨迹须从新 episode 开始")
    docs = root / "workspace" / "docs"
    docs.mkdir()
    # Public SDK source is safe reference material; no engine/private data copied.
    sdk = pathlib.Path(cfg["finlab_root"]) / "finlab/sdk.py"
    (docs / "sdk_reference.txt").write_bytes(sdk.read_bytes())
    meta = backend.call("status")
    save(root / "state.json", {"status": "prepared", "segments": 0,
        "binding_hash": meta["binding_hash"], "images": meta["images"],
        "synthetic": status["synthetic"], "deliveries": [], "results": []})
    save(root / "public_files.json", {p.relative_to(root / "workspace").as_posix(): digest(p)
        for p in (root / "workspace").rglob("*") if p.is_file() and p.name not in SUBMISSION})


def verify_public(root):
    for name, expected in read(root / "public_files.json").items():
        if digest(root / "workspace" / name) != expected:
            raise RuntimeError("公开任务或 SDK 参考被更改")


def initialize_test_memory(cfg, root):
    scoped = any(server.get("token_env") == "FINLAB_ISOLATED_MEMORY_TOKEN"
                 for skill in cfg.get("skills", []) if skill["name"] == "mcp" and skill.get("enabled", True)
                 for server in skill["config"]["servers"])
    marker = root / "private/memory-start.json"
    if not scoped or marker.exists() or (root / "stages").exists():
        return
    token = os.environ.get("FINLAB_ISOLATED_MEMORY_TOKEN")
    if not token:
        raise RuntimeError("缺少测试专用记忆凭证，请通过 trajectory-runner/start.sh 启动")
    plugin = ROOT / "mem-plugin"
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR")
           if key in os.environ}
    try:
        result = subprocess.run(
            [str(plugin / ".venv/bin/python"), "-I", "-B", str(PROFILE_SCRIPT),
             "reset-trajectory-memory", str(root.resolve())],
            input=token, cwd=plugin, env=env, capture_output=True, text=True, timeout=90, check=True,
        )
        deleted = int(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError("无法确认测试记忆已清空") from None
    save(marker, {"deleted": deleted})
    print(f"本次测试从空记忆开始，已清理 {deleted} 条旧记录", flush=True)


def public_prompt(root, index, backend):
    workspace = root / "workspace"
    prompt = (workspace / "TASK.md").read_text(encoding="utf-8")
    status = backend.call("status")
    prompt += "\n当前研发可见结束边界（不包含）：" + status["research_visible_stop"]
    if index:
        metrics = read(workspace / "reports" / f"segment_{index}" / "metrics.json")
        prompt += "\n最近正式阶段运行反馈：\n" + json.dumps(metrics, ensure_ascii=False)
        prompt += f"\n明细在 reports/segment_{index}/。请自主决定保留、调参或修改逻辑，并追加研究记录。"
    return prompt


def model_history(messages):
    """Keep recent complete tool batches in context and retain the full audit log."""
    size = 0
    cut = len(messages)
    for pos in range(len(messages) - 1, 1, -1):
        size += len(json.dumps(messages[pos], ensure_ascii=False))
        if messages[pos]["role"] != "tool":
            if size > 120_000 and cut < len(messages):
                break
            cut = pos
    if cut == 2:
        return messages
    return [*messages[:2], {"role": "user", "content":
        "早期对话已保存在轨迹中。需要此前实验结果时，请查阅 RESEARCH.md、CHANGELOG.md 和已保存的数据文件。"},
        *messages[cut:]]


def stage_request_config(cfg, remaining_seconds, *, final_only):
    if final_only and remaining_seconds <= 0:
        return {**cfg, "_request_attempts": 1}
    timeout = min(cfg["timeout_seconds"], max(1, math.floor(remaining_seconds)))
    # Reserve 2 + 4 seconds for backoff and one second for bookkeeping.
    attempts = MAX_REQUEST_ATTEMPTS
    while attempts > 1 and remaining_seconds < attempts * timeout + 2 ** attempts - 1:
        attempts -= 1
    return {**cfg, "timeout_seconds": timeout, "_request_attempts": attempts}


def delivery_path(root, index, retries):
    name = f"{index:02d}-retry-{retries}" if retries else f"{index:02d}"
    return root / "deliveries" / name


def repair_feedback(code):
    hint = REPAIR_HINTS.get(code, "检查策略是否符合 SUBMISSION.md 和运行配置中的约束。")
    return (f"本段正式回测未提交，错误码为 {code}。{hint}"
            "请使用当前已披露的数据修正策略，追加 CHANGELOG.md，执行 run_backtest 并重新交付。")


def queue_formal_retry(cfg, root, stage, code):
    retries = stage.setdefault("formal_retries", [])
    delay = 2 ** (len(retries) + 1)
    # Recovery must still fit in the experiment's existing tool and time budgets.
    if len(retries) >= MAX_FORMAL_RETRIES or (not cfg.get("evidence_driven") and (
            stage["calls"] + 2 > cfg["max_model_calls"] + 16
            or stage["evaluations"] >= cfg["max_strategy_evaluations"]
            or time.time() + delay >= stage["started"] + cfg["stage_hard_seconds"])):
        return False
    workspace = root / "workspace"
    if fingerprint(workspace, SUBMISSION) != stage["submission"]:
        raise RuntimeError("待修复的工作区与失败交付不一致")
    retries.append({"code": code, "submission": stage.pop("submission"),
                    "message_index": len(stage["messages"]), "not_before": time.time() + delay})
    stage["messages"].append({"role": "user", "content": repair_feedback(code)})
    stage.update(status="running", verified=None, verification_job=None,
                 before_log=(workspace / "CHANGELOG.md").read_text(encoding="utf-8"))
    for key in ("delivery", "completed", "local_backtest_evidence", "recent_actions"):
        stage.pop(key, None)
    save(root / "stages" / f"{stage['index']:02d}.json", stage)
    print(f"第 {stage['index'] + 1} 段回测失败（{code}），{delay} 秒后交给 Agent 修复"
          f"（{len(retries)}/{MAX_FORMAL_RETRIES}）", file=sys.stderr, flush=True)
    return True


def run_stage(cfg, root, index):
    backend, workspace = Backend(cfg, root), root / "workspace"
    path = root / "stages" / f"{index:02d}.json"
    exploring = cfg.get("evidence_driven", False)
    if path.exists():
        stage = read(path)
        if stage["status"] == "delivered":
            dest = delivery_path(root, index, len(stage.get("formal_retries", [])))
            if fingerprint(dest, SUBMISSION) != stage["submission"]:
                raise RuntimeError("已交付文件改变")
            return stage
        if stage["status"] == "failed" or stage.get("pending"):
            raise RuntimeError("本阶段失败或有结果未知的请求；保留现场，禁止自动重放")
    skills = SkillRegistry(cfg.get("skills"))
    model_tools = list(TOOLS) + skills.discover({tool["name"] for tool in TOOLS})
    if not path.exists():
        prompt = public_prompt(root, index, backend)
        if not exploring:
            prompt += "\n本轮正常预算见下；另有最多16次收尾回复，研究与数据查询分别计数。\n"
            prompt += json.dumps({k: cfg[k] for k in ("max_model_calls", "max_research_calls", "max_data_queries",
                "max_strategy_evaluations", "reserved_final_evaluations", "max_sweep_evaluations",
                "stage_warning_seconds", "stage_urgent_seconds", "stage_hard_seconds")}, ensure_ascii=False)
        system = SYSTEM
        if exploring:
            system = SYSTEM.replace("已有可用方案时及时收敛。", "") + "\n" + EXPLORATION_PROMPT
        system = skills.system_prompt(system)
        prior = read(root / "stages" / f"{index-1:02d}.json") if index else {}
        stage = {"index": index, "status": "running", "pending": None, "started": time.time(),
            "calls": 0, "research_calls": 0, "data_queries": 0, "evaluations": 0,
            "verified": prior.get("verified"), "verification_job": prior.get("verification_job"),
            "before_log": (workspace / "CHANGELOG.md").read_text(encoding="utf-8"),
            "prompt": prompt, "reminders": [], "usage": [],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "user_agent": {"model_calls": 0, "events": []}}
        # Validation is bound to data visibility as well as executable content.
        # A retained strategy may be delivered unchanged after a review; changed
        # executable files always need fresh validation in that review.
        stage["before_execution"] = fingerprint(workspace)
        stage["workspace_submission"] = fingerprint(workspace, SUBMISSION)
        save(path, stage)
    if stage.get("workspace_submission") != fingerprint(workspace, SUBMISSION):
        raise RuntimeError("研发工作文件与最近已保存状态不符，拒绝静默覆盖或恢复")
    retries = stage.get("formal_retries", [])
    if retries and (delay := retries[-1]["not_before"] - time.time()) > 0:
        time.sleep(delay)
    def persist():
        stage["workspace_submission"] = fingerprint(workspace, SUBMISSION)
        save(path, stage)
    tools = Tools(backend, workspace, cfg, stage, persist)
    skills.bind_core(tools.execute, {tool["name"] for tool in TOOLS})
    mc = {**cfg, "_retry_http": True, "_usage_turn": f"v2_stage_{index}",
          "_usage_ledger_path": str(root / "private" / "usage.json")}
    user = UserAgent(mc, ModelClient)
    user.state = stage["user_agent"]
    hard_calls = cfg["max_model_calls"] + 16

    def missing():
        out = [n for n in SUBMISSION if not (workspace / n).is_file() or not (workspace / n).read_text(encoding="utf-8").strip()]
        log = (workspace / "CHANGELOG.md").read_text(encoding="utf-8")
        if not log.startswith(stage["before_log"]) or len(log) <= len(stage["before_log"]):
            out.append("追加 CHANGELOG.md")
        if stage.get("verified") != fingerprint(workspace) or len(fingerprint(workspace)) != 3:
            out.append("当前执行文件的完整默认参数研发回测")
        if exploring and not stage.get("local_backtest_evidence"):
            out.append("本轮成功运行并输出结果的本地研究回测")
        return out

    def user_event(kind, absent, failed_tools=()):
        ctx = user_context(kind, stage["prompt"], absent,
            {"remaining": None if exploring else cfg["max_strategy_evaluations"] - stage["evaluations"]},
            stage["calls"], None if exploring else hard_calls-stage["calls"],
            messages=stage["messages"] if exploring else None)
        if exploring and kind == "candidate":
            ctx["public_research"] = (workspace / "RESEARCH.md").read_text(encoding="utf-8")[-6000:]
        if failed_tools:
            ctx["dialogue"]["failed_tools"] = sorted(set(failed_tools))
        record = user.begin(f"v2_{index}:{kind}:{stage['calls']}", ctx, len(stage["messages"]),
            allow_model=exploring or time.time() - stage["started"] + user.settings["timeout_seconds"] < cfg["stage_hard_seconds"])
        stage["pending"] = "user_agent"
        persist()
        stage["messages"].append(user.finish(record))
        stage["pending"] = None
        persist()

    try:
        if not user.state["events"]:
            user_event("initial" if index == 0 else "review", [])
        final_only = False
        while exploring or stage["calls"] < hard_calls:
            elapsed = time.time() - stage["started"]
            if not exploring and elapsed >= cfg["stage_hard_seconds"]:
                if missing() or final_only:
                    raise RuntimeError("STAGE_WALLCLOCK_EXHAUSTED")
                final_only = True
                stage["messages"].append({"role": "user", "content": "阶段时间已到，禁止调用工具；请立即用最终回复交付当前版本。"})
            if not exploring and not final_only and stage["calls"] == hard_calls - 6 and "final_turn_warning" not in stage["reminders"]:
                stage["reminders"].append("final_turn_warning")
                stage["messages"].append({"role": "user", "content": "还剩 6 次回复；最后一次回复将禁止调用工具，须直接用文字交付。"
                    "请现在补齐 CHANGELOG.md，并对当前三个执行文件完成不带参数的 run_backtest，之后不要再改动执行文件。"})
            if not exploring and not final_only and stage["calls"] == hard_calls - 1:
                final_only = True
                stage["messages"].append({"role": "user", "content": "这是最后一次回复，禁止调用工具；请用不含工具调用的最终回复交付当前版本。"})
            for label, threshold in (("time_warning", cfg["stage_warning_seconds"]), ("time_urgent", cfg["stage_urgent_seconds"])):
                if not exploring and elapsed >= threshold and label not in stage["reminders"]:
                    stage["reminders"].append(label)
                    stage["messages"].append({"role": "user", "content": f"时间提醒：已用 {int(elapsed)} 秒，阶段上限 {cfg['stage_hard_seconds']} 秒。请收敛并完成交付。"})
            stage["pending"] = "model"
            persist()
            remaining = cfg["stage_hard_seconds"] - (time.time() - stage["started"])
            request_cfg = mc if exploring else stage_request_config(mc, remaining, final_only=final_only)
            messages = model_history(stage["messages"]) if exploring else stage["messages"]
            reply = ModelClient(request_cfg).complete(messages, [] if final_only else model_tools)
            stage["calls"] += 1
            stage["usage"].append(reply.get("usage"))
            stage["messages"].append({"role": "assistant", **{k: v for k,v in reply.items() if k != "usage"}})
            stage["pending"] = "tools"
            persist()
            failed_tools = []
            for call in reply.get("tool_calls", []):
                if final_only or (not exploring and time.time() - stage["started"] >= cfg["stage_hard_seconds"]):
                    content = "时间上限已到，不再执行工具；请交付当前版本。"
                else:
                    if call.get("argument_error"):
                        content = call["argument_error"]
                    elif skills.handles(call["name"]):
                        timeout = 30 if exploring else min(
                            30, cfg["stage_hard_seconds"] - (time.time() - stage["started"]))
                        content = skills.execute(call["name"], call["arguments"], timeout=timeout)
                    else:
                        content = tools.execute(call["name"], call["arguments"])
                    if exploring:
                        outcome = {} if call.get("argument_error") else json.loads(content)
                        result = outcome.get("result")
                        if call.get("argument_error") or "error" in outcome or (isinstance(result, dict)
                                and (result.get("returncode", 0) != 0 or result.get("submission_errors"))):
                            failed_tools.append(call["name"])
                stage["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": content})
                persist()
            stage["pending"] = None
            points = {math.ceil(cfg["max_model_calls"] * .6), math.ceil(cfg["max_model_calls"] * .85), cfg["max_model_calls"]}
            if not exploring and stage["calls"] in points:
                stage["messages"].append({"role": "user", "content": f"已用 {stage['calls']} 次回复；正常预算 {cfg['max_model_calls']} 次，硬上限 {hard_calls} 次。请收敛，补齐交付。"})
                stage["reminders"].append("calls_" + str(stage["calls"]))
            calls = reply.get("tool_calls", [])
            absent = []
            if not calls:
                absent = missing()
                review_after = retries[-1]["message_index"] if retries else -1
                reviewed = any(e["context"]["event"] == "candidate" and e.get("message_index", 0) > review_after
                               for e in user.state["events"])
                if not absent and exploring and not reviewed:
                    user_event("candidate", [])
                    continue
                if not absent and exploring and not (reply.get("content") or "").rstrip().endswith("\n确认交付"):
                    absent = ["完成复核后，在最终回复末尾单独一行写「确认交付」"]
                if not absent:
                    checked = backend.call("check", submission=str(workspace))
                    absent = checked["errors"] if not checked["passed"] else []
                if not absent:
                    dest = delivery_path(root, index, len(retries))
                    stage["submission"] = snapshot_submission(workspace, dest)
                    stage.update(status="delivered", delivery="agent", completed=time.time())
                    persist()
                    return stage
                if not exploring and stage["evaluations"] >= cfg["max_strategy_evaluations"] and stage.get("verified") != fingerprint(workspace):
                    raise RuntimeError("EVAL_BUDGET_EXHAUSTED_WITHOUT_VALID_DELIVERY")
            if exploring:
                progress = [fingerprint(workspace, SUBMISSION), stage.get("verified"),
                            stage.get("local_backtest_evidence", {}).get("output_sha256"),
                            [[c["name"], c.get("arguments"), c.get("argument_error")] for c in calls], absent]
                signature = hashlib.sha256(json.dumps(progress, sort_keys=True).encode()).hexdigest()
                recent = (stage.get("recent_actions", []) + [signature])[-8:]
                stage["recent_actions"] = recent
                if recent.count(signature) >= 4:
                    raise RuntimeError("STAGE_REPEATED_ACTION_WITHOUT_PROGRESS")
            persist()
            if not calls:
                user_event("delivery", absent)
            elif failed_tools:
                user_event("tool_error", [], failed_tools)
        raise RuntimeError("MODEL_BUDGET_EXHAUSTED")
    except Exception as exc:
        stage.update(status="failed", error_type=type(exc).__name__)
        # Fixed local reasons are safe; model/provider bodies are never persisted.
        reason = getattr(exc, "code", None) or str(exc)
        stage["failure_reason"] = (
            reason if isinstance(reason, str) and re.fullmatch(r"[A-Z_0-9]{1,80}", reason) else "STAGE_FAILED"
        )
        persist()
        raise


def complete_segment(cfg, root, backend, state, index):
    while True:
        before = backend.call("status")
        if before["binding_hash"] != state["binding_hash"] or before["images"] != state["images"]:
            raise RuntimeError("绑定或镜像改变")
        stage_path = root / "stages" / f"{index:02d}.json"
        delivered = stage_path.exists() and read(stage_path)["status"] == "delivered"
        if not delivered and before["completed_segment"] != index:
            raise RuntimeError("平台段号与待研发阶段不一致，拒绝使用错误可见范围")
        stage = run_stage(cfg, root, index)
        dest = delivery_path(root, index, len(stage.get("formal_retries", [])))
        checked = backend.call("check", submission=str(dest))
        if not checked["passed"]:
            raise RuntimeError("交付校验失败")
        current = backend.call("status")
        if current["binding_hash"] != state["binding_hash"] or current["images"] != state["images"]:
            raise RuntimeError("绑定或镜像改变")
        if current["completed_segment"] < index:
            raise RuntimeError("平台状态落后于轨迹")
        if current["completed_segment"] == index:
            if stage.get("formal_failure"):
                raise RuntimeError(f"第 {index + 1} 段策略修复已停止（{stage['formal_failure']}）")
            try:
                backend.call("run", submission=str(dest), research=False)
            except BackendError as exc:
                if exc.code != "BACKTEST_FAILED" or not exc.strategy_code:
                    raise
                if backend.call("status")["completed_segment"] != index:
                    raise RuntimeError("正式回测提交状态改变，拒绝重试") from None
                if queue_formal_retry(cfg, root, stage, exc.strategy_code):
                    continue
                stage["formal_failure"] = exc.strategy_code
                save(stage_path, stage)
                raise RuntimeError(f"第 {index + 1} 段策略修复额度已用尽（{exc.strategy_code}）") from None
            current = backend.call("status")
        return stage, checked, current


def start(cfg, root):
    backend = Backend(cfg, root)
    state = read(root / "state.json")
    if state["status"] == "completed":
        return state
    verify_public(root)
    initialize_test_memory(cfg, root)
    for index in range(7):
        stage, checked, current = complete_segment(cfg, root, backend, state, index)
        segment = index + 1
        # Resume reconciles committed platform work, never blindly runs next segment.
        commit = current["commits"][index]
        if commit["segment"] != segment or commit["submission_hash"] != checked["submission_hash"]:
            raise RuntimeError("平台提交与交付版本不匹配")
        report = backend.call("results", job_id=commit["job_id"],
            destination=str(root / "workspace" / "reports" / f"segment_{segment}"))
        row = {"segment": segment, "job_id": commit["job_id"], "submission_hash": checked["submission_hash"],
               "metrics": report["metrics"], "public_files": report["files"]}
        if len(state["results"]) < segment:
            state["results"].append(row)
            state["deliveries"].append(stage["submission"])
        elif state["results"][index] != row:
            raise RuntimeError("已登记结果改变")
        state.pop("last_error", None)
        state.update(status="running", segments=max(state["segments"], segment))
        save(root / "state.json", state)
        print(json.dumps({"stage": "segment_completed", "segment": segment}), flush=True)
    final = read(root / "workspace/reports/segment_7/metrics.json")
    save(root / "summary.json", {"synthetic": state["synthetic"], "segments": state["results"],
         "final_continuous_account_metrics": final,
         "scope": "v2系统生成的连续账户结果；未额外运行冻结初始策略基线"})
    audit(cfg, root)
    archive = root / "public_results.zip"
    if not archive.exists():
        backend.call("archive", destination=str(archive))
    # A finalization crash may leave an archive. Verify it rather than treating
    # mere existence as evidence of a successful export.
    with zipfile.ZipFile(archive) as zipped:
        manifest = json.loads(zipped.read("MANIFEST.json"))
        current = backend.call("status")
        expected = [{k: c[k] for k in ("job_id", "segment", "submission_hash", "dataset_id", "images")}
                    for c in current["commits"]]
        if manifest.get("completed_segments") != 7 or manifest.get("receipts") != expected:
            raise RuntimeError("归档与平台提交不符")
        if set(zipped.namelist()) != set(manifest["files"]) | {"MANIFEST.json"}:
            raise RuntimeError("归档文件白名单不符")
        for name, expected_hash in manifest["files"].items():
            if hashlib.sha256(zipped.read(name)).hexdigest() != expected_hash:
                raise RuntimeError("归档文件哈希不符")
    state["archive_sha256"] = digest(archive)
    state["status"] = "completed"
    save(root / "state.json", state)
    return state


def audit(cfg, root):
    state = read(root / "state.json")
    backend = Backend(cfg, root)
    current = backend.call("status")
    if current["completed_segment"] != 7 or len(current["commits"]) != 7:
        raise RuntimeError("七段提交不完整")
    verify_public(root)
    exploring = cfg.get("evidence_driven", False)
    retry_counts = {}
    for index in range(7):
        stage = read(root / "stages" / f"{index:02d}.json")
        if stage["status"] != "delivered" or stage["delivery"] != "agent" or (
                not exploring and stage["calls"] > cfg["max_model_calls"] + 16):
            raise RuntimeError("研发阶段交付或预算记录无效")
        for field, maximum in (("research_calls", cfg["max_research_calls"]),
                ("data_queries", cfg["max_data_queries"]), ("evaluations", cfg["max_strategy_evaluations"])):
            if stage[field] < 0 or (not exploring and stage[field] > maximum):
                raise RuntimeError("工具预算记录无效")
        for event in stage["user_agent"]["events"]:
            if event["status"] != "sent" or stage["messages"][event["message_index"]] != {"role": "user", "content": render_user(event)}:
                raise RuntimeError("User Agent 消息记录不一致")
        retries = stage.get("formal_retries", [])
        if len(retries) > MAX_FORMAL_RETRIES:
            raise RuntimeError("策略修复次数记录无效")
        for attempt, retry in enumerate(retries):
            feedback = {"role": "user", "content": repair_feedback(retry["code"])}
            if (fingerprint(delivery_path(root, index, attempt), SUBMISSION) != retry["submission"]
                    or stage["messages"][retry["message_index"]] != feedback):
                raise RuntimeError("策略修复反馈或历史交付记录不符")
        if retries:
            retry_counts[str(index + 1)] = len(retries)
        events = stage["user_agent"]["events"]
        review_after = retries[-1]["message_index"] if retries else -1
        if exploring and (not stage.get("local_backtest_evidence")
                or not any(e["context"]["event"] == "candidate" and e["message_index"] > review_after for e in events)
                or stage["user_agent"]["model_calls"] != sum(e["attempted"] for e in events)):
            raise RuntimeError("缺少本轮研究回测、用户复核或调用记录")
        if not exploring and stage["user_agent"]["model_calls"] > cfg["user_agent"]["max_model_calls"]:
            raise RuntimeError("User Agent 超过预算")
        dest = delivery_path(root, index, len(retries))
        if fingerprint(dest, SUBMISSION) != stage["submission"]:
            raise RuntimeError("交付文件改变")
        if stage["verified"] != fingerprint(dest):
            raise RuntimeError("交付执行文件与验证记录不匹配")
        if stage["calls"] != sum(m["role"] == "assistant" for m in stage["messages"]):
            raise RuntimeError("模型回复计数不一致")
    save(root / "audit.json", {"status": "passed", "synthetic": state["synthetic"], "formal_retries": retry_counts,
        "checks": ["seven_platform_commits", "seven_agent_deliveries", "six_reviews",
                   "research_evidence_and_review" if exploring else "tool_budgets",
                   "user_agent_message_provenance", "submission_hashes", "public_task_unchanged"],
        "limitations": ["不替代策略语义审查", "不重放仿真", "私有桥接审计保留在 episode；本报告未逐请求独立复核"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "start", "status"))
    parser.add_argument("--config", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        cfg = config(args.config.resolve())
        root = pathlib.Path(cfg["runs_root"]) / cfg["run_id"]
        if args.action == "status":
            state = read(root / "state.json")
            print(json.dumps({k: state.get(k) for k in ("status", "segments", "synthetic", "last_error")}, ensure_ascii=False))
            return
        with exclusive(root):
            prepare(cfg, root)
            try:
                result = start(cfg, root) if args.action == "start" else read(root / "state.json")
            except Exception as exc:
                failed = read(root / "state.json")
                if failed["status"] != "completed":
                    failed.update(status="failed", last_error=type(exc).__name__)
                    save(root / "state.json", failed)
                raise
            print(json.dumps({"status": result["status"], "segments": result["segments"], "synthetic": result["synthetic"]}))
    except Exception as exc:
        # Keep console errors useful without exposing model bodies or private bindings.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print("FAILED: " + message[:250], file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
