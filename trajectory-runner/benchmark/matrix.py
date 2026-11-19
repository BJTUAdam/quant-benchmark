"""Run a task × model × repeat matrix as separate, locked per-task batches."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import batch
import bench
import package_stage

MATRICES = HERE / "matrices"


def parse(config: pathlib.Path) -> dict:
    spec = bench.read(config.resolve())
    allowed = {"schema_version", "matrix_id", "mode", "project_root", "packages", "repeats", "policy", "models"}
    bench.require(set(spec) == allowed and spec["schema_version"] == 1, "矩阵配置字段或版本错误")
    bench.require(re.fullmatch(r"[a-z][a-z0-9-]{0,7}", spec["matrix_id"]), "matrix_id 最多 8 位小写标识")
    bench.require(spec["mode"] in ("fake", "real"), "未知矩阵模式")
    bench.require(isinstance(spec["packages"], list) and spec["packages"] and len(spec["packages"]) <= 100,
                  "矩阵须列出 1—100 个题目 ZIP")
    bench.require(len(spec["packages"]) == len(set(spec["packages"])), "题目 ZIP 重复")
    for value in spec["packages"]:
        bench.require(isinstance(value, str) and value.lower().endswith(".zip"), "题目必须是 ZIP 路径")
    return spec


def task_spec(matrix: dict, package: pathlib.Path, task_path: pathlib.Path, index: int) -> dict:
    task = bench.read(task_path)
    case_id = task["cases"][0]["case_id"]
    spec = {"schema_version": 1, "batch_id": f"{matrix['matrix_id']}-{index:02d}",
            "mode": matrix["mode"], "project_root": matrix["project_root"],
            "package_root": str(package), "task": str(task_path), "case_id": case_id,
            "track": "end_to_end", "seed_run_id": None, "repeats": matrix["repeats"],
            "policy": matrix["policy"], "models": matrix["models"]}
    return batch.validate_spec(spec)


def lock_matrix(config: pathlib.Path, folder: pathlib.Path, spec: dict) -> dict:
    source = {"config_sha256": bench.digest(config), "matrix_source_sha256": bench.digest(pathlib.Path(__file__))}
    if folder.exists():
        lock = bench.read(folder / "matrix.lock.json")
        bench.require(lock["source"] == source and lock["spec"] == spec, "矩阵配置或调度代码改变；需新建 matrix_id")
        return lock
    MATRICES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".matrix-", dir=MATRICES) as tmp:
        scratch = pathlib.Path(tmp)
        lock = {"schema_version": 1, "spec": spec, "source": source}
        batch.atomic(scratch / "matrix.lock.json", lock)
        (scratch / "configs").mkdir()
        (scratch / "batches").mkdir()
        scratch.rename(folder)
    return lock


def run(config: pathlib.Path) -> dict:
    config = config.resolve()
    spec = parse(config)
    folder = MATRICES / spec["matrix_id"]
    lock_matrix(config, folder, spec)
    rows = []
    for index, zip_name in enumerate(spec["packages"], 1):
        source = (config.parent / zip_name).resolve()
        task_name = source.stem
        row = {"task_id": task_name, "zip": str(source), "status": "pending",
               "completed": 0, "expected": len(spec["models"]) * spec["repeats"], "failed": 0}
        try:
            package, task_path, _ = package_stage.stage(source)
            per_task = task_spec(spec, package, task_path, index)
            path = folder / "configs" / f"{index:02d}_{task_name}.json"
            if path.is_file():
                bench.require(bench.read(path) == per_task, "已登记题目配置改变")
            else:
                batch.atomic(path, per_task)
            batch_folder = folder / "batches" / f"{index:02d}_{task_name}"
            if not batch_folder.exists():
                batch.initialize(path, batch_folder)
            else:
                registered = batch.load_lock(batch_folder)
                resolved, _, _ = batch.resolve_spec(path)
                bench.require(resolved == registered["spec"], "已登记批次配置改变")
            result = batch.run_batch(batch_folder)
            row.update(status="completed" if result["completed"] == result["expected_runs"] else "failed",
                       completed=result["completed"], expected=result["expected_runs"], failed=result["failed"],
                       batch=str(batch_folder), report=str(batch_folder / "batch-results.json"))
        except Exception as error:
            # Preserve the task's own batch diagnostics. Never include raw model output here.
            row.update(status="failed", failed=max(1, row["failed"]), error_type=type(error).__name__,
                       error=str(error)[:300] if isinstance(error, (bench.Invalid, ValueError, RuntimeError)) else "检查题目批次诊断")
        rows.append(row)
        batch.atomic(folder / "matrix-results.json", {"schema_version": 1, "matrix_id": spec["matrix_id"],
                     "mode": spec["mode"], "tasks": rows, "completed_tasks": sum(x["status"] == "completed" for x in rows),
                     "expected_tasks": len(spec["packages"])})
    return bench.read(folder / "matrix-results.json")


def status(folder: pathlib.Path) -> dict:
    lock = bench.read(folder / "matrix.lock.json")
    rows = []
    for index, name in enumerate(lock["spec"]["packages"], 1):
        task_name = pathlib.Path(name).stem
        batch_folder = folder / "batches" / f"{index:02d}_{task_name}"
        item = batch.read_status(batch_folder) if batch_folder.is_dir() else None
        rows.append({"task_id": task_name, "batch": item})
    return {"matrix_id": lock["spec"]["matrix_id"], "mode": lock["spec"]["mode"],
            "read_only": True, "tasks": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status"))
    parser.add_argument("--config", type=pathlib.Path)
    parser.add_argument("--matrix", type=pathlib.Path)
    args = parser.parse_args()
    try:
        if args.action == "start":
            bench.require(args.config is not None, "start 需要 --config")
            result = run(args.config)
            print(json.dumps({"matrix_id": result["matrix_id"], "completed_tasks": result["completed_tasks"],
                              "expected_tasks": result["expected_tasks"]}, ensure_ascii=False))
            if result["completed_tasks"] != result["expected_tasks"]:
                raise SystemExit(1)
        else:
            bench.require(args.matrix is not None, "status 需要 --matrix")
            print(json.dumps(status(args.matrix.resolve()), ensure_ascii=False, indent=2))
    except (bench.Invalid, ValueError, FileNotFoundError) as exc:
        print("FAILED: " + str(exc)[:300], file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
