"""Offline forensic replay; no API calls or writes to original checkpoints/data."""
import argparse
import collections
import hashlib
import json
import math
import pathlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import autonomous_harness as h
from agent_tools import sandbox_command

_preparser = argparse.ArgumentParser(add_help=False)
_preparser.add_argument("--dependency-site", type=pathlib.Path)
_site = _preparser.parse_known_args()[0].dependency_site
if _site:
    _site = _site.resolve()
    sys.path.insert(0, str(_site))
    _original_run = h.subprocess.run

    def _run_with_reference_dependencies(command, *args, **kwargs):
        if isinstance(command, list) and pathlib.Path(command[0]).name == "bwrap":
            command = list(command)
            position = command.index("--")
            # Overlay dependencies read-only inside /usr; keep all isolation
            # and strategy audit guards identical to the production harness.
            command[position:position] = ["--ro-bind", str(_site), "/usr/lib/python3/dist-packages"]
        return _original_run(command, *args, **kwargs)

    h.subprocess.run = _run_with_reference_dependencies
import numpy as np
import pandas as pd


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fingerprint(root):
    hashes = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            require(p.name != ".env" and not p.is_symlink(), "Unexpected secret or link")
            hashes.append((str(p.relative_to(root)), h.sha256(p)))
    return hashlib.sha256(json.dumps(hashes).encode()).hexdigest()


def compare(actual, expected, label, tolerance=1e-10):
    if isinstance(expected, dict):
        require(actual.keys() == expected.keys(), label + ": keys differ")
        for key in expected:
            compare(actual[key], expected[key], label + "/" + key, tolerance)
    elif isinstance(expected, list):
        require(len(actual) == len(expected), label + ": length differs")
        for i, value in enumerate(expected):
            compare(actual[i], value, label + "/" + str(i), tolerance)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        require(math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), f"{label}: {actual} != {expected}")
    else:
        require(actual == expected, label + ": value differs")


def metrics(values, initial=1000000.0):
    equity = np.r_[initial, np.asarray(values, dtype=float)]
    returns = equity[1:] / equity[:-1] - 1
    vol = np.std(returns, ddof=1)
    return {"days": len(returns), "total_return": equity[-1] / initial - 1,
            "annual_return": (equity[-1] / initial) ** (252 / len(returns)) - 1,
            "annual_volatility": float(vol * np.sqrt(252)),
            "sharpe": float(np.mean(returns) / vol * np.sqrt(252)) if vol else 0.0,
            "max_drawdown": float(np.min(equity / np.maximum.accumulate(equity) - 1))}


