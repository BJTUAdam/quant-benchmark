"""Trusted environment driver for environment_feedback_v1.

The Agent loop receives only a staged workspace and a model-facing message.
This module owns stage transitions, market release, evaluator calls and feedback.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import agent_view
import market_view
from docker_sandbox import validate_image


def _require_protocol(cfg: dict) -> None:
    if cfg.get("interaction_protocol") != agent_view.PROTOCOL:
        raise RuntimeError("环境控制程序只接受 environment_feedback_v1")
    if cfg.get("execution_backend") != "docker":
        raise RuntimeError("环境反馈协议要求 Docker 执行后端")


def _visible_end_t(h, part: int) -> int:
    return (h.SPLITS["research"][1] - 1 if part == 0 else
            h.SPLITS["segments"][part - 1][1] - 1)


def prepare(h, run_id: str, cfg: dict, seed_run_id: str | None = None) -> None:
    """Publish the initial Agent view without exposing the operator package."""
    _require_protocol(cfg)
    if seed_run_id:
        raise RuntimeError("环境反馈协议要求 Agent 独立研发初始策略")
    h.check_runtime()
    root = h.run_root(run_id)
    if root.exists():
        raise RuntimeError("run-id 已存在；不可覆盖正式轨迹")
    with tempfile.TemporaryDirectory(prefix="environment-prepare-") as name:
        workspace = h.extract_initial(pathlib.Path(name) / "agent")
        agent_view.publish_initial(workspace, h.package_kind() == "futures_daily")
        market_view.publish(workspace, h.PACKAGE, 0, h.package_kind() == "futures_daily")
        h.verify_workspace(workspace, 0, True)
        session = h.new_session(agent_view.SYSTEM) if cfg.get("context_mode", "fresh") == "continuous" else None
        (root / "events").mkdir(parents=True)
        (root / "checkpoints").mkdir()
        h.commit(root, None, "prepared", 0, workspace, None, None, h.model_identity(cfg), session)


def materialize(h, event: dict, temp: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Rebuild exactly the visible market set for this checkpoint."""
    cp = event["checkpoint_path"]
    workspace, eval_run = temp / "workspace", temp / "eval_run"
    h.copytree_clean(cp / "workspace", workspace)
    visible_part = int(event["visible_part"])
    market_view.clear_history(workspace, h.package_kind() == "futures_daily")
    if h.package_kind() == "futures_daily":
        market = agent_view.public_market(json.loads((h.PACKAGE / "data/market.json").read_text(encoding="utf-8")))
        target = workspace / "data/market.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in h.part_files(visible_part):
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(h.PACKAGE / name, target)
    market_view.publish(workspace, h.PACKAGE, visible_part, h.package_kind() == "futures_daily")
    h.verify_workspace(workspace, visible_part, True)
    if (cp / "eval_run").is_dir():
        h.copytree_clean(cp / "eval_run", eval_run)
    else:
        eval_run.mkdir()
    return workspace, eval_run


def evaluator_stage(h, dst: pathlib.Path, upto: int) -> pathlib.Path:
    """Keep the evaluator and its future data outside the Agent workspace."""
    names = ["simulate.py", "backtester/engine.py", "backtester/__init__.py",
             "data/splits.json", "prompts/round_review.md", *h.part_files(upto)]
    if h.package_kind() == "futures_daily":
        names += ["data/market.json", "task_config.json"]
    h.verify_manifest(names)
    for name in names:
        target = dst / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(h.PACKAGE / name, target)
    return dst


def guarded_sim(h, stage: pathlib.Path, workspace: pathlib.Path, eval_run: pathlib.Path,
                args: list[str], cfg: dict) -> None:
    """Run the trusted scorer while strategy decisions execute in Docker."""
    if cfg.get("execution_backend") != "docker":
        raise RuntimeError("环境反馈协议要求 Docker 执行后端")
    image = validate_image(cfg.get("docker_image"))
    name = "financial-sim-" + uuid.uuid4().hex[:16]
    mapped = [str(workspace) if arg == "/workspace" else
              str(eval_run) if arg == "/run" else arg for arg in args]
    command = [sys.executable, str(h.HERE / "simulator_host.py"),
               "--image", image, "--container-name", name, "--eval", str(stage),
               "--run", str(eval_run), *mapped]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp",
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = subprocess.run(command, cwd="/tmp", env=env, capture_output=True,
                                text=True, timeout=600)
    finally:
        for index in range(3):
            subprocess.run(["docker", "rm", "-f", f"{name}-{index}"], cwd="/tmp",
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20)
    if result.returncode:
        raise RuntimeError("Docker 隔离仿真失败：" + (result.stderr[-3000:] or result.stdout[-3000:]))


