"""Independent replay and metric checks for newly staged task packages."""
from __future__ import annotations

import csv
import json
import math
import pathlib
import shutil
import statistics
import tempfile

import bench


def independent_metrics(initial: float, equity: list[float]) -> dict:
    values = [initial, *equity]
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    sd = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.mean(returns) / sd * math.sqrt(252) if sd > 0 else 0.0
    return {"days": len(equity), "total_return": equity[-1] / initial - 1,
            "sharpe": sharpe, "max_drawdown": drawdown}


def audit(h, run_id: str, out: pathlib.Path, case: dict) -> dict:
    root = h.run_root(run_id)
    events = h.load_events(root)
    bench.require(len(events) == 16 and events[-1]["phase"] == "summary_done", "重放需要完整七段")
    model = events[0]["model"]
    public_controller = model.get("interaction_protocol") == "environment_feedback_v1"
    from audit_formal_run import audit_transcripts
    deliveries = audit_transcripts(events, root)
    fallback_count = sum(item.get("delivery") == "harness_fallback" for item in deliveries)
    if public_controller:
        import environment_controller
    final = root / events[-1]["checkpoint"]
    source_run = final / "eval_run"
    source_summary = bench.read(source_run / "summary.json")
    bench.validate_summary(source_summary, case)
    marker_hashes = {str(root / e["checkpoint"] / "_COMPLETE.json"):
                     h.sha256(root / e["checkpoint"] / "_COMPLETE.json") for e in events}
    with tempfile.TemporaryDirectory(prefix="task-replay-") as temp_name:
        temp = pathlib.Path(temp_name)
        initial_event = {**events[1], "checkpoint_path": root / events[1]["checkpoint"]}
        if public_controller:
            workspace, replay = environment_controller.materialize(h, initial_event, temp)
        else:
            workspace, replay = h.materialize(initial_event, temp)
        segments = []
        for k in range(1, 8):
            frozen = source_run / "strategies" / f"seg{k}_strategy.py"
            shutil.copy2(frozen, workspace / "strategy.py")
            if public_controller:
                stage = environment_controller.evaluator_stage(h, temp / f"operator_{k}", k)
                environment_controller.guarded_sim(h, stage, workspace, replay,
                    ["--workspace", "/workspace", "--segment", str(k)], model)
            else:
                stage = h.evaluator_stage(temp / f"operator_{k}", k)
                h.guarded_sim(stage, workspace, replay, ["--workspace", "/workspace", "--segment", str(k)],
                              backend=model.get("execution_backend", "bubblewrap"), docker_image=model.get("docker_image"))
            expected = source_run / "segments" / str(k)
            actual = replay / "segments" / str(k)
            files = ("equity.csv", "trades.csv")
            match = all(h.sha256(actual / name) == h.sha256(expected / name) for name in files)
            state_source = (expected / "state.json" if h.package_kind() == "futures_daily"
                            else root / events[2 * k]["checkpoint"] / "eval_run/state.json")
            match = match and h.sha256(replay / "state.json") == h.sha256(state_source)
            bench.require(match, f"第 {k} 段重放与正式结果不一致")
            segments.append({"segment": k, "equity_and_trades_numerically_match": True,
                             "cash_positions_memory_match": True, "byte_identical": True})
        if public_controller:
            stage = environment_controller.evaluator_stage(h, temp / "operator_summary", 7)
            environment_controller.guarded_sim(h, stage, workspace, replay, ["--summary"], model)
        else:
            stage = h.evaluator_stage(temp / "operator_summary", 7)
            h.guarded_sim(stage, workspace, replay, ["--summary"],
                          backend=model.get("execution_backend", "bubblewrap"), docker_image=model.get("docker_image"))
        replay_summary = bench.read(replay / "summary.json")
        bench.require(replay_summary == source_summary, "独立重放总评与正式总评不一致")
    bench.require(all(h.sha256(pathlib.Path(name)) == expected for name, expected in marker_hashes.items()),
                  "审计期间已提交 checkpoint 被改动")
    rows = []
    for k in range(1, 8):
        with (source_run / "segments" / str(k) / "equity.csv").open(encoding="utf-8", newline="") as f:
            rows += list(csv.DictReader(f))
    initial_cash = (json.loads((h.PACKAGE / "data/market.json").read_text(encoding="utf-8"))["initial_cash"]
                    if h.package_kind() == "futures_daily" else 1_000_000)
    metrics = independent_metrics(float(initial_cash), [float(r["equity"]) for r in rows])
    for name, value in metrics.items():
        bench.require(math.isclose(value, source_summary["agent"][name], rel_tol=1e-8, abs_tol=1e-8),
                      "独立指标不一致：" + name)
    errors = [len(log["errors"]) if "errors" in log else log["num_errors"] for log in source_summary["log"]]
    baseline_errors = source_summary.get("v1_errors", [])
    bench.require(not baseline_errors, "冻结 v1 基线存在运行错误")
    record = {"run_id": run_id, "status": "passed", "agent": source_summary["agent"],
              "deliveries": deliveries, "harness_fallback_count": fallback_count,
              "fully_agent_delivered": fallback_count == 0,
              "v1_baseline": source_summary["v1_baseline"], "num_errors": errors,
              "package_hashes_match": True, "independent_metrics_match": True,
              "original_checkpoints_unchanged": True, "strict_v1_baseline_has_no_errors": True,
              "replay": {"segments": segments, "summary_matches": True},
              "model_calls_during_audit": 0, "scope": "使用原引擎逐段重放并独立核算收益、夏普、回撤；不验证真实市场来源。"}
    out.mkdir(parents=True, exist_ok=True)
    bench.write_json(out / "audit_results.json", record)
    return record
