import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import demo
from agent_tools import AgentTools, TOOLS, safe_path, validate_strategy
import strategy_guard


class ToolTests(unittest.TestCase):
    def test_tool_schemas_and_path_boundary(self):
        self.assertEqual(len({t["name"] for t in TOOLS}), len(TOOLS))
        for tool in TOOLS:
            self.assertEqual(tool["parameters"]["type"], "object")
            self.assertFalse(tool["parameters"]["additionalProperties"])
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            with self.assertRaises(ValueError):
                safe_path(root, "../future/part_01.csv")
            with self.assertRaises(ValueError):
                safe_path(root, "/tmp/secret.key")
            (root / "link").symlink_to(pathlib.Path(temp).parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                safe_path(root, "link/secret.key")
            tools = AgentTools(root)
            self.assertIn("工具错误", tools.execute("read_file", {"path": "../future/part_01.csv"}))
            self.assertIn("工具错误", tools.execute("write_file", {"path": "../../bad.py", "content": "x"}))

    def test_strategy_guard(self):
        validate_strategy("import pandas as pd\nPARAMS={}\ndef decide(t,minute,bars,portfolio,params,memory):\n    return None\n")
        for source in ["import os", "open('/tmp/secret')", "a.__class__", "__builtins__"]:
            with self.assertRaises(ValueError):
                validate_strategy(source)

    def test_guard_denies_workspace_file_open(self):
        with tempfile.TemporaryDirectory() as temp:
            p = pathlib.Path(temp) / "ordinary.txt"
            p.write_text("test")
            with strategy_guard.no_io():
                with self.assertRaises(PermissionError):
                    p.read_text()

    def test_agent_initial_protocol_with_fake_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            ws = base / "star50_trend"
            ws.mkdir()
            strategy = "DECISION_TIMES=[1500]\nPARAMS={}\ndef decide(t,minute,bars,portfolio,params,memory):\n    return None\n"
            steps = [
                {"content": "", "tool_calls": [{"id": "t1", "name": "write_file", "arguments": {"path": "strategy.py", "content": strategy}}]},
                {"content": "", "tool_calls": [{"id": "t2", "name": "write_file", "arguments": {"path": "CHANGELOG.md", "content": "v1: 测试"}}]},
                {"content": "", "tool_calls": [{"id": "t3", "name": "write_file", "arguments": {"path": "RESEARCH.md", "content": "研究记录"}}]},
                {"content": "", "tool_calls": [{"id": "t4", "name": "run_backtest", "arguments": {}}]},
                {"content": "交付", "tool_calls": []},
            ]
            original = AgentTools.execute
            def execute(tool, name, args):
                if name == "run_backtest":
                    tool.passed_backtest = True
                    tool.passed_strategy_hash = hashlib.sha256((ws / "strategy.py").read_bytes()).hexdigest()
                    return "回测成功（仅单元测试模拟；未运行引擎）"
                return original(tool, name, args)
            class FakeClient:
                def __init__(self, cfg):
                    self.i = 0
                def complete(self, messages, tools):
                    result = steps[self.i]
                    self.i += 1
                    return result
            with patch.object(demo, "ModelClient", FakeClient), patch.object(AgentTools, "execute", execute), patch.object(demo, "assert_visible_parts"):
                demo.run_agent(base, ws, {"max_model_calls_per_phase": 6, "provider": "openai_compatible", "base_url": "https://example.test", "model": "unit-test"}, "initial", "任务说明")
            transcript = json.loads((base / "transcripts/initial.json").read_text())
            self.assertEqual(transcript["status"], "completed")
            self.assertEqual((ws / "RESEARCH.md").read_text(), "研究记录")


if __name__ == "__main__":
    unittest.main()
