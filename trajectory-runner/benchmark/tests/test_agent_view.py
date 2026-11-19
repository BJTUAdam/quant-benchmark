"""Offline checks of the eight real task packages' model-facing boundary."""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "project/demo_runner"))
sys.path.insert(0, str(ROOT / "benchmark"))
import agent_view
import agent_tools
import model_client
import research_evidence
import worker

PACKAGES = pathlib.Path("D:/Learn/Project/changguiji_domo/题目包/题目包")


class AgentViewTests(unittest.TestCase):
    def test_public_protocol_config_and_tool_descriptions(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            config = pathlib.Path(folder) / "config.json"
            config.write_text(json.dumps({"provider": "openai_compatible", "base_url": "https://example.com/v1",
                "model": "test", "api_key_env": "TEST_KEY", "interaction_protocol": agent_view.PROTOCOL,
                "execution_backend": "docker", "docker_image": "sha256:" + "a" * 64}),
                encoding="utf-8")
            self.assertEqual(model_client.load_config(config)["interaction_protocol"], agent_view.PROTOCOL)
            raw = json.loads(config.read_text(encoding="utf-8"))
            raw["execution_backend"] = "bubblewrap"
            raw.pop("docker_image")
            config.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(model_client.ModelError, "要求 Docker"):
                model_client.load_config(config)
        public = agent_view.public_tools(agent_tools.TOOLS)
        self.assertIsNone(agent_view.FORBIDDEN.search(" ".join(item["description"] for item in public)))
        self.assertIn("研发回测", next(item["description"] for item in public if item["name"] == "run_backtest"))
        self.assertIn("当前可用", agent_tools.TOOLS[2]["description"])
        self.assertIsNone(agent_view.FORBIDDEN.search(" ".join(item["description"] for item in agent_tools.TOOLS)))
        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            workspace = pathlib.Path(folder)
            (workspace / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
            tool = agent_tools.AgentTools(workspace, max_visible_t=10, public_view=True)
            inspect_error = tool.execute("inspect_data", {"kind": "daily", "symbol": "S01", "start_t": 0, "end_t": 11})
            backtest_error = tool.execute("run_backtest", {"end": 11})
            self.assertIsNone(agent_view.FORBIDDEN.search(inspect_error + backtest_error))
            self.assertIn("当前可用数据", inspect_error + backtest_error)

    def test_all_eight_initial_workspaces_hide_environment_controls(self):
        archives = sorted(PACKAGES.glob("*.zip"))
        self.assertEqual(len(archives), 8)
        for path in archives:
            with self.subTest(path=path.name), zipfile.ZipFile(path) as package:
                compressed = package.read(path.stem + "/agent_workspace.tar.gz")
                with tarfile.open(fileobj=io.BytesIO(compressed), mode="r:gz") as archive:
                    docs = {member.name.rsplit("/", 1)[-1]: archive.extractfile(member).read().decode("utf-8")
                            for member in archive.getmembers() if member.isfile() and
                            member.name.endswith(("TASK.md", "BACKTEST.md"))}
                task = agent_view.public_task(docs["TASK.md"])
                backtest = agent_view.public_backtest(docs["BACKTEST.md"], path.stem.startswith("a"))
                self.assertIn("strategy.py", task)
                self.assertIn("run_backtest.py", backtest)
                self.assertIsNone(agent_view.FORBIDDEN.search(task + backtest))
                if path.stem.startswith("a"):
                    if "task_config.json" in docs["BACKTEST.md"]:
                        self.assertIn("data/market.json", backtest)
                    self.assertNotIn("task_config.json", backtest)
                    self.assertNotIn("模型 API 自动调度", backtest)
                    market = json.loads(package.read(path.stem + "/data/market.json"))
                    operator_config = json.loads(package.read(path.stem + "/task_config.json"))
                    strategy_keys = set(operator_config) - {"research_fraction", "segments", "data_status", "cost_policy", "min_days"}
                    self.assertEqual({key: operator_config[key] for key in strategy_keys},
                                     {key: market[key] for key in strategy_keys})

    def test_feedback_packet_contains_results_without_operator_prompt(self):
        temp_parent = (ROOT / "benchmark").resolve()
        self.assertTrue(temp_parent.is_relative_to(ROOT.resolve()))
        with tempfile.TemporaryDirectory(dir=temp_parent) as temp_name:
            root = pathlib.Path(temp_name)
            reports, eval_run = root / "workspace/reports", root / "eval"
            reports.mkdir(parents=True)
            eval_run.mkdir()
            (reports / "segment_1.md").write_text(
                "# 仿真实盘第 1 段报告\n本段收益 5%。\n明细：reports/segment_1_equity.csv\n", encoding="utf-8")
            (reports / "segment_1_equity.csv").write_text("t,equity\n144,1050000\n", encoding="utf-8")
            (reports / "segment_1_trades.csv").write_text("t,side\n144,buy\n", encoding="utf-8")
            (eval_run / "log.json").write_text(json.dumps([{"segment": 1, "metrics":
                {"total_return": 0.05, "sharpe": 1.2, "max_drawdown": -0.03}, "num_errors": 0}]), encoding="utf-8")
            agent_view.publish_feedback(root / "workspace", eval_run, 1)
            prompt = agent_view.review_prompt(root / "workspace", eval_run, 1)
            self.assertIn("sharpe: 1.2", prompt)
            self.assertIn("策略运行反馈", prompt)
            self.assertIn("不是你自行调用工具得到的研发回测结果", prompt)
            self.assertNotIn("已完成一次回测", prompt)
            self.assertIn("策略运行错误次数: 0", prompt)
            self.assertFalse((reports / "segment_1.md").exists())
            self.assertIn("# 策略运行反馈", (reports / "feedback.md").read_text(encoding="utf-8"))
            self.assertIsNone(agent_view.FORBIDDEN.search(prompt +
                              (reports / "feedback.md").read_text(encoding="utf-8")))

    def test_existing_stock_report_can_be_published(self):
        source = pathlib.Path("D:/Learn/Project/changguiji_domo/demo_runner/audits/environment_fix/original_segment_1.md")
        with tempfile.TemporaryDirectory(dir=ROOT / "benchmark") as folder:
            (pathlib.Path(folder) / "TASK.md").write_text("策略研发任务", encoding="utf-8")
            (pathlib.Path(folder) / "BACKTEST.md").write_text("回测规则", encoding="utf-8")
            reports = pathlib.Path(folder) / "reports"
            reports.mkdir()
            (reports / "segment_1.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            for suffix in ("_equity.csv", "_trades.csv"):
                (reports / ("segment_1" + suffix)).write_text("t\n144\n", encoding="utf-8")
            agent_view.publish_feedback(pathlib.Path(folder), pathlib.Path(folder), 1)
            agent_view.verify_public(pathlib.Path(folder), False)

    def test_futures_market_config_excludes_schedule(self):
        source = {"kind": "futures", "initial_cash": 1000000, "segments": 7,
                  "research_fraction": 0.3, "max_margin_fraction": 0.8}
        public = agent_view.public_market(source)
        self.assertEqual(public["max_margin_fraction"], 0.8)
        self.assertNotIn("segments", public)
        self.assertNotIn("research_fraction", public)

    def test_audit_accepts_public_prompt_and_tool_budget_reminder(self):
        temp_parent = (ROOT / "benchmark").resolve()
        self.assertTrue(temp_parent.is_relative_to(ROOT.resolve()))
        with tempfile.TemporaryDirectory(dir=temp_parent) as temp_name:
            root = pathlib.Path(temp_name)
            prepared = root / "prepared/workspace"
            prepared.mkdir(parents=True)
            (prepared / "TASK.md").write_text("research task", encoding="utf-8")
            (prepared / "BACKTEST.md").write_text("trading rules", encoding="utf-8")
            result = root / "initial"
            result.mkdir()
            requirements = ["strategy.py", "RESEARCH.md", "追加 CHANGELOG.md", "最终策略的完整默认参数研发回测"]
            messages = [{"role": "system", "content": agent_view.SYSTEM},
                        {"role": "user", "content": "research task"}]
            for number, (name, args) in enumerate((("list_files", {}), ("read_file", {"path": "TASK.md"}),
                                                    ("write_file", {"path": "strategy.py", "content": "code"})), 1):
                messages.append({"role": "assistant", "content": "", "tool_calls":
                                 [{"id": str(number), "name": name, "arguments": args}]})
                messages.append({"role": "tool", "tool_call_id": str(number), "content": "ok"})
            messages.append({"role": "user", "content": agent_view.reminder(3, 5, True, requirements)})
            messages.append({"role": "user", "content": agent_view.time_reminder(1440, 2400, True)})
            messages.append({"role": "assistant", "content": "v1 delivered", "tool_calls": []})
            (result / "transcript.json").write_text(json.dumps({"messages": messages}, ensure_ascii=False),
                                                    encoding="utf-8")
            model = {"interaction_protocol": agent_view.PROTOCOL, "max_model_calls": 5,
                     "context_mode": "fresh", "execution_backend": "docker"}
            events = [{"phase": "prepared", "checkpoint": "prepared", "model": model},
                      {"phase": "initial_done", "checkpoint": "initial", "visible_part": 0, "model": model}]
            harness_stub = SimpleNamespace(run_root=lambda _id: root, load_events=lambda _root: events,
                                           package_kind=lambda: "stock_minute", agent_view=agent_view,
                                           MODEL_SYSTEM="legacy", research_evidence=research_evidence)
            audit = worker.visibility_audit(harness_stub, "test", {"research": [0, 144], "segments": []})
            self.assertEqual(audit["issues"], [])
            self.assertEqual(audit["stages"][0]["successful_tool_responses_checked"], 3)


if __name__ == "__main__":
    unittest.main()