def step(h, run_id: str, cfg: dict) -> dict:
    """Alternate one Agent delivery and one operator-controlled market stage."""
    _require_protocol(cfg)
    h.check_runtime()
    root = h.run_root(run_id)
    event = h.current(root)
    identity = h.model_identity(cfg)
    if any(identity.get(key) != value for key, value in event["model"].items()):
        raise RuntimeError("正式轨迹已固定模型、交互协议和执行环境；变更请用新 run-id")
    if event["model"].get("interaction_protocol") != agent_view.PROTOCOL:
        raise RuntimeError("检查点不属于环境反馈协议")
    session = h.checkpoint_session(event)
    phase = event["phase"]
    with tempfile.TemporaryDirectory(prefix="environment-step-") as name:
        temp = pathlib.Path(name)
        workspace, eval_run = materialize(h, event, temp)
        if phase == "prepared":
            prompt = (workspace / "TASK.md").read_text(encoding="utf-8")
            transcript = h.agent_loop(workspace, cfg, prompt, initial=True, session=session,
                                      turn_name="initial", usage_ledger_path=root / "model_usage.jsonl",
                                      max_visible_t=_visible_end_t(h, 0),
                                      progress_path=root / "live/initial.json",
                                      progress_key=event["checkpoint"])
            session = transcript.pop("session", None)
            h.verify_workspace(workspace, 0, True)
            return h.commit(root, event, "initial_done", 0, workspace, eval_run, transcript, event["model"], session)

        if phase == "initial_done" or re.fullmatch(r"review[1-6]_done", phase):
            k = 1 if phase == "initial_done" else int(phase.removeprefix("review").removesuffix("_done")) + 1
            h.verify_workspace(workspace, k - 1, True)
            h.verify_eval(eval_run, k - 1)
            h.validate_strategy((workspace / "strategy.py").read_text(encoding="utf-8"))
            stage = evaluator_stage(h, temp / "evaluator", k)
            guarded_sim(h, stage, workspace, eval_run, ["--workspace", "/workspace", "--segment", str(k)], cfg)
            agent_view.publish_feedback(workspace, eval_run, k)
            market_view.publish(workspace, h.PACKAGE, k, h.package_kind() == "futures_daily")
            h.verify_workspace(workspace, k, True)
            h.verify_eval(eval_run, k)
            return h.commit(root, event, f"segment{k}_done", k, workspace, eval_run, None, event["model"], session)

        match = re.fullmatch(r"segment([1-6])_done", phase)
        if match:
            k = int(match.group(1))
            h.verify_workspace(workspace, k, True)
            h.verify_eval(eval_run, k)
            prompt = agent_view.review_prompt(workspace, eval_run, k)
            market_view.publish(workspace, h.PACKAGE, k, h.package_kind() == "futures_daily")
            transcript = h.agent_loop(workspace, cfg, prompt, initial=False, session=session,
                                      turn_name=f"review{k}", usage_ledger_path=root / "model_usage.jsonl",
                                      max_visible_t=_visible_end_t(h, k),
                                      progress_path=root / "live" / f"review{k}.json",
                                      progress_key=event["checkpoint"])
            session = transcript.pop("session", None)
            if transcript["status"] == "review_budget_exhausted":
                # Keep the real conversation, but discard every uncommitted delivery edit.
                original = event["checkpoint_path"] / "workspace"
                for filename in ("strategy.py", "RESEARCH.md", "CHANGELOG.md"):
                    target, source = workspace / filename, original / filename
                    if source.is_file():
                        shutil.copy2(source, target)
                    else:
                        target.unlink(missing_ok=True)
                reason = transcript.get("limit_reason", "model_call_budget_exhausted")
                transcript["changed"] = False
                transcript["delivery"] = "harness_fallback"
                transcript["strategy_sha256"] = h.sha256(workspace / "strategy.py")
                transcript["fallback"] = {"decision": "unchanged", "reason": reason,
                                          "source_checkpoint": event["checkpoint"],
                                          "restored_strategy_sha256": transcript["strategy_sha256"],
                                          "records_consistency": "same_committed_checkpoint",
                                          "semantic_consistency": "not_assessed"}
            h.verify_workspace(workspace, k, True)
            return h.commit(root, event, f"review{k}_done", k, workspace, eval_run, transcript, event["model"], session)

        if phase == "segment7_done":
            h.verify_workspace(workspace, 7, True)
            h.verify_eval(eval_run, 7)
            stage = evaluator_stage(h, temp / "evaluator", 7)
            guarded_sim(h, stage, workspace, eval_run, ["--summary"], cfg)
            summary = json.loads((eval_run / "summary.json").read_text(encoding="utf-8"))
            if len(summary["log"]) != 7 or int(summary["agent"]["days"]) != h.SPLITS["num_days"] - h.SPLITS["research"][1]:
                raise RuntimeError("总评覆盖范围不正确")
            return h.commit(root, event, "summary_done", 7, workspace, eval_run, None, event["model"], session)

        if phase == "summary_done":
            return event
        raise RuntimeError("未知环境阶段：" + phase)
