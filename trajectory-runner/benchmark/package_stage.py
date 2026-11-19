"""Verify a supplied task ZIP and stage only operator runtime files.

The model receives only agent_workspace.tar.gz plus one completed part at a time.
Answer-like dry runs, provider inputs, verification files and future data never enter
its workspace. Staging does not execute code from the ZIP.
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import shutil
import tarfile
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PACKAGES = HERE / "packages"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> bool:
    parts = pathlib.PurePosixPath(name).parts
    return bool(parts) and not name.startswith("/") and "\\" not in name and all(
        p not in ("", ".", "..") and not p.startswith(".") for p in parts)


def operator_files(adapter: str, names: set[str]) -> set[str]:
    base = {"MANIFEST.json", "agent_workspace.tar.gz", "simulate.py",
            "backtester/engine.py", "backtester/__init__.py", "data/splits.json",
            "prompts/round_review.md"}
    if adapter == "futures_daily_v1":
        base |= {"data/market.json", "task_config.json"}
        for name in names:
            parts = pathlib.PurePosixPath(name).parts
            if len(parts) == 3 and parts[0] == "data" and parts[1].startswith("part_") and parts[2] in {"quotes.csv", "rules.csv", "instruments.csv"}:
                base.add(name)
    else:
        for name in names:
            parts = pathlib.PurePosixPath(name).parts
            if len(parts) == 3 and parts[0] == "data" and parts[1] in {"daily", "minute"} and parts[2].startswith("part_"):
                base.add(name)
    if not base <= names:
        raise ValueError("题目包缺少运行必需文件：" + ",".join(sorted(base - names)))
    return base


def inspect_workspace(archive: bytes, adapter: str, root_name: str) -> dict:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        files = []
        allowed_common = {"TASK.md", "BACKTEST.md", "CHANGELOG.md", "requirements.txt",
                          "run_backtest.py", "strategy_example.py", "backtester/__init__.py",
                          "backtester/engine.py"}
        if adapter == "futures_daily_v1":
            allowed_common |= {"data/market.json", "task_config.json",
                               "data/part_00/quotes.csv", "data/part_00/rules.csv",
                               "data/part_00/instruments.csv"}
        else:
            allowed_common |= {"data/daily/part_00.csv", "data/minute/part_00.csv.gz"}
        for member in members:
            if not safe_name(member.name) or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError("Agent 初始归档含越界路径或链接")
            parts = pathlib.PurePosixPath(member.name).parts
            if parts[0] != root_name:
                raise ValueError("Agent 初始工作区根目录不一致")
            if member.isfile():
                rel = "/".join(parts[1:])
                if rel not in allowed_common:
                    raise ValueError("Agent 初始工作区含未允许文件：" + rel)
                files.append(rel)
        if not {"TASK.md", "BACKTEST.md", "CHANGELOG.md", "run_backtest.py", "strategy_example.py"} <= set(files):
            raise ValueError("Agent 初始工作区缺少任务接口")
    return {"root": root_name, "files": sorted(files)}


def stage(zip_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, dict]:
    zip_path = zip_path.resolve()
    if zip_path.suffix.lower() != ".zip" or not zip_path.is_file():
        raise ValueError("需要题目 ZIP 文件")
    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    task_id = zip_path.stem
    PACKAGES.mkdir(parents=True, exist_ok=True)
    folder = PACKAGES / (task_id + "-" + zip_sha[:12])
    package = folder / task_id
    receipt_path = folder / "stage.json"
    if folder.exists():
        if not receipt_path.is_file():
            raise ValueError("已存在未完成的题目暂存目录，拒绝覆盖")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        # The same ZIP may be reached as D:\\... on Windows or /mnt/d/... in WSL.
        # Its content hash, not the OS spelling of its path, identifies the source.
        if receipt["source_sha256"] != zip_sha:
            raise ValueError("已暂存题目与源 ZIP 不一致")
        for rel, expected in receipt["staged_sha256"].items():
            target = package / rel
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("题目暂存文件被改动：" + rel)
        return package, folder / "task.json", receipt
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip() is not None:
            raise ValueError("ZIP CRC 校验失败")
        names = {x.filename for x in z.infolist() if not x.is_dir()}
        prefix = task_id + "/"
        if any(not n.startswith(prefix) or not safe_name(n) for n in names):
            raise ValueError("ZIP 含越界路径或多个题目根目录")
        rels = {n[len(prefix):] for n in names}
        manifest = json.loads(z.read(prefix + "MANIFEST.json"))
        hashes = manifest.get("package_sha256", manifest.get("sha256"))
        if not isinstance(hashes, dict):
            raise ValueError("题目包没有文件哈希清单")
        for rel, expected in hashes.items():
            if rel not in rels or not safe_name(rel) or digest(z.read(prefix + rel)) != expected:
                raise ValueError("题目包清单校验失败：" + rel)
        adapter = "futures_daily_v1" if "data/market.json" in rels else "star50_v1"
        selected = operator_files(adapter, rels)
        splits = json.loads(z.read(prefix + "data/splits.json"))
        if len(splits.get("segments", [])) != 7 or splits["research"][0] != 0 or splits["segments"][0][0] != splits["research"][1] or splits["segments"][-1][1] != splits["num_days"]:
            raise ValueError("题目数据切分不是连续的研发期和七段")
        root_name = task_id[:-3] if adapter == "futures_daily_v1" else "star50_trend"
        initial = inspect_workspace(z.read(prefix + "agent_workspace.tar.gz"), adapter, root_name)
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=PACKAGES) as tmp:
            scratch = pathlib.Path(tmp)
            staged = {}
            for rel in sorted(selected):
                data = z.read(prefix + rel)
                target = scratch / task_id / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                staged[rel] = digest(data)
            case_id = task_id.replace("_v1", "") + "_dev"
            task = {"schema_version": 1, "task_id": task_id, "task_version": "1",
                    "adapter": adapter, "title": task_id,
                    "cases": [{"case_id": case_id, "role": "development",
                               "num_days": splits["num_days"], "research": splits["research"],
                               "segments": splits["segments"], "scored_days": splits["num_days"] - splits["research"][1],
                               "reviews": 6, "note": "新题目包开发验证；不标记为隐藏测试集。"}],
                    "tracks": {"end_to_end": "每个模型从研发期独立生成并维护策略。"},
                    "scoring": {"primary": "sharpe", "secondary": ["total_return", "max_drawdown"],
                                "composite_score": None}}
            (scratch / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            receipt = {"source_zip": str(zip_path), "source_sha256": zip_sha,
                       "task_id": task_id, "adapter": adapter, "workspace": initial,
                       "staged_sha256": staged}
            (scratch / "stage.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            scratch.rename(folder)
    return package, folder / "task.json", receipt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("zip", type=pathlib.Path)
    args = parser.parse_args()
    package, task, receipt = stage(args.zip)
    print(json.dumps({"package_root": str(package), "task": str(task),
                      "adapter": receipt["adapter"], "files": len(receipt["staged_sha256"])}, ensure_ascii=False))
