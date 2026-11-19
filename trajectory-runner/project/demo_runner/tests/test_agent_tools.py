"""Checks that research remains usable without certifying an untested strategy."""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agent_tools import AgentTools, sandbox_research


class AgentToolTests(unittest.TestCase):
    def test_sweep_does_not_certify_default_strategy(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = pathlib.Path(temp)
            (workspace / "strategy.py").write_text(
                "PARAMS = {'lookback': 20}\n"
                "def decide(t, minute, bars, portfolio, params, memory):\n"
                "    return None\n", encoding="utf-8")
            tools = AgentTools(workspace)
            success = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with patch("agent_tools.sandbox_command", return_value=success) as run:
                self.assertIn("研究回测成功", tools.execute("run_backtest", {"sweep": {"lookback": [10, 20]}}))
                self.assertIsNone(tools.passed_strategy_hash)
                self.assertIn("完整默认参数回测成功", tools.execute("run_backtest", {}))
                self.assertIsNotNone(tools.passed_strategy_hash)
            self.assertIn("--sweep", run.call_args_list[0].args[2])

    def test_research_cannot_bypass_metered_strategy_evaluation(self):
        with tempfile.TemporaryDirectory() as temp:
            tools = AgentTools(pathlib.Path(temp), max_research_calls=3)
            with patch("agent_tools.sandbox_research") as run:
                result = tools.execute("run_research", {"code": "import harness; harness.run('strategy.py')"})
            self.assertIn("工具错误", result)
            self.assertIn("策略评估", result)
            run.assert_not_called()
            self.assertEqual(tools.research_calls, 0)

    def test_sweep_and_total_strategy_evaluation_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = pathlib.Path(temp)
            (workspace / "strategy.py").write_text(
                "PARAMS = {'lookback': 20}\n"
                "def decide(t, minute, bars, portfolio, params, memory):\n"
                "    return None\n", encoding="utf-8")
            tools = AgentTools(workspace, max_strategy_evaluations=5, max_sweep_evaluations=3)
            success = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with patch("agent_tools.sandbox_command", return_value=success) as run:
                rejected = tools.execute("run_backtest", {"sweep": {"lookback": [5, 10, 20, 30]}})
                self.assertIn("单次 sweep 最多 3 组", rejected)
                self.assertEqual(tools.strategy_evaluations, 0)
                accepted = tools.execute("run_backtest", {"sweep": {"lookback": [5, 10, 20]}})
                self.assertIn("策略评估累计 3/5", accepted)
                exhausted = tools.execute("run_backtest", {"sweep": {"lookback": [5, 10, 20]}})
                self.assertIn("只剩 2 次策略评估额度", exhausted)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(tools.strategy_evaluations, 3)

    def test_research_can_read_visible_workspace_but_cannot_change_it(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = pathlib.Path(temp)
            task = workspace / "TASK.md"
            task.write_text("visible", encoding="utf-8")
            read = sandbox_research(workspace, "from pathlib import Path; print(Path('TASK.md').read_text())")
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertIn("visible", read.stdout)
            write = sandbox_research(workspace, "from pathlib import Path; Path('TASK.md').write_text('changed')")
            self.assertNotEqual(write.returncode, 0)
            self.assertEqual(task.read_text(encoding="utf-8"), "visible")

    def test_default_load_bars_works_without_writable_data_cache(self):
        import autonomous_harness as h
        with tempfile.TemporaryDirectory() as name:
            workspace = h.extract_initial(pathlib.Path(name) / "initial")
            code = ("from backtester import load_bars\n"
                    "bars=load_bars('data')\n"
                    "from backtester.engine import load_bars as engine_load_bars\n"
                    "assert engine_load_bars('data',True,0).num_days==144\n"
                    "print('visible_days='+str(bars.num_days))\n")
            result = sandbox_research(workspace, code)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("visible_days=144", result.stdout)
            self.assertFalse((workspace / "data/.cache").exists())

    def test_spearman_research_dependency_available(self):
        with tempfile.TemporaryDirectory() as name:
            result = sandbox_research(pathlib.Path(name),
                "import pandas as pd; print(pd.Series([1,2,3]).corr(pd.Series([1,3,2]),method='spearman'))")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertAlmostEqual(float(result.stdout), .5)


if __name__ == "__main__":
    unittest.main()
