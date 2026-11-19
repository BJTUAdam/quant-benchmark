"""Offline checks for the Docker formal-simulation boundary."""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


RUNNER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNNER))
import autonomous_harness as harness
import simulator_host


class DockerFormalTests(unittest.TestCase):
    def test_formal_container_has_no_task_or_secret_mount(self):
        command = simulator_host.container_command("sha256:" + "a" * 64, "case-one",
                                                    RUNNER, 1000, 1000)
        mounts = [command[i + 1] for i, item in enumerate(command) if item == "--mount"]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(all(mount.endswith("readonly") for mount in mounts))
        self.assertTrue(all("strategy_" in mount for mount in mounts))
        self.assertNotIn("dst=/workspace", " ".join(mounts))
        for flag in ("--network", "--read-only", "--cap-drop", "--pids-limit",
                     "--cpus", "--memory", "--tmpfs", "--ulimit"):
            self.assertIn(flag, command)
        self.assertIn("none", command)

    def test_stock_and_futures_decisions_preserve_memory(self):
        proxy = object.__new__(simulator_host.DockerStrategy)
        sent = []

        def answer(request):
            sent.append(request)
            return {"ok": True, "target": {"asset": 0.5}, "memory": {"step": 2}}

        proxy.request = answer
        stock_memory = {"step": 1}
        self.assertEqual(proxy.decide(144, 930, {"visible": True}, {}, {}, stock_memory), {"asset": 0.5})
        self.assertEqual(stock_memory, {"step": 2})
        futures_memory = {"step": 1}
        self.assertEqual(proxy.decide(144, {"visible": True}, {}, {}, futures_memory), {"asset": 0.5})
        self.assertEqual(futures_memory, {"step": 2})
        self.assertEqual([len(item["args"]) for item in sent], [6, 5])
        self.assertTrue(all(any(isinstance(arg, dict) and arg.get("visible") for arg in item["args"])
                            for item in sent))

    def test_strategy_error_is_not_reported_as_success(self):
        proxy = object.__new__(simulator_host.DockerStrategy)
        proxy.request = lambda value: {"ok": False, "error": "invalid strategy", "memory": {}}
        with self.assertRaisesRegex(RuntimeError, "invalid strategy"):
            proxy.decide(1, {}, {}, {}, {})

    def test_formal_dispatch_uses_registered_docker_image(self):
        image = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory(dir=RUNNER) as folder:
            root = pathlib.Path(folder)
            calls = []

            def invoke(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("docker_sandbox.validate_image", return_value=image), \
                 mock.patch.object(harness.subprocess, "run", side_effect=invoke), \
                 mock.patch.object(harness.shutil, "which", side_effect=AssertionError("bubblewrap checked")):
                harness.guarded_sim(root, root / "workspace", root / "eval_run",
                                    ["--workspace", "/workspace", "--segment", "1"],
                                    backend="docker", docker_image=image)
        command = calls[0][0]
        self.assertIn(str(RUNNER / "simulator_host.py"), command)
        self.assertIn(image, command)
        self.assertNotIn("/workspace", command)
        self.assertNotIn("bwrap", " ".join(command))
        self.assertEqual(len([c for c, _ in calls if c[0] == "docker"]), 3)

    def test_doctor_docker_does_not_require_bubblewrap(self):
        cfg = {"execution_backend": "docker", "docker_image": "sha256:" + "a" * 64}
        versions = {"numpy": "1.26.4", "pandas": "2.2.3"}
        fake = subprocess.CompletedProcess([], 0, '{"numpy":"1.26.4","pandas":"2.2.3"}', "")
        make_temp = tempfile.TemporaryDirectory
        with mock.patch.object(harness, "check_runtime", return_value=versions), \
             mock.patch.object(harness, "verify_manifest"), \
             mock.patch.object(harness, "package_kind", return_value="stock"), \
             mock.patch.object(harness, "sandbox_research", return_value=fake), \
             mock.patch("docker_sandbox.inspect_image"), \
             mock.patch.object(harness.tempfile, "TemporaryDirectory",
                               side_effect=lambda *a, **kw: make_temp(*a, dir=RUNNER, **kw)), \
             mock.patch.object(harness.shutil, "which", side_effect=AssertionError("bubblewrap checked")):
            harness.doctor(cfg, check_key=False)


if __name__ == "__main__":
    unittest.main()
