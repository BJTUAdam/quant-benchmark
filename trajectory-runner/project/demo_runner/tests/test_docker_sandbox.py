"""Offline Docker boundary tests; pass the built image ID via FINANCIAL_DOCKER_IMAGE."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import autonomous_harness as h
from agent_tools import sandbox_command, sandbox_research
from docker_sandbox import execute, inspect_image


IMAGE = os.environ.get("FINANCIAL_DOCKER_IMAGE")


@unittest.skipUnless(IMAGE, "Docker image ID not supplied")
class DockerSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inspect_image(IMAGE)

    def test_research_executes_with_only_visible_files(self):
        with tempfile.TemporaryDirectory(prefix="docker-isolation-") as name:
            root = pathlib.Path(name)
            workspace = root / "current_task"
            workspace.mkdir()
            (workspace / "TASK.md").write_text("visible-task", encoding="utf-8")
            (workspace / "data").mkdir()
            (workspace / "data/part_00.csv").write_text("visible-part", encoding="utf-8")
            for folder in ("future", "operator", "other_task"):
                target = root / folder
                target.mkdir()
                (target / "secret.txt").write_text(folder, encoding="utf-8")
            code = """import json, os, pathlib, socket
from pathlib import Path
visible = Path('/workspace/TASK.md').read_text() == 'visible-task'
future = Path('/workspace/data/part_01.csv').exists()
outside = any(Path(p).exists() for p in ('/eval/simulate.py', '/run/state.json',
                                         '/mnt/d/Learn/Project/changguiji_domo/demo_runner/.env',
                                         '/operator/secret.txt', '/other_task/secret.txt'))
try:
    Path('/workspace/TASK.md').write_text('changed')
    writable = True
except OSError:
    writable = False
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
    network = True
except OSError:
    network = False
Path('/tmp/probe').write_text('ok')
print(json.dumps({'visible': visible, 'future': future, 'outside': outside,
                  'writable': writable, 'network': network,
                  'secret': bool(os.getenv('FINANCIAL_TEST_SECRET')),
                  'uid': os.geteuid(), 'tmp': Path('/tmp/probe').read_text()}))
"""
            with patch.dict(os.environ, {"FINANCIAL_TEST_SECRET": "offline-test-only"}):
                result = sandbox_research(workspace, code, backend="docker", docker_image=IMAGE)
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            report = json.loads(result.stdout)
            self.assertEqual(report, {"visible": True, "future": False, "outside": False,
                                      "writable": False, "network": False, "secret": False,
                                      "uid": os.getuid(), "tmp": "ok"})
            self.assertEqual((workspace / "TASK.md").read_text(encoding="utf-8"), "visible-task")

    def test_stock_strategy_runs_in_docker_backtest(self):
        with tempfile.TemporaryDirectory(prefix="docker-backtest-") as name:
            workspace = h.extract_initial(pathlib.Path(name) / "agent")
            (workspace / "strategy.py").write_text(
                (workspace / "strategy_example.py").read_text(encoding="utf-8"), encoding="utf-8")
            result = sandbox_command(workspace, "run_backtest.py",
                                     ["--out", "/workspace/output/docker-check"],
                                     backend="docker", docker_image=IMAGE)
            self.assertEqual(result.returncode, 0, result.stderr[-1500:])
            self.assertTrue((workspace / "output/docker-check").is_dir())

    def test_output_symlink_cannot_mount_another_task(self):
        with tempfile.TemporaryDirectory(prefix="docker-symlink-") as name:
            root = pathlib.Path(name)
            workspace = root / "current"
            other = root / "other"
            workspace.mkdir()
            other.mkdir()
            (workspace / "output").symlink_to(other, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "符号链接"):
                execute(IMAGE, "research", workspace, code="print('x')")


if __name__ == "__main__":
    unittest.main()
