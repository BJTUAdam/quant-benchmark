"""Behavioral checks for portable offline skill helpers, with no market access."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/finlab-strategy"


def load(name):
    spec = importlib.util.spec_from_file_location("finlab_skill_" + name, SKILL / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StrategySkillHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load("api_docs")
        cls.preflight = load("preflight")
        cls.maker = load("make_submission")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.submission = self.root / "submission"
        self.submission.mkdir()
        (self.submission / "strategy.py").write_text(
            "from finlab.sdk import Strategy as BaseStrategy\n"
            "class Strategy(BaseStrategy):\n"
            "    def initialize(self, ctx): ctx.memory.setdefault('bars', 0)\n"
            "    def on_bars(self, ctx): ctx.memory['bars'] += 1\n", encoding="utf-8")
        (self.submission / "backtest.py").write_text(
            "from finlab.sdk import launch\nif __name__ == '__main__': launch('strategy.py', 'params.json')\n", encoding="utf-8")
        (self.submission / "params.json").write_text("{}", encoding="utf-8")
        for name in ("RESEARCH.md", "CHANGELOG.md"):
            (self.submission / name).write_text("# Research template\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, result, kind="errors"):
        return {r["code"] for r in result[kind]}

    def strategy(self, text):
        (self.submission / "strategy.py").write_text(text, encoding="utf-8")

    def task(self):
        folder = self.root / "public"
        folder.mkdir()
        (folder / "bridge_contract.json").write_text(json.dumps({
            "schema_version": "finlab.bridge_contract.v2", "binding_status": "not_bound",
            "endpoints": [{"api_name": "daily", "fields": ["ts_code", "trade_date", "close"]}]}), encoding="utf-8")
        return folder

    def test_valid_empty_strategy_is_static_only(self):
        result = self.preflight.check(self.submission)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["static_only"])
        self.assertFalse(result["execution_verified"])
        self.assertFalse(result["security_verified"])
        self.assertEqual(len(result["files"]), 5)

    def test_never_executes_submission(self):
        sentinel = self.root / "must_not_exist"
        original = (self.submission / "strategy.py").read_text(encoding="utf-8")
        self.strategy("open(" + repr(str(sentinel)) + ", 'w').write('executed')\n" + original)
        self.assertTrue(self.preflight.check(self.submission)["passed"])
        self.assertFalse(sentinel.exists())

    def test_missing_files_nonfinite_json_and_unsupported_imports(self):
        (self.submission / "RESEARCH.md").unlink()
        (self.submission / "params.json").write_text('{"x":NaN}', encoding="utf-8")
        self.strategy("import akshare\nfrom tushare import pro_api\nfrom akquant import Strategy\n")
        result = self.preflight.check(self.submission)
        self.assertFalse(result["passed"])
        self.assertTrue({"REQUIRED_FILE_MISSING_OR_LINK", "INVALID_SYNTAX_ENCODING_OR_JSON", "IMPORT_NOT_ALLOWED"} <= self.codes(result))

    def test_extreme_float_and_non_object_params_are_rejected(self):
        for text in ('{"x":1e10000}', '[]'):
            (self.submission / "params.json").write_text(text, encoding="utf-8")
            self.assertFalse(self.preflight.check(self.submission)["passed"])

    def test_context_mistakes_and_order_in_notification_are_rejected(self):
        self.strategy("from finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n"
                      " def on_bars(self, ctx):\n  ctx.history('x')\n  ctx.order('X', -2)\n"
                      " def on_order(self, ctx, event): ctx.order('X', 100)\n")
        result = self.preflight.check(self.submission)
        self.assertTrue({"UNKNOWN_CONTEXT_METHOD", "ORDER_QUANTITY_MUST_BE_POSITIVE", "ORDER_ONLY_IN_ON_BARS"} <= self.codes(result))

    def test_native_callback_and_version_mismatch_are_rejected(self):
        self.strategy("from finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n api_version='v1'\n def on_bar(self, bar): pass\n")
        result = self.preflight.check(self.submission)
        self.assertTrue({"RAW_AKQUANT_CALLBACK_NOT_SUPPORTED", "STRATEGY_API_VERSION_MISMATCH"} <= self.codes(result))

    def test_helper_data_is_not_mistaken_for_context(self):
        self.strategy("from finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n"
                      " def helper(self, data): return data.rolling(5).mean()\n"
                      " def on_bars(self, ctx): pass\n")
        self.assertTrue(self.preflight.check(self.submission)["passed"])

    def test_contract_checks_ctx_and_dynamic_pro_calls(self):
        self.strategy("from finlab.sdk import Strategy as BaseStrategy\nfrom finlab import tushare as ts\nclass Strategy(BaseStrategy):\n"
                      " def on_bars(self, ctx):\n  ctx.query('income')\n  pro=ts.pro_api()\n  pro.daily(fields='ts_code,open')\n")
        result = self.preflight.check(self.submission, task=self.task())
        self.assertTrue({"API_NOT_IN_TASK_CONTRACT", "FIELDS_NOT_IN_TASK_CONTRACT"} <= self.codes(result))
        self.assertTrue(result["task_contract_checked"])

    def test_dynamic_query_reports_uncertainty_without_false_rejection(self):
        self.strategy("from finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n"
                      " def on_bars(self, ctx): ctx.query(ctx.params['api'], fields=ctx.params['fields'])\n")
        result = self.preflight.check(self.submission, task=self.task())
        self.assertTrue(result["passed"])
        self.assertIn("DYNAMIC_API_NEEDS_RUNTIME_CHECK", self.codes(result, "warnings"))

    def test_private_platform_modules_are_not_available(self):
        self.strategy("from finlab import engine\nfrom finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n def on_bars(self, ctx): pass\n")
        self.assertIn("PLATFORM_PRIVATE_MODULE_UNAVAILABLE", self.codes(self.preflight.check(self.submission)))

    def test_plain_module_import_entry_and_callback_signatures(self):
        (self.submission / "backtest.py").write_text("import finlab.sdk\nif __name__ == '__main__': finlab.sdk.launch()\n", encoding="utf-8")
        self.assertTrue(self.preflight.check(self.submission)["passed"])
        self.strategy("from finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n def on_bars(self): pass\n")
        self.assertIn("CALLBACK_SIGNATURE_MISMATCH", self.codes(self.preflight.check(self.submission)))

    def test_exact_api_lookup_and_chunking_reconstruct_document(self):
        full = self.api.document(api="daily", max_chars=100000)
        cursor, chunks = 0, []
        while True:
            part = self.api.document(api="daily", start=cursor, max_chars=130)
            chunks.append(part["content"])
            if part["next_start"] is None:
                break
            cursor = part["next_start"]
        self.assertEqual("".join(chunks), full["content"])
        self.assertEqual(full["api_name"], "daily")
        with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
            self.api.document(api="invented_missing_endpoint")

    def test_search_exact_name_ranks_first_and_guide_is_available(self):
        result = self.api.search("fut_daily", limit=3)
        self.assertEqual(result["results"][0]["api_name"], "fut_daily")
        self.assertLessEqual(len(result["results"]), 3)
        guide = self.api.document(doc_id="109")
        self.assertIn("pro_bar", guide["content"])
        self.assertTrue(guide["offline"])

    def test_helpers_are_portable_and_detect_docs_tampering(self):
        portable = self.root / "renamed_skill"
        shutil.copytree(SKILL / "scripts", portable / "scripts")
        index = json.loads((SKILL / "references/tushare/snapshot.json").read_text(encoding="utf-8"))
        index["documents"] = [r for r in index["documents"] if r.get("api_name") == "daily"]
        ref = portable / "references/tushare"
        ref.mkdir(parents=True)
        (ref / "snapshot.json").write_text(json.dumps(index), encoding="utf-8")
        row = index["documents"][0]
        dest = ref / row["path"]
        dest.parent.mkdir(parents=True)
        shutil.copyfile(SKILL / "references/tushare" / row["path"], dest)
        output = subprocess.run([sys.executable, "-B", str(portable / "scripts/api_docs.py"), "--api", "daily", "--max-chars", "200"], cwd=self.root,
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(json.loads(output.stdout)["api_name"], "daily")
        dest.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HASH_CHANGED"):
            self.api.document(api="daily", skill_root=portable)

    def test_template_copy_refuses_overwrite_and_is_preflight_valid(self):
        destination = self.root / "generated"
        result = self.maker.create(destination)
        self.assertEqual(set(result["files"]), self.preflight.FILES)
        self.assertTrue(self.preflight.check(destination)["passed"])
        before = (destination / "params.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "ALREADY_EXISTS"):
            self.maker.create(destination)
        self.assertEqual(before, (destination / "params.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "TRAVERSAL"):
            self.maker.create(self.root / "unused" / ".." / "elsewhere")

    def test_import_policy_matches_platform_current_policy(self):
        tree = ast.parse((ROOT / "finlab/submission.py").read_text(encoding="utf-8"))
        current = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ALLOWED_IMPORTS" for t in n.targets))
        self.assertEqual(self.preflight.ALLOWED_IMPORTS, current)


if __name__ == "__main__":
    unittest.main()
