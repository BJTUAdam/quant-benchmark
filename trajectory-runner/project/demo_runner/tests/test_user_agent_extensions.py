"""Offline checks for memory, evidence, independent billing and review recovery."""
import copy
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import autonomous_harness as harness
import research_evidence as evidence
import user_agent
from usage_ledger import UsageLedger, BudgetError, totals

SETTINGS = {"mode": "hybrid_v1", "max_model_calls": 3, "max_tokens": 512, "timeout_seconds": 30}
PRICE = {"budget_cny": 0.006, "input_cny_per_million": 1, "output_cny_per_million": 2,
         "input_token_ceiling": 1024, "price_reference": "https://example.com/prices"}


def config():
    return {"provider": "openai_compatible", "base_url": "https://actor.example.com/v1", "model": "actor",
            "api_key_env": "ACTOR_KEY", "api_key_file": "actor-only.txt", "max_tokens": 2048,
            "timeout_seconds": 300, "output_token_parameter": "max_completion_tokens", "cost_control": PRICE,
            "user_agent": {**SETTINGS, "model_config": {
                "provider": "openai_compatible", "base_url": "https://user.example.com/v1", "model": "user",
                "api_key_env": "USER_KEY", "output_token_parameter": "max_completion_tokens",
                "cost_control": {**PRICE, "input_cny_per_million": 3}}}}


