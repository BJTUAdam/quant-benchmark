from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from .io_utils import read_json
from .workflow import Production, WorkflowError
from .registry import RegistryError


def parser():
    p = argparse.ArgumentParser(description="金融长程Task规格生成器：模型生成、检查、自动接纳、导出")
    local_root = Path.cwd() if (Path.cwd() / "catalog" / "families.json").is_file() else Path(__file__).resolve().parent.parent
    p.add_argument("--root", type=Path, default=local_root)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "doctor", "import-examples", "report"):
        command = sub.add_parser(name)
        if name == "doctor":
            command.add_argument("--config", type=Path)
    gen = sub.add_parser("generate")
    gen.add_argument("--batch-config", type=Path, default=Path("config/batch.json"))
    gen.add_argument("--config", type=Path)
    imp = sub.add_parser("import-spec")
    imp.add_argument("path", type=Path)
    snapshot = sub.add_parser("snapshot", help="保存未批准候选的可分发快照，不含私有登记库或凭证")
    snapshot.add_argument("--batch", required=True)
    for name in ("check", "review"):
        command = sub.add_parser(name)
        command.add_argument("--batch", required=True)
        if name == "review":
            command.add_argument("--api", action="store_true", help="实际调用API；不加只展示当前报告")
            command.add_argument("--candidate-id", action="append", help="可重复指定候选；省略时检查全批")
            command.add_argument("--config", type=Path)
    approve = sub.add_parser("approve")
    approve.add_argument("candidate_id")
    approve.add_argument("--note", required=True)
    group = approve.add_mutually_exclusive_group()
    group.add_argument("--as-variant", metavar="PARENT_TASK_ID")
    group.add_argument("--update-task", metavar="TASK_ID")
    accept = sub.add_parser('accept',help='根据当前程序与模型证据自动接纳；无需人工审批')
    accept.add_argument('candidate_id',nargs='?')
    accept.add_argument('--batch')
    accepted_snapshot=sub.add_parser('acceptance-snapshot',help='分发已自动接纳的不可变规格，不含私有登记库或行情')
    accepted_snapshot.add_argument('--output',type=Path)
    reject = sub.add_parser("reject")
    reject.add_argument("candidate_id")
    reject.add_argument("--note", required=True)
    export = sub.add_parser("export")
    export.add_argument("candidate_id")
    sub.add_parser("approved", help="列出真正获批的不可变规格版本，不把历史ID当作可运行题")
    show = sub.add_parser("approved-show", help="读取不可变批准版本及其原始证据")
    show.add_argument("reference")
    show.add_argument("--version", type=int)
    opts = sub.add_parser("reuse-options", help="解析候选查重ID，列出可复用版本或未解决引用")
    opts.add_argument("candidate_id")
    reuse = sub.add_parser("reuse", help="取回已批准版本，不创建新题或新批准")
    reuse.add_argument("reference")
    reuse.add_argument("--version", type=int)
    reuse.add_argument("--output", type=Path, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    app = Production(args.root, getattr(args, "config", None))
    try:
        if args.command == "init":
            result = app.init()
        elif args.command == "doctor":
            cfg = read_json(app.config_path)
            key_present = bool(os.environ.get(cfg.get("api_key_env", "TASKGEN_API_KEY")))
            result = {"python": sys.version.split()[0], "config_file": str(app.config_path),
                      "adapter": cfg.get("adapter"), "key_environment_variable": cfg.get("api_key_env"),
                      "key_present": key_present, "base_url_configured": bool(cfg.get("base_url")),
                      "generation_model_configured": bool(cfg.get("generation_model")),
                      "review_model_configured": bool(cfg.get("review_model") or cfg.get("generation_model")),
                      "connection_status": "not_tested", "api_called": False,
                      "ready_for_attempt": key_present and bool(cfg.get("base_url")) and bool(cfg.get("generation_model")),
                      "data_status": "not_bound", "pipeline_status": "not_connected"}
            verification_path = app.root / "docs" / "live_api_connection.json"
            if verification_path.is_file():
                verification = read_json(verification_path)
                result["last_recorded_verification"] = {k: verification.get(k) for k in
                    ("status", "checked_at", "completion_verified", "last_completion_at", "response_models")}
        elif args.command == "import-examples":
            result = app.import_examples()
        elif args.command == "import-spec":
            result = app.import_spec(args.path)
        elif args.command == "snapshot":
            from .batch_snapshot import snapshot
            result = snapshot(app.root, args.batch)
        elif args.command == "generate":
            result = app.generate(args.batch_config)
        elif args.command == "check":
            result = app.check_batch(args.batch)
        elif args.command == "review":
            result = app.review_batch(args.batch, args.api, args.candidate_id)
        elif args.command == "approve":
            result = app.approve(args.candidate_id, args.note, args.as_variant, args.update_task)
        elif args.command == 'accept':
            if bool(args.candidate_id) == bool(args.batch): raise WorkflowError('须指定候选ID或--batch，不能同时指定')
            result = app.accept(args.candidate_id) if args.candidate_id else app.accept_batch(args.batch)
        elif args.command == 'acceptance-snapshot':
            result = app.snapshot_acceptances(args.output)
        elif args.command == "reject":
            result = app.reject(args.candidate_id, args.note)
        elif args.command == "export":
            result = app.export(args.candidate_id)
        elif args.command == "approved":
            result = app.approved_catalog()
        elif args.command == "approved-show":
            result = app.registry.resolve_approved(args.reference, args.version)
        elif args.command == "reuse-options":
            result = app.reuse_options(args.candidate_id)
        elif args.command == "reuse":
            result = app.reuse_approved(args.reference, args.output, args.version)
        else:
            reports = [read_json(x) for x in sorted((app.root / "runs").glob("*/coverage_report.json"))]
            records = app.registry.records()
            result = {"historical_tasks": sum(x["status"] != "spec_approved" for x in records),
                      "approved_independent_tasks": sum(x["status"] == "spec_approved" for x in records), "batches": reports}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("errors") or result.get("failures") or result.get("status") in {"configuration_error", "attention_required"}:
            return 2
        return 0
    except (WorkflowError, RegistryError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        from .api import APIError
        if isinstance(exc, APIError):
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
