import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from conversation import (ContextLimitError, begin_turn, finish_turn, input_budget,
                          new_session, observe_request, validate_history)
from model_client import prepare_request

CFG = {"provider": "openai_compatible", "base_url": "https://example.test", "model": "fake",
       "max_tokens": 1024, "context_window_tokens": 1000000, "context_margin_tokens": 4096}
TOOLS = [{"name": "read_file", "description": "read", "parameters": {"type": "object"}}]


class ConversationTests(unittest.TestCase):
    def test_full_replay_preserves_tool_results_reasoning_and_prefix(self):
        original = new_session("system")
        session, start = begin_turn(original, "first task")
        session["messages"].extend([
            {"role": "assistant", "content": "", "reasoning_content": "prior research",
             "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "TASK.md"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "past observations"},
            {"role": "assistant", "content": "v1 delivered", "tool_calls": [], "reasoning_content": "first decision"}])
        finish_turn(session, start, "initial", 0)
        prior = copy.deepcopy(session)
        continued, start = begin_turn(session, "segment1 ended")
        self.assertEqual(original["messages"], [{"role": "system", "content": "system"}])
        self.assertEqual(session, prior)
        self.assertEqual(continued["messages"][:-1], prior["messages"])
        _, _, payload = prepare_request(CFG, continued["messages"], TOOLS)
        self.assertEqual(payload["messages"][2]["reasoning_content"], "prior research")
        self.assertEqual(payload["messages"][3]["content"], "past observations")

    def test_budget_uses_api_usage_and_rejects_changed_prefix(self):
        session, _ = begin_turn(new_session("system"), "first task")
        budget = input_budget(CFG, session["messages"], TOOLS, None)
        observe_request(session, CFG, TOOLS, budget, 2, {"prompt_tokens": 1234}, "initial")
        session["messages"].append({"role": "assistant", "content": "done", "tool_calls": []})
        anchored = input_budget(CFG, session["messages"], TOOLS, session["last_request"])
        self.assertTrue(anchored["usage_anchored"])
        self.assertGreater(anchored["estimated_input_tokens"], 1234)
        changed = copy.deepcopy(session["messages"])
        changed[0]["content"] = "changed system"
        self.assertFalse(input_budget(CFG, changed, TOOLS, session["last_request"])["usage_anchored"])
        self.assertFalse(input_budget(CFG, session["messages"], [], session["last_request"])["usage_anchored"])

    def test_budget_includes_reasoning_and_output_reserve(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "ok", "reasoning_content": "研" * 2000}]
        with self.assertRaises(ContextLimitError):
            input_budget({**CFG, "context_window_tokens": 6000}, messages, TOOLS, None)

    def test_incomplete_or_orphan_tool_history_is_rejected(self):
        system = {"role": "system", "content": "s"}
        with self.assertRaisesRegex(RuntimeError, "没有对应"):
            validate_history([system, {"role": "tool", "tool_call_id": "missing", "content": "x"}])
        with self.assertRaisesRegex(RuntimeError, "未完成"):
            validate_history([system, {"role": "assistant", "tool_calls": [{"id": "pending"}]}])


if __name__ == "__main__":
    unittest.main()