class UserAgentExtensionTests(unittest.TestCase):
    def test_independent_provider_uses_own_credentials_and_shared_budget(self):
        cfg = config()
        request = user_agent.UserAgent(cfg, None).request_config()
        self.assertEqual(request["model"], "user")
        self.assertEqual(request["api_key_env"], "USER_KEY")
        self.assertNotIn("api_key_file", request)
        self.assertEqual(request["_request_attempts"], 1)
        with tempfile.TemporaryDirectory() as folder:
            actor = UsageLedger(pathlib.Path(folder) / "usage.jsonl", cfg)
            other = UsageLedger(actor.path, request)
            self.assertEqual(actor.header, other.header)
            # Windows has no fcntl: stub only locking for this single-process
            # accounting test. Production locking remains Linux-only and unchanged.
            try:
                import fcntl
            except ImportError:
                fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_UN=2, flock=lambda *_args: None)
            with patch.dict(sys.modules, {"fcntl": fcntl}):
                first = actor.reserve("actor", 1)
                actor.settle(first, {"prompt_tokens": 100, "completion_tokens": 100})
                second = other.reserve("user", 1)
                other.settle(second, {"prompt_tokens": 100, "completion_tokens": 100})
                records = [json.loads(line) for line in actor.path.read_text().splitlines()]
                self.assertEqual(float(totals(records)["estimated_cny"]), 0.0008)
                actor.reserve("actor-pending", 1)
                with self.assertRaises(BudgetError):
                    other.reserve("user-over-budget", 1)
                changed = copy.deepcopy(request)
                changed["_ledger_owner"] = copy.deepcopy(cfg)
                changed["_ledger_owner"]["user_agent"]["model_config"]["model"] = "changed"
                with self.assertRaises(BudgetError):
                    UsageLedger(actor.path, changed).reserve("user", 2)

    def test_independent_budget_cannot_silently_expand_or_inherit_prices(self):
        cfg = config()
        del cfg["user_agent"]["model_config"]["cost_control"]
        with self.assertRaises(ValueError):
            user_agent.UserAgent(cfg, None)
        cfg = config()
        cfg["user_agent"]["model_config"]["api_key"] = "not-allowed"
        with self.assertRaises(ValueError):
            user_agent.validate_config(cfg["user_agent"])

    def test_review_format_failures_and_recovery_are_explicit(self):
        cfg = {"user_agent": SETTINGS, "max_tokens": 2048}
        class Client:
            calls = 0
            def __init__(self, _cfg): pass
            def complete(self, *_args):
                Client.calls += 1
                return {"content": "not json", "tool_calls": []}
        agent = user_agent.UserAgent(cfg, Client)
        record = agent.begin_review({}, 2, True)
        self.assertEqual(agent.finish_review(record)["decision"], "not_assessed")
        agent.finish_review(record)
        self.assertEqual(Client.calls, 1)
        other = user_agent.UserAgent(cfg, Client)
        pending = other.begin_review({}, 2, True)
        self.assertEqual(other.finish_review(pending, recovering=True)["decision"], "not_assessed")
        self.assertEqual(Client.calls, 1)
        self.assertEqual(pending["reason"], "interrupted_request")

    def test_memory_is_bounded_and_uses_only_public_prior_documents(self):
        files = {"RESEARCH.md": "历史假设\n" * 2000, "CHANGELOG.md": "## 保持\n已解释理由\n下一段不可见\nsk-example",
                 "strategy.py": "hidden_code"}
        memory = evidence.memory_from_files(files)
        self.assertEqual([s["file"] for s in memory["sources"]], ["RESEARCH.md", "CHANGELOG.md"])
        self.assertTrue(memory["sources"][0]["truncated"])
        self.assertLessEqual(len(memory["sources"][0]["excerpt"]), 4000)
        self.assertNotIn("下一段", json.dumps(memory, ensure_ascii=False))
        self.assertNotIn("sk-example", json.dumps(memory))
        updated = {**files, "CHANGELOG.md": files["CHANGELOG.md"] + "\n## 调整\n新的证据"}
        self.assertNotEqual(evidence.memory_from_files(updated), memory)

    def test_evidence_binds_parameters_range_result_and_failed_run(self):
        call = {"id": "b", "name": "run_backtest", "arguments": {"params": {"window": 8}, "start": 2, "end": 10}}
        record = evidence.evidence_record(call, "研发回测成功（不计为交付验证）", "abc", 20, 1)
        self.assertEqual(record["parameters"], {"params": {"window": 8}})
        self.assertEqual(record["range"], [2, 10])
        self.assertFalse(record["full_default_validation"])
        failed = evidence.evidence_record({**call, "arguments": {}}, "回测失败，退出码 1", "abc", 20, 2)
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["full_default_validation"])

    def test_review_inherits_memory_and_allows_unchanged_strategy(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = pathlib.Path(folder)
            for name, content in {"strategy.py": "PARAMS = {}\n", "RESEARCH.md": "已有实验结论",
                                  "CHANGELOG.md": "## v1\n初始依据\n"}.items():
                (workspace / name).write_text(content, encoding="utf-8")
            initial_files = harness._progress_workspace(workspace)
            class Tools:
                def __init__(self, workspace, **_kwargs):
                    self.workspace = workspace
                    self.backtests = self.research_calls = self.strategy_evaluations = 0
                    self.passed_backtest = False
                    self.passed_strategy_hash = None
                def execute(self, name, args):
                    if name != "append_changelog":
                        raise AssertionError("unchanged strategy needs no new backtest")
                    with (self.workspace / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
                        handle.write("\n" + args["content"].strip() + "\n")
                    return "已在 CHANGELOG.md 末尾追加版本记录（旧内容未改动）"
            class Client:
                def __init__(self, cfg):
                    self.user = str(cfg.get("_usage_turn", "")).startswith("user_agent:")
                    self.calls = 0
                def complete(self, messages, _tools):
                    if self.user:
                        if messages[0]["content"] == user_agent.REVIEW_SYSTEM:
                            return {"content": json.dumps({"decision": "uncertain", "issues": ["insufficient_context"],
                                                           "summary": "本次未展示历史实验的完整结果。"}), "tool_calls": []}
                        return {"content": "请依据历史记录继续复盘。", "tool_calls": []}
                    self.calls += 1
                    if self.calls == 1:
                        return {"content": "维持", "tool_calls": [{"id": "c", "name": "append_changelog",
                                 "arguments": {"content": "## v2\n历史判断仍成立，保持策略。"}}]}
                    return {"content": "保持策略，理由见决策日志。", "tool_calls": []}
            cfg = {"user_agent": SETTINGS, "max_tokens": 2048, "max_model_calls": 5,
                   "interaction_protocol": "environment_feedback_v1"}
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", Client):
                result = harness.agent_loop(workspace, cfg, "本次公开运行反馈", False, turn_name="review1", max_visible_t=20)
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["changed"])
            self.assertEqual(result["backtests"], 0)
            self.assertEqual(result["delivery_review"]["semantic_quality"], "uncertain")
            self.assertEqual(result["memory_input"], evidence.memory_from_files(initial_files))
            evidence.audit_evidence(result, initial_files, 20)

    def test_batch_accepts_independent_model_without_running(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / "benchmark"))
        import batch
        example = json.loads((root / "examples/matrix.t01.example.json").read_text(encoding="utf-8"))
        spec = {"schema_version": 1, "batch_id": "offline", "mode": "real", "track": "end_to_end",
                "repeats": 1, "policy": example["policy"], "models": example["models"]}
        spec["policy"]["user_agent"]["model_config"] = {
            "provider": "openai_compatible", "base_url": "https://user.example.com", "model": "user", "api_key_env": "USER_KEY"}
        self.assertEqual(batch.validate_spec(spec), spec)

    def test_semantic_interruption_resumes_without_repeating_any_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "CHANGELOG.md").write_text("# history\n", encoding="utf-8")
            initial_files = harness._progress_workspace(workspace)
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
                    if name == "append_changelog":
                        with (self.workspace / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
                            handle.write("\n" + args["content"].strip() + "\n")
                        return "已在 CHANGELOG.md 末尾追加版本记录（旧内容未改动）"
                    self.backtests += 1
                    self.strategy_evaluations += 1
                    self.passed_backtest = True
                    self.passed_strategy_hash = harness.sha256(self.workspace / "strategy.py")
                    return "完整默认参数研发回测成功"
            class Client:
                def __init__(self, _cfg): self.calls = 0
                def complete(self, messages, _tools):
                    if messages[0]["content"] == user_agent.REVIEW_SYSTEM:
                        raise KeyboardInterrupt()
                    self.calls += 1
                    if self.calls == 1:
                        return {"content": "研究并交付", "tool_calls": [
                            {"id": "s", "name": "write_file", "arguments": {"path": "strategy.py", "content": "PARAMS = {}\n"}},
                            {"id": "r", "name": "write_file", "arguments": {"path": "RESEARCH.md", "content": "记录实验依据"}},
                            {"id": "c", "name": "append_changelog", "arguments": {"content": "## v1\n初始版本"}},
                            {"id": "b", "name": "run_backtest", "arguments": {}}]}
                    return {"content": "完成交付，依据见研究记录。", "tool_calls": []}
            cfg = {"user_agent": SETTINGS, "max_tokens": 2048, "max_model_calls": 5, "model": "test",
                   "interaction_protocol": "environment_feedback_v1"}
            live = root / "live.json"
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", Client):
                with self.assertRaises(KeyboardInterrupt):
                    harness.agent_loop(workspace, cfg, "任务", True, max_visible_t=10, progress_path=live, progress_key="cp")
            saved = json.loads(live.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "submission_review_request")
            harness._restore_progress_workspace(workspace, initial_files)
            class NoRequests:
                def __init__(self, _cfg): pass
                def complete(self, *_args): raise AssertionError("must not repeat actor or reviewer")
            with patch.object(harness, "AgentTools", Tools), patch.object(harness, "ModelClient", NoRequests):
                result = harness.agent_loop(workspace, cfg, "任务", True, max_visible_t=10, progress_path=live, progress_key="cp")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["delivery_review"]["semantic_quality"], "not_assessed")
            self.assertEqual(result["user_agent"]["model_calls"], 1)
            evidence.audit_evidence(result, initial_files, 10)
            tampered = copy.deepcopy(result)
            tampered["research_evidence"][0]["strategy_sha256"] = "fake"
            with self.assertRaises(ValueError):
                evidence.audit_evidence(tampered, initial_files, 10)


if __name__ == "__main__":
    unittest.main()
