"""Offline benchmark registry and verified historical-result importer (stdlib only).

This evaluator never imports the financial harness or executes model/strategy code.
Audit evidence is archived and checked for consistency, not rerun here.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import math
import pathlib
import re
import shutil
import statistics
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
METRICS = ("days", "total_return", "annual_return", "annual_volatility", "sharpe",
           "max_drawdown", "final_equity", "benchmark_return", "excess_return")
MODEL_FIELDS = ("provider", "base_url", "model", "max_tokens", "timeout_seconds",
                "max_model_calls", "thinking", "reasoning_effort", "context_mode",
                "output_token_parameter", "context_window_tokens", "context_margin_tokens", "interaction_protocol",
                "execution_backend", "docker_image", "reserved_final_evaluations", "user_agent",
                "max_strategy_evaluations", "max_sweep_evaluations", "max_research_calls",
                "research_timeout_seconds", "stage_warning_seconds", "stage_urgent_seconds", "stage_hard_seconds")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), "非法标识符")
    return value


def read(path):
    # No model config or credential files are needed by this offline tool.
    require(not path.name.startswith(".env"), "禁止读取密钥文件")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    require(not path.name.startswith(".env"), "禁止读取密钥文件")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def contained(root, relative):
    require(isinstance(relative, str) and relative, "缺少相对路径")
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "证据路径越界")
    return path


def load_task(path, case_id):
    task = read(path)
    require(task.get("schema_version") == 1 and task.get("adapter") in ("star50_v1", "futures_daily_v1"), "不支持的任务版本或适配器")
    identifier(task["task_id"])
    require(isinstance(task.get("task_version"), str), "缺少任务版本")
    cases = task.get("cases", [])
    ids = [identifier(c["case_id"]) for c in cases]
    require(len(ids) == len(set(ids)), "重复 case_id")
    for case in cases:
        require(case.get("role") in ("development", "held_out"), "未知数据集用途")
        research, segments = case["research"], case["segments"]
        require(research[0] == 0 and research[1] > 0 and len(segments) == 7, "研发期或七段定义错误")
        cursor = research[1]
        for start, end in segments:
            require(isinstance(start, int) and isinstance(end, int) and start == cursor and end > start, "分段不连续或区间非法")
            cursor = end
        require(cursor == case["num_days"] and cursor - research[1] == case["scored_days"], "计分天数不一致")
        require(case["reviews"] == 6, "本适配器要求六次复盘")
    require(case_id in ids, "找不到 case_id")
    require(task["scoring"]["primary"] == "sharpe" and task["scoring"]["composite_score"] is None,
            "当前适配器只登记原包指标，不支持另设总分")
    return task, cases[ids.index(case_id)]


def number(value, label):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), label + " 必须是有限数值")
    return value


def validate_summary(summary, case):
    logs = summary.get("log", [])
    require(len(logs) == 7, "必须有七条 segment log")
    futures = "valid" in summary and "v1_errors" in summary
    metrics = tuple(m for m in METRICS if not futures or m not in ("benchmark_return", "excess_return"))
    for key in ("agent", "v1_baseline"):
        for metric in metrics:
            number(summary[key].get(metric), key + "." + metric)
        require(summary[key]["days"] == case["scored_days"], "总计分天数不一致")
        require(-1 <= summary[key]["max_drawdown"] <= 0, "回撤应为 [-1,0] 内的负数")
    for k, (log, interval) in enumerate(zip(logs, case["segments"]), 1):
        expected_range = [interval[0], interval[1]] if futures else [interval[0], interval[1] - 1]
        require(log.get("segment") == k and log.get("range" if futures else "t_range") == expected_range,
                "分段序号或行情区间不一致")
        errors = log.get("errors") if futures else log.get("num_errors")
        require(isinstance(errors, list) if futures else type(errors) is int and errors >= 0,
                "缺少合法策略错误记录，不能默认无错误")
        require(log.get("change") in ("新建", "不改", "调参", "改逻辑"), "未知改动类型")
        require(log["metrics"].get("days") == interval[1] - interval[0], "分段天数不一致")
        for metric in metrics:
            number(log["metrics"].get(metric), "segment." + metric)
    return logs


def expected_stages():
    stages = [("prepared", 0, 0), ("initial_done", 0, 0)]
    for k in range(1, 8):
        stages.append((f"segment{k}_done", k, k))
        if k < 7:
            stages.append((f"review{k}_done", k, k))
    return stages + [("summary_done", 7, 7)]


def collect_run(project, run_id, task, case, audit_dir, track, package_override=None):
    root = project / "autonomous_runs" / identifier(run_id)
    require(track in task["tracks"], "未知赛道")
    files = sorted((root / "events").glob("*.json"))
    require(len(files) == 16, "实验未完整提交十六个阶段，停止导入")
    events, evidence, stages = [], {}, []

    def remember(name, path):
        require(path.is_file(), "缺少文件：" + str(path))
        evidence[name] = path

    previous_model = None
    for seq, (event_path, expected) in enumerate(zip(files, expected_stages())):
        event = read(event_path)
        require((event.get("phase"), event.get("segments"), event.get("visible_part")) == expected and event.get("seq") == seq,
                "阶段顺序、可见范围或已完成段数不一致")
        checkpoint = contained(root, event["checkpoint"])
        marker = read(checkpoint / "_COMPLETE.json")
        require(all(marker.get(key) == event.get(key) for key in marker), "事件与 checkpoint 标记不一致")
        require(all(key in marker for key in ("seq", "phase", "segments", "visible_part", "model", "strategy_sha256")), "checkpoint 完成标记缺少必要字段")
        model = event["model"]
        require(previous_model is None or model == previous_model, "同一 run 中模型身份或设置发生变化")
        previous_model = model
        require(isinstance(model.get("model"), str) and model["model"], "缺少模型身份")
        prefix = f"stages/{seq:02d}_{event['phase']}"
        remember(prefix + "/event.json", event_path)
        remember(prefix + "/_COMPLETE.json", checkpoint / "_COMPLETE.json")
        if seq:
            strategy = checkpoint / "workspace/strategy.py"
            require(digest(strategy) == event["strategy_sha256"], "策略哈希与已提交记录不符")
            for name in ("strategy.py", "RESEARCH.md", "CHANGELOG.md"):
                remember(prefix + "/workspace/" + name, checkpoint / "workspace" / name)
        transcript = checkpoint / "transcript.json"
        if event["phase"] == "initial_done" or event["phase"].startswith("review"):
            remember(prefix + "/transcript.json", transcript)
        stages.append({"phase": event["phase"], "visible_part": event["visible_part"],
                       "max_visible_t": case["research"][1] - 1 if not event["visible_part"] else case["segments"][event["visible_part"] - 1][1] - 1,
                       "evidence": prefix})
        events.append((event, checkpoint))

    # Source metadata only: no raw market data, model config or .env file is read.
    package = package_override or project / "star50_trend_task_v1/star50_trend_task_v1"
    splits = read(package / "data/splits.json")
    require(all(splits.get(key) == case[key] for key in ("num_days", "research", "segments")), "原包分段与任务登记不匹配")
    remember("package/splits.json", package / "data/splits.json")
    remember("package/MANIFEST.json", package / "MANIFEST.json")
    final = events[-1][1]
    summary = read(final / "eval_run/summary.json")
    logs = validate_summary(summary, case)
    for name in ("summary.json", "summary.md"):
        remember("final/" + name, final / "eval_run" / name)

    checks = read(audit_dir / "completion_checks.json")
    audit = read(audit_dir / "audit_results.json")
    visibility = read(audit_dir / "future_visibility_audit.json")
    for name in ("completion_checks.json", "audit_results.json", "future_visibility_audit.json"):
        remember("audits/" + name, audit_dir / name)
    for document in (checks, audit, visibility):
        require(document.get("run_id") == run_id, "审计材料不属于此 run")
    require(checks.get("checkpoint") == events[-1][0]["checkpoint"] and checks.get("phase") == "summary_done", "审计 checkpoint 不匹配")
    require(audit.get("agent") == summary["agent"] and audit.get("v1_baseline") == summary["v1_baseline"], "审计指标与正式总评不一致")
    errors = [log["num_errors"] for log in logs]
    require(audit.get("num_errors") == errors and checks.get("num_errors") == errors, "审计错误数与总评不一致")
    require(audit.get("package_hashes_match") is True and audit.get("independent_metrics_match") is True,
            "缺少数据包及独立指标核验通过证据")
    require(audit.get("original_checkpoints_unchanged") is True and audit.get("strict_v1_baseline_has_no_errors") is True,
            "检查点或 v1 基线审计未通过")
    replay = audit.get("replay", {})
    require(replay.get("summary_matches") is True and len(replay.get("segments", [])) == 7, "缺少七段重放证据")
    require([s.get("segment") for s in replay["segments"]] == list(range(1, 8)), "重放段落不完整")
    require(all(s.get("equity_and_trades_numerically_match") is True and s.get("cash_positions_memory_match") is True
                for s in replay["segments"]), "七段重放未通过")
    require(visibility.get("issues") == [] and (len(visibility.get("stages", [])) == 7 or len(visibility.get("reviews", [])) == 6),
            "缺少可见范围审计或审计发现问题")

    initial = read(events[1][1] / "transcript.json")
    inherited = initial.get("kind") == "shared_v1_import"
    require(not inherited or track == "maintenance_shared_v1", "继承 v1 的实验不能登记为独立研发赛道")
    init_files = {name: digest(events[1][1] / "workspace" / name)
                  for name in ("strategy.py", "RESEARCH.md", "CHANGELOG.md")}
    settings = {key: previous_model[key] for key in MODEL_FIELDS if key in previous_model}
    # Whitelist monetary limit only, never copy arbitrary config fields/credentials.
    cost = previous_model.get("cost_control", {})
    settings["budget_cny"] = cost.get("budget_cny")
    protocol = {key: value for key, value in settings.items() if key not in ("provider", "base_url", "model")}
    protocol["context_mode"] = settings.get("context_mode", "fresh")
    protocol["prompt_policy"] = "historical_unregistered"
    protocol["tool_policy"] = "historical_unregistered"
    record = {
        "schema_version": 1, "run_id": run_id, "task_id": task["task_id"], "task_version": task["task_version"],
        "case_id": case["case_id"], "case_role": case["role"], "track": track,
        "registration": "retrospective", "phase": "summary_done", "segments": 7,
        "model": settings, "initialization": {"mode": "inherited" if inherited else "model_generated",
          "files_sha256": init_files, "source": initial.get("source") if inherited else None},
        "protocol": protocol, "protocol_sha256": fingerprint(protocol),
        "task_sha256": fingerprint(task), "package_manifest_sha256": digest(package / "MANIFEST.json"),
        "metrics": summary["agent"], "v1_baseline": summary["v1_baseline"],
        "segment_log": logs, "stages": stages, "num_errors": errors,
        "formal_error_free": not any(errors),
        "review_decisions": [{"review": k, "applies_to_segment": k + 1, "change": logs[k]["change"]} for k in range(1, 7)],
        "audit": {"mode": "archived_evidence_consistency_check", "replay_rerun_during_import": False,
          "recorded_status": audit.get("status"), "reference_strategy_errors": audit.get("package_numerical_reference", {}).get("num_errors"),
          "visibility_claim": "已归档审计未发现未解锁行情访问证据；不证明无法识别真实时间。",
          "research_tool_errors": sum(len(d.get("research_tool_failures", [])) for d in audit.get("decision_audit", []))},
    }
    record["comparison_group"] = fingerprint({"task": record["task_sha256"], "case": case["case_id"],
        "package": record["package_manifest_sha256"], "track": track,
        "initialization": init_files if track == "maintenance_shared_v1" else None,
        "protocol": record["protocol_sha256"]})
    return record, evidence


def collect_staged_run(project, run_id, task, case, audit_dir, track, package):
    """Import a new-task run only after actual replay and visibility audits."""
    root = project / "autonomous_runs" / identifier(run_id)
    require(track == "end_to_end" and track in task["tracks"], "新题目须从研发期独立执行")
    files = sorted((root / "events").glob("*.json"))
    require(len(files) == 16, "新题目实验未完整提交十六个阶段")
    evidence, events, stages = {}, [], []

    def remember(name, path):
        require(path.is_file(), "缺少证据文件：" + str(path))
        evidence[name] = path

    model = None
    for seq, (event_path, expected) in enumerate(zip(files, expected_stages())):
        event = read(event_path)
        require((event.get("phase"), event.get("segments"), event.get("visible_part")) == expected and event.get("seq") == seq,
                "新题目阶段或数据可见范围不一致")
        cp = contained(root, event["checkpoint"])
        marker = read(cp / "_COMPLETE.json")
        require(all(marker.get(k) == event.get(k) for k in marker), "checkpoint 标记与事件不一致")
        if model is None:
            model = event["model"]
        require(event["model"] == model, "实验中途改变模型或交互协议")
        prefix = f"stages/{seq:02d}_{event['phase']}"
        remember(prefix + "/event.json", event_path)
        remember(prefix + "/_COMPLETE.json", cp / "_COMPLETE.json")
        if seq:
            strategy = cp / "workspace/strategy.py"
            require(digest(strategy) == event["strategy_sha256"], "策略快照哈希不一致")
            for name in ("strategy.py", "RESEARCH.md", "CHANGELOG.md"):
                remember(prefix + "/workspace/" + name, cp / "workspace" / name)
        if event["phase"] == "initial_done" or event["phase"].startswith("review"):
            remember(prefix + "/transcript.json", cp / "transcript.json")
        stages.append({"phase": event["phase"], "visible_part": event["visible_part"],
                       "max_visible_t": case["research"][1] - 1 if not event["visible_part"] else case["segments"][event["visible_part"] - 1][1] - 1,
                       "evidence": prefix})
        events.append((event, cp))
    require(model and isinstance(model.get("model"), str) and model["model"], "缺少模型身份")
    splits = read(package / "data/splits.json")
    require(all(splits.get(k) == case[k] for k in ("num_days", "research", "segments")), "任务包分段不一致")
    remember("package/splits.json", package / "data/splits.json")
    remember("package/MANIFEST.json", package / "MANIFEST.json")
    remember("package/stage.json", package.parent / "stage.json")
    final = events[-1][1]
    summary = read(final / "eval_run/summary.json")
    logs = validate_summary(summary, case)
    for name in ("summary.json", "summary.md"):
        remember("final/" + name, final / "eval_run" / name)
    checks = read(audit_dir / "completion_checks.json")
    audit = read(audit_dir / "audit_results.json")
    visibility = read(audit_dir / "future_visibility_audit.json")
    for name in ("completion_checks.json", "audit_results.json", "future_visibility_audit.json"):
        remember("audits/" + name, audit_dir / name)
    require(all(d.get("run_id") == run_id for d in (checks, audit, visibility)), "审计材料不属于本实验")
    errors = [len(s["errors"]) if "errors" in s else s["num_errors"] for s in logs]
    require(checks.get("phase") == "summary_done" and checks.get("checkpoint") == events[-1][0]["checkpoint"] and
            checks.get("days") == case["scored_days"] and checks.get("num_errors") == errors, "完成核验不一致")
    require(audit.get("status") == "passed" and audit.get("agent") == summary["agent"] and
            audit.get("v1_baseline") == summary["v1_baseline"] and audit.get("num_errors") == errors and
            audit.get("package_hashes_match") is True and audit.get("independent_metrics_match") is True and
            audit.get("original_checkpoints_unchanged") is True and audit.get("strict_v1_baseline_has_no_errors") is True,
            "独立重放或总评核验不一致")
    replay = audit.get("replay", {})
    require(replay.get("summary_matches") is True and len(replay.get("segments", [])) == 7 and
            all(x.get("byte_identical") is True for x in replay["segments"]), "七段重放不完整")
    require(visibility.get("issues") == [] and len(visibility.get("stages", [])) == 7,
            "可见范围审计未通过")
    initial = read(events[1][1] / "transcript.json")
    require(initial.get("kind") != "shared_v1_import", "新题目不能继承其他任务的 v1")
    deliveries = []
    for event, cp in events:
        if event["phase"] == "initial_done" or event["phase"].startswith("review"):
            transcript = read(cp / "transcript.json")
            fallback = transcript.get("fallback")
            deliveries.append({"phase": event["phase"], "delivery": "harness_fallback" if fallback else "agent",
                               "fallback": fallback})
    fallback_count = sum(item["delivery"] == "harness_fallback" for item in deliveries)
    if "reserved_final_evaluations" in model:
        require(audit.get("harness_fallback_count") == fallback_count and
                audit.get("fully_agent_delivered") == (fallback_count == 0), "交付来源审计不一致")
    init_files = {name: digest(events[1][1] / "workspace" / name)
                  for name in ("strategy.py", "RESEARCH.md", "CHANGELOG.md")}
    settings = {key: model[key] for key in MODEL_FIELDS if key in model}
    cost = model.get("cost_control", {})
    settings["budget_cny"] = cost.get("budget_cny")
    protocol = {key: value for key, value in settings.items() if key not in ("provider", "base_url", "model")}
    protocol["prompt_policy"] = model.get("interaction_protocol", "legacy")
    protocol["tool_policy"] = "controlled_task_tools_v1"
    record = {"schema_version": 1, "run_id": run_id, "task_id": task["task_id"], "task_version": task["task_version"],
              "case_id": case["case_id"], "case_role": case["role"], "track": track,
              "registration": "prospective", "phase": "summary_done", "segments": 7,
              "model": settings, "initialization": {"mode": "harness_fallback" if initial.get("fallback") else "model_generated", "files_sha256": init_files, "source": None},
              "deliveries": deliveries, "harness_fallback_count": fallback_count,
              "fully_agent_delivered": fallback_count == 0,
              "protocol": protocol, "protocol_sha256": fingerprint(protocol),
              "task_sha256": fingerprint(task), "package_manifest_sha256": digest(package / "MANIFEST.json"),
              "metrics": summary["agent"], "v1_baseline": summary["v1_baseline"],
              "segment_log": logs, "stages": stages, "num_errors": errors,
              "formal_error_free": not any(errors),
              "review_decisions": [{"review": k, "applies_to_segment": k + 1, "change": logs[k]["change"]} for k in range(1, 7)],
              "audit": {"mode": "actual_replay", "replay_rerun_during_import": False,
                        "recorded_status": audit["status"], "reference_strategy_errors": None,
                        "visibility_claim": "逐轮核对已提交提示和结构化工具访问；不证明无法识别真实时间。",
                        "research_tool_errors": sum(len(item.get("research_tool_failures", []))
                                                     for item in audit.get("deliveries", []))}}
    if model.get("user_agent") is not None:
        user_stages = [item.get("user_agent") for item in visibility["stages"]]
        require(all(isinstance(item, dict) for item in user_stages), "缺少 User Agent 阶段审计")
        record["user_agent"] = {key: sum(item[key] for item in user_stages)
                                for key in ("model_calls", "messages", "model_messages", "template_fallbacks")}
        record["user_agent"]["model"] = model["model"]
    record["comparison_group"] = fingerprint({"task": record["task_sha256"], "case": case["case_id"],
        "package": record["package_manifest_sha256"], "track": track, "protocol": record["protocol_sha256"]})
    return record, evidence


def import_run(project, run_id, task_path, case_id, audit_dir, track, output, *, package_override=None, registration=None):
    task, case = load_task(task_path, case_id)
    if package_override is not None and (package_override.parent / "stage.json").is_file():
        record, evidence = collect_staged_run(project, run_id, task, case, audit_dir, track, package_override)
    else:
        record, evidence = collect_run(project, run_id, task, case, audit_dir, track, package_override)
    if registration is not None:
        record["registration"] = "prospective"
        record["batch_registration"] = registration
        record["comparison_group"] = fingerprint({"legacy_group": record["comparison_group"], "registered": registration["protocol_sha256"], "mode": registration["mode"]})
    # Use the exact task supplied on the command line, including external registries.
    evidence["task.json"] = task_path
    output.mkdir(parents=True, exist_ok=True)
    destination = output / identifier(run_id)
    require(not destination.exists(), "此 run 已导入，禁止覆盖；使用 verify 检查现有记录")
    with tempfile.TemporaryDirectory(prefix=".import-", dir=output) as scratch:
        stage = pathlib.Path(scratch) / "record"
        stage.mkdir()
        hashes = {}
        for name, source in evidence.items():
            target = stage / "evidence" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            source_hash = digest(source)
            shutil.copyfile(source, target)
            require(digest(target) == source_hash, "复制期间证据发生变化")
            hashes[name] = source_hash
        record["evidence_sha256"] = hashes
        write_json(stage / "result.json", record)
        write_json(stage / "_COMPLETE.json", {"result_sha256": digest(stage / "result.json")})
        stage.rename(destination)
    return record


def verified_records(output):
    records = []
    for directory in sorted(output.iterdir()) if output.exists() else []:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        marker = read(directory / "_COMPLETE.json")
        require(digest(directory / "result.json") == marker["result_sha256"], "导入结果哈希不匹配：" + directory.name)
        record = read(directory / "result.json")
        require(record["run_id"] == directory.name, "结果目录与 run-id 不符")
        for name, expected in record["evidence_sha256"].items():
            require(digest(contained(directory / "evidence", name)) == expected, "归档证据被改动：" + name)
        records.append(record)
    return records


def report(output, report_dir):
    records = verified_records(output)
    require(records, "尚无已导入结果")
    groups = collections.defaultdict(list)
    for row in records:
        delivery = ("agent" if row.get("fully_agent_delivered") is True else
                    "harness_fallback" if row.get("fully_agent_delivered") is False else "not_recorded")
        groups[(row["comparison_group"], row["model"]["model"], delivery)].append(row)
    aggregates = []
    for (group, model, delivery), rows in sorted(groups.items()):
        valid = [r for r in rows if r["formal_error_free"]]
        aggregates.append({"comparison_group": group, "model": model, "delivery": delivery, "attempts": len(rows),
            "error_free_runs": len(valid), "sharpe_mean": statistics.mean(r["metrics"]["sharpe"] for r in valid) if valid else None,
            "sharpe_sample_std": statistics.stdev(r["metrics"]["sharpe"] for r in valid) if len(valid) >= 2 else None,
            "note": "按相同登记协议分组；历史协议未完整预注册，不能据此宣布公平排名。"})
    modes = {r.get("batch_registration", {}).get("mode", "historical") for r in records}
    bench_mode = "fake" if modes == {"fake"} else "registered" if all(r["registration"] == "prospective" for r in records) else "historical"
    intro = "假模型离线流程验证；所有指标不代表真实模型能力。" if bench_mode == "fake" else (
        "以下运行已在启动前登记协议；行情用途、资源设置和重复次数见批次锁定文件。" if bench_mode == "registered" else
        "以下是已完成实验的标准化登记，不是模型排行榜。历史样例为事后导入，协议存在差异；行情已被查看，不能作为未见测试集。")
    lines = ["# 长程 Agent Benchmark · 结果", "", intro, "",
        "指标沿用原包：夏普为主指标，收益和回撤辅助；没有额外的综合总分。当前工具检查归档审计与正式产物一致性，没有重新执行回测或调用模型。", "",
        "| 模型 | 累计收益 | 夏普 | 最大回撤 | v1 收益 | 比 v1 多赚 | 正式错误 | 分组 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in records:
        m, b = row["metrics"], row["v1_baseline"]
        lines.append(f"| {row['model']['model']} | {m['total_return']:+.2%} | {m['sharpe']:.3f} | {m['max_drawdown']:.2%} | {b['total_return']:+.2%} | {(m['total_return']-b['total_return'])*100:+.2f} 个百分点 | {sum(row['num_errors'])} | {row['comparison_group'][:10]} |")
    lines += ["", "## 范围与协议", "",
        f"- 已登记实验 {len(records)} 次，任务 {len({r['task_id'] for r in records})} 道，独立行情 case {len({(r['task_id'],r['case_id']) for r in records})} 组。七段是同一个连续账户，不是七次独立实验。",
        "- 所有协议分组独立统计；单次运行的样本标准差留空，不填 0，不使用交易日数当作模型重复次数。",
        "- 共用 v1 赛道记录 strategy.py、RESEARCH.md、CHANGELOG.md 三份初始交接材料哈希；独立研发赛道单独统计。",
        "- 不把历史研究工具报错当作正式策略错误，也不隐去工具报错；原包参考策略错误单列。", ""]
    for row in records:
        lines += [f"### {row['run_id']}", "", f"- 模型设置：`{canonical(row['model'])}`",
            f"- 环境兜底次数：{row.get('harness_fallback_count', '历史记录未标注')}；全程 Agent 自主交付：{row.get('fully_agent_delivered', '未知')}。含兜底的运行单独统计。",
            f"- 初始交付：{row['initialization']['mode']}；策略 SHA256 `{row['initialization']['files_sha256']['strategy.py']}`。",
            "- 六次决策（依次用于第 2—7 段）：" + " / ".join(d["change"] for d in row["review_decisions"]) + "。",
            f"- 七段正式错误：{row['num_errors']}；历史研究工具错误 {row['audit']['research_tool_errors']} 次。",
            f"- 原包参考策略错误：{row['audit']['reference_strategy_errors']}；与此模型正式错误分开报告。",
            "- 防泄露范围：" + row["audit"]["visibility_claim"], ""]
    lines += ["## 下一轮实验前必须固定", "",
        "1. 选定新行情 case 或保留开发样例用途；记录数据、题目、提示、工具、评分器版本。",
        "2. 选择共用 v1 维护赛道或独立研发赛道；同赛道使用相同任务、数据解锁和工具规则。",
        "3. 预先登记模型具体版本、推理设置、上下文模式、调用/回测/时间/预算规则及重复次数，不在看到结果后改规则。",
        "4. 同模型多次运行，保留失败和无效尝试；缺少统一协议的历史运行不混进新实验均值。",
        "5. 过程质量先提供可核验证据（目标/约束保留、前轮计划与后轮验证、决策与交付匹配），主观过程总分另行定义。", ""]
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "results.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(report_dir / "results.json", {"schema_version": 1, "mode": bench_mode, "ranking": None, "runs": records, "aggregates": aggregates})
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["run_id", "task_id", "case_id", "model", "comparison_group", "total_return", "sharpe", "max_drawdown", "v1_return", "delta_vs_v1_pp", "formal_errors", "harness_fallback_count", "fully_agent_delivered"])
    for row in records:
        m = row["metrics"]
        writer.writerow([row["run_id"], row["task_id"], row["case_id"], row["model"]["model"], row["comparison_group"], m["total_return"], m["sharpe"], m["max_drawdown"], row["v1_baseline"]["total_return"], (m["total_return"]-row["v1_baseline"]["total_return"])*100, sum(row["num_errors"]), row.get("harness_fallback_count"), row.get("fully_agent_delivered")])
    (report_dir / "results.csv").write_text(buffer.getvalue(), encoding="utf-8-sig")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "import-run", "verify", "report"))
    parser.add_argument("--task", type=pathlib.Path, default=HERE / "tasks/star50_trend.json")
    parser.add_argument("--case", default="star50_479_dev")
    parser.add_argument("--project", type=pathlib.Path)
    parser.add_argument("--run-id")
    parser.add_argument("--audit-dir", type=pathlib.Path)
    parser.add_argument("--track", default="maintenance_shared_v1")
    parser.add_argument("--output", type=pathlib.Path, default=HERE / "results")
    parser.add_argument("--report-dir", type=pathlib.Path, default=HERE / "reports")
    args = parser.parse_args()
    try:
        if args.action == "validate":
            task, case = load_task(args.task, args.case)
            print(f"VALID: {task['task_id']} / {case['case_id']} / {case['scored_days']} days")
        elif args.action == "import-run":
            require(args.project and args.run_id and args.audit_dir, "导入需要 --project、--run-id、--audit-dir")
            row = import_run(args.project.resolve(), args.run_id, args.task.resolve(), args.case, args.audit_dir.resolve(), args.track, args.output.resolve())
            print(f"IMPORTED: {row['run_id']} / summary_done / 7 segments / formal errors={sum(row['num_errors'])}")
        elif args.action == "verify":
            print(f"VERIFIED: {len(verified_records(args.output))} records")
        else:
            print(f"REPORTED: {report(args.output, args.report_dir)} records -> {args.report_dir}")
    except (Invalid, OSError, ValueError, KeyError, TypeError) as error:
        print("FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
