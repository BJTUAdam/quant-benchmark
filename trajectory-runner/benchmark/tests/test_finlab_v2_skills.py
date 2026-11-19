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


if __name__ == "__main__":
    unittest.main()
