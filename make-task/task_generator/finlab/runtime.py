"""Host operator controller. Docker is never made available to a submission."""
from __future__ import annotations
from contextlib import closing
import copy
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from .common import ROOT, LabError, canonical, digest, file_hash, lock, now, read, write
from .data import FrozenView, db_open
from .temporal import TimeMap, iso, parse_time
from .submission import check
from .setup import load_settings, validate_resources
from .disclosure import (AuditLog, QueryContext, make_policy, check_policy, policy_scope,
                         time_ns, ns_iso)


def bind_operator_resources(binding):
    """Bind the operator's explicit first-use choice to a new episode."""
    settings = load_settings()
    bound = copy.deepcopy(binding)
    supplied = bound["runtime"].get("resources")
    if supplied is not None and supplied != settings["resources"]:
        raise LabError("RESOURCE_SELECTION_CONFLICT", "运行预算与本人配置不同；请本人确认，AI不得覆盖")
    bound["runtime"]["resources"] = copy.deepcopy(settings["resources"])
    bound["runtime"]["resource_status"] = "human_selected"
    bound["resource_setup"] = {"method": settings["method"], "settings_hash": digest(settings)}
    return bound


def docker(args, *, timeout=60, check_exit=True):
    try:
        r = subprocess.run(["docker", *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise LabError("DOCKER_UNAVAILABLE_OR_TIMEOUT") from None
    if check_exit and r.returncode:
        raise LabError("DOCKER_COMMAND_FAILED", "详情由运营方检查Docker状态；命令不含上游令牌")
    return r


def build_images():
    for stage in ("engine", "worker"):
        r = subprocess.run(["docker", "build", "--target", stage, "-f", "deployment/Dockerfile.v2", "-t", "finlab-" + stage + ":2.0.0", "."], cwd=ROOT)
        if r.returncode: raise LabError("IMAGE_BUILD_FAILED")
    ids = {s: docker(["image", "inspect", "finlab-" + s + ":2.0.0", "--format", "{{.Id}}"]).stdout.strip() for s in ("engine", "worker")}
    write(ROOT / "deployment" / "images.lock.json", {"schema_version": "finlab.images.v2", "images": ids})
    return ids


def validate_binding(binding, frozen):
    errors = []
    profile = binding.get("runtime", {})
    instruments = profile.get("instruments") or []
    supported = {"frequency","fill_policy","market_rules_status","initial_cash","instruments","calendar","sessions",
                 "execution_rules","corporate_actions","corporate_actions_verified","settlement_mode","resources","benchmark",
                 "volume_limit_pct","risk","schema_version","engine","engine_version","strategy_api","asset_types","index_role",
                 "currency","formal_segments","multi_leg_execution","state_continuity","resource_status","resource_recommendations"}
    if set(profile)-supported: errors.append("尚未实现的运行规则："+",".join(sorted(set(profile)-supported)))
    def number(value, *, positive=False):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    if not instruments: errors.append("instruments")
    if profile.get("fill_policy") != "NextOpen": errors.append("fill_policy=NextOpen")
    if profile.get("frequency") not in {"daily", "minute"}: errors.append("frequency")
    if not profile.get("calendar"): errors.append("真实交易日历calendar")
    if not profile.get("sessions"): errors.append("交易时段sessions")
    if profile.get("market_rules_status") != "bound": errors.append("market_rules_status=bound")
    if not isinstance(profile.get("corporate_actions"), list): errors.append("公司行为清单（没有事件也须显式空数组）")
    if profile.get("corporate_actions_verified") is not True: errors.append("corporate_actions_verified=true")
    if not number(profile.get("initial_cash"), positive=True): errors.append("initial_cash")
    if not number(binding.get("research_fraction", .3), positive=True) or binding.get("research_fraction", .3) >= 1: errors.append("research_fraction")
    if not number(profile.get("volume_limit_pct", .1), positive=True) or profile.get("volume_limit_pct", .1) > 1: errors.append("volume_limit_pct")
    symbols = [i.get("symbol") for i in instruments]
    if len(set(symbols)) != len(symbols): errors.append("重复交易标的")
    for item in instruments:
        allowed_instrument = {"symbol","asset_type","multiplier","lot_size","tick_size","sellable_after_days","commission_rate",
                              "min_commission","stamp_tax_rate","transfer_fee_rate","slippage_bps","margin_ratio","expiry_date",
                              "fee_per_contract","no_daily_price_limit"}
        if set(item)-allowed_instrument: errors.append("尚未实现的标的规则")
        required = {"symbol", "asset_type", "multiplier", "lot_size", "tick_size", "sellable_after_days",
                    "commission_rate", "min_commission", "stamp_tax_rate", "transfer_fee_rate", "slippage_bps"}
        if item.get("asset_type") == "FUTURES": required |= {"margin_ratio", "expiry_date"}
        if required - item.keys(): errors.append("合约规则缺失：" + item.get("symbol", "unknown") + ":" + ",".join(sorted(required - item.keys())))
        if item.get("asset_type") not in {"STOCK", "FUND", "FUTURES"}: errors.append("指数或未知资产不可交易")
        if item.get("symbol") not in frozen.manifest["symbols"]: errors.append("标的不在冻结域")
        if item.get("asset_type") == "FUTURES" and profile.get("settlement_mode") != "settlement_mark": errors.append("期货settlement_mode=settlement_mark")
        for key in ("multiplier", "lot_size", "tick_size"):
            if not number(item.get(key), positive=True): errors.append("无效合约数值：" + key)
        for key in ("commission_rate", "min_commission", "stamp_tax_rate", "transfer_fee_rate", "slippage_bps", "fee_per_contract"):
            if not number(item.get(key, 0) if key == "fee_per_contract" else item.get(key)): errors.append("无效费用：" + key)
        delay = item.get("sellable_after_days")
        if isinstance(delay, bool) or not isinstance(delay, int) or delay not in {0,1}: errors.append("sellable_after_days仅支持0或1")
        if item.get("fee_per_contract",0) != 0: errors.append("当前按成交额费率；不接受未实现的按手收费")
        if item.get("asset_type") == "FUTURES":
            if profile.get("risk",{}).get("account_mode","margin") != "margin": errors.append("期货需要margin账户")
            if not number(item.get("margin_ratio"), positive=True) or item.get("margin_ratio", 0) > 1: errors.append("margin_ratio")
            try: frozen.mapping.shift(item.get("expiry_date") or "missing", "date")
            except LabError: errors.append("expiry_date")
        sessions = (profile.get("sessions") or {}).get(item.get("symbol"), [])
        if not sessions or any(not isinstance(s, list) or len(s) not in {3, 4} or s[2] not in {"continuous", "auction"} or
                               len(s) == 4 and s[3] not in {"prev", "same", "next"} for s in sessions):
            errors.append("每标的sessions需要3或4项")
    for action in profile.get("corporate_actions") or []:
        if set(action)-{"symbol","date","type","value"}: errors.append("尚未实现的公司行为规则")
        if (action.get("symbol") not in symbols or action.get("type") not in {"dividend", "split"} or
                not number(action.get("value"), positive=True)):
            errors.append("公司行为字段无效")
        try: frozen.mapping.shift(action["date"], "date")
        except (LabError, KeyError): errors.append("公司行为日期无效")
    try:
        validate_resources(profile.get("resources"))
    except LabError:
        errors.append("资源预算须显式填写CPU、内存与时限（null表示本人选择不限时）；无默认值")
    rules = profile.get("execution_rules")
    rule_index = {}
    if not isinstance(rules, list) or not rules: errors.append("execution_rules")
    for rule in rules or []:
        if set(rule)-{"symbol","trade_date","tradable","up_limit","down_limit"}: errors.append("尚未实现的逐日执行规则")
        key = (rule.get("symbol"), rule.get("trade_date"))
        if key in rule_index: errors.append("重复逐日交易规则")
        rule_index[key] = rule
        if rule.get("symbol") not in symbols or not isinstance(rule.get("tradable"), bool): errors.append("逐日规则标的或tradable")
        for name in ("up_limit", "down_limit"):
            if name in rule and not number(rule[name], positive=True): errors.append("价格限制无效：" + name)
        if number(rule.get("up_limit")) and number(rule.get("down_limit")) and rule["down_limit"] > rule["up_limit"]: errors.append("价格限制倒置")
        try: frozen.mapping.shift(rule["trade_date"], "date")
        except (LabError, KeyError): errors.append("逐日规则日期无效")
    calendar = profile.get("calendar") or []
    try:
        if not isinstance(calendar, list) or len(set(calendar)) != len(calendar) or calendar != sorted(calendar): raise ValueError()
        for d in calendar: frozen.mapping.shift(d, "date")
    except (LabError, ValueError, TypeError): errors.append("calendar需有序且日期有效")
    feeds = binding.get("feeds", [])
    if not feeds: errors.append("feeds")
    for feed in feeds:
        if set(feed)-{"api_name","event_field","close_time","volume_multiplier","amount_multiplier"}: errors.append("尚未实现的feed规则")
        if feed.get("api_name") not in frozen.manifest["apis"]: errors.append("feed未冻结")
        if not {"volume_multiplier", "amount_multiplier", "event_field"} <= feed.keys(): errors.append("feed单位与时间定义")
        if not number(feed.get("volume_multiplier"), positive=True) or not number(feed.get("amount_multiplier"), positive=True): errors.append("feed单位倍率")
        if feed.get("api_name") in frozen.manifest["apis"]:
            fields = frozen.manifest["apis"][feed["api_name"]]["fields"]
            if not {"ts_code", "open", "high", "low", "close", "vol", feed.get("event_field")} <= fields.keys(): errors.append("行情关键字段未冻结")
    benchmark = profile.get("benchmark")
    if benchmark:
        ep = frozen.manifest["apis"].get(benchmark.get("api_name"))
        if (benchmark.get("api_name") != "index_daily" or benchmark.get("symbol") not in frozen.manifest["symbols"] or
                not ep or not {"ts_code", "trade_date", "close"} <= ep["fields"].keys()): errors.append("基准需冻结index_daily、代码和收盘价")
    if errors: raise LabError("BINDING_INCOMPLETE", "；".join(errors))
    # Validate per-day rules before creating an episode, rather than discovering
    # a missing limit/margin input after containers have started.
    inst = {i["symbol"]: i for i in instruments}
    observed = set()
    with closing(db_open(frozen.folder / "snapshot.sqlite")) as con:
        for feed in feeds:
            for (body,) in con.execute("select body from records where api=?", (feed["api_name"],)):
                row = json.loads(body); symbol = row.get("ts_code")
                if symbol not in inst: continue
                at = iso(row[feed["event_field"]])
                if not frozen.manifest["real_start"] <= at < frozen.manifest["real_stop"]: continue
                day = parse_time(row.get("trade_date", at)).date().isoformat()
                observed.add((symbol, day))
                rule = rule_index.get((symbol, day))
                if day not in calendar or not rule: errors.append("行情日期缺少日历或交易规则")
                elif not inst[symbol].get("no_daily_price_limit", False) and not {"up_limit", "down_limit"} <= rule.keys(): errors.append("逐日价格限制缺失")
                if inst[symbol]["asset_type"] == "FUTURES" and not number(row.get("settle"), positive=True): errors.append("期货结算价缺失")
    for day in calendar:
        if not frozen.manifest["real_start"] <= iso(day) < frozen.manifest["real_stop"]: continue
        for symbol in inst:
            rule = rule_index.get((symbol, day))
            if rule is None: errors.append("交易日缺少标的规则；停牌或未上市须显式tradable=false")
            elif rule["tradable"] and (symbol, day) not in observed: errors.append("可交易日行情缺口")
    if errors: raise LabError("BINDING_INCOMPLETE", "；".join(sorted(set(errors))))
    return profile


def derive_windows(binding, frozen, profile=None):
    """Shared preflight and creation validation; never creates an episode."""
    profile = profile or binding["runtime"]
    dates = set()
    with closing(db_open(frozen.folder / "snapshot.sqlite")) as con:
        for feed in binding["feeds"]:
            for body, event_at in con.execute("select body,event_at from records where api=?", (feed["api_name"],)):
                row = json.loads(body)
                if row.get("ts_code") in {i["symbol"] for i in profile["instruments"]} and frozen.manifest["real_start"] <= event_at < frozen.manifest["real_stop"]:
                    dates.add(parse_time(event_at).date().isoformat())
    dates = sorted(dates)
    split = int(len(dates) * binding.get("research_fraction", .30))
    if split < 2 or len(dates) - split < 14:
        raise LabError("INSUFFICIENT_SEGMENT_DATES", "研发至少2日，七段每段至少2日")
    windows = [{"segment": 0, "start": dates[0], "stop": dates[split]}]
    rest = dates[split:]
    for i in range(7):
        a, b = len(rest) * i // 7, len(rest) * (i + 1) // 7
        windows.append({"segment": i + 1, "start": rest[a], "stop": rest[b] if b < len(rest) else (parse_time(rest[-1]) + timedelta(days=1)).date().isoformat()})
    if "stage_windows" in binding:
        windows = copy.deepcopy(binding["stage_windows"])
        # AKQuant episodes are currently split on complete trading days. More
        # precise clocks remain supported by the query scope and minute feeds.
        if (not isinstance(windows, list) or len(windows) != 8 or
                any(not isinstance(w, dict) or any(not isinstance(w.get(k), str) or len(w[k]) != 10
                                                   for k in ("start", "stop")) for w in windows)):
            raise LabError("INVALID_STAGE_WINDOWS", "须显式提供研发期和七段的YYYY-MM-DD真实交易日边界")
        for w in windows:
            if sum(w["start"] <= d < w["stop"] for d in dates) < 2:
                raise LabError("INSUFFICIENT_SEGMENT_DATES", "各阶段至少覆盖2个交易日")
        if windows[0]["start"] > dates[0] or windows[-1]["stop"] <= dates[-1]:
            raise LabError("INCOMPLETE_STAGE_WINDOWS")
    make_policy(windows, frozen.manifest, "preflight")
    return windows


def create_episode(binding, folder):
    dest = Path(folder).resolve()
    if dest.exists(): raise LabError("ALREADY_EXISTS")
    frozen = FrozenView(binding["frozen_dir"])
    synthetic = frozen.manifest.get("synthetic") is True
    if not synthetic or binding.get("runtime", {}).get("resources") is None:
        binding = bind_operator_resources(binding)
    profile = validate_binding(binding, frozen)
    if not synthetic:
        from .delivery import validate_approved_contract
        from taskgen.registry import Registry
        acceptance = validate_approved_contract(binding, frozen, Registry(ROOT / 'registry/tasks.sqlite'))
        approved = acceptance['spec']
    windows = derive_windows(binding, frozen, profile)
    image_lock = ROOT / "deployment/images.lock.json"
    episode_id = "ep_" + uuid.uuid4().hex
    disclosure = make_policy(windows, frozen.manifest, episode_id)
    immutable = {**binding, "images": read(image_lock)["images"] if image_lock.is_file() else None,
                 "approved_spec": approved if not synthetic else None, "frozen_dir": str(frozen.folder.resolve()), "windows": windows,
                 "dataset_id": frozen.manifest["dataset_id"], "freeze_hash": frozen.manifest["manifest_hash"], "synthetic": synthetic,
                 "disclosure_policy_hash": disclosure["policy_hash"]}
    if not synthetic:
        immutable['acceptance_method'] = acceptance.get('approval_method', 'human')
        immutable['acceptance_novelty'] = acceptance.get('novelty', acceptance.get('action'))
    dest.mkdir(parents=True)
    write(dest / "binding.private.json", immutable)
    write(dest / "disclosure.private.json", disclosure)
    state = {"schema_version": "finlab.episode.v2", "episode_id": episode_id,
             "binding_hash": digest(immutable), "completed_segment": 0, "checkpoint": None,
             "commits": [], "status": "research_ready", "synthetic": synthetic,
             "disclosure_policy_hash": disclosure["policy_hash"]}
    write(dest / "state.private.json", state)
    return status(dest)


def load_episode(folder):
    folder = Path(folder).resolve()
    binding, state = read(folder / "binding.private.json"), read(folder / "state.private.json")
    if digest(binding) != state["binding_hash"]: raise LabError("BINDING_CHANGED")
    view = FrozenView(binding["frozen_dir"])
    if view.manifest["manifest_hash"] != binding["freeze_hash"]: raise LabError("FROZEN_VERSION_CHANGED")
    load_disclosure(folder, binding, state, view)
    return folder, binding, state, view


def load_disclosure(folder, binding, state, view):
    path = Path(folder) / "disclosure.private.json"
    expected_hash = binding.get("disclosure_policy_hash")
    if not path.is_file() or not expected_hash:
        raise LabError("DISCLOSURE_POLICY_REQUIRED")
    if state.get("disclosure_policy_hash") != expected_hash:
        raise LabError("DISCLOSURE_POLICY_CHANGED")
    completed, commits = state.get("completed_segment"), state.get("commits")
    if (isinstance(completed, bool) or not isinstance(completed, int) or not 0 <= completed <= 7 or
            not isinstance(commits, list) or len(commits) != completed or
            any(not isinstance(c, dict) or c.get("segment") != i + 1 for i, c in enumerate(commits))):
        raise LabError("INVALID_COMMITTED_DISCLOSURE_STATE")
    try:
        policy = read(path)
    except (OSError, ValueError):
        raise LabError("DISCLOSURE_POLICY_CHANGED") from None
    return check_policy(policy, binding["windows"], view.manifest, state["episode_id"], expected_hash)


def status(folder):
    folder, binding, state, view = load_episode(folder)
    mapping = view.mapping
    upto = binding["windows"][state["completed_segment"]]["stop"]
    return {"episode_id": state["episode_id"], "status": state["status"], "completed_segment": state["completed_segment"],
            "synthetic": state["synthetic"], "research_visible_stop": mapping.shift(upto, "date"),
            "formal_windows": [{"segment": w["segment"], "start": mapping.shift(w["start"], "date"), "stop": mapping.shift(w["stop"], "date")} for w in binding["windows"][1:]],
            "dataset_id": binding["dataset_id"]}


class EpisodeResearchView:
    def __init__(self, folder):
        self.folder = Path(folder)
        _, self.binding, state, self.view = load_episode(folder)
        policy = load_disclosure(self.folder, self.binding, state, self.view)
        self.view.scope = policy_scope(policy, state["completed_segment"])
        self.view.audit = AuditLog(self.folder / "bridge_audit.private.sqlite")
    def assert_ready(self):
        self._context()
        self.view.assert_ready()
    def register_secret(self, secret):
        self.view.register_secret(secret)
    def _context(self):
        # Read one atomic state version for each request. Never mutate a shared
        # FrozenView clock while another request is using its permission scope.
        state = read(self.folder / "state.private.json")
        if state["binding_hash"] != digest(self.binding): raise LabError("BINDING_CHANGED")
        policy = load_disclosure(self.folder, self.binding, state, self.view)
        scope = policy_scope(policy, state["completed_segment"])
        return QueryContext(scope, ns_iso(time_ns(scope.stop) - 1))
    def query(self, api, params, fields):
        return self.view.query(api, params, fields, _context=self._context())
    def audit_rejection(self, api, params, code, *, request_id=None):
        return self.view.audit_rejection(api, params, code, request_id=request_id, _context=self._context())


def episode_images(binding, state):
    # A rebuild for another task must not silently switch a running episode.
    if binding.get("images"): return binding["images"]
    if state.get("commits"): return state["commits"][0]["images"]
    path = ROOT / "deployment/images.lock.json"
    return read(path)["images"] if path.is_file() else {}


STRATEGY_FAILURES = {
    "INVALID_ORDER", "INVALID_LIMIT_PRICE", "ORDER_NOT_ACTIVE", "ORDER_LOT_SIZE",
    "SHORT_OR_T_PLUS_ONE_VIOLATION", "ORDERS_OUTSIDE_BAR_CALLBACK",
    "STRATEGY_PROTOCOL_ERROR", "STRATEGY_REPLY_TOO_LARGE", "STRATEGY_RPC_FAILED",
}


class BacktestFailed(LabError):
    def __init__(self, engine_log):
        super().__init__("BACKTEST_FAILED", "运行未提交，查看该job的运营日志")
        prefix = "TRUSTED_ENGINE_FAILED LabError "
        self.strategy_code = next(
            (line.removeprefix(prefix) for line in reversed(engine_log.splitlines())
             if line.startswith(prefix) and line.removeprefix(prefix) in STRATEGY_FAILURES),
            None,
        )


def run(folder, submission, *, research=False):
    folder = Path(folder).resolve()
    with lock(folder):
        folder, binding, state, frozen = load_episode(folder)
        validate_resources(binding["runtime"].get("resources"))
        report = check(submission)
        if not report["passed"]: raise LabError("SUBMISSION_INVALID", "；".join(report["errors"]))
        segment = 0 if research else state["completed_segment"] + 1
        if segment > 7: raise LabError("EPISODE_COMPLETE")
        if research:
            window = {"start": binding["windows"][0]["start"], "stop": binding["windows"][state["completed_segment"]]["stop"]}
        else:
            window = binding["windows"][segment]
        job_id = ("research_" if research else f"segment_{segment}_") + uuid.uuid4().hex[:12]
        job = folder / "jobs" / job_id
        (job / "submission").mkdir(parents=True)
        (job / "artifacts").mkdir()
        (job / "input").mkdir()
        for name in report["files"]: shutil.copyfile(Path(submission) / name, job / "submission" / name)
        if check(job / "submission")["submission_hash"] != report["submission_hash"]:
            raise LabError("SUBMISSION_CHANGED_DURING_COPY")
        previous = state.get("checkpoint") if not research else None
        if previous:
            p = folder / previous["file"]
            if file_hash(p) != previous["sha256"]: raise LabError("CHECKPOINT_CHANGED")
            shutil.copyfile(p, job / "input" / "checkpoint.pkl")
            shutil.copyfile(folder / previous["memory_file"], job / "input" / "memory.json")
            if file_hash(job / "input" / "memory.json") != previous["memory_sha256"]: raise LabError("MEMORY_CHANGED")
            account = folder / previous["account_file"]
            if file_hash(account) != previous["account_sha256"]: raise LabError("ACCOUNT_CHANGED")
            shutil.copyfile(account, job / "input" / "account.json")
            prior_public = account.parent / "public"
            for name in ("equity.csv", "orders.csv", "trades.csv"):
                shutil.copyfile(prior_public / name, job / "input" / name)
        images = episode_images(binding,state)
        if not {"engine", "worker"} <= images.keys(): raise LabError("IMAGES_NOT_BUILT")
        rpc_token, bridge_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        profile = binding["runtime"]
        job_binding = binding
        calendar = profile["calendar"]
        if (not research and segment == 7 and profile["frequency"] == "daily"
                and calendar[-1] < window["stop"]
                and all(i["asset_type"] in {"STOCK", "FUND"} for i in profile["instruments"])):
            # AKQuant can resolve the day after the final daily bar during
            # checkpoint resume. Supply an engine-only boundary date while
            # keeping the frozen calendar and the exclusive data window intact.
            next_day = (parse_time(calendar[-1]).date() + timedelta(days=1)).isoformat()
            job_binding = {**binding, "runtime": {**profile, "calendar": [*calendar, next_day]}}
        config = {"binding": job_binding, "window": window, "resume": bool(previous), "segment": segment,
                  "research": research, "submission_hash": report["submission_hash"], "job_id": job_id,
                  "worker_url": "http://worker:8080", "callback_timeout": 20,
                  "disclosure_policy": load_disclosure(folder, binding, state, frozen),
                  "disclosure_stage": state["completed_segment"] if research else segment}
        # Credentials are only per-job local capability tokens; never upstream keys.
        write(job / "input" / "job.private.json", config)
        network, engine_name, worker_name = ["finlab-" + job_id.replace("_", "-") + s for s in ("-net", "-engine", "-worker")]
        resources = binding["runtime"]["resources"]
        seconds = resources["timeout_seconds"]
        common = ["--network", network, "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                  "--dns", "127.0.0.1", "-e", "RES_OPTIONS=attempts:1 timeout:1",
                  "--pids-limit", "128", "--cpus", str(resources["cpus"] / 2),
                  "--memory", str(int(resources["memory_mb"] / 2)) + "m", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
                  "--log-opt", "max-size=10m", "--log-opt", "max-file=1", "--log-opt", "compress=false"]
        # The job files are private to the host operator and artifacts must remain writable.
        if os.name == "posix" and os.getuid() != 0:
            common.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        try:
            docker(["network", "create", "--internal", network])
            docker(["run", "-d", "--name", worker_name, "--network-alias", "worker", *common,
                    "--mount", f"type=bind,src={job / 'submission'},dst=/submission,readonly",
                    "-e", "FINLAB_WORKER=1", "-e", "FINLAB_RPC_TOKEN=" + rpc_token,
                    "-e", "MARKET_BRIDGE_TOKEN=" + bridge_token, "-e", "MARKET_BRIDGE_URL=http://engine:8766", images["worker"]])
            docker(["run", "-d", "--name", engine_name, "--network-alias", "engine", *common,
                    "--mount", f"type=bind,src={Path(binding['frozen_dir']).resolve()},dst=/frozen,readonly",
                    "--mount", f"type=bind,src={job / 'input'},dst=/input,readonly",
                    "--mount", f"type=bind,src={job / 'artifacts'},dst=/out",
                    "-e", "FINLAB_RPC_TOKEN=" + rpc_token, "-e", "MARKET_BRIDGE_TOKEN=" + bridge_token, images["engine"]])
            result = docker(["wait", engine_name], timeout=seconds)
            for kind, name in (("engine", engine_name), ("worker", worker_name)):
                logs = docker(["logs", name], check_exit=False)
                (job / (kind + ".private.log")).write_text(logs.stdout + logs.stderr, encoding="utf-8")
            if result.stdout.strip() != "0" or not (job / "artifacts" / "success.private.json").is_file():
                raise BacktestFailed((job / "engine.private.log").read_text(encoding="utf-8"))
            success = read(job / "artifacts" / "success.private.json")
            for name, expected in success["hashes"].items():
                target = (job / "artifacts" / name).resolve()
                if not target.is_relative_to((job / "artifacts").resolve()) or file_hash(target) != expected: raise LabError("ARTIFACT_CHANGED")
            receipt = {"job_id": job_id, "segment": segment, "research": research, "submission_hash": report["submission_hash"],
                       "dataset_id": binding["dataset_id"], "images": images, "committed_at": now(), "hashes": success["hashes"]}
            write(job / "receipt.private.json", receipt)
            if not research:
                # This one atomic write is the release boundary. Crashes before
                # it leave an orphan job which cannot advance research visibility.
                state["completed_segment"] = segment
                prefix = (Path("jobs") / job_id / "artifacts").as_posix()
                state["checkpoint"] = {"file": prefix + "/checkpoint.pkl", "sha256": success["hashes"]["checkpoint.pkl"],
                                       "memory_file": prefix + "/memory.json", "memory_sha256": success["hashes"]["memory.json"],
                                       "account_file": prefix + "/account.private.json", "account_sha256": success["hashes"]["account.private.json"]}
                state["commits"].append(receipt)
                state["status"] = "complete" if segment == 7 else "maintenance_ready"
                write(folder / "state.private.json", state)
            return {"status": "research_completed" if research else "segment_committed", "job_id": job_id,
                    "segment": segment, "public_results": str(job / "artifacts" / "public"), "synthetic": binding["synthetic"]}
        except Exception as exc:
            failure = {"status": "not_committed", "error_code": exc.code if isinstance(exc, LabError) else "RUN_FAILED"}
            if isinstance(exc, BacktestFailed) and exc.strategy_code:
                failure["strategy_code"] = exc.strategy_code
            write(job / "failure.private.json", failure)
            raise
        finally:
            for kind, name in (("engine", engine_name), ("worker", worker_name)):
                if not (job / (kind + ".private.log")).exists():
                    logs = docker(["logs", name], check_exit=False)
                    (job / (kind + ".private.log")).write_text(logs.stdout + logs.stderr, encoding="utf-8")
            for name in (engine_name, worker_name):
                docker(["rm", "-f", name], check_exit=False)
            docker(["network", "rm", network], check_exit=False)
