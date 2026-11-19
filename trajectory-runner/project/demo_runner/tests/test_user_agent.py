import pathlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import user_agent
import autonomous_harness as harness


SETTINGS = {"mode": "hybrid_v1", "max_model_calls": 3,
            "max_tokens": 512, "timeout_seconds": 30}
QUOTA = {"remaining": 2}


class ReplyClient:
    def __init__(self, cfg, content="请结合反馈完成本轮复盘。", fail=False):
        self.content = content
        self.fail = fail

    def complete(self, messages, tools):
        if self.fail:
            raise RuntimeError("provider detail must not be copied")
        return {"content": self.content, "tool_calls": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8}}


def config():
    return {"user_agent": dict(SETTINGS), "max_tokens": 32768,
            "timeout_seconds": 300, "model": "test", "_usage_turn": "review1"}


class UserAgentTests(unittest.TestCase):
    def test_finish_and_recovery_never_repeat_request(self):
        factory = unittest.mock.Mock(side_effect=ReplyClient)
        agent = user_agent.UserAgent(config(), factory)
        ctx = user_agent.context("review", "公开反馈", [], QUOTA, 0, 96)
        record = agent.begin("review1:review:0", ctx, 2, True)
        first = agent.finish(record)
        self.assertEqual(agent.finish(record), first)
        self.assertEqual(factory.call_count, 1)
        pending = agent.begin("review1:delivery:1", user_agent.context(
            "delivery", "公开反馈", ["strategy.py"], QUOTA, 1, 95), 4, True)
        agent.finish(pending, recovering=True)
        agent.finish(pending)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(pending["reason"], "interrupted_request")

    def test_question_context_and_template(self):
        messages = [{"role": "assistant", "content": "是否需要调整策略？"}]
        ctx = user_agent.context("delivery", "公开任务", ["strategy.py"], QUOTA, 1, 95,
                                 messages=messages)
        self.assertEqual(ctx["dialogue"]["recent_replies"], ["是否需要调整策略？"])
        self.assertIn("说明假设", user_agent.template(ctx))
        messages += [{"role": "assistant", "content": "下一段行情和 sk-example 不应进入上下文"}]
        self.assertEqual(user_agent.dialogue_context(messages)["recent_replies"], ["是否需要调整策略？"])

    def test_tool_error_event_is_auditable_and_context_tampering_rejected(self):
        agent = user_agent.UserAgent(config(), ReplyClient)
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "公开任务"}]
        start = agent.begin("initial:initial:0", user_agent.context(
            "initial", "公开任务", [], QUOTA, 0, 96, messages), 2, True)
        messages.append(agent.finish(start))
        messages.extend([
            {"role": "assistant", "content": "检查当前策略", "tool_calls": [
                {"id": "tool1", "name": "run_backtest", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "tool1", "content": "工具错误：内部错误详情"}])
        ctx = user_agent.context("tool_error", "公开任务", [], QUOTA, 1, 95, messages)
        record = agent.begin("initial:tool_error:1", ctx, len(messages), True)
        messages.append(agent.finish(record))
        self.assertEqual(ctx["dialogue"]["failed_tools"], ["run_backtest"])
        self.assertNotIn("内部错误详情", json.dumps(ctx, ensure_ascii=False))
        self.assertEqual(user_agent.audit(agent.state, messages, SETTINGS, "公开任务",
                                         True, "initial", 80, 40), {2, 5})
        ctx["dialogue"]["recent_replies"] = ["伪造的发言"]
        with self.assertRaises(ValueError):
            user_agent.audit(agent.state, messages, SETTINGS, "公开任务", True, "initial", 80, 40)

    def test_package_binding_validates_before_replacing_current_task(self):
        with tempfile.TemporaryDirectory() as folder:
            package = pathlib.Path(folder)
            (package / "data").mkdir()
            path = package / "data/splits.json"
            splits = {"research": [0, 3], "segments": [[i, i + 1] for i in range(3, 10)], "num_days": 10}
            path.write_text(json.dumps(splits), encoding="utf-8")
            with patch.object(harness, "PACKAGE", harness.PACKAGE), patch.object(harness, "SPLITS", harness.SPLITS):
                harness.bind_package(package)
                self.assertEqual(harness.visible_end_t(7), 9)
                splits["segments"][2][0] = 0
                path.write_text(json.dumps(splits), encoding="utf-8")
                with self.assertRaises(ValueError):
                    harness.bind_package(package)
                self.assertEqual(harness.SPLITS["segments"][2], [5, 6])

    def test_initial_task_is_fixed_and_auditable(self):
        agent = user_agent.UserAgent(config(), ReplyClient)
        ctx = user_agent.context("initial", "公开任务", [], QUOTA, 0, 96)
        record = agent.begin("initial:initial:0", ctx, 2, allow_model=True)
        messages = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "公开任务"}, agent.finish(record)]
        self.assertFalse(record["attempted"])
        self.assertEqual(record["reason"], "rule")
        self.assertEqual(user_agent.audit(agent.state, messages, SETTINGS, "公开任务",
                                          True, "initial", 80, 40), {2})

    def test_review_uses_model_without_tools(self):
        seen = {}

        class Client(ReplyClient):
            def complete(self, messages, tools):
                seen["messages"], seen["tools"], seen["cfg"] = messages, tools, self
                return super().complete(messages, tools)

        agent = user_agent.UserAgent(config(), Client)
        ctx = user_agent.context("review", "公开反馈", [], QUOTA, 0, 96)
        record = agent.begin("review1:review:0", ctx, 2, allow_model=True)
        messages = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "公开反馈"}, agent.finish(record)]
        self.assertEqual(record["reason"], "model")
        self.assertEqual(seen["tools"], [])
        self.assertEqual(user_agent.audit(agent.state, messages, SETTINGS, "公开反馈",
                                          False, "review1", 80, 40), {2})

    def test_invalid_or_failed_output_uses_template(self):
        for factory, reason in ((lambda cfg: ReplyClient(cfg, "建议买入并使用10日均线"), "invalid_output"),
                                (lambda cfg: ReplyClient(cfg, fail=True), "request_failed")):
            agent = user_agent.UserAgent(config(), factory)
            ctx = user_agent.context("review", "公开反馈", [], QUOTA, 0, 96)
            record = agent.begin("review1:review:0", ctx, 2, allow_model=True)
            message = agent.finish(record)
            self.assertEqual(record["reason"], reason)
            self.assertNotIn("provider detail", message["content"])
            self.assertEqual(record["text"], user_agent.template(ctx))

    def test_delivery_is_limited_to_two_model_attempts(self):
        agent = user_agent.UserAgent(config(), ReplyClient)
        for completed in (1, 2, 3):
            ctx = user_agent.context("delivery", "公开任务", ["追加 CHANGELOG.md"],
                                     QUOTA, completed, 96 - completed)
            record = agent.begin(f"initial:delivery:{completed}", ctx, completed + 2,
                                 allow_model=True)
            agent.finish(record)
        self.assertEqual([r["attempted"] for r in agent.state["events"]], [True, True, False])
        self.assertEqual(agent.state["model_calls"], 2)

    def test_audit_rejects_tampered_message(self):
        agent = user_agent.UserAgent(config(), ReplyClient)
        ctx = user_agent.context("initial", "公开任务", [], QUOTA, 0, 96)
        record = agent.begin("initial:initial:0", ctx, 2, allow_model=True)
        messages = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "公开任务"}, agent.finish(record)]
        messages[2]["content"] += "已篡改"
        with self.assertRaises(ValueError):
            user_agent.audit(agent.state, messages, SETTINGS, "公开任务", True,
                             "initial", 80, 40)

    def test_harness_injects_start_and_delivery_messages(self):
        self._run_harness_delivery(False)

    def test_harness_recovers_after_tool_error(self):
        self._run_harness_delivery(True)

    def _run_harness_delivery(self, fail_backtest):
        with tempfile.TemporaryDirectory(dir=HERE / "tests") as folder:
            workspace = pathlib.Path(folder)
            (workspace / "CHANGELOG.md").write_text("# versions\n", encoding="utf-8")

            class Tools:
                def __init__(self, workspace, **_kwargs):
                    self.workspace = workspace
                    self.backtests = self.research_calls = self.strategy_evaluations = 0
                    self.passed_backtest = False
                    self.passed_strategy_hash = None

                def execute(self, name, args):
                    if name == "write_file":
                        (self.workspace / args["path"]).write_text(args["content"], encoding="utf-8")
                        return "已写入 " + args["path"]
                    elif name == "append_changelog":
                        with (self.workspace / "CHANGELOG.md").open("a", encoding="utf-8") as output:
                            output.write("\n" + args["content"] + "\n")
                        return "已在 CHANGELOG.md 末尾追加版本记录（旧内容未改动）"
                    elif name == "run_backtest":
                        self.backtests += 1
                        self.strategy_evaluations += 1
                        if fail_backtest and self.backtests == 1:
                            return "工具错误：测试执行失败"
                        self.passed_backtest = True
                        self.passed_strategy_hash = harness.sha256(self.workspace / "strategy.py")
                        return "完整默认参数研发回测成功"
                    return "ok"

            class Client:
                tested_calls = 0

                def __init__(self, cfg):
                    self.user_side = str(cfg.get("_usage_turn", "")).startswith("user_agent:")

                def complete(self, _messages, _tools):
                    if self.user_side:
                        if _messages[0]["content"] == user_agent.REVIEW_SYSTEM:
                            return {"content": json.dumps({"decision": "pass", "issues": [], "summary": "交付说明与提供的证据一致。"}), "tool_calls": []}
                        return {"content": "请补齐交付项并提交当前方案。", "tool_calls": [],
                                "usage": {"prompt_tokens": 8, "completion_tokens": 8}}
                    Client.tested_calls += 1
                    if Client.tested_calls == 1:
                        return {"content": "先提交", "tool_calls": []}
                    if Client.tested_calls == 2:
                        return {"content": "", "tool_calls": [
                            {"id": "s", "name": "write_file", "arguments":
                             {"path": "strategy.py", "content": "PARAMS = {}\n"}},
                            {"id": "r", "name": "write_file", "arguments":
                             {"path": "RESEARCH.md", "content": "research\n"}},
                            {"id": "c", "name": "append_changelog", "arguments":
                             {"content": "## v1\n- initial"}},
                            {"id": "b", "name": "run_backtest", "arguments": {}},
                        ]}
                    if fail_backtest and Client.tested_calls == 3:
                        return {"content": "修复后重试", "tool_calls": [
                            {"id": "retry", "name": "run_backtest", "arguments": {}}]}
                    return {"content": "完成交付", "tool_calls": []}

            cfg = {"interaction_protocol": "environment_feedback_v1", "max_model_calls": 5,
                   "max_tokens": 32768, "timeout_seconds": 300, "model": "test",
                   "user_agent": dict(SETTINGS)}
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", Client), \
                 patch.object(harness, "validate_strategy", return_value=None):
                result = harness.agent_loop(workspace, cfg, "公开任务", True, max_visible_t=10)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["delivery_review"]["decision"], "accepted")
            self.assertEqual(result["delivery_review"]["semantic_quality"], "pass")
            self.assertEqual(result["user_agent"]["model_calls"], 3 if fail_backtest else 2)
            self.assertEqual(sum(r["context"]["event"] == "tool_error"
                                 for r in result["user_agent"]["events"]), int(fail_backtest))
            user_agent.audit(result["user_agent"], result["messages"], SETTINGS,
                             "公开任务", True, "initial", 5, 40, result["memory_input"])
            harness.research_evidence.audit_evidence(result, {"strategy.py": None,
                "RESEARCH.md": None, "CHANGELOG.md": "# versions\n"}, 10)
            user_messages = [m["content"] for m in result["messages"] if m["role"] == "user"]
            self.assertTrue(any(m.startswith("用户请求：请按照以上任务要求") for m in user_messages))
            self.assertTrue(any("交付校验缺失项" in m for m in user_messages))


if __name__ == "__main__":
    unittest.main()
