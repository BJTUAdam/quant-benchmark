"""Build a stable, cumulative market view for the research Agent.

Operator data parts remain in the trusted package. Only the active workspace is
rewritten; evaluation still uses the original package and trusted scorer.
"""
from __future__ import annotations

import gzip
import pathlib
import shutil


def _combine(sources: list[pathlib.Path], target: pathlib.Path, compressed: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if compressed else open
    with opener(target, "wb") as output:
        for index, source in enumerate(sources):
            with opener(source, "rb") as input_file:
                if index:
                    input_file.readline()  # identical CSV header
                shutil.copyfileobj(input_file, output)


def publish(workspace: pathlib.Path, package: pathlib.Path, part: int, futures: bool) -> None:
    data = workspace / "data"
    if futures:
        sources = [package / "data" / f"part_{i:02d}" for i in range(part + 1)]
        target = data / "history"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("quotes.csv", "rules.csv", "instruments.csv", "membership.csv", "benchmark.csv"):
            files = [folder / name for folder in sources if (folder / name).is_file()]
            if files:
                _combine(files, target / name)
        for folder in data.glob("part_*"):
            if folder.is_dir():
                shutil.rmtree(folder)
        engine = workspace / "backtester/engine.py"
        source = engine.read_text(encoding="utf-8")
        source = source.replace("folder.glob('part_*')", "folder.glob('history')")
        source = source.replace("int(p.name.split('_')[-1]) <= max_part", "max_part >= 0")
        source = source.replace("分段仿真", "策略运行").replace("仿真实盘", "策略运行")
        engine.write_text(source, encoding="utf-8")
    else:
        for kind, suffix in (("daily", ".csv"), ("minute", ".csv.gz")):
            directory = data / kind
            sources = [package / "data" / kind / f"part_{i:02d}{suffix}" for i in range(part + 1)]
            target = directory / f"history{suffix}"
            _combine(sources, target, compressed=kind == "minute")
            for path in directory.glob("part_*"):
                path.unlink()
        engine = workspace / "backtester/engine.py"
        source = engine.read_text(encoding="utf-8")
        source = source.replace('glob("part_*.csv")', 'glob("history.csv")')
        source = source.replace('glob("part_*.csv.gz")', 'glob("history.csv.gz")')
        source = source.replace('part_00 … part_<max_part>', '当前可用行情')
        source = source.replace('part_*.csv.gz', 'history.csv.gz').replace('part_*.csv', 'history.csv')
        source = source.replace('part_XX.csv', 'history.csv').replace('part_XX.csv.gz', 'history.csv.gz')
        source = source.replace("分段仿真", "策略运行").replace("仿真实盘", "策略运行")
        engine.write_text(source, encoding="utf-8")


def clear_history(workspace: pathlib.Path, futures: bool) -> None:
    data = workspace / "data"
    if futures:
        target = data / "history"
        if target.is_dir():
            shutil.rmtree(target)
    else:
        for path in (data / "daily/history.csv", data / "minute/history.csv.gz"):
            path.unlink(missing_ok=True)
