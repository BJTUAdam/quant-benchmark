"""One isolated harness operation per process. Strategy execution uses the registered backend."""
from __future__ import annotations

import argparse
import hashlib
import contextlib
import importlib.util
import io
import json
import math
import pathlib
import re
import socket
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import batch
import bench


def load_harness(lock, job, folder):
    runner = pathlib.Path(lock["spec"]["project_root"]) / "demo_runner"
    sys.path.insert(0, str(runner))
    import autonomous_harness as h
    h.bind_package(pathlib.Path(lock["spec"]["package_root"]))
    project = folder / "jobs" / job["run_id"]
    h.RUNS = project / "autonomous_runs"
    h.RUNS.mkdir(parents=True, exist_ok=True)
    # Resolve the shared seed ONLY in the source project's research checkpoint.
    # Run destinations and failures always stay in the private job directory.
    original_source = h.shared_initial_source
    def seed_source(source_run_id):
        bench.require(source_run_id == lock["spec"].get("seed_run_id"), "未登记的初始材料来源")
        saved = h.RUNS
        try:
            h.RUNS = pathlib.Path(lock["spec"]["project_root"]) / "autonomous_runs"
            return original_source(source_run_id)
        finally:
            h.RUNS = saved
    h.shared_initial_source = seed_source
    config_path = project / "model.public.json"
    if not config_path.exists():
        batch.atomic(config_path, job["config"])
    bench.require(bench.read(config_path) == job["config"], "实验配置与登记协议不一致")
    cfg = h.load_config(config_path)
    if lock["spec"]["mode"] == "fake":
        if (h.PACKAGE.parent / "stage.json").is_file():
            from fake_fixture import make_client
            h.ModelClient = make_client(h.PACKAGE)
        else:
            fixture_spec = importlib.util.spec_from_file_location("batch_fake_fixture", runner / "tests/test_autonomous.py")
            fixture = importlib.util.module_from_spec(fixture_spec)
            fixture_spec.loader.exec_module(fixture)
            h.ModelClient = fixture.FakeClient
        def blocked(*args, **kwargs):
            raise RuntimeError("假模型模式禁止网络")
        socket.socket = blocked
        socket.create_connection = blocked
    return h, cfg, project


def state_of(h, run_id):
    root = h.run_root(run_id)
    if not root.exists():
        return None
    event = h.current(root)  # Existing incomplete directories are never silently re-prepared.
    return {**{k: event[k] for k in ("seq", "phase", "segments", "checkpoint")},
            "last_failure_exists": (h.RUNS / "_last_failure.json").is_file(),
            "live_progress": h.progress_summary(root)}


