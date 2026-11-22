"""验证专项 Skill 在 v2 阶段中仍通过受控核心工具记录额度与轨迹。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import finlab_v2 as runner


class FinLabV2SpecialistSkillTests(unittest.TestCase):
    def test_specialist_calls_use_existing_tool_budgets_and_stage_audit_messages(self):
        class Backend:
            def __init__(self, cfg, root):
                self.root = root

            def call(self, action, **kwargs):
                replies = {
                    "status": {"research_visible_stop": "2400-01-01"},
                    "query": {"rows": []},
                    "check": {"passed": True, "errors": []},
                    "run": {"job_id": "test"},
                }
                return replies[action]

        class User:
            settings = {"timeout_seconds": 1}

            def __init__(self, *args):
                pass

            def begin(self, event_id, ctx, message_index, **kwargs):
                event = {"context": ctx}
                self.state["events"].append(event)
                return event

            def finish(self, record):
                return {"role": "user", "content": "请继续。"}

        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root = pathlib.Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            for name in runner.SUBMISSION:
                (workspace / name).write_text("initial", encoding="utf-8")
            (workspace / "TASK.md").write_text("test task", encoding="utf-8")
            cfg = runner.read(HERE / "finlab.v2.demo.example.json")
            cfg.update(max_model_calls=4, skills=[
                {"name": "data_access", "enabled": True, "config": {}},
                {"name": "backtesting", "enabled": True, "config": {}},
                {"name": "strategy_evaluation", "enabled": True, "config": {}},
                {"name": "news_sentiment", "enabled": True, "config": {}},
            ])
            calls = [
                {"id": "data", "name": "data_query_market", "arguments": {"api": "daily"}},
                {"id": "backtest", "name": "backtest_run_local", "arguments": {"code": "print(1)"}},
                {"id": "evaluate", "name": "strategy_evaluate", "arguments": {"code": "print(2)"}},
                {"id": "news", "name": "news_query_text", "arguments": {"api": "news"}},
                {"id": "score", "name": "news_score_sentiment", "arguments": {"code": "print(3)"}},
                {"id": "log", "name": "append_changelog", "arguments": {"content": "decision"}},
                {"id": "check", "name": "run_backtest", "arguments": {}},
            ]
            replies = iter([
                {"content": "", "tool_calls": calls},
                {"content": "完成", "tool_calls": []},
                {"content": "完成", "tool_calls": []},
            ])
            offered = []

            class Model:
                def __init__(self, cfg):
                    pass

                def complete(self, messages, tools):
                    offered.append(tools)
                    return next(replies)

            with patch.object(runner, "Backend", Backend), patch.object(runner, "UserAgent", User), \
                 patch.object(runner, "ModelClient", Model), \
                 patch("finlab_v2_adapter.sandbox_research", return_value=types.SimpleNamespace(
                     returncode=0, stdout="ok", stderr="")):
                stage = runner.run_stage(cfg, root, 0)

            self.assertEqual(stage["status"], "delivered")
            self.assertEqual((stage["data_queries"], stage["research_calls"], stage["evaluations"]), (2, 3, 1))
            self.assertEqual(len(offered[0]), len(runner.TOOLS) + 5)
            names = {message.get("tool_call_id") for message in stage["messages"] if message["role"] == "tool"}
            self.assertTrue({"data", "backtest", "evaluate", "news", "score"} <= names)
            self.assertIn("数据调用管理 Skill", stage["messages"][0]["content"])
            self.assertIn("新闻情绪评估 Skill", stage["messages"][0]["content"])
            self.assertEqual(json.loads(next(message["content"] for message in stage["messages"]
                                              if message.get("tool_call_id") == "data"))["budget"]["data_queries"], 1)

    def test_formal_failure_returns_to_agent_and_keeps_previous_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            for name in runner.SUBMISSION:
                (workspace / name).write_text("initial", encoding="utf-8")
            (workspace / "TASK.md").write_text("test task", encoding="utf-8")
            cfg = runner.read(HERE / "finlab.v2.demo.example.json")
            cfg.update(evidence_driven=False, skills=[])
            validated = []
            submitted = []
            requests = []
            platform = {"binding_hash": "binding", "images": {}, "completed_segment": 0,
                        "research_visible_stop": "2400-01-01"}

            class Backend:
                def call(self, action, **kwargs):
                    if action == "status":
                        return dict(platform)
                    if action == "check":
                        return {"passed": True, "submission_hash": "submission"}
                    if action == "run" and kwargs["research"]:
                        validated.append(kwargs["submission"])
                        return {"job_id": "research"}
                    assert action == "run"
                    submitted.append(pathlib.Path(kwargs["submission"]).name)
                    if len(submitted) == 1:
                        raise runner.BackendError("BACKTEST_FAILED", "ORDER_NOT_ACTIVE")
                    platform["completed_segment"] = 1
                    return {"job_id": "formal"}

                root = workspace.parent

            class Model:
                def __init__(self, cfg):
                    pass

                def complete(self, messages, tools):
                    requests.append(messages[-1]["content"])
                    attempt = len(requests)
                    if attempt in {2, 4}:
                        return {"content": "交付", "tool_calls": []}
                    calls = [
                        {"id": f"code_{attempt}", "name": "write_file",
                         "arguments": {"path": "strategy.py", "content": f"strategy version {attempt}"}},
                        {"id": f"log_{attempt}", "name": "append_changelog",
                         "arguments": {"content": f"decision {attempt}"}},
                        {"id": f"check_{attempt}", "name": "run_backtest", "arguments": {}},
                    ]
                    return {"content": "", "tool_calls": calls}

            backend = Backend()
            with patch.object(runner, "Backend", return_value=backend), \
                 patch.object(runner, "ModelClient", Model), patch.object(runner.time, "sleep") as sleep:
                stage, _, current = runner.complete_segment(cfg, root, backend, platform, 0)

            self.assertEqual(current["completed_segment"], 1)
            self.assertEqual(submitted, ["00", "00-retry-1"])
            self.assertEqual(len(validated), 2)
            self.assertIn("ORDER_NOT_ACTIVE", requests[2])
            self.assertNotIn("2400-", requests[2])
            self.assertAlmostEqual(sleep.call_args.args[0], 2, delta=0.25)
            self.assertEqual(stage["formal_retries"][0]["submission"],
                             runner.fingerprint(root / "deliveries/00", runner.SUBMISSION))
            self.assertEqual(stage["submission"], runner.fingerprint(root / "deliveries/00-retry-1", runner.SUBMISSION))

    def test_test_memory_reset_is_scoped_to_fresh_start(self):
        cfg = {"skills": [{"name": "mcp", "config": {"servers": [
            {"token_env": "FINLAB_ISOLATED_MEMORY_TOKEN"}]}}]}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(runner.os.environ, {"FINLAB_ISOLATED_MEMORY_TOKEN": "sample-token"}), \
             patch.object(runner.subprocess, "run", return_value=types.SimpleNamespace(stdout="3\n")) as reset:
            root = pathlib.Path(tmp)
            runner.initialize_test_memory(cfg, root)
            marker = root / "private/memory-start.json"
            self.assertEqual(runner.read(marker), {"deleted": 3})
            self.assertEqual(reset.call_args.kwargs["input"], "sample-token")
            self.assertNotIn("FINLAB_ISOLATED_MEMORY_TOKEN", reset.call_args.kwargs["env"])
            runner.initialize_test_memory(cfg, root)
            marker.unlink()
            (root / "stages").mkdir()
            runner.initialize_test_memory(cfg, root)
            reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
