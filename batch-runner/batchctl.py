#!/usr/bin/env python3
"""批跑系统：按任务清单批量启动金融长轨迹（调用轨迹运行器的 finlab_v2.py start）。

    python3 batchctl.py check   batch.json      校验配置，并用轨迹运行器自己的校验检查每条任务的配置
    python3 batchctl.py run     batch.json      开始/续跑批次（Ctrl-C 安全中断，下次运行自动续跑）
    python3 batchctl.py status  batch.json      查看每个任务的状态
    python3 batchctl.py report  batch.json      生成 out/<batch_id>/report.json 与 report.csv
    python3 batchctl.py requeue batch.json --failed     把失败任务重新排队（换新 run_id）

不读取、不写入任何密钥；模型密钥由轨迹配置模板里的 api_key_file 指向。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import report as reportmod
import spec as specmod
import store as storemod
from scheduler import BatchBusy, Scheduler, exclusive

VALIDATOR = r'''
import importlib, json, pathlib, sys
script = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(script.parent))
module = importlib.import_module(script.stem)
result = []
checked_bindings = {}
for path in json.loads(sys.stdin.read()):
    try:
        cfg = module.config(pathlib.Path(path).resolve())
        if cfg["task"]["kind"] == "bound_task":
            binding = cfg["task"]["binding"]
            key = (binding, cfg["finlab_root"], cfg["finlab_python"], cfg["docker_image"])
            if key not in checked_bindings:
                from docker_sandbox import inspect_image
                inspect_image(cfg["docker_image"])
                checked_bindings[key] = module.Backend(cfg, pathlib.Path(path).parent).call(
                    "preflight", binding=binding)
            check = checked_bindings[key]
            if not check["passed"]:
                raise ValueError("正式题预检失败：" + ", ".join(
                    item["check"] + "=" + item["code"] for item in check["blockers"]))
        result.append([path, None])
    except BaseException as exc:
        result.append([path, str(exc)[:300] or type(exc).__name__])
print("VALIDATION " + json.dumps(result, ensure_ascii=False))
'''


def validate_configs(spec, jobs):
    """用轨迹运行器自带的 config() 校验每条任务生成的配置，不启动任何运行。"""
    if not jobs:
        return
    paths = {}
    with tempfile.TemporaryDirectory() as scratch:
        for job in jobs:
            cfg = specmod.build_config(spec, job, 1)
            path = os.path.join(scratch, cfg["run_id"] + ".json")
            specmod.write_json(path, cfg)
            paths[path] = job["job_id"]
        try:
            result = subprocess.run([spec["python"], "-I", "-B", "-X", "utf8", "-c", VALIDATOR, spec["runner_script"]],
                                    input=json.dumps(list(paths)), capture_output=True, text=True, encoding="utf-8",
                                    env={**os.environ, **spec["env"]}, cwd=spec["runner_root"], timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise specmod.SpecError("配置预检未完成：" + type(exc).__name__) from None
    line = next((l for l in reversed(result.stdout.splitlines()) if l.startswith("VALIDATION ")), None)
    if line is None:
        raise specmod.SpecError("无法运行轨迹运行器的配置校验：" + (result.stderr.strip()[-300:] or "无输出"))
    problems = [f"{paths[path]}: {message}" for path, message in json.loads(line[len("VALIDATION "):]) if message]
    if problems:
        raise specmod.SpecError("轨迹配置校验未通过：\n  " + "\n  ".join(problems))


def row_job(row):
    return {"job_id": row["job_id"], "task": json.loads(row["task"]), "overrides": json.loads(row["overrides"])}


def cmd_check(args):
    spec = specmod.load_spec(args.spec)
    if not args.no_validate:
        validate_configs(spec, spec["jobs"])
    print(f"批次 {spec['batch_id']} 配置有效：{len(spec['jobs'])} 个任务，并发 {spec['concurrency']}，"
          f"每任务最多 {spec['max_attempts']} 次尝试"
          + ("" if not args.no_validate else "（已跳过轨迹运行器校验）"))
    return 0


def cmd_run(args):
    spec = specmod.load_spec(args.spec)
    try:
        with exclusive(spec["batch_dir"]):
            store = storemod.open_store(spec)
            try:
                if not args.no_validate:
                    validate_configs(spec, [j for j in spec["jobs"] if store.get(j["job_id"]) is None
                                            or store.get(j["job_id"])["status"] != "completed"])
                added = store.sync(spec)
                if added:
                    print(f"登记 {added} 个新任务", flush=True)
                try:
                    return Scheduler(spec, store).run()
                finally:
                    reportmod.write_report(spec, store)
            finally:
                store.close()
    except BatchBusy as exc:
        print("FAILED: " + str(exc), file=sys.stderr)
        return 2


def with_store(args, action):
    spec = specmod.load_spec(args.spec)
    store = storemod.open_store(spec)
    try:
        return action(spec, store)
    finally:
        store.close()


def cmd_status(args):
    def show(spec, store):
        rows = reportmod.collect(spec, store)
        if args.json:
            print(json.dumps({"summary": reportmod.aggregate(rows), "jobs": rows}, ensure_ascii=False, indent=2))
            return 0
        print(f"批次 {spec['batch_id']}  {json.dumps(store.counts(), ensure_ascii=False)}")
        for row in rows:
            ret = row.get("total_return")
            print(f"  {row['job_id']:<22}{row['status']:<11}尝试 {row['attempt']}/{row['attempt_limit']}  "
                  f"段数 {row.get('segments') if row.get('segments') is not None else '-'}  "
                  f"收益 {'-' if ret is None else format(ret, '.4%')}  {row['error'] or ''}")
        return 0
    return with_store(args, show)


def cmd_report(args):
    def write(spec, store):
        document, json_path, csv_path = reportmod.write_report(spec, store)
        print(json.dumps(document["summary"], ensure_ascii=False, indent=2))
        print(json_path)
        print(csv_path)
        return 0
    return with_store(args, write)


def cmd_requeue(args):
    def requeue(spec, store):
        names = [r["job_id"] for r in store.with_status("failed")] if args.failed else args.jobs
        if not names:
            print("没有需要重新排队的任务")
            return 0
        changed = store.requeue(spec, names)
        print("已重新排队：" + (", ".join(changed) if changed else "无（只有 failed 状态的任务可重新排队）"))
        return 0
    spec = specmod.load_spec(args.spec)
    try:
        with exclusive(spec["batch_dir"]):   # 控制器运行时不允许改状态
            return with_store(args, requeue)
    except BatchBusy as exc:
        print("FAILED: " + str(exc), file=sys.stderr)
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("check", cmd_check), ("run", cmd_run), ("status", cmd_status),
                       ("report", cmd_report), ("requeue", cmd_requeue)):
        p = sub.add_parser(name)
        p.add_argument("spec", help="批次配置 batch.json")
        p.set_defaults(func=func)
        if name in ("check", "run"):
            p.add_argument("--no-validate", action="store_true", help="跳过轨迹运行器的配置校验")
        if name == "status":
            p.add_argument("--json", action="store_true")
        if name == "requeue":
            p.add_argument("jobs", nargs="*")
            p.add_argument("--failed", action="store_true", help="重新排队所有失败任务")
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (specmod.SpecError, RuntimeError) as exc:
        print("FAILED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