def visibility_audit(h, run_id, case):
    """Structured prompt/access audit, with an explicit limited scope."""
    root = h.run_root(run_id)
    events = h.load_events(root)
    stages, issues = [], []
    for index, event in enumerate(events):
        phase = event["phase"]
        if phase != "initial_done" and not phase.startswith("review"):
            continue
        cp = root / event["checkpoint"]
        previous = root / events[index - 1]["checkpoint"]
        transcript = bench.read(cp / "transcript.json")
        part = event["visible_part"]
        max_t = case["research"][1] - 1 if part == 0 else case["segments"][part - 1][1] - 1
        if transcript.get("kind") == "shared_v1_import":
            stages.append({"phase": phase, "visible_part": 0, "max_visible_t": case["research"][1] - 1, "shared_initialization_research_only": True})
            continue
        protocol = event["model"].get("interaction_protocol", "legacy")
        if protocol == h.agent_view.PROTOCOL:
            h.agent_view.verify_public(previous / "workspace", h.package_kind() == "futures_daily")
        if phase == "initial_done":
            expected_prompt = (previous / "workspace/TASK.md").read_text(encoding="utf-8")
        elif protocol == h.agent_view.PROTOCOL:
            expected_prompt = h.agent_view.review_prompt(previous / "workspace", previous / "eval_run", part)
        else:
            expected_prompt = (previous / f"eval_run/segments/{part}/next_prompt.md").read_text(encoding="utf-8")
        if protocol == h.agent_view.PROTOCOL:
            bench.require(not h.agent_view.FORBIDDEN.search(expected_prompt), "环境控制信息进入 Agent 提示")
        if phase == "initial_done" and protocol in ("guided_delivery_v1", "guided_delivery_v2"):
            expected_prompt = h.render_guided_initial_prompt(expected_prompt, event["model"].get("max_model_calls", 80))
        if phase.startswith("review") and protocol == "guided_delivery_v2":
            expected_prompt = h.render_guided_review_prompt(previous / "workspace", expected_prompt, part,
                                                             event["model"].get("max_model_calls", 80))
        if phase.startswith("review") and protocol in ("bundled_review_v1", "guided_delivery_v1"):
            expected_prompt = h.render_bundled_review_prompt(previous / "workspace", expected_prompt, part)
        messages = transcript["messages"]
        initial_files = {name: (previous / "workspace" / name).read_text(encoding="utf-8")
                         if (previous / "workspace" / name).is_file() else None
                         for name in ("strategy.py", "RESEARCH.md", "CHANGELOG.md")}
        if protocol == h.agent_view.PROTOCOL:
            try:
                h.research_evidence.audit_evidence(transcript, initial_files, max_t)
            except (ValueError, KeyError, TypeError):
                raise bench.Invalid("研究证据、跨阶段记忆或审阅输入与轨迹不一致") from None
        if event["model"].get("context_mode", "fresh") == "continuous":
            prior = bench.read(previous / "session.json")["messages"]
            bench.require(messages[:len(prior)] == prior, "持续会话历史前缀不匹配")
            active = messages[len(prior):]
        else:
            expected_system = h.agent_view.SYSTEM if protocol == h.agent_view.PROTOCOL else h.MODEL_SYSTEM
            if event["model"].get("skills_sha256"):
                # Use the host checkpoint to audit the discovered guidance offline.
                recorded_hash = event.get("system_prompt_sha256")
                system = messages[0]["content"]
                bench.require(messages[0]["role"] == "system" and
                              (system == expected_system or system.startswith(expected_system + "\n\n")) and
                              recorded_hash == bench.read(cp / "_COMPLETE.json").get("system_prompt_sha256") and
                              recorded_hash == hashlib.sha256(system.encode("utf-8")).hexdigest(),
                              "Skill 系统提示与检查点不一致")
            else:
                bench.require(messages[0] == {"role": "system", "content": expected_system}, "系统提示不匹配")
            active = messages[1:]
        bench.require(active and active[0] == {"role": "user", "content": expected_prompt}, "阶段输入与原报告不匹配")
        max_calls = event["model"].get("max_model_calls", 80)
        user_indices = set()
        user_settings = event["model"].get("user_agent")
        if user_settings is not None:
            bench.require(protocol == h.agent_view.PROTOCOL and
                          event["model"].get("context_mode", "fresh") == "fresh",
                          "User Agent 协议或上下文模式不支持")
            try:
                user_indices = h.user_agent.audit(
                    transcript.get("user_agent"), messages, user_settings, expected_prompt,
                    phase == "initial_done", phase.removesuffix("_done"), max_calls,
                    event["model"].get("max_strategy_evaluations", 40),
                    h.research_evidence.memory_from_files(initial_files))
            except (ValueError, KeyError, TypeError) as exc:
                raise bench.Invalid("User Agent 来源审计失败") from exc
        else:
            bench.require("user_agent" not in transcript, "未登记的 User Agent 记录")
        reminders_enabled = (protocol == h.agent_view.PROTOCOL or protocol == "guided_delivery_v2" or
                             (phase == "initial_done" and protocol == "guided_delivery_v1"))
        reminder_points = (({math.ceil(max_calls * fraction) for fraction in (0.60, 0.85)} | {max_calls})
                           if protocol == h.agent_view.PROTOCOL else
                           set(h.delivery_reminder_points(max_calls)) if reminders_enabled else set())
        expected_reminders, seen_reminders, open_calls = set(), set(), set()
        assistant_count = 0
        for message_index, msg in enumerate(active[1:], start=len(messages) - len(active) + 1):
            if msg["role"] == "assistant":
                assistant_count += 1
                calls = msg.get("tool_calls", [])
                open_calls.update(call["id"] for call in calls)
                if assistant_count in reminder_points and (calls or msg is not active[-1]):
                    expected_reminders.add(assistant_count)
            elif msg["role"] == "tool":
                open_calls.discard(msg["tool_call_id"])
            elif message_index in user_indices:
                if msg["role"] != "user" or open_calls:
                    issues.append(phase + ": user agent interrupted pending tools")
            elif msg["role"] == "user" and msg["content"].startswith("交付进度提醒："):
                if protocol == h.agent_view.PROTOCOL:
                    requirements = (["strategy.py", "RESEARCH.md", "追加 CHANGELOG.md", "最终策略的完整默认参数研发回测"]
                                    if phase == "initial_done" else ["保留/调参/改逻辑的决定", "追加 CHANGELOG.md", "改动策略时的完整默认参数研发回测"])
                    expected_text = h.agent_view.reminder(assistant_count, max_calls, phase == "initial_done", requirements)
                else:
                    expected_text = ((h.delivery_reminder(assistant_count, max_calls) if phase == "initial_done" else
                                      h.review_delivery_reminder(assistant_count, max_calls))
                                     if reminders_enabled else None)
                valid = (assistant_count in expected_reminders and assistant_count not in seen_reminders
                         and not open_calls and msg["content"] == expected_text)
                if valid:
                    seen_reminders.add(assistant_count)
                else:
                    issues.append(phase + ": invalid delivery reminder")
            elif msg["role"] == "user" and msg["content"].startswith(("交付时间提醒：", "交付紧急提醒：")):
                if (protocol != h.agent_view.PROTOCOL or open_calls or
                        not re.fullmatch(r"交付(?:时间|紧急)提醒：本轮已持续约 \d+ 分钟，可用时间约剩 \d+ 分钟。.+", msg["content"])):
                    issues.append(phase + ": invalid wallclock reminder")
            elif msg["role"] == "user" and msg["content"].startswith("交付最终提醒："):
                if (protocol != h.agent_view.PROTOCOL or open_calls or
                        msg["content"] != h.agent_view.finalization_reminder(phase == "initial_done")):
                    issues.append(phase + ": invalid finalization reminder")
            elif msg["role"] == "system" or (msg["role"] == "user" and not msg["content"].startswith("交付校验尚未通过：")):
                issues.append(phase + ": unexpected extra instruction")
        if seen_reminders != expected_reminders:
            issues.append(phase + ": missing delivery reminder")
        pending, successes = {}, 0
        for msg in active:
            if msg["role"] == "assistant":
                for call in msg.get("tool_calls", []):
                    pending[call["id"]] = call
            if msg["role"] != "tool":
                continue
            call = pending.pop(msg["tool_call_id"])
            value = msg["content"]
            if call.get("argument_error") or value.startswith(("工具错误：", "错误：", "研究代码失败", "回测失败", "研究代码超时", "回测超时")):
                continue
            successes += 1
            args, name = call["arguments"], call["name"]
            if name in ("read_file", "list_files"):
                text = str(args.get("path", "")) + (value if name == "list_files" else "")
                if any(int(n) > part for n in re.findall(r"part_(\d+)", text)):
                    issues.append(phase + ": successful future-part access")
            if name == "inspect_data" and args.get("end_t", 0) > max_t:
                issues.append(phase + ": successful inspect_data beyond boundary")
            if name == "run_backtest" and args.get("end", 0) > max_t:
                issues.append(phase + ": successful backtest beyond boundary")
        if pending:
            issues.append(phase + ": unmatched tool calls")
        stages.append({"phase": phase, "visible_part": part, "max_visible_t": max_t,
                       "prompt_matches_previous_checkpoint": True, "successful_tool_responses_checked": successes})
        if user_settings is not None:
            records = transcript["user_agent"]["events"]
            stages[-1]["user_agent"] = {
                "model_calls": transcript["user_agent"]["model_calls"], "messages": len(records),
                "model_messages": sum(r["reason"] == "model" for r in records),
                "template_fallbacks": sum(r["attempted"] and r["reason"] != "model" for r in records),
                "submission_reviews": len(transcript["user_agent"].get("submission_reviews", [])),
                "semantic_quality": transcript.get("delivery_review", {}).get("semantic_quality", "not_assessed"),
                "scope": "核对来源、消息、触发边界和调用额度；不证明生成话语在语义上完全无策略建议。"}
    bench.require(not issues, "可见范围审计发现问题")
    backend = events[0].get("model", events[1]["model"]).get("execution_backend", "bubblewrap")
    return {"run_id": run_id, "model_calls_during_check": 0, "stages": stages, "issues": issues,
            "agent_tool_backend": backend,
            "operator_evaluator_backend": ("trusted_host_with_docker_strategy" if backend == "docker" else "bubblewrap"),
            "scope": "检查阶段原始提示、结构化成功工具访问与边界。Docker 模式下运营侧评分器在可信控制端，策略代码在独立容器内；bubblewrap 模式保留原执行路径。不声称能检测所有原生逃逸或行情真实时间识别。"}


