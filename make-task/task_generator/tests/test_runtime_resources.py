"""Execution budgets are explicit, human-selected inputs rather than fallbacks."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from finlab.cli import main, parser
from finlab.common import LabError, read
from finlab.demo import build
from finlab.runtime import create_episode, run


class RuntimeResourceTests(unittest.TestCase):
    def test_new_demo_contains_no_chosen_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'demo'
            build(folder)
            runtime = read(folder / 'binding.private.json')['runtime']
            self.assertIsNone(runtime['resources'])
            with patch('finlab.setup.ROOT', Path(tmp) / 'unconfigured'):
                with self.assertRaisesRegex(LabError, 'SETUP_REQUIRED'):
                    create_episode(read(folder / 'binding.private.json'), folder / 'episode')

    def test_cli_does_not_accept_timeout_override(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser().parse_args(['backtest', 'run', '--episode', 'unused', '--submission', 'unused', '--timeout', '1'])

    def test_cli_requires_personal_setup_before_building_demo(self):
        with tempfile.TemporaryDirectory() as tmp, patch('finlab.setup.ROOT', Path(tmp)):
            with redirect_stderr(io.StringIO()) as output:
                status = main(['demo', 'build', '--output', str(Path(tmp) / 'demo')])
            self.assertEqual(status, 2)
            self.assertIn('SETUP_REQUIRED', output.getvalue())
            self.assertFalse((Path(tmp) / 'demo').exists())

    def test_explicit_unlimited_budget_reaches_docker_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'fixture'
            # Controlled synthetic regression input, never a saved operator choice.
            resources = {'cpus': 1.5, 'memory_mb': 1536, 'timeout_seconds': None}
            build(folder, resources=resources)
            create_episode(read(folder / 'binding.private.json'), folder / 'episode')
            calls = []

            def docker(args, **kwargs):
                calls.append((args, kwargs))
                if args[0] == 'wait':
                    raise LabError('SYNTHETIC_WAIT_STOP')
                return SimpleNamespace(stdout='', stderr='')

            with patch('finlab.runtime.docker', side_effect=docker):
                with self.assertRaisesRegex(LabError, 'SYNTHETIC_WAIT_STOP'):
                    run(folder / 'episode', folder / 'submission')
            launches = [args for args, _ in calls if args[:2] == ['run', '-d']]
            self.assertEqual(len(launches), 2)
            for args in launches:
                self.assertEqual(args[args.index('--cpus') + 1], '0.75')
                self.assertEqual(args[args.index('--memory') + 1], '768m')
            waits = [kwargs for args, kwargs in calls if args[0] == 'wait']
            self.assertEqual(waits, [{'timeout': None}])
            self.assertEqual(read(folder / 'episode/state.private.json')['completed_segment'], 0)

    def test_missing_resource_field_is_not_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'fixture'
            build(folder, resources={'cpus': 2, 'memory_mb': 4096})
            with self.assertRaisesRegex(LabError, 'BINDING'):
                create_episode(read(folder / 'binding.private.json'), folder / 'episode')


if __name__ == '__main__':
    unittest.main()
