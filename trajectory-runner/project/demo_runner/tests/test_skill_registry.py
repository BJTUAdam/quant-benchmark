"""Skill 注册、兼容配置和核心工具隔离的离线检查。"""
from __future__ import annotations

import pathlib
import json
import sys
import unittest
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from skill_registry import SkillError, SkillRegistry, normalize_skills


class SkillRegistryTests(unittest.TestCase):
    def test_legacy_mcp_configuration_becomes_enabled_skill(self):
        servers = [{"name": "memory", "url": "http://127.0.0.1:8000/mcp", "token_env": "MEM_TOKEN"}]
        self.assertEqual(normalize_skills(None, servers), [
            {"name": "mcp", "enabled": True, "config": {"servers": servers}},
        ])

    def test_disabled_skill_is_not_loaded_or_exposed(self):
        registry = SkillRegistry([{"name": "mcp", "enabled": False, "config": {}}])
        self.assertEqual(registry.discover({"list_files"}), [])
        self.assertFalse(registry.handles("mcp_memory__memory_search"))

    def test_rejects_unknown_and_ambiguous_configuration(self):
        with self.assertRaisesRegex(SkillError, "未发现"):
            normalize_skills([{"name": "untrusted", "enabled": True, "config": {}}])
        with self.assertRaisesRegex(SkillError, "不能同时配置"):
            normalize_skills([{"name": "mcp", "enabled": False, "config": {}}], [])

    def test_skill_schema_cannot_replace_core_tool(self):
        class FakeSkill:
            def discover(self):
                return [{"name": "list_files", "description": "x", "parameters": {}}]

            def guidance(self):
                return ""

            def execute(self, *_args, **_kwargs):
                return "never"

        registry = SkillRegistry()
        registry._skills = [("fake", FakeSkill())]
        with self.assertRaisesRegex(SkillError, "核心工具冲突"):
            registry.discover({"list_files"})

    def test_mcp_skill_delegates_only_registered_tools(self):
        class FakeMcpTools:
            def __init__(self, servers):
                self.servers = servers
                self.guidance = ["只记录有证据的结论。"]

            def discover(self):
                return [{"name": "mcp_memory__memory_search", "description": "搜索", "parameters": {}}]

            def handles(self, name):
                return name == "mcp_memory__memory_search"

            def execute(self, name, arguments, timeout=30):
                return f"{name}:{arguments}:{timeout}"

        servers = [{"name": "memory", "url": "http://127.0.0.1:8000/mcp", "token_env": "MEM_TOKEN"}]
        with patch("skills.mcp.MCPTools", FakeMcpTools):
            registry = SkillRegistry([{"name": "mcp", "enabled": True, "config": {"servers": servers}}])
            schemas = registry.discover({"list_files"})
            self.assertEqual([item["name"] for item in schemas], ["mcp_memory__memory_search"])
            self.assertIn("有证据", registry.system_prompt("基础规则"))
            self.assertEqual(registry.execute("mcp_memory__memory_search", {"query": "x"}, timeout=3),
                             "mcp_memory__memory_search:{'query': 'x'}:3")

    def test_finlab_specialist_skills_are_discoverable_and_use_only_bound_core_tools(self):
        registry = SkillRegistry([
            {"name": "data_access", "enabled": True, "config": {}},
            {"name": "backtesting", "enabled": True, "config": {}},
            {"name": "strategy_evaluation", "enabled": True, "config": {}},
            {"name": "news_sentiment", "enabled": True, "config": {}},
        ])
        schemas = registry.discover({"query_data", "run_research", "run_local_backtest"})
        self.assertEqual([item["name"] for item in schemas], [
            "data_query_market", "backtest_run_local", "strategy_evaluate",
            "news_query_text", "news_score_sentiment",
        ])
        calls = []

        def core(name, arguments):
            calls.append((name, arguments))
            return json.dumps({"result": name}, ensure_ascii=False)

        registry.bind_core(core, {"query_data", "run_research", "run_local_backtest"})
        self.assertEqual(json.loads(registry.execute("data_query_market", {"api": "daily"}))["result"], "query_data")
        self.assertEqual(json.loads(registry.execute("backtest_run_local", {"code": "print(1)"}))["result"], "run_local_backtest")
        self.assertEqual(json.loads(registry.execute("strategy_evaluate", {"code": "print(1)"}))["result"], "run_research")
        self.assertEqual(json.loads(registry.execute("news_query_text", {"api": "news"}))["result"], "query_data")
        self.assertEqual(json.loads(registry.execute("news_score_sentiment", {"code": "print(1)"}))["result"], "run_research")
        self.assertEqual([name for name, _ in calls], [
            "query_data", "run_local_backtest", "run_research", "query_data", "run_research",
        ])

    def test_specialist_skill_fails_closed_when_harness_lacks_its_core_tool(self):
        registry = SkillRegistry([{"name": "data_access", "enabled": True, "config": {}}])
        registry.discover()
        called = []
        registry.bind_core(lambda name, arguments: called.append((name, arguments)) or "unexpected", set())
        result = json.loads(registry.execute("data_query_market", {"api": "daily"}))
        self.assertIn("不提供", result["error"])
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
