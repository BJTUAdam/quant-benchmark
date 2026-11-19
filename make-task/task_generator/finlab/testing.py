"""Run repository unit/integration checks and persist an honest summary."""
from __future__ import annotations
import sys
import time
import unittest
from .common import ROOT, now, write


def run_tests():
    started = time.monotonic()
    suite = unittest.TestSuite()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == "test_market_bridge.py": continue  # retired local archive, not shipped
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=path.name))
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    report = {"schema_version": "finlab.unit_tests.v2", "checked_at": now(),
              "status": "passed" if result.wasSuccessful() else "failed", "passed": result.wasSuccessful(),
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "elapsed_seconds": round(time.monotonic()-started,3),
              "failing_tests": [str(case) for case,_ in result.failures+result.errors],
              "real_upstream_tested": False, "docker_acceptance": "separate_finlab_verify_report"}
    write(ROOT / "docs/unit_tests_v2.json",report)
    return report
