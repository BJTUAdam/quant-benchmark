from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from .common import ROOT, LabError, read, write


def parser():
    p = argparse.ArgumentParser(description="金融长程系统v2：任务、Tushare桥接、AKQuant Docker")
    sub = p.add_subparsers(dest="group", required=True)
    sub.add_parser("setup", help="由使用者本人交互选择资源；无默认预算")
    sub.add_parser("doctor")
    sub.add_parser("test")
    q = sub.add_parser("verify"); q.add_argument("--output", type=Path)
    t = sub.add_parser("task", help="生产与审核原生v2任务")
    t.add_argument("args", nargs=argparse.REMAINDER)
    cat = sub.add_parser("catalog").add_subparsers(dest="action", required=True)
    for name in ("update", "check"):
        q = cat.add_parser(name); q.add_argument("--output", type=Path)
    dat = sub.add_parser("data").add_subparsers(dest="action", required=True)
    q = dat.add_parser("init"); q.add_argument("db", type=Path)
    q = dat.add_parser("collect"); q.add_argument("--db", type=Path, required=True); q.add_argument("--plan", type=Path, required=True)
    q = dat.add_parser("ingest"); q.add_argument("--db", type=Path, required=True); q.add_argument("--input", type=Path, required=True)
    q = dat.add_parser("freeze"); q.add_argument("--db", type=Path, required=True); q.add_argument("--plan", type=Path, required=True); q.add_argument("--output", type=Path, required=True)
    q = dat.add_parser("probe"); q.add_argument("--plan", type=Path, required=True); q.add_argument("--output", type=Path)
    delivery = sub.add_parser("delivery", help="获批版本、冻结数据和批跑交接验收").add_subparsers(dest="action", required=True)
    q = delivery.add_parser("prepare"); q.add_argument("--task", required=True); q.add_argument("--version", type=int); q.add_argument("--selection", type=Path, required=True); q.add_argument("--frozen", type=Path); q.add_argument("--output", type=Path, required=True)
    q = delivery.add_parser("check"); q.add_argument("--binding", type=Path, required=True); q.add_argument("--submission", type=Path); q.add_argument("--output", type=Path)
    q = delivery.add_parser("export"); q.add_argument("--episode", type=Path, required=True); q.add_argument("--submission", type=Path, required=True); q.add_argument("--output", type=Path, required=True)
    q = delivery.add_parser("import"); q.add_argument("archive", type=Path); q.add_argument("--output", type=Path, required=True); q.add_argument("--trust-note", required=True)
    q = delivery.add_parser("batch"); q.add_argument("plan", type=Path); q.add_argument("--output", type=Path, required=True); q.add_argument("--execute", action="store_true")
    bridge = sub.add_parser("bridge").add_subparsers(dest="action", required=True)
    q = bridge.add_parser("serve"); q.add_argument("--episode", type=Path, required=True); q.add_argument("--host", default="127.0.0.1"); q.add_argument("--port", type=int, default=8766)
    q = bridge.add_parser("audit"); q.add_argument("--episode", type=Path, required=True); q.add_argument("--job"); q.add_argument("--limit", type=int, default=100)
    submission = sub.add_parser("submission").add_subparsers(dest="action", required=True)
    q = submission.add_parser("check"); q.add_argument("folder", type=Path)
    bt = sub.add_parser("backtest").add_subparsers(dest="action", required=True)
    bt.add_parser("build-images")
    q = bt.add_parser("init"); q.add_argument("--binding", type=Path, required=True); q.add_argument("--episode", type=Path, required=True)
    q = bt.add_parser("status"); q.add_argument("--episode", type=Path, required=True)
    q = bt.add_parser("run"); q.add_argument("--episode", type=Path, required=True); q.add_argument("--submission", type=Path, required=True); q.add_argument("--research", action="store_true")
    q = bt.add_parser("save-images"); q.add_argument("output", type=Path)
    q = bt.add_parser("load-images"); q.add_argument("input", type=Path)
    q = bt.add_parser("archive"); q.add_argument("--episode", type=Path, required=True); q.add_argument("--output", type=Path, required=True)
    q = bt.add_parser("task-package"); q.add_argument("--episode", type=Path, required=True); q.add_argument("--output", type=Path, required=True)
    demo = sub.add_parser("demo").add_subparsers(dest="action", required=True)
    q = demo.add_parser("build"); q.add_argument("--asset", choices=["STOCK", "FUND", "FUTURES"], default="STOCK"); q.add_argument("--output", type=Path, required=True)
    docs = sub.add_parser("docs").add_subparsers(dest="action", required=True)
    docs.add_parser("build")
    q = sub.add_parser("bundle"); q.add_argument("--output", type=Path, default=ROOT / "dist")
    q.add_argument("--all-in-one", type=Path, help="Create one portable ZIP including fixed Docker images")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.group == "setup":
            from .setup import interactive_setup
            result = interactive_setup()
        elif args.group == "test":
            from .testing import run_tests
            result = run_tests()
        elif args.group == "verify":
            from .verify import verify
            from .setup import load_settings
            result = verify(args.output, resources=load_settings()["resources"])
        elif args.group == "task":
            from taskgen.cli import main as task_main
            return task_main(["--root", str(ROOT), *args.args])
        elif args.group == "doctor":
            from .runtime import docker
            try:
                version = docker(["version", "--format", "{{.Server.Version}}"], timeout=15).stdout.strip()
            except LabError: version = None
            from .setup import load_settings
            try:
                setup = {"status": "configured", "resources": load_settings()["resources"]}
            except LabError:
                setup = {"status": "requires_human_setup", "command": "finlab setup"}
            from .upstream import upstream_settings
            upstream = upstream_settings()
            result = {"version": "2.0.0", "python": sys.version.split()[0], "docker_server": version, "operator_setup": setup,
                      "upstream_url": upstream['url'],
                      "tushare_token_present": upstream['token_present'], "upstream_connection": "not_tested",
                      "images_locked": (ROOT / "deployment/images.lock.json").is_file()}
        elif args.group == "catalog":
            from .catalog import update_catalog, check_catalog
            result = update_catalog(args.output) if args.action == "update" and args.output else update_catalog() if args.action == "update" else check_catalog()
            if args.action == "check" and args.output: write(args.output, result)
        elif args.group == "data":
            from . import data
            if args.action == "init": result = data.init(args.db)
            elif args.action == "collect": result = data.collect(args.db, read(args.plan))
            elif args.action == "freeze": result = data.freeze(args.db, read(args.plan), args.output)
            elif args.action == "probe":
                from .upstream import probe
                result = probe(read(args.plan))
                if args.output: write(args.output, result)
            else:
                obj = read(args.input); result = data.ingest(args.db, obj["api_name"], obj["rows"], obj["rule"], partition=obj.get("partition"))
        elif args.group == "delivery":
            from taskgen.workflow import Production
            Production(ROOT).init()  # Verify and load bundled machine acceptances; never infer a human verdict.
            from . import delivery
            if args.action == "prepare": result = delivery.prepare(args.task, read(args.selection), args.output, version=args.version, frozen_dir=args.frozen)
            elif args.action == "check":
                result = delivery.check_binding(read(args.binding), args.submission)
                if args.output: write(args.output, result)
            elif args.action == "export": result = delivery.export_handoff(args.episode, args.submission, args.output)
            elif args.action == "import": result = delivery.import_handoff(args.archive, args.output, args.trust_note)
            else:
                result = delivery.batch(read(args.plan), execute=args.execute)
                write(args.output, result)
        elif args.group == "bridge":
            if args.action == "audit":
                from .disclosure import read_audit
                from .common import safe_name
                from .runtime import load_episode
                folder, _, _, _ = load_episode(args.episode)
                path = (folder / "jobs" / safe_name(args.job) / "artifacts" / "bridge_audit.private.sqlite"
                        if args.job else folder / "bridge_audit.private.sqlite")
                print(json.dumps(read_audit(path, args.limit), ensure_ascii=False, indent=2))
                return 0
            from .runtime import EpisodeResearchView
            from .server import query_server
            token = os.environ.get("MARKET_BRIDGE_TOKEN")
            server = query_server(EpisodeResearchView(args.episode), token, args.host, args.port)
            print(json.dumps({"status": "research_bridge_serving", "upstream_access": False}), flush=True)
            try: server.serve_forever()
            finally: server.server_close()
            return 0
        elif args.group == "submission":
            from .submission import check
            result = check(args.folder)
        elif args.group == "backtest":
            from . import runtime
            if args.action == 'init':
                from taskgen.workflow import Production
                Production(ROOT).init()
            if args.action == "build-images": result = runtime.build_images()
            elif args.action == "init": result = runtime.create_episode(runtime.bind_operator_resources(read(args.binding)), args.episode)
            elif args.action == "status": result = runtime.status(args.episode)
            elif args.action == "run":
                from .setup import load_settings
                load_settings()
                result = runtime.run(args.episode, args.submission, research=args.research)
            elif args.action == "save-images":
                images = read(ROOT / "deployment/images.lock.json")["images"]
                runtime.docker(["save", "-o", args.output, *images.values()], timeout=600)
                result = {"saved": str(args.output)}
            elif args.action == "archive":
                from .archive import archive_episode
                result = archive_episode(args.episode, args.output)
            elif args.action == "task-package":
                from .archive import bound_package
                result = bound_package(args.episode, args.output)
            else:
                runtime.docker(["load", "-i", args.input], timeout=600)
                images = read(ROOT / "deployment/images.lock.json")["images"]
                for image_id in images.values(): runtime.docker(["image", "inspect", image_id])
                result = {"loaded": True, "locked_image_ids_verified": True}
        elif args.group == "demo":
            from .demo import build
            from .setup import load_settings
            result = build(args.output, args.asset, resources=load_settings()["resources"])
        elif args.group == "docs":
            from .docs import build
            result = build()
        else:
            from .bundle import build, build_all_in_one
            result = build(args.output)
            if args.all_in_one: result['all_in_one'] = build_all_in_one(args.all_in_one, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if result.get("passed") is False else 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        message = str(exc) if isinstance(exc, LabError) else "操作失败；由运营方检查配置和私有日志"
        print(json.dumps({"status": "failed", "error": message, "type": type(exc).__name__,
                          "error_code": exc.code if isinstance(exc, LabError) else None,
                          "upstream_code": getattr(exc, 'upstream_code', None),
                          "http_status": getattr(exc, 'http_status', None)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
