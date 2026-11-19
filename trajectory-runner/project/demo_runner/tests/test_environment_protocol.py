"""Offline integration checks for public preparation and checkpoint restoration."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "project/demo_runner"))
import autonomous_harness as harness
import agent_view
import environment_controller


class EnvironmentProtocolTests(unittest.TestCase):
    def test_controller_releases_feedback_and_one_market_stage_before_review(self):
        package = ROOT / "benchmark/packages/t01_short_trend_v1-b618221e7637/t01_short_trend_v1"
        temp_parent = (ROOT / "benchmark").resolve()
        with tempfile.TemporaryDirectory(dir=temp_parent) as folder:
            root = pathlib.Path(folder)
            splits = json.loads((package / "data/splits.json").read_text(encoding="utf-8"))
            observed = []

            def fake_agent_loop(workspace, _cfg, prompt, initial, **_kwargs):
                if initial:
                    shutil.copy2(workspace / "strategy_example.py", workspace / "strategy.py")
                    (workspace / "RESEARCH.md").write_text("研究记录", encoding="utf-8")
                else:
                    observed.append((prompt, sorted(p.name for p in (workspace / "data/daily").glob("*.csv")),
                                     (workspace / "reports/feedback.md").read_text(encoding="utf-8")))
                with (workspace / "CHANGELOG.md").open("a", encoding="utf-8") as log:
                    log.write("\n本轮策略决定\n")
                return {"status": "completed", "messages": [], "changed": initial,
                        "strategy_sha256": harness.sha256(workspace / "strategy.py"), "backtests": 1}

            def fake_simulator(_h, _stage, workspace, eval_run, args, _cfg):
                self.assertEqual(args[-2:], ["--segment", "1"])
                for name in harness.part_files(1)[-2:]:
                    target = workspace / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(package / name, target)
                reports = workspace / "reports"
                reports.mkdir(exist_ok=True)
                (reports / "segment_1.md").write_text("# 第 1 段报告\n本段收益 5%。\n", encoding="utf-8")
                for suffix in ("_equity.csv", "_trades.csv"):
                    (reports / ("segment_1" + suffix)).write_text("t\n144\n", encoding="utf-8")
                (eval_run / "log.json").write_text(json.dumps([{"segment": 1, "metrics":
                    {"total_return": 0.05, "sharpe": 1.2, "max_drawdown": -0.03}, "num_errors": 0}]), encoding="utf-8")

            cfg = {"interaction_protocol": agent_view.PROTOCOL, "context_mode": "fresh", "execution_backend": "docker"}
            with patch.object(harness, "PACKAGE", package), patch.object(harness, "SPLITS", splits), \
                 patch.object(harness, "RUNS", root / "runs"), patch.object(harness, "check_runtime", return_value={}), \
                 patch.object(harness, "agent_loop", side_effect=fake_agent_loop), \
                 patch.object(environment_controller, "evaluator_stage", return_value=root / "operator"), \
                 patch.object(environment_controller, "guarded_sim", side_effect=fake_simulator), \
                 patch.object(harness, "verify_eval", return_value=None), \
                 patch.object(tempfile, "tempdir", str(temp_parent)):
                environment_controller.prepare(harness, "flow-test", cfg)
                event = harness.step("flow-test", cfg)
                self.assertEqual(event["phase"], "initial_done")
                event = environment_controller.step(harness, "flow-test", cfg)
                self.assertEqual(event["phase"], "segment1_done")
                event = environment_controller.step(harness, "flow-test", cfg)
                self.assertEqual(event["phase"], "review1_done")
            self.assertEqual(len(observed), 1)
            prompt, files, report = observed[0]
            self.assertIn("sharpe: 1.2", prompt)
            self.assertIn("上次交付的策略已有新的运行结果", prompt)
            self.assertEqual(files, ["history.csv"])
            self.assertIn("# 策略运行反馈", report)
            self.assertIn("本次收益 5%", report)
            self.assertIsNone(agent_view.FORBIDDEN.search(prompt + report))

    def test_prepare_and_restore_stock_and_futures(self):
        packages = (
            ROOT / "benchmark/packages/t01_short_trend_v1-b618221e7637/t01_short_trend_v1",
            pathlib.Path("D:/Learn/Project/changguiji_domo/batch_packages/a01_soy_crush_v1" if os.name == "nt" else
                         "/mnt/d/Learn/Project/changguiji_domo/batch_packages/a01_soy_crush_v1"),
        )
        temp_parent = (ROOT / "benchmark").resolve()
        self.assertTrue(temp_parent.is_relative_to(ROOT.resolve()))
        for package in packages:
            with self.subTest(package=package.name), tempfile.TemporaryDirectory(dir=temp_parent) as temp_name:
                folder = pathlib.Path(temp_name)
                splits = json.loads((package / "data/splits.json").read_text(encoding="utf-8"))
                with patch.object(harness, "PACKAGE", package), patch.object(harness, "SPLITS", splits), \
                     patch.object(harness, "RUNS", folder / "runs"), patch.object(harness, "check_runtime", return_value={}), \
                     patch.object(tempfile, "tempdir", str(temp_parent)):
                    harness.prepare("public-test", {"interaction_protocol": agent_view.PROTOCOL,
                                                    "execution_backend": "docker"})
                    event = harness.current(harness.run_root("public-test"))
                    self.assertEqual(event["phase"], "prepared")
                    with tempfile.TemporaryDirectory(dir=temp_parent) as scratch:
                        workspace, _ = environment_controller.materialize(harness, event, pathlib.Path(scratch))
                        harness.verify_workspace(workspace, 0, True)
                        agent_view.verify_public(workspace, harness.package_kind() == "futures_daily")
                        self.assertIn("strategy.py", (workspace / "TASK.md").read_text(encoding="utf-8"))

    def test_reminder_follows_tool_results_and_initial_gate_requires_changelog(self):
        temp_parent = (ROOT / "benchmark").resolve()
        self.assertTrue(temp_parent.is_relative_to(ROOT.resolve()))
        with tempfile.TemporaryDirectory(dir=temp_parent) as temp_name:
            workspace = pathlib.Path(temp_name)
            (workspace / "CHANGELOG.md").write_text("# versions\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "外部环境程序提供可见数据边界"):
                harness.agent_loop(workspace, {"interaction_protocol": agent_view.PROTOCOL}, "research task", True)

            class Tools:
                def __init__(self, folder, **_kwargs):
                    self.folder = folder
                    self.passed_strategy_hash = None
                    self.backtests = 0

                def execute(self, name, args):
                    if name == "write_file":
                        path = self.folder / args["path"]
                        path.write_text(args["content"], encoding="utf-8")
                    elif name == "run_backtest":
                        self.backtests += 1
                        self.passed_strategy_hash = harness.sha256(self.folder / "strategy.py")
                    return "ok"

            replies = [
                {"content": "", "tool_calls": [{"id": "a", "name": "write_file", "arguments":
                    {"path": "strategy.py", "content": "PARAMS = {}\n"}}]},
                {"content": "", "tool_calls": [
                    {"id": "b", "name": "write_file", "arguments": {"path": "RESEARCH.md", "content": "research"}},
                    {"id": "c", "name": "write_file", "arguments": {"path": "CHANGELOG.md", "content": "# versions\nv1\n"}}]},
                {"content": "", "tool_calls": [{"id": "d", "name": "run_backtest", "arguments": {}}]},
                {"content": "v1 已交付", "tool_calls": []},
            ]

            class Client:
                def __init__(self, _cfg):
                    self.index = 0

                def complete(self, _messages, _tools):
                    reply = replies[self.index]
                    self.index += 1
                    return reply

            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", Client), \
                 patch.object(harness, "validate_strategy", return_value=None), \
                 patch.object(harness, "visible_part_of", return_value=0):
                result = harness.agent_loop(workspace, {"interaction_protocol": agent_view.PROTOCOL,
                    "max_model_calls": 5}, "research task", True, max_visible_t=143)
            self.assertEqual(result["status"], "completed")
            messages = result["messages"]
            reminder = next(i for i, msg in enumerate(messages) if msg["role"] == "user" and
                            msg["content"].startswith("交付进度提醒："))
            self.assertEqual(messages[reminder - 1]["role"], "tool")
            self.assertIn("已用 3/5 次正常模型回复", messages[reminder]["content"])
            self.assertIn("剩余 2 次", messages[reminder]["content"])

    def test_review_budget_exhaustion_returns_fallback_record(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            workspace = pathlib.Path(folder)
            (workspace / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
            (workspace / "CHANGELOG.md").write_text("old\n", encoding="utf-8")

            class FakeTools:
                def __init__(self, *_args, **_kwargs):
                    self.backtests = 0
                    self.passed_strategy_hash = None

            class FakeClient:
                def __init__(self, _cfg):
                    self.count = 0

                def complete(self, _messages, _tools):
                    self.count += 1
                    return {"content": "still researching", "tool_calls": []}

            with patch.object(harness, "AgentTools", FakeTools), patch.object(harness, "ModelClient", FakeClient), \
                 patch.object(harness, "visible_part_of", return_value=1), \
                 patch.object(harness, "RUNS", workspace):
                result = harness.agent_loop(workspace, {"interaction_protocol": agent_view.PROTOCOL,
                    "max_model_calls": 10}, "review", False, max_visible_t=191)
            self.assertEqual(result["status"], "review_budget_exhausted")
            self.assertEqual(result["model_calls"], 26)
            reminders = [m["content"] for m in result["messages"] if m["role"] == "user" and
                         m["content"].startswith("交付进度提醒：")]
            self.assertEqual(len(reminders), 3)

    def test_wallclock_limit_fails_initial_and_falls_back_review_without_model_call(self):
        class FakeClient:
            def __init__(self, _cfg): pass
            def complete(self, _messages, _tools):
                raise AssertionError("hard wall-clock limit must stop before another model request")

        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            workspace = pathlib.Path(folder)
            (workspace / "CHANGELOG.md").write_text("old\n", encoding="utf-8")
            common = {"interaction_protocol": agent_view.PROTOCOL, "max_model_calls": 10,
                      "stage_warning_seconds": 0, "stage_urgent_seconds": 0,
                      "stage_hard_seconds": 0}
            with patch.object(harness, "ModelClient", FakeClient), patch.object(harness, "RUNS", workspace):
                with self.assertRaisesRegex(RuntimeError, "时间上限"):
                    harness.agent_loop(workspace, common, "research", True, max_visible_t=143)

                (workspace / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
                result = harness.agent_loop(workspace, common, "review", False, max_visible_t=191)
            self.assertEqual(result["status"], "review_budget_exhausted")
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["limit_reason"], "stage_wallclock_exhausted")

    def test_live_progress_is_visible_and_resumes_from_last_stable_tool_batch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            root = pathlib.Path(folder)
            live = root / "run/live/initial.json"

            def make_workspace(name):
                workspace = root / name
                workspace.mkdir()
                (workspace / "CHANGELOG.md").write_text("# versions\n", encoding="utf-8")
                return workspace

            class Tools:
                def __init__(self, workspace, **_kwargs):
                    self.workspace = workspace
                    self.backtests = 0
                    self.passed_backtest = False
                    self.passed_strategy_hash = None

                def execute(self, name, args):
                    if name == "write_file":
                        (self.workspace / args["path"]).write_text(args["content"], encoding="utf-8")
                    elif name == "append_changelog":
                        with (self.workspace / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
                            handle.write("\n" + args["content"] + "\n")
                    elif name == "run_backtest":
                        self.backtests += 1
                        self.passed_backtest = True
                        self.passed_strategy_hash = harness.sha256(self.workspace / "strategy.py")
                    return "ok"

            delivery = {"content": "", "tool_calls": [
                {"id": "s", "name": "write_file", "arguments":
                    {"path": "strategy.py", "content": "PARAMS = {}\n"}},
                {"id": "r", "name": "write_file", "arguments":
                    {"path": "RESEARCH.md", "content": "research\n"}},
                {"id": "c", "name": "append_changelog", "arguments":
                    {"content": "## v1\n- initial"}},
                {"id": "b", "name": "run_backtest", "arguments": {}},
            ]}

            class InterruptedClient:
                def __init__(self, _cfg): self.calls = 0
                def complete(self, _messages, _tools):
                    self.calls += 1
                    if self.calls == 1:
                        return delivery
                    raise KeyboardInterrupt()

            cfg = {"interaction_protocol": agent_view.PROTOCOL, "max_model_calls": 5}
            first = make_workspace("first")
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", InterruptedClient):
                with self.assertRaises(KeyboardInterrupt):
                    harness.agent_loop(first, cfg, "task", True, max_visible_t=143,
                                       progress_path=live, progress_key="checkpoint-0")
            saved = json.loads(live.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "model_request")
            self.assertEqual(saved["completed_model_calls"], 1)
            self.assertEqual(saved["active_model_call"], 2)
            self.assertEqual(saved["backtests"], 1)
            self.assertTrue(saved["delivery"]["ready"])
            self.assertEqual(harness.progress_summary(root / "run")["completed_model_calls"], 1)

            class FinishClient:
                def __init__(self, _cfg): self.calls = 0
                def complete(self, messages, _tools):
                    self.calls += 1
                    self.assert_previous = any(m.get("tool_call_id") == "b" for m in messages)
                    if not self.assert_previous:
                        raise AssertionError("stable tool history was not restored")
                    return {"content": "delivered", "tool_calls": []}

            second = make_workspace("second")
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", FinishClient):
                result = harness.agent_loop(second, cfg, "task", True, max_visible_t=143,
                                            progress_path=live, progress_key="checkpoint-0")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["completed_model_calls"], 2)
            self.assertTrue((second / "strategy.py").is_file())

            class NoMoreCalls:
                def __init__(self, _cfg): pass
                def complete(self, _messages, _tools):
                    raise AssertionError("delivery_ready must not call the model again")

            third = make_workspace("third")
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", NoMoreCalls):
                cached = harness.agent_loop(third, cfg, "task", True, max_visible_t=143,
                                            progress_path=live, progress_key="checkpoint-0")
            self.assertEqual(cached["strategy_sha256"], harness.sha256(third / "strategy.py"))

    def test_interrupted_tool_batch_restores_pre_batch_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            root = pathlib.Path(folder)
            live = root / "run/live/initial.json"

            def make_workspace(name):
                workspace = root / name
                workspace.mkdir()
                (workspace / "CHANGELOG.md").write_text("# versions\n", encoding="utf-8")
                return workspace

            delivery = {"content": "", "tool_calls": [
                {"id": "s", "name": "write_file", "arguments":
                    {"path": "strategy.py", "content": "PARAMS = {}\n"}},
                {"id": "r", "name": "write_file", "arguments":
                    {"path": "RESEARCH.md", "content": "research\n"}},
                {"id": "c", "name": "append_changelog", "arguments":
                    {"content": "## v1\n- initial"}},
                {"id": "b", "name": "run_backtest", "arguments": {}},
            ]}

            class Client:
                def __init__(self, _cfg): self.calls = 0
                def complete(self, _messages, _tools):
                    self.calls += 1
                    return delivery if self.calls == 1 else {"content": "delivered", "tool_calls": []}

            class Tools:
                interrupt = True
                def __init__(self, workspace, **_kwargs):
                    self.workspace = workspace
                    self.backtests = 0
                    self.passed_backtest = False
                    self.passed_strategy_hash = None
                def execute(self, name, args):
                    if name == "write_file" and args["path"] == "RESEARCH.md" and self.interrupt:
                        raise KeyboardInterrupt()
                    if name == "write_file":
                        (self.workspace / args["path"]).write_text(args["content"], encoding="utf-8")
                    elif name == "append_changelog":
                        with (self.workspace / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
                            handle.write("\n" + args["content"] + "\n")
                    elif name == "run_backtest":
                        self.backtests += 1
                        self.passed_backtest = True
                        self.passed_strategy_hash = harness.sha256(self.workspace / "strategy.py")
                    return "ok"

            cfg = {"interaction_protocol": agent_view.PROTOCOL, "max_model_calls": 5}
            first = make_workspace("first")
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", Client):
                with self.assertRaises(KeyboardInterrupt):
                    harness.agent_loop(first, cfg, "task", True, max_visible_t=143,
                                       progress_path=live, progress_key="checkpoint-0")
            saved = json.loads(live.read_text(encoding="utf-8"))
            self.assertEqual(saved["completed_model_calls"], 0)
            self.assertIsNone(saved["workspace_files"]["strategy.py"])

            Tools.interrupt = False
            second = make_workspace("second")
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", Client):
                result = harness.agent_loop(second, cfg, "task", True, max_visible_t=143,
                                            progress_path=live, progress_key="checkpoint-0")
            self.assertEqual(result["status"], "completed")
            self.assertEqual((second / "CHANGELOG.md").read_text(encoding="utf-8").count("## v1"), 1)


if __name__ == "__main__":
    unittest.main()
