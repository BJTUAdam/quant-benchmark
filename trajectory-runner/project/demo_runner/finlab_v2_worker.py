"""Trusted subprocess bridge to an independently installed finlab v2 checkout.

Never imports submitted Python. stdout contains only the structured response.
The parent invokes this process without model or upstream API credentials.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import shutil
import sys


def real_binding(path):
    from finlab.common import read, LabError
    path = pathlib.Path(path).resolve()
    binding = read(path)
    if binding.get("synthetic"):
        raise LabError("REAL_BINDING_REQUIRED")
    frozen = pathlib.Path(binding["frozen_dir"])
    if not frozen.is_absolute():
        base = path.parent
        if frozen.parts[:1] == ("platform_private",) and base.name == "platform_private":
            base = base.parent
        binding["frozen_dir"] = str((base / frozen).resolve())
    return binding


def dispatch(req):
    from finlab import runtime, submission, tasks
    from finlab.common import read, write, file_hash, LabError
    from finlab.setup import load_settings

    episode = pathlib.Path(req["episode"])
    action = req["action"]
    if action == "preflight":
        from finlab.delivery import check_binding
        result = check_binding(real_binding(req["binding"]))
        return {"passed": result["passed"], "blockers": [
            {"check": item["check"], "code": item["code"]} for item in result["blockers"]]}
    if action == "prepare":
        settings = load_settings()  # Preserve upstream operator setup requirements.
        from finlab.common import ROOT
        image_file = ROOT / "deployment/images.lock.json"
        if not image_file.is_file():
            raise LabError("IMAGES_NOT_BUILT")
        images = read(image_file).get("images", {})
        if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", images.get(key, "")) for key in ("engine", "worker")):
            raise LabError("INVALID_IMAGE_LOCK")
        if req["kind"] == "synthetic_demo":
            from finlab.demo import build
            demo = episode.parent / "demo"
            build(demo, req["asset"], resources=settings["resources"])
            binding = read(demo / "binding.private.json")
        else:
            binding = real_binding(req["binding"])
        runtime.create_episode(runtime.bind_operator_resources(binding), episode)
        workspace = pathlib.Path(req["workspace"])
        if req["kind"] == "bound_task":
            from finlab.archive import bound_package
            bound_package(episode, workspace)
        else:
            # A demo gets an explicitly synthetic public contract. Do not use an
            # unrelated F01/F05 research specification or the fixture's strategy.
            _, binding, state, view = runtime.load_episode(episode)
            workspace.mkdir()
            for name, content in tasks.templates().items():
                (workspace / name).write_text(content, encoding="utf-8")
            profile = binding["runtime"]
            instruments = json.loads(json.dumps(profile["instruments"]))
            for item in instruments:
                if item.get("expiry_date"):
                    item["expiry_date"] = view.mapping.shift(item["expiry_date"], "date")
            endpoints = [{"api_name": api, "fields": contract["fields"]}
                         for api, contract in view.manifest["apis"].items()]
            write(workspace / "bridge_contract.json", {
                "schema_version": "finlab.bridge_contract.v2", "synthetic": True,
                "date_domain": "virtual", "dataset_id": binding["dataset_id"],
                "symbols": view.manifest["symbols"], "endpoints": endpoints})
            write(workspace / "runtime_profile.json", {
                "schema_version": "finlab.runtime.v2", "engine": "akquant",
                "strategy_api": "finlab.strategy.v2", "synthetic": True,
                "instruments": instruments, "initial_cash": profile["initial_cash"],
                "fill_policy": profile["fill_policy"], "formal_segments": 7})
            (workspace / "TASK.md").write_text(
                "# v2 合成数据接口联调任务\n全部行情为程序合成，不代表真实投资表现。\n"
                "请基于当前可见数据研究并交付一个可执行策略，逐段结合公开反馈维护。\n"
                "七段之间延续账户、订单与 JSON memory；不得假设未来行情。\n"
                "数据与交易标的以 bridge_contract.json、runtime_profile.json 为准。\n",
                encoding="utf-8")
            (workspace / "SUBMISSION.md").write_text(
                "提交 strategy.py、backtest.py、params.json、RESEARCH.md、CHANGELOG.md。\n"
                "Strategy 继承 finlab.sdk.Strategy；使用 initialize(ctx)、on_bars(ctx)、"
                "on_order(ctx,event)、on_trade(ctx,event)、on_finish(ctx)。\n"
                "ctx.bars 是已完成行情；ctx.portfolio 是账户；ctx.params 是参数；"
                "ctx.memory 必须为 JSON 对象。ctx.order(symbol,quantity,side='buy'/'sell',"
                "limit_price=None,tif='GTC'/'DAY') 下单，ctx.cancel(order_id) 撤单。\n"
                "backtest.py 调用 finlab.sdk.launch('strategy.py','params.json')。\n",
                encoding="utf-8")
            (workspace / "DATA_API.md").write_text(
                "研发通过 query_data 工具查询；策略内通过 ctx.query(api,params,fields) 查询。\n"
                "全部日期是虚拟标签，只按返回的时间顺序研究。查询受累计披露范围限制；"
                "正式策略另受事件时钟限制，越界请求整次拒绝。\n"
                "接口、字段白名单见 bridge_contract.json。不确定日期时先用 params={} 查询，"
                "再依据返回的虚拟日期筛选。query_data 返回可见边界内的数据，"
                "可用 run_research 分析保存的 JSON。禁止直接访问上游或猜测真实时期。\n",
                encoding="utf-8")
            write(workspace / "task_input.draft.json", {
                "schema_version": "finance_task_input.draft.v2", "synthetic": True,
                "initial_message": {"role": "user", "content_file": "TASK.md"},
                "expected_outputs": sorted(tasks.SUBMISSION_FILES),
                "spec_status": "synthetic_demo", "data_status": "synthetic_frozen",
                "runtime_status": "not_verified", "dataset_id": binding["dataset_id"]})
        return runtime.status(episode)
    if action == "status":
        _, binding, state, _ = runtime.load_episode(episode)
        return {**runtime.status(episode), "commits": state["commits"],
                "binding_hash": state["binding_hash"],
                "images": runtime.episode_images(binding, state)}
    if action == "query":
        # Uses the same live disclosure and audit implementation as the HTTP bridge.
        return runtime.EpisodeResearchView(episode).query(req["api"], req["params"], req["fields"])
    if action == "check":
        return submission.check(req["submission"])
    if action == "run":
        load_settings()
        result = runtime.run(episode, req["submission"], research=req["research"])
        return {"job_id": result["job_id"], "segment": result["segment"],
                "status": result["status"], "synthetic": result["synthetic"]}
    if action == "results":
        job_id = req["job_id"]
        if not re.fullmatch(r"(?:research|segment_[1-7])_[0-9a-f]{12}", job_id):
            raise LabError("INVALID_JOB_ID")
        job = episode / "jobs" / job_id
        receipt = read(job / "receipt.private.json")
        if receipt["job_id"] != job_id:
            raise LabError("RECEIPT_MISMATCH")
        dest = pathlib.Path(req["destination"])
        dest.mkdir(parents=True, exist_ok=True)
        hashes = {}
        # Deliberately do not expose private logs/checkpoints or copy whole jobs.
        for name in ("equity.csv", "orders.csv", "trades.csv", "benchmark.csv", "metrics.json"):
            rel = "public/" + name
            if rel not in receipt["hashes"]:
                continue
            source = job / "artifacts" / rel
            if source.is_symlink() or file_hash(source) != receipt["hashes"][rel]:
                raise LabError("ARTIFACT_CHANGED")
            shutil.copyfile(source, dest / name)
            hashes[name] = receipt["hashes"][rel]
        return {"files": hashes, "metrics": read(dest / "metrics.json"),
                "submission_hash": receipt["submission_hash"]}
    if action == "archive":
        from finlab.archive import archive_episode
        return archive_episode(episode, req["destination"])
    raise LabError("UNKNOWN_ACTION")


def main():
    sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve()))
    try:
        request = json.load(sys.stdin)
        with contextlib.redirect_stdout(io.StringIO()):
            result = dispatch(request)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        # Never relay upstream exception bodies, private paths or credentials.
        code = getattr(exc, "code", type(exc).__name__)
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,80}", code):
            code = "BACKEND_ERROR"
        print(json.dumps({"ok": False, "code": code}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
