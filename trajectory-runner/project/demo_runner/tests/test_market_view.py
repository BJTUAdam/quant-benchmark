"""Offline checks for the Agent-facing cumulative market files."""
from __future__ import annotations

import gzip
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_view

TEST_TMP = pathlib.Path(__file__).resolve().parents[3] / "benchmark"


class MarketViewTests(unittest.TestCase):
    def test_stock_view_has_one_stable_pair_without_part_names(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = pathlib.Path(folder)
            package, workspace = root / "package", root / "workspace"
            for kind in ("daily", "minute"):
                (package / "data" / kind).mkdir(parents=True)
                (workspace / "data" / kind).mkdir(parents=True)
            (workspace / "backtester").mkdir()
            (workspace / "backtester/engine.py").write_text(
                'daily.glob("part_*.csv")\nminute.glob("part_*.csv.gz")\n', encoding="utf-8")
            for part in (0, 1):
                (package / f"data/daily/part_{part:02d}.csv").write_text(
                    f"t,symbol\n{part},S01\n", encoding="utf-8")
                with gzip.open(package / f"data/minute/part_{part:02d}.csv.gz", "wt", encoding="utf-8") as out:
                    out.write(f"t,symbol\n{part},S01\n")
            market_view.publish(workspace, package, 1, False)
            self.assertEqual([p.name for p in (workspace / "data/daily").iterdir()], ["history.csv"])
            self.assertEqual((workspace / "data/daily/history.csv").read_text(),
                             "t,symbol\n0,S01\n1,S01\n")
            with gzip.open(workspace / "data/minute/history.csv.gz", "rt") as data:
                self.assertEqual(data.read(), "t,symbol\n0,S01\n1,S01\n")
            self.assertNotIn("part_", (workspace / "backtester/engine.py").read_text())

    def test_futures_view_uses_fixed_history_directory(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = pathlib.Path(folder)
            package, workspace = root / "package", root / "workspace"
            (workspace / "backtester").mkdir(parents=True)
            (workspace / "backtester/engine.py").write_text(
                "folder.glob('part_*')\nint(p.name.split('_')[-1]) <= max_part\n", encoding="utf-8")
            for part in (0, 1):
                source = package / f"data/part_{part:02d}"
                source.mkdir(parents=True)
                for name in ("quotes.csv", "rules.csv", "instruments.csv"):
                    (source / name).write_text(f"t,symbol\n{part},F01\n", encoding="utf-8")
            market_view.publish(workspace, package, 1, True)
            self.assertEqual((workspace / "data/history/quotes.csv").read_text(),
                             "t,symbol\n0,F01\n1,F01\n")
            self.assertEqual([p.name for p in (workspace / "data").iterdir()], ["history"])
            self.assertNotIn("part_*", (workspace / "backtester/engine.py").read_text())


if __name__ == "__main__":
    unittest.main()