def finalize(h, lock, job, folder, project):
    run_id = job["run_id"]
    root = h.run_root(run_id)
    event = h.current(root)
    bench.require(event["phase"] == "summary_done", "七段尚未完成")
    result_dir = folder / "results" / run_id
    if result_dir.exists():
        rows = bench.verified_records(folder / "results")
        bench.require(any(row["run_id"] == run_id for row in rows), "结果归档未完成")
        return
    out = folder / "jobs" / run_id / "audits"
    staged = (h.PACKAGE.parent / "stage.json").is_file()
    if staged:
        import audit_new
        audit_new.audit(h, run_id, out, lock["case"])
    else:
        import audit_formal_run as audit
        audit.audit(run_id, out)
    summary = bench.read(event["checkpoint_path"] / "eval_run/summary.json")
    bench.validate_summary(summary, lock["case"])
    errors = [len(s["errors"]) if "errors" in s else s["num_errors"] for s in summary["log"]]
    # Strategy runtime errors are recorded (num_errors / formal_error_free below)
    # and counted separately downstream; they do not block registration.
    record = bench.read(out / "audit_results.json")
    if not staged:
        bench.require(record["package_numerical_reference"]["matches"] and record["environment"]["pandas_requirement_met"] and record["environment"]["numpy_requirement_met"], "原包参考或环境核验未通过")
    batch.atomic(out / "future_visibility_audit.json", visibility_audit(h, run_id, lock["case"]))
    batch.atomic(out / "completion_checks.json", {"run_id": run_id, "phase": "summary_done",
        "segments": 7, "days": lock["case"]["scored_days"], "checkpoint": event["checkpoint"], "num_errors": errors})
    registration = {"batch_id": lock["spec"]["batch_id"], "mode": lock["spec"]["mode"],
        "model_id": job["model_id"], "repeat": job["repeat"],
        "protocol_sha256": bench.read(folder / "_REGISTERED.json")["protocol_sha256"]}
    bench.import_run(project, run_id, pathlib.Path(lock["spec"]["task"]), lock["spec"]["case_id"], out,
                     lock["spec"]["track"], folder / "results", package_override=h.PACKAGE, registration=registration)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=pathlib.Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--action", choices=("doctor", "status", "prepare", "step", "finalize"), required=True)
    args = parser.parse_args()
    h = None
    buffer = io.StringIO()
    try:
        folder = args.batch.resolve()
        lock = batch.load_lock(folder)
        job = next(j for j in lock["jobs"] if j["run_id"] == args.run_id)
        # A surviving child keeps this job lock even if its scheduler is killed.
        # A new scheduler can never mutate the same checkpoint concurrently.
        with batch.exclusive(folder / "jobs" / args.run_id), contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            h, cfg, project = load_harness(lock, job, folder)
            controller = None
            if cfg.get("interaction_protocol") == h.agent_view.PROTOCOL:
                import environment_controller
                controller = environment_controller
            if args.action == "doctor":
                h.doctor(cfg, check_key=False)
            elif args.action == "prepare":
                if controller:
                    controller.prepare(h, args.run_id, cfg, lock["spec"].get("seed_run_id"))
                else:
                    h.prepare(args.run_id, cfg, lock["spec"].get("seed_run_id"))
            elif args.action == "step":
                if controller:
                    controller.step(h, args.run_id, cfg)
                else:
                    h.step(args.run_id, cfg)
            elif args.action == "finalize":
                finalize(h, lock, job, folder, project)
            state = state_of(h, args.run_id)
        print(json.dumps({"ok": True, "state": state}, ensure_ascii=False))
    except Exception as error:
        state = None
        if h:
            try:
                state = state_of(h, args.run_id)
            except Exception:
                pass
        # Provider adapter already strips HTTP bodies/credentials. Other exception
        # messages can contain arbitrary strategy/tool text: do not expose them.
        safe = str(error)[:500] if isinstance(error, RuntimeError) or isinstance(error, bench.Invalid) else type(error).__name__ + ": operation failed; inspect this job's diagnostics"
        diagnostic = args.batch / "jobs" / args.run_id / "diagnostics"
        diagnostic.mkdir(parents=True, exist_ok=True)
        batch.atomic(diagnostic / "last_failure.json", {"utc": batch.now(), "action": args.action,
                     "error_type": type(error).__name__, "error": safe, "state": state})
        # Store program progress, not raw exception/request bodies.
        (diagnostic / "last_progress.log").write_text(buffer.getvalue(), encoding="utf-8")
        print(json.dumps({"ok": False, "state": state, "error": safe}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