def audit_transcripts(events, root):
    records = []
    for i, event in enumerate(events):
        if not (event["phase"] == "initial_done" or event["phase"].startswith("review")):
            continue
        cp = root / event["checkpoint"]
        prior = root / events[i - 1]["checkpoint"] / "workspace"
        files = {n: (prior / n).read_text(encoding="utf-8") if (prior / n).exists() else None
                 for n in ("strategy.py", "CHANGELOG.md", "RESEARCH.md")}
        before = files["strategy.py"]
        previous_lines = len((files["RESEARCH.md"] or "").splitlines())
        lines_read, certified, pending = set(), set(), {}
        research_written = False
        log_read = False
        failures = []
        counts = collections.Counter()
        assistant_count, snapshots, last_certified = 0, {}, None
        transcript = read(cp / "transcript.json")
        if transcript.get("kind") == "shared_v1_import":
            require(event["phase"] == "initial_done", "Shared initialization outside initial delivery")
            with tempfile.TemporaryDirectory(prefix="shared-seed-audit-") as name:
                materialized, _ = h.materialize({**event, "checkpoint_path": cp}, pathlib.Path(name))
                h.verify_shared_initialization(transcript, materialized)
            for name in h.SHARED_INITIAL_FILES:
                require(h.sha256(prior / name) == h.sha256(cp / "workspace" / name), "Shared seed changed during activation")
            records.append({"phase": event["phase"], "changed": False,
                            "initialization": "shared_v1_import", "source": transcript["source"],
                            "model_calls": 0, "tool_calls": {}, "research_tool_failures": [],
                            "prior_research_lines": previous_lines, "prior_research_fully_read": None,
                            "changelog_read": None, "delivered_files_match_model_tools": None,
                            "inherited_files_match_source": True, "changed_strategy_certified": True})
            continue
        def capture_snapshot():
            if pending or not all(isinstance(v, str) for v in files.values()):
                return
            digest = hashlib.sha256(files["strategy.py"].encode()).hexdigest()
            prior_log = (prior / "CHANGELOG.md").read_text(encoding="utf-8")
            appended = files["CHANGELOG.md"].startswith(prior_log) and len(files["CHANGELOG.md"]) > len(prior_log)
            ready = (bool(files["RESEARCH.md"].strip()) and digest == last_certified
                     if event["phase"] == "initial_done" else
                     files["strategy.py"] == before or digest == last_certified)
            if appended and ready:
                snapshots[assistant_count] = dict(files)

        for message in transcript["messages"]:
            if message["role"] == "assistant":
                assistant_count += 1
                for call in message.get("tool_calls", []):
                    pending[call["id"]] = call
                    counts[call["name"]] += 1
            if message["role"] != "tool":
                continue
            call = pending.pop(message["tool_call_id"])
            name, args, value = call["name"], call["arguments"], message["content"]
            if call.get("argument_error"):
                failures.append({"tool": name, "error": value.splitlines()[0][:160]})
                capture_snapshot()
                continue
            if value.startswith(("工具错误：", "错误：", "研究代码失败", "回测失败", "研究代码超时", "回测超时")):
                failures.append({"tool": name, "error": value.splitlines()[0][:160]})
            if name == "write_file" and value.startswith("已写入"):
                files[args["path"]] = args["content"]
                research_written |= args["path"] == "RESEARCH.md"
            if name == "append_changelog" and value.startswith("已在 CHANGELOG.md"):
                files["CHANGELOG.md"] += "\n" + args["content"].strip() + "\n"
            if name == "run_backtest" and value.startswith(("完整默认参数回测成功", "完整默认参数研发回测成功")):
                last_certified = hashlib.sha256(files["strategy.py"].encode()).hexdigest()
                certified.add(last_certified)
            if name == "read_file" and args["path"] == "RESEARCH.md" and not research_written:
                lines_read.update(int(n) for n in re.findall(r"(?m)^(\d+): ", value))
            if name == "read_file" and args["path"] == "CHANGELOG.md":
                log_read = True
            capture_snapshot()
        if transcript.get("fallback"):
            fallback = transcript["fallback"]
            if fallback.get("decision") == "restore_validated_delivery":
                require(transcript.get("status") == "harness_fallback" and
                        transcript.get("delivery") == "harness_fallback", "Unmarked environment delivery")
                source = fallback.get("source_model_call")
                require(source in snapshots and source == max(snapshots), "Fallback is not the last validated bundle")
                restored = snapshots[source]
                hashes = {name: hashlib.sha256(content.encode()).hexdigest() for name, content in restored.items()}
                require(fallback.get("files_sha256") == hashes and
                        fallback.get("restored_strategy_sha256") == hashes["strategy.py"], "Fallback bundle hash mismatch")
                require(fallback.get("records_consistency") == "same_validated_snapshot" and
                        fallback.get("semantic_consistency") == "not_assessed", "Unsupported consistency claim")
                for name, content in restored.items():
                    require(content == (cp / "workspace" / name).read_text(encoding="utf-8"), "Fallback file mismatch: " + name)
                changed = before != restored["strategy.py"]
                require(changed == transcript["changed"], "Fallback changed flag mismatch")
                require(not changed or hashes["strategy.py"] in certified, "Uncertified fallback strategy")
                records.append({"phase": event["phase"], "changed": changed,
                                "delivery": "harness_fallback", "fallback": fallback,
                                "tool_calls": dict(counts), "research_tool_failures": failures,
                                "delivered_files_match_model_tools": False,
                                "restored_files_match_validated_snapshot": True,
                                "changed_strategy_certified": True})
                continue
            require(transcript["fallback"].get("decision") == "unchanged", "Unknown fallback decision")
            for name in files:
                require((prior / name).read_bytes() == (cp / "workspace" / name).read_bytes(),
                        f"Fallback changed committed file: {event['phase']} {name}")
            records.append({"phase": event["phase"], "changed": False, "tool_calls": dict(counts),
                            "research_tool_failures": failures, "prior_research_lines": previous_lines,
                            "prior_research_fully_read": set(range(1, previous_lines + 1)).issubset(lines_read),
                            "changelog_read": log_read, "delivered_files_match_model_tools": None,
                            "discarded_uncommitted_edits": True, "changed_strategy_certified": True,
                            "delivery": "harness_fallback", "fallback": transcript["fallback"]})
            continue
        for name, content in files.items():
            require(content == (cp / "workspace" / name).read_text(encoding="utf-8"), f"Model delivery mismatch: {event['phase']} {name}")
        changed = before != files["strategy.py"]
        require(changed == transcript["changed"], "Changed flag mismatch")
        require(not changed or h.sha256(cp / "workspace/strategy.py") in certified, "Uncertified changed strategy")
        records.append({"phase": event["phase"], "changed": changed, "delivery": "agent", "tool_calls": dict(counts),
                        "research_tool_failures": failures, "prior_research_lines": previous_lines,
                        "prior_research_fully_read": set(range(1, previous_lines + 1)).issubset(lines_read),
                        "changelog_read": log_read, "delivered_files_match_model_tools": True,
                        "changed_strategy_certified": True})
    return records


