"""Offline end-to-end test of autonomous_harness: fake model, real simulator, temp run dir.

No network, no API key, no Claude. Runs the full prepare -> 7 segments -> summary path.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import autonomous_harness as h

STRATEGY = '''DECISION_TIMES = [1500]
PARAMS = {"lookback": 20, "top_n": 5, "weight": 0.18}

def decide(t, minute, bars, portfolio, params, memory):
    close = bars.daily.close
    if len(close) <= params["lookback"]:
        return None
    if t - int(memory.get("last", -99)) < 5:
        return None
    memory["last"] = int(t)
    mom = close.iloc[-1] / close.iloc[-params["lookback"] - 1] - 1
    mom = mom[bars.today.tradable & (mom > 0)]
    return {s: params["weight"] for s in mom.nlargest(params["top_n"]).index}
'''

CFG = {"provider": "openai_compatible", "base_url": "https://example.test", "model": "fake-model",
       "max_model_calls": 4}


class FakeClient:
    """Initial: write 3 files + backtest. Review: append changelog only (decision: no change)."""
    def __init__(self, cfg):
        self.step = 0
        self.initial = None

    def complete(self, messages, tools):
        if self.initial is None:
            prompt = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            self.initial = "TASK" in prompt[:200] or "# 任务" in prompt[:20]
        initial = self.initial
        self.step += 1
        if initial:
            script = [
                [("write_file", {"path": "strategy.py", "content": STRATEGY})],
                [("write_file", {"path": "RESEARCH.md", "content": "测试研究记录"}),
                 ("append_changelog", {"content": "## v1 · 研发期\n- 决策：新建"})],
                [("run_backtest", {})],
            ]
        else:
            script = [[("append_changelog", {"content": "## 复盘\n- 决策：不改\n- 理由：离线测试"})]]
        if self.step <= len(script):
            calls = [{"id": f"c{self.step}_{i}", "name": n, "arguments": a} for i, (n, a) in enumerate(script[self.step - 1])]
            return {"content": "", "tool_calls": calls}
        return {"content": "完成", "tool_calls": []}


class AutonomousHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-e2e-")
        self.runs = pathlib.Path(self.temp.name)
        self.patches = [patch.object(h, "RUNS", self.runs), patch.object(h, "ModelClient", FakeClient)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def test_guided_initial_delivery_reminder(self):
        cfg = {**CFG, "interaction_protocol": "guided_delivery_v1", "max_model_calls": 10}
        observed = []

        class GuidedClient:
            def __init__(self, config):
                self.calls = 0

            def complete(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    observed.append(messages[1]["content"])
                if self.calls <= 4:
                    return {"content": "", "tool_calls": [{"id": f"research-{self.calls}",
                            "name": "list_files", "arguments": {}}]}
                if self.calls == 5:
                    observed.append(messages[-1]["content"])
                    return {"content": "", "tool_calls": [
                        {"id": "strategy", "name": "write_file", "arguments": {"path": "strategy.py", "content": STRATEGY}},
                        {"id": "research", "name": "write_file", "arguments": {"path": "RESEARCH.md", "content": "测试研究记录"}},
                        {"id": "changelog", "name": "append_changelog", "arguments": {"content": "## v1\n- 决策：新建"}},
                        {"id": "backtest", "name": "run_backtest", "arguments": {}}]}
                return {"content": "已交付", "tool_calls": []}

        h.prepare("guided", cfg)
        with patch.object(h, "ModelClient", GuidedClient):
            event = h.step("guided", cfg)
        self.assertEqual(event["phase"], "initial_done")
        self.assertIn("本阶段最多 10 次模型回复", observed[0])
        self.assertEqual(observed[1], h.delivery_reminder(4, 10))
        transcript = json.loads((event["checkpoint_path"] / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(m["role"] == "user" and m["content"].startswith("交付进度提醒：")
                             for m in transcript["messages"]), 1)
        self.assertGreaterEqual(transcript["backtests"], 1)

    def test_bundled_review_replays_from_visible_checkpoint_and_locks_protocol(self):
        cfg = {**CFG, "interaction_protocol": "bundled_review_v1"}
        h.prepare("bundled", cfg)
        h.step("bundled", cfg)
        segment = h.step("bundled", cfg)
        self.assertEqual(segment["phase"], "segment1_done")
        with self.assertRaisesRegex(RuntimeError, "固定交互协议"):
            h.step("bundled", CFG)
        self.assertEqual(h.current(self.runs / "bundled")["phase"], "segment1_done")

        prompts = []
        class ReviewClient:
            def __init__(self, config):
                self.calls = 0

            def complete(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    prompts.append(messages[1]["content"])
                    return {"content": "", "tool_calls": [{"id": "review-log", "name": "append_changelog",
                            "arguments": {"content": "## 本轮复盘\n- 决策：不改\n- 理由：离线测试"}}]}
                return {"content": "不改", "tool_calls": []}

        with patch.object(h, "ModelClient", ReviewClient):
            review = h.step("bundled", cfg)
        self.assertEqual(review["phase"], "review1_done")
        expected = h.render_bundled_review_prompt(
            segment["checkpoint_path"] / "workspace",
            (segment["checkpoint_path"] / "eval_run/segments/1/next_prompt.md").read_text(encoding="utf-8"), 1)
        self.assertEqual(prompts, [expected])
        self.assertIn("reports/segment_1.md", expected)
        self.assertIn("strategy.py", expected)
        self.assertIn("RESEARCH.md", expected)
        self.assertIn("CHANGELOG.md", expected)
        self.assertNotIn("daily/part_02.csv", expected)
        transcript = json.loads((review["checkpoint_path"] / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(m["role"] == "assistant" for m in transcript["messages"]), 2)
        self.assertEqual(sum(m["role"] == "tool" and m["tool_call_id"] == "review-log" for m in transcript["messages"]), 1)

    def test_guided_review_delivery_reminder(self):
        cfg = {**CFG, "interaction_protocol": "guided_delivery_v2", "max_model_calls": 10}
        h.prepare("guided-review", cfg)
        h.step("guided-review", cfg)
        segment = h.step("guided-review", cfg)
        self.assertEqual(segment["phase"], "segment1_done")
        observed = []

        class GuidedReviewClient:
            def __init__(self, config):
                self.calls = 0

            def complete(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    observed.append(messages[1]["content"])
                if self.calls <= 4:
                    return {"content": "", "tool_calls": [{"id": f"file-{self.calls}",
                            "name": "list_files", "arguments": {}}]}
                if self.calls == 5:
                    observed.append(messages[-1]["content"])
                    return {"content": "", "tool_calls": [{"id": "review-log", "name": "append_changelog",
                            "arguments": {"content": "## 本轮复盘\n- 决策：不改\n- 理由：离线测试"}}]}
                return {"content": "不改", "tool_calls": []}

        with patch.object(h, "ModelClient", GuidedReviewClient):
            event = h.step("guided-review", cfg)
        self.assertEqual(event["phase"], "review1_done")
        self.assertIn("本轮复盘交付提示", observed[0])
        self.assertIn("本轮最多 10 次模型回复", observed[0])
        self.assertEqual(observed[1], h.review_delivery_reminder(4, 10))
        transcript = json.loads((event["checkpoint_path"] / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(m["role"] == "user" and m["content"].startswith("交付进度提醒：")
                             for m in transcript["messages"]), 1)
        self.assertEqual(transcript["backtests"], 0)

    def test_full_run_and_guards(self):
        h.prepare("e2e", CFG)
        with self.assertRaises(RuntimeError):
            h.prepare("e2e", CFG)  # never overwrite
        with self.assertRaisesRegex(RuntimeError, "固定模型"):
            h.step("e2e", {**CFG, "model": "other"})
        phases = []
        for _ in range(15):
            state = h.step("e2e", CFG)
            phases.append(state["phase"])
            if state["phase"] == "summary_done":
                break
        expected = ["initial_done"] + [p for k in range(1, 7) for p in (f"segment{k}_done", f"review{k}_done")] + ["segment7_done", "summary_done"]
        self.assertEqual(phases, expected)
        final = h.current(self.runs / "e2e")
        summary = json.loads((final["checkpoint_path"] / "eval_run" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(summary["log"]), 7)
        self.assertEqual(int(summary["agent"]["days"]), 335)
        changes = [e["change"] for e in summary["log"]]
        self.assertEqual(changes, ["新建"] + ["不改"] * 6)
        # checkpoints must not duplicate market data
        for cp in (self.runs / "e2e" / "checkpoints").iterdir():
            self.assertFalse((cp / "workspace" / "data").exists(), cp.name)
        self.assertEqual(h.step("e2e", CFG)["phase"], "summary_done")  # idempotent at end

    def test_invalid_arguments_do_not_execute_and_model_can_recover(self):
        from model_client import parse_response
        executed = []
        execute = h.AgentTools.execute
        testcase = self

        class CorrectingClient(FakeClient):
            def __init__(self, cfg):
                super().__init__(cfg)
                self.invalid_sent = False

            def complete(self, messages, tools):
                if not self.invalid_sent:
                    self.invalid_sent = True
                    return parse_response("openai_compatible", {"choices": [{"message": {"tool_calls": [
                        {"id": "invalid", "function": {"name": "list_files", "arguments": '{}{}'}}]}}]})
                if self.step == 0:
                    testcase.assertEqual(messages[-1]["tool_call_id"], "invalid")
                    testcase.assertIn("未执行", messages[-1]["content"])
                return super().complete(messages, tools)

        def tracked_execute(tools, name, arguments):
            executed.append(name)
            return execute(tools, name, arguments)

        cfg = {**CFG, "max_model_calls": 5}
        h.prepare("invalid-tool", cfg)
        with patch.object(h, "ModelClient", CorrectingClient), patch.object(h.AgentTools, "execute", tracked_execute):
            event = h.step("invalid-tool", cfg)
        self.assertEqual(event["phase"], "initial_done")
        self.assertNotIn("list_files", executed)
        transcript = json.loads((event["checkpoint_path"] / "transcript.json").read_text())
        invalid = transcript["messages"][2]["tool_calls"][0]
        self.assertEqual(invalid["raw_arguments"], '{}{}')
        self.assertTrue(transcript["strategy_sha256"])
        self.assertIn("run_backtest", executed)

    def test_failed_agent_does_not_advance(self):
        class Silent:
            def __init__(self, cfg): pass
            def complete(self, messages, tools): return {"content": "我不交付", "tool_calls": []}
        h.prepare("fail", CFG)
        with patch.object(h, "ModelClient", Silent):
            with self.assertRaisesRegex(RuntimeError, "未在模型调用上限内完成"):
                h.step("fail", CFG)
        self.assertEqual(h.current(self.runs / "fail")["phase"], "prepared")

    def test_budget_stop_keeps_last_checkpoint_and_cannot_change_budget(self):
        from usage_ledger import BudgetError
        policy={'budget_cny':20,'input_cny_per_million':0.8,'output_cny_per_million':2.7,
                'input_token_ceiling':1000000,'price_reference':'https://help.aliyun.com/zh/model-studio/model-pricing'}
        cfg={**CFG,'cost_control':policy,'output_token_parameter':'max_completion_tokens'}
        h.prepare('budget-stop',cfg)
        previous=h.current(self.runs/'budget-stop')
        with patch.object(FakeClient,'complete',side_effect=BudgetError('预算不足，未发送请求')):
            with self.assertRaisesRegex(BudgetError,'预算不足'):h.step('budget-stop',cfg)
        current=h.current(self.runs/'budget-stop')
        self.assertEqual(current['checkpoint'],previous['checkpoint'])
        self.assertEqual(len(h.load_events(self.runs/'budget-stop')),1)
        failure=json.loads((self.runs/'_last_failure.json').read_text())
        self.assertEqual(failure['turn'],'initial')
        with self.assertRaisesRegex(RuntimeError,'固定模型'):
            h.step('budget-stop',{**cfg,'cost_control':{**policy,'budget_cny':200}})

    def test_all_tool_calls_and_parameter_change(self):
        class InitialClient:
            def __init__(self, cfg):
                self.step = 0

            def complete(self, messages, tools):
                self.step += 1
                if self.step == 1:
                    calls = [("list_files", {}), ("read_file", {"path": "TASK.md"}),
                             ("list_files", {}), ("write_file", {"path": "strategy.py", "content": STRATEGY}),
                             ("write_file", {"path": "RESEARCH.md", "content": "研究记录"}),
                             ("append_changelog", {"content": "## v1\n- 决策：新建"}),
                             ("run_backtest", {})]
                    return {"content": "", "tool_calls": [
                        {"id": f"bulk_{i}", "name": name, "arguments": args}
                        for i, (name, args) in enumerate(calls)]}
                return {"content": "已交付", "tool_calls": []}

        class ParameterClient:
            def __init__(self, cfg):
                self.step = 0

            def complete(self, messages, tools):
                self.step += 1
                if self.step == 1:
                    calls = [("write_file", {"path": "strategy.py", "content": STRATEGY.replace('"lookback": 20', '"lookback": 30')}),
                             ("append_changelog", {"content": "## v2\n- 决策：调参"}),
                             ("run_backtest", {})]
                    return {"content": "", "tool_calls": [
                        {"id": f"param_{i}", "name": name, "arguments": args}
                        for i, (name, args) in enumerate(calls)]}
                return {"content": "调参", "tool_calls": []}

        h.prepare("changes", CFG)
        with patch.object(h, "ModelClient", InitialClient):
            first = h.step("changes", CFG)
        self.assertEqual(first["phase"], "initial_done")
        tool_results = json.loads((first["checkpoint_path"] / "transcript.json").read_text(encoding="utf-8"))["messages"]
        self.assertEqual(sum(m["role"] == "tool" for m in tool_results), 7)
        h.step("changes", CFG)  # segment 1
        with patch.object(h, "ModelClient", ParameterClient):
            review = h.step("changes", CFG)
        self.assertEqual(review["phase"], "review1_done")
        segment2 = h.step("changes", CFG)
        self.assertEqual(segment2["phase"], "segment2_done")
        log = json.loads((segment2["checkpoint_path"] / "eval_run" / "log.json").read_text(encoding="utf-8"))
        self.assertEqual(log[1]["change"], "调参")

    def test_run_reports_completion_after_fifteenth_transition(self):
        progress = {"steps": 0}

        def fake_current(root):
            return {"phase": "summary_done" if progress["steps"] == 15 else "in_progress",
                    "segments": 7 if progress["steps"] == 15 else 0}

        def fake_step(run_id, cfg):
            progress["steps"] += 1
            return fake_current(None)

        with patch.object(h, "current", side_effect=fake_current), \
             patch.object(h, "step", side_effect=fake_step), \
             patch.object(h, "load_config", return_value=CFG), \
             patch.object(sys, "argv", ["autonomous_harness.py", "run", "--run-id", "fifteen"]), \
             patch("builtins.print") as printed:
            h.main()
        self.assertEqual(progress["steps"], 15)
        printed.assert_any_call("RUN COMPLETED")

    def test_continuous_full_run_resume_and_failure(self):
        cfg = {**CFG, "context_mode": "continuous", "max_tokens": 1024,
               "context_window_tokens": 1000000, "context_margin_tokens": 4096}
        outer = self

        class RememberingClient(FakeClient):
            def complete(self, messages, tools):
                # Each stage gets a NEW client. Its first request must still
                # contain all preceding deliveries and their reasoning.
                if self.step == 0 and len(messages) > 2:
                    outer.assertTrue(any(m.get("reasoning_content") == "offline-reasoning-marker" for m in messages))
                    outer.assertTrue(any(m["role"] == "tool" for m in messages))
                    outer.assertTrue(any(m.get("content") == "完成" for m in messages))
                reply = super().complete(messages, tools)
                reply["reasoning_content"] = "offline-reasoning-marker"
                reply["usage"] = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}
                return reply

        h.prepare("continuous", cfg)
        with self.assertRaisesRegex(RuntimeError, "上下文模式"):
            h.step("continuous", CFG)
        root = self.runs / "continuous"
        prior = h.checkpoint_session(h.current(root))
        failed_review_checked = False
        for _ in range(15):
            event = h.current(root)
            if event["phase"] == "segment1_done":
                class Broken:
                    def __init__(self, cfg): pass
                    def complete(self, messages, tools):
                        raise h.ModelError("offline temporary failure")
                checkpoint_hash = h.sha256(event["checkpoint_path"] / "session.json")
                with patch.object(h, "ModelClient", Broken), self.assertRaisesRegex(h.ModelError, "temporary failure"):
                    h.step("continuous", cfg)
                unchanged = h.current(root)
                self.assertEqual(unchanged["seq"], event["seq"])
                self.assertEqual(h.sha256(unchanged["checkpoint_path"] / "session.json"), checkpoint_hash)
                self.assertTrue((self.runs / "_last_failure.json").is_file())
                failed_review_checked = True
            with patch.object(h, "ModelClient", RememberingClient):
                event = h.step("continuous", cfg)
            session = h.checkpoint_session(event)
            self.assertEqual(session["messages"][:len(prior["messages"])], prior["messages"])
            if not (event["phase"] == "initial_done" or event["phase"].startswith("review")):
                self.assertEqual(session, prior)  # simulation cannot reset/rewrite memory
            prior = session
        self.assertTrue(failed_review_checked)
        self.assertEqual(event["phase"], "summary_done")
        self.assertEqual([t["name"] for t in session["turns"]], ["initial"] + [f"review{k}" for k in range(1, 7)])
        self.assertEqual([t["visible_part"] for t in session["turns"]], list(range(7)))
        summary = json.loads((event["checkpoint_path"] / "eval_run" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(int(summary["agent"]["days"]), 335)
        self.assertTrue(all(row["num_errors"] == 0 for row in summary["log"]))
        # Missing history must fail closed, never silently start a fresh chat.
        (event["checkpoint_path"] / "session.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "持续会话缺失"):
            h.current(root)

    def test_shared_v1_is_research_only_and_first_segment_matches(self):
        h.prepare("source", CFG)
        initial = h.step("source", CFG)
        source = h.step("source", CFG)
        cfg = {**CFG, "model": "other-model"}
        h.prepare("shared", cfg, seed_run_id="source")
        prepared = h.current(self.runs / "shared")
        self.assertEqual(prepared["phase"], "prepared")
        proof = json.loads((prepared["checkpoint_path"] / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(proof["source"]["source_checkpoint"], initial["checkpoint"])
        self.assertFalse((prepared["checkpoint_path"] / "workspace/reports/segment_1.md").exists())
        # Corrupting a handoff cannot trigger re-development or advance the phase.
        research = prepared["checkpoint_path"] / "workspace/RESEARCH.md"
        original = research.read_text(encoding="utf-8")
        research.write_text(original + "\nchanged", encoding="utf-8")
        with patch.object(h, "ModelClient") as client:
            with self.assertRaisesRegex(RuntimeError, "交接文件"):
                h.step("shared", cfg)
            client.assert_not_called()
        self.assertEqual(h.current(self.runs / "shared")["seq"], 0)
        research.write_text(original, encoding="utf-8")
        with patch.object(h, "ModelClient") as client:
            imported = h.step("shared", cfg)
            client.assert_not_called()
        self.assertEqual(imported["phase"], "initial_done")
        for name in h.SHARED_INITIAL_FILES:
            self.assertEqual(h.sha256(initial["checkpoint_path"] / "workspace" / name),
                             h.sha256(imported["checkpoint_path"] / "workspace" / name))
        shared = h.step("shared", cfg)
        for name in ("equity.csv", "trades.csv"):
            self.assertEqual(h.sha256(source["checkpoint_path"] / "eval_run/segments/1" / name),
                             h.sha256(shared["checkpoint_path"] / "eval_run/segments/1" / name))
        import audit_formal_run
        record = audit_formal_run.audit_transcripts(h.load_events(self.runs / "shared"), self.runs / "shared")[0]
        self.assertEqual(record["initialization"], "shared_v1_import")
        self.assertEqual(record["model_calls"], 0)
        self.assertTrue(record["inherited_files_match_source"])

    def test_shared_v1_rejects_invalid_sources_without_creating_run(self):
        with self.assertRaisesRegex(RuntimeError, "initial_done"):
            h.prepare("missing-source", CFG, seed_run_id="missing")
        self.assertFalse((self.runs / "missing-source").exists())
        with self.assertRaisesRegex(RuntimeError, "fresh"):
            h.prepare("unsupported-context", {**CFG, "context_mode": "continuous"}, seed_run_id="missing")
        self.assertFalse((self.runs / "unsupported-context").exists())

    def test_context_exhaustion_does_not_call_model_or_commit(self):
        cfg = {**CFG, "context_mode": "continuous", "max_tokens": 1024,
               "context_window_tokens": 1025, "context_margin_tokens": 0}
        h.prepare("context-limit", cfg)
        root = self.runs / "context-limit"
        with patch.object(h, "ModelClient") as client:
            with self.assertRaisesRegex(h.ModelError, "上下文预算不足"):
                h.step("context-limit", cfg)
            client.return_value.complete.assert_not_called()
        self.assertEqual(h.current(root)["phase"], "prepared")
        failure = json.loads((self.runs / "_last_failure.json").read_text(encoding="utf-8"))
        self.assertIn("上下文预算不足", failure["error"])


if __name__ == "__main__":
    unittest.main()
