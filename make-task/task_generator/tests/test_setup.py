"""Operator setup checks using fake terminals and isolated temporary files."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finlab import setup
from finlab.common import LabError, read, write


class OperatorSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root_patch = patch.object(setup, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def configure(self, answers, *, tty=True):
        output = io.StringIO()
        with patch.object(setup.sys.stdin, "isatty", return_value=tty), patch("builtins.input", side_effect=answers) as entry, contextlib.redirect_stdout(output):
            result = setup.interactive_setup()
        return result, entry.call_count, output.getvalue()

    def test_missing_setup_is_required_and_does_not_create_file(self):
        self.assertEqual(setup.settings_path(), self.root / ".finlab/operator.json")
        with self.assertRaisesRegex(LabError, "SETUP_REQUIRED"):
            setup.load_settings()
        self.assertFalse(setup.settings_path().exists())

    def test_noninteractive_setup_rejected_before_input(self):
        with patch.object(setup.sys.stdin, "isatty", return_value=False), patch("builtins.input") as entry:
            with self.assertRaisesRegex(LabError, "SETUP_REQUIRES_INTERACTIVE_TERMINAL"):
                setup.interactive_setup()
            entry.assert_not_called()
        self.assertFalse(setup.settings_path().exists())

    def test_blank_answers_require_explicit_choices(self):
        result, calls, output = self.configure(["", "3.5", "", "5120", "", "77", "本人确认"])
        self.assertEqual(calls, 7)
        self.assertEqual(result["resources"], {"cpus": 3.5, "memory_mb": 5120, "timeout_seconds": 77})
        self.assertIn("空输入不会采用", output)
        self.assertEqual(setup.load_settings()["resources"], result["resources"])

    def test_none_is_an_explicit_choice(self):
        result, _, _ = self.configure(["2", "4096", "NoNe", "本人确认"])
        self.assertIsNone(result["resources"]["timeout_seconds"])
        self.assertIsNone(setup.load_settings()["resources"]["timeout_seconds"])

    def test_invalid_answers_are_retried_without_defaults(self):
        result, _, _ = self.configure(["nan", "0", "1.25", "255", "2.5", "3072", "-1", "1.5", "31", "本人确认"])
        self.assertEqual(result["resources"], {"cpus": 1.25, "memory_mb": 3072, "timeout_seconds": 31})

    def test_final_confirmation_must_be_exact(self):
        with self.assertRaisesRegex(LabError, "SETUP_CANCELLED"):
            self.configure(["2", "4096", "99", "yes"])
        self.assertFalse(setup.settings_path().exists())

    def test_cancel_does_not_overwrite_previous_configuration(self):
        self.configure(["2", "4096", "99", "本人确认"])
        before = setup.settings_path().read_bytes()
        for answers in (["取消"], ["4", "8192", "none", "取消"]):
            with self.assertRaisesRegex(LabError, "SETUP_CANCELLED"):
                self.configure(answers)
            self.assertEqual(setup.settings_path().read_bytes(), before)

    def test_eof_or_keyboard_interrupt_does_not_save(self):
        for error in (EOFError(), KeyboardInterrupt()):
            with self.assertRaisesRegex(LabError, "SETUP_CANCELLED"):
                self.configure([error])
            self.assertFalse(setup.settings_path().exists())

    def test_human_can_reconfigure_explicitly(self):
        self.configure(["1", "1024", "45", "本人确认"])
        self.configure(["4", "6144", "none", "本人确认"])
        self.assertEqual(setup.load_settings()["resources"], {"cpus": 4.0, "memory_mb": 6144, "timeout_seconds": None})

    def test_resource_keys_are_required_and_unknown_keys_rejected(self):
        valid = {"cpus": 2, "memory_mb": 4096, "timeout_seconds": 60}
        for key in valid:
            with self.subTest(missing=key), self.assertRaisesRegex(LabError, "INVALID_RESOURCES"):
                setup.validate_resources({k: v for k, v in valid.items() if k != key})
        for invalid in (None, [], {**valid, "default": True}):
            with self.assertRaisesRegex(LabError, "INVALID_RESOURCES"):
                setup.validate_resources(invalid)

    def test_resource_types_and_finite_values(self):
        valid = {"cpus": 2, "memory_mb": 4096, "timeout_seconds": 60}
        changes = {"cpus": [True, 0, -1, "2", float("nan"), float("inf"), 10 ** 400],
                   "memory_mb": [True, 255, 4096.0, "4096"],
                   "timeout_seconds": [True, 0, -1, 30.0, "none"]}
        for key, values in changes.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(LabError, "INVALID_RESOURCES"):
                    setup.validate_resources({**valid, key: value})

    def test_invalid_settings_cannot_claim_configuration(self):
        self.configure(["2", "4096", "none", "本人确认"])
        original = read(setup.settings_path())
        modifications = [{**original, "schema_version": "other"}, {**original, "method": "ai_selected"},
                         {**original, "resources": {"cpus": 2, "memory_mb": 4096}},
                         {**original, "configured_at": "2026-11-05"}, {**original, "extra": True}]
        for invalid in modifications:
            write(setup.settings_path(), invalid)
            with self.assertRaisesRegex(LabError, "INVALID_OPERATOR_SETUP"):
                setup.load_settings()
        setup.settings_path().write_text("invalid JSON", encoding="utf-8")
        with self.assertRaisesRegex(LabError, "INVALID_OPERATOR_SETUP"):
            setup.load_settings()


if __name__ == "__main__":
    unittest.main()