def simulate(workspace, run, k=None, backend="bubblewrap", docker_image=None):
    with tempfile.TemporaryDirectory(prefix="forensic-evaluator-") as stage_name:
        stage = h.evaluator_stage(pathlib.Path(stage_name), k or 7)
        args = ["--workspace", "/workspace", "--segment", str(k)] if k else ["--summary"]
        h.guarded_sim(stage, workspace, run, args, backend=backend, docker_image=docker_image)


def audit(run_id, out):
    root = h.run_root(run_id)
    latest = h.current(root)
    require(latest["phase"] == "summary_done", "Expected completed experiment")
    before = fingerprint(root / "checkpoints")
    events = h.load_events(root)
    model = events[0]["model"]
    backend = model.get("execution_backend", "bubblewrap")
    docker_image = model.get("docker_image")
    phases = {e["phase"]: root / e["checkpoint"] for e in events}
    expected = read(latest["checkpoint_path"] / "eval_run/summary.json")
    manifest = read(h.PACKAGE / "MANIFEST.json")
    h.verify_manifest(list(manifest["sha256"]))
    out.mkdir(parents=True, exist_ok=True)
    result = {"run_id": run_id, "model_calls_during_audit": 0,
              "environment": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__,
                              "pandas_requirement_met": int(pd.__version__.split(".")[0]) >= 2,
                              "numpy_requirement_met": tuple(int(v) for v in np.__version__.split(".")[:2]) >= (1, 24),
                              "package_recorded_with": manifest["recorded_with"]},
              "package_hashes_checked": len(manifest["sha256"]), "package_hashes_match": True}
    for event in events:
        cp = root / event["checkpoint"]
        marker = read(cp / "_COMPLETE.json")
        require(marker["phase"] == event["phase"] and marker["seq"] == event["seq"], "Checkpoint metadata mismatch")
        if event.get("strategy_sha256"):
            require(h.sha256(cp / "workspace/strategy.py") == event["strategy_sha256"], "Checkpoint strategy hash mismatch")
        for name in ("backtester/engine.py", "backtester/__init__.py", "run_backtest.py"):
            require(h.sha256(cp / "workspace" / name) == manifest["sha256"][name], "Workspace evaluator modified: " + name)
    result["decision_audit"] = audit_transcripts(events, root)
    print("Package, checkpoint and model-delivery audit passed", flush=True)
    with tempfile.TemporaryDirectory(prefix="formal-forensic-") as temp_name:
        temp = pathlib.Path(temp_name)
        workspace, run = temp / "workspace", temp / "run"
        h.copytree_clean(phases["initial_done"] / "workspace", workspace)
        h.restore_data(workspace, 0)
        run.mkdir()
        replay = []
        for k in range(1, 8):
            decision = phases["initial_done" if k == 1 else f"review{k - 1}_done"]
            snapshot = latest["checkpoint_path"] / "eval_run/strategies" / f"seg{k}_strategy.py"
            require(h.sha256(decision / "workspace/strategy.py") == h.sha256(snapshot), f"Executed version mismatch: {k}")
            for name in ("strategy.py", "CHANGELOG.md"):
                shutil.copy2(decision / "workspace" / name, workspace / name)
            h.verify_workspace(workspace, k - 1)
            simulate(workspace, run, k, backend, docker_image)
            h.verify_workspace(workspace, k)
            original = phases[f"segment{k}_done"] / "eval_run"
            compare(read(run / "state.json"), read(original / "state.json"), f"account segment{k}")
            byte_identical = True
            for name in ("equity.csv", "trades.csv"):
                actual_path, expected_path = run / "segments" / str(k) / name, original / "segments" / str(k) / name
                if h.sha256(actual_path) != h.sha256(expected_path):
                    byte_identical = False
                    pd.testing.assert_frame_equal(pd.read_csv(actual_path), pd.read_csv(expected_path),
                                                  check_dtype=False, rtol=1e-12, atol=1e-9)
            replay.append({"segment": k, "equity_and_trades_byte_identical": byte_identical,
                           "equity_and_trades_numerically_match": True, "cash_positions_memory_match": True})
            print(f"Replay {k}/7: equity, trades and account state match; byte_identical={byte_identical}", flush=True)
        simulate(workspace, run, backend=backend, docker_image=docker_image)
        compare(read(run / "summary.json"), expected, "summary replay")
        result["replay"] = {"segments": replay, "summary_matches": True}
        agent = pd.concat([pd.read_csv(run / "segments" / str(k) / "equity.csv") for k in range(1, 8)], ignore_index=True)
        agent.to_csv(out / "agent_equity.csv", index=False)
        for key, value in metrics(agent.equity).items():
            compare(value, expected["agent"][key], "independent agent " + key)
        shutil.copy2(phases["initial_done"] / "workspace/strategy.py", workspace / "strategy.py")
        strict = sandbox_command(workspace, "run_backtest.py", ["--start", "143", "--end", "478", "--out", "/workspace/output/audit-v1"], backend=backend, docker_image=docker_image)
        require(strict.returncode == 0, "Strict baseline error: " + strict.stderr[-1000:])
        baseline = pd.read_csv(workspace / "output/audit-v1/equity.csv")
        compare(read(workspace / "output/audit-v1/metrics.json"), expected["v1_baseline"], "strict baseline")
        for key, value in metrics(baseline.equity).items():
            compare(value, expected["v1_baseline"][key], "independent baseline " + key)
        baseline.to_csv(out / "v1_equity.csv", index=False)
        result["independent_metrics_match"] = True
        result["strict_v1_baseline_has_no_errors"] = True
        curves = agent[["t", "equity"]].rename(columns={"equity": "agent_equity"}).merge(
            baseline[["t", "equity"]].rename(columns={"equity": "v1_equity"}), on="t")
        daily = pd.concat([pd.read_csv(h.PACKAGE / f"data/daily/part_{i:02d}.csv") for i in range(8)])
        close = daily.pivot(index="t", columns="symbol", values="close").sort_index().to_numpy()
        tradable = daily.pivot(index="t", columns="symbol", values="tradable").sort_index().to_numpy().astype(bool)
        returns = close[144:479] / close[143:478] - 1
        benchmark = np.sum(np.where(tradable[144:479], returns, 0), axis=1) / np.sum(tradable[144:479], axis=1)
        curves["benchmark_equity"] = 1000000 * np.cumprod(1 + benchmark)
        compare(curves.benchmark_equity.iloc[-1] / 1000000 - 1, expected["agent"]["benchmark_return"], "independent benchmark")
        curves.to_csv(out / "comparison.csv", index=False)
        segments = []
        for k, (start, end) in enumerate(h.SPLITS["segments"], 1):
            part, prior = curves[(curves.t >= start) & (curves.t < end)], curves[curves.t == start - 1]
            row = {"segment": k}
            for name in ("agent", "v1"):
                initial = 1000000 if prior.empty else float(prior[name + "_equity"].iloc[0])
                row[name] = metrics(part[name + "_equity"], initial)
            segments.append(row)
        result.update({"segment_comparison": segments, "agent": expected["agent"], "v1_baseline": expected["v1_baseline"],
                       "num_errors": [s["num_errors"] for s in expected["log"]]})
        result["status"] = "actual_trajectory_reproduced_reference_pending"
        (out / "audit_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Strict baseline and independent metrics match", flush=True)
        # Numerical reference in the original package; keep its environment gate separate.
        golden_ws = h.extract_initial(temp / "golden-initial")
        golden_run = temp / "golden-run"
        golden_run.mkdir()
        shutil.copy2(h.PACKAGE / "examples/selftest_strategy.py", golden_ws / "strategy.py")
        for k in range(1, 8):
            if k == 4:
                p = golden_ws / "strategy.py"
                p.write_text(p.read_text().replace('"top_n": 5', '"top_n": 4'), encoding="utf-8")
            simulate(golden_ws, golden_run, k, backend, docker_image)
        simulate(golden_ws, golden_run, backend=backend, docker_image=docker_image)
        golden = read(golden_run / "summary.json")
        observed = {"agent_final_equity": golden["agent"]["final_equity"], "agent_sharpe": golden["agent"]["sharpe"],
                    "v1_final_equity": golden["v1_baseline"]["final_equity"]}
        reference_matches = all(math.isclose(observed[k], v, rel_tol=1e-6, abs_tol=1e-6)
                                for k, v in manifest["selftest_expected"].items())
        result["package_numerical_reference"] = {"matches": True, "observed": observed,
            "full_selftest_environment_gate_passed": result["environment"]["pandas_requirement_met"] and result["environment"]["numpy_requirement_met"]}
        result["package_numerical_reference"]["matches"] = reference_matches
        result["package_numerical_reference"]["num_errors"] = [s["num_errors"] for s in golden["log"]]
        result["package_numerical_reference"]["error_free"] = not any(s["num_errors"] for s in golden["log"])
        for report in (golden_ws / "reports").glob("segment_*.md"):
            shutil.copy2(report, out / ("reference_" + report.name))
        (out / "reference_segment1.md").write_text((golden_ws / "reports/segment_1.md").read_text(), encoding="utf-8")
        print("Package numerical reference: " + ("matches" if reference_matches else "MISMATCH"), flush=True)
    require(fingerprint(root / "checkpoints") == before, "Original checkpoints changed")
    result["original_checkpoints_unchanged"] = True
    result["status"] = "actual_trajectory_reproduced_with_findings" if (not reference_matches or not result["environment"]["pandas_requirement_met"] or not result["environment"]["numpy_requirement_met"]) else ("passed" if result["package_numerical_reference"]["error_free"] else "passed_with_reference_strategy_errors")
    (out / "audit_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AUDIT COMPLETE: " + str(out / "audit_results.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="formal-v4pro-1")
    parser.add_argument("--out", type=pathlib.Path, default=h.HERE / "audits/formal-v4pro-1")
    parser.add_argument("--dependency-site", type=pathlib.Path)
    args = parser.parse_args()
    audit(args.run_id, args.out.resolve())
