"""Operator-only handoff from an exact task specification to data/runtime plans.

This module does not approve tasks, obtain data, fill resource choices or create
episodes. It resolves mechanical work and lists evidence which is still absent.
Every output is private: it contains real dates and may contain execution rules.
"""
from __future__ import annotations

from contextlib import closing
import copy
from datetime import timedelta
import json
import math
from pathlib import Path

from .catalog import endpoint
from .common import LabError, digest, write
from .data import FrozenView, db_open
from .temporal import DEFAULT_YEAR_OFFSET, iso, parse_time


SCHEMA = "finlab.preparation.v1"
_SECRET_KEYS = {"token", "api_token", "api_key", "password", "secret", "tushare_token", "authorization"}
_FEED_APIS = {"daily": "STOCK", "fund_daily": "FUND", "fut_daily": "FUTURES"}


def _reject_credentials(value):
    if isinstance(value, dict):
        if any(str(k).lower() in _SECRET_KEYS for k in value):
            raise LabError("PREPARATION_CREDENTIAL_FORBIDDEN", "凭证只通过环境变量配置，不进入准备文件")
        for child in value.values():
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


def _day(value):
    return parse_time(value).date().isoformat()


def _finite(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def _rows(view, api, *, visible_on=None):
    """Private source rows; callers must restrict scope and visible revisions."""
    if api not in view.manifest["apis"]:
        return []
    fields = set(view.manifest["apis"][api]["fields"])
    latest = {}
    with closing(db_open(view.folder / "snapshot.sqlite")) as db:
        for key, at, event, body in db.execute(
                "select row_key,available_at,event_at,body from records where api=?", (api,)):
            # Runtime rules are frozen for trusted execution. Never use a
            # revision acquired after the task's stop as a historical rule.
            if iso(at) >= view.manifest["real_stop"]:
                continue
            row = {k: v for k, v in json.loads(body).items() if k in fields}
            if visible_on and row.get(visible_on) and iso(at) >= iso(parse_time(row[visible_on]) + timedelta(days=1)):
                continue
            row["_available_at"] = at
            if key not in latest or at > latest[key][0]:
                latest[key] = (at, row)
    return [item[1] for item in latest.values()]


def _contract(spec, gap):
    result = {}
    for item in spec.get("bridge_contract", {}).get("endpoints", []):
        name, fields = item.get("api_name"), item.get("fields")
        if not isinstance(name, str) or not isinstance(fields, list) or not fields:
            gap("INVALID_ENDPOINT_CONTRACT", "bridge_contract.endpoints", "接口与字段列表不完整", "spec")
            continue
        if name in result:
            gap("DUPLICATE_ENDPOINT", "bridge_contract.endpoints", "同一接口不能出现多个冲突契约", "spec")
            continue
        try:
            ep = endpoint(name)
        except LabError:
            gap("UNKNOWN_ENDPOINT", "bridge_contract.endpoints." + name, "当前目录没有该接口", "spec")
            continue
        if len(fields) != len(set(fields)) or any(k not in ep["fields"] for k in fields):
            gap("INVALID_ENDPOINT_FIELDS", "bridge_contract.endpoints." + name, "字段重复或未登记", "spec")
            continue
        if any(ep["fields"][k] == "unclassified" for k in fields):
            gap("UNCLASSIFIED_FIELD", "bridge_contract.endpoints." + name, "存在未完成类型登记的字段", "spec")
        result[name] = {"fields": list(fields), "endpoint": ep}
    if not result:
        gap("ENDPOINTS_REQUIRED", "bridge_contract.endpoints", "任务必须声明实际接口", "spec")
    return result


def _collection(contracts, selection, symbols, start, stop, gap):
    policies = selection.get("api_policies", {})
    queries, frozen_apis = [], {}
    benchmark = selection.get("runtime", {}).get("benchmark")
    for name, declared in contracts.items():
        ep, fields = declared["endpoint"], declared["fields"]
        policy = copy.deepcopy(policies.get(name, {}))
        rule = policy.get("rule", {})
        # Keys/event selection may be inferred from the declared schema. Timing
        # and historical completeness can only come from operator evidence.
        event = next((k for k in ("trade_date", "cal_date", "ann_date", "end_date") if k in fields), None)
        keys = [k for k in ("ts_code", "exchange", event) if k and k in fields]
        if name == "dividend":
            # Corporate-action uniqueness cannot be inferred from announce day:
            # one company can publish multiple fiscal plans on the same day.
            keys = []
        if not rule.get("key_fields") and keys:
            rule["key_fields"] = list(dict.fromkeys(keys))
        if not rule.get("event_field") and event:
            rule["event_field"] = event
        if not rule.get("key_fields") or not rule.get("event_field"):
            gap("INGEST_RULE_REQUIRED", "api_policies." + name + ".rule", "须登记唯一键和事件字段", "collection")
        if rule.get("timing_basis") not in {"operator_supplied", "publication_field", "market_close_assumption"}:
            gap("VISIBILITY_EVIDENCE_REQUIRED", "api_policies." + name + ".rule", "须提供历史可见时点依据；下载时刻不能证明过去可见", "collection")
        elif rule.get("timing_basis") == "operator_supplied" and not rule.get("available_at"):
            gap("ROW_AVAILABILITY_REQUIRED", "api_policies." + name + ".rule.available_at", "原始API响应不带_available_at；须另行导入逐行时点，不能伪造常量", "collection")
        if policy.get("coverage_confirmed") is not True:
            gap("COVERAGE_REVIEW_REQUIRED", "api_policies." + name + ".coverage_confirmed", "分页、缺口与范围完整性尚未确认", "collection")
        if policy.get("replayability") != "point_in_time":
            gap("POINT_IN_TIME_REQUIRED", "api_policies." + name + ".replayability", "尚未证明可用于历史回放", "collection")
        if not policy.get("evidence"):
            gap("DATA_POLICY_EVIDENCE_REQUIRED", "api_policies." + name + ".evidence", "记录覆盖和可见性核验依据", "collection")
        frozen_apis[name] = {"fields": fields, "coverage_confirmed": policy.get("coverage_confirmed") is True,
                            "replayability": policy.get("replayability", "not_proven")}
        for optional in ("event_scope", "query_time_rules"):
            if optional in policy:
                frozen_apis[name][optional] = policy[optional]
        partitions = policy.get("partitions")
        if partitions is None:
            params = ep["params"]
            date_params = {}
            if start and stop and {"start_date", "end_date"} <= params.keys():
                date_params = {"start_date": parse_time(start).strftime("%Y%m%d"),
                               "end_date": (parse_time(stop) - timedelta(days=1)).strftime("%Y%m%d")}
            chosen = symbols
            if name == "index_daily":
                chosen = [benchmark["symbol"]] if isinstance(benchmark, dict) and benchmark.get("symbol") else []
                if not chosen:
                    gap("BENCHMARK_SELECTION_REQUIRED", "runtime.benchmark", "index_daily需要显式选择基准代码", "collection")
            elif name in _FEED_APIS:
                chosen = [x["symbol"] for x in selection.get("runtime", {}).get("instruments", [])
                          if x.get("asset_type") == _FEED_APIS[name]]
            if "ts_code" in params and chosen:
                partitions = [{**date_params, "ts_code": s} for s in chosen]
            elif name == "trade_cal" and policy.get("exchanges"):
                partitions = [{**date_params, "exchange": e} for e in policy["exchanges"]]
            else:
                partitions = None
            if partitions and not date_params:
                # E.g. dividend has exact-date selectors, no range parameters.
                # A per-symbol unbounded query may be selected explicitly, but
                # must not be invented and declared complete by this compiler.
                gap("QUERY_PARTITIONS_REQUIRED", "api_policies." + name + ".partitions", "接口不支持自动范围分区；请显式给出查询分区", "collection")
                partitions = None
        if not isinstance(partitions, list) or not partitions or any(not isinstance(p, dict) for p in partitions):
            gap("QUERY_PARTITIONS_REQUIRED", "api_policies." + name + ".partitions", "须提供有限且可核验的接口查询分区", "collection")
            partitions = []
        pagination = policy.get("pagination", {"mode": "offset", "page_size": 2000, "max_pages": 100})
        for params in partitions:
            queries.append({"api_name": name, "params": params, "fields": fields,
                            "pagination": copy.deepcopy(pagination), "rule": copy.deepcopy(rule)})
    return {"upstream_url": None, "queries": queries}, frozen_apis


def _derive_runtime(view, selection, runtime, gap):
    derive = selection.get("derive_from_frozen", {})
    start, stop = view.manifest["real_start"], view.manifest["real_stop"]
    symbols = [x["symbol"] for x in runtime.get("instruments") or []]
    if not runtime.get("calendar") and derive.get("calendar_exchange"):
        exchange = derive["calendar_exchange"]
        calendar = sorted({_day(r["cal_date"]) for r in _rows(view, "trade_cal", visible_on="cal_date")
                           if r.get("exchange") == exchange and str(r.get("is_open")) == "1"
                           and start <= iso(r["cal_date"]) < stop})
        if calendar:
            runtime["calendar"] = calendar
        else:
            gap("FROZEN_CALENDAR_MISSING", "derive_from_frozen.calendar_exchange", "冻结trade_cal没有所选交易所日历", "binding")
    if not runtime.get("execution_rules") and derive.get("execution_rules") is True and runtime.get("calendar"):
        limits = {(r.get("ts_code"), _day(r["trade_date"])): r for r in _rows(view, "stk_limit", visible_on="trade_date")
                  if r.get("trade_date") and iso(r["_available_at"]) < iso(parse_time(r["trade_date"]) + timedelta(days=1))}
        observed = set()
        for name in _FEED_APIS:
            observed |= {(r.get("ts_code"), _day(r["trade_date"])) for r in _rows(view, name, visible_on="trade_date")
                         if r.get("trade_date")}
        nontradable = {(x.get("symbol"), x.get("trade_date")): x
                       for x in derive.get("nontradable_days", []) if x.get("evidence")}
        rules = []
        for instrument in runtime.get("instruments") or []:
            symbol = instrument["symbol"]
            for day in runtime["calendar"]:
                if (symbol, day) in nontradable:
                    rules.append({"symbol": symbol, "trade_date": day, "tradable": False})
                    continue
                if (symbol, day) not in observed:
                    gap("PRICE_GAP_NOT_SUSPENSION", "execution_rules." + symbol + "." + day,
                        "没有行情，不能推断停牌；补行情或提供有依据的不可交易日", "binding")
                    continue
                rule = {"symbol": symbol, "trade_date": day, "tradable": True}
                limit = limits.get((symbol, day), {})
                if not instrument.get("no_daily_price_limit"):
                    if not all(_finite(limit.get(k), positive=True) for k in ("up_limit", "down_limit")):
                        gap("DAILY_PRICE_LIMIT_MISSING", "execution_rules." + symbol + "." + day,
                            "缺少冻结的真实涨跌停价格，不能按统一百分比生成", "binding")
                        continue
                    rule.update(up_limit=limit["up_limit"], down_limit=limit["down_limit"])
                rules.append(rule)
        runtime["execution_rules"] = rules
    policy = derive.get("corporate_actions")
    if runtime.get("corporate_actions") is None and isinstance(policy, dict):
        if (policy.get("accounting") != "ex_date_cash_credit" or policy.get("cash_field") not in {"cash_div", "cash_div_tax"}
                or policy.get("confirmed") is not True or not policy.get("evidence")):
            gap("CORPORATE_ACTION_ACCOUNTING_REQUIRED", "derive_from_frozen.corporate_actions",
                "须显式确认税前/税后字段与除息日现金入账实验口径；不能假装模拟登记日权益和派息日现金", "binding")
        elif "dividend" not in view.manifest["apis"]:
            gap("CORPORATE_ACTION_SOURCE_MISSING", "derive_from_frozen.corporate_actions", "冻结版本未包含dividend", "binding")
        else:
            actions, keys = [], set()
            needed = {"ts_code", "ex_date", "div_proc", "stk_div", "stk_bo_rate", "stk_co_rate", policy["cash_field"]}
            if not needed <= set(view.manifest["apis"]["dividend"]["fields"]):
                gap("CORPORATE_ACTION_FIELDS_MISSING", "bridge_contract.dividend", "识别现金分红和非现金行为所需字段未冻结", "binding")
                return
            for row in _rows(view, "dividend"):
                if row.get("ts_code") not in symbols or row.get("div_proc") != "实施" or not row.get("ex_date"):
                    continue
                if not start <= iso(row["ex_date"]) < stop:
                    continue
                if iso(row["_available_at"]) >= iso(parse_time(row["ex_date"]) + timedelta(days=1)):
                    gap("DIVIDEND_AVAILABLE_AFTER_EVENT", "corporate_actions." + row["ts_code"],
                        "实施信息在除息日之后才可见，不能作为当日已知公司行为", "binding")
                    continue
                if any(row.get(k) not in (None, "", 0, 0.0) for k in ("stk_div", "stk_bo_rate", "stk_co_rate")):
                    gap("STOCK_DIVIDEND_REQUIRES_ADAPTER", "corporate_actions." + row["ts_code"],
                        "含送股/转增，不能丢弃或按现金分红处理", "binding")
                    continue
                value = row.get(policy["cash_field"])
                if value in (None, "", 0, 0.0):
                    continue
                if not _finite(value, positive=True):
                    gap("INVALID_DIVIDEND_VALUE", "corporate_actions." + row["ts_code"], "每股现金分红无效", "binding")
                    continue
                key = (row["ts_code"], _day(row["ex_date"]))
                if key in keys:
                    gap("CONFLICTING_DIVIDEND", "corporate_actions." + row["ts_code"], "相同除息日多个实施记录，须人工核验，不能重复计息", "binding")
                    continue
                keys.add(key)
                actions.append({"symbol": key[0], "date": key[1], "type": "dividend", "value": value})
            runtime["corporate_actions"] = sorted(actions, key=lambda x: (x["date"], x["symbol"]))
            runtime["corporate_actions_verified"] = not any(x["code"] in {
                "STOCK_DIVIDEND_REQUIRES_ADAPTER", "INVALID_DIVIDEND_VALUE", "CONFLICTING_DIVIDEND",
                "DIVIDEND_AVAILABLE_AFTER_EVENT"} for x in gap.items)


def prepare(spec, selection, *, approval=None, frozen_dir=None, operator_resources=None):
    """Return reviewable private plans and blockers without network/mutations.

    ``approval`` is a recorded machine or historical human acceptance fetched
    by the caller from Registry, never a caller's claim. Its hash is checked here;
    episode creation checks the actual
    registry again. ``operator_resources`` must come from load_settings(), not a
    model-selected budget. Pure unit fixtures may supply explicit test resources.
    """
    if not isinstance(spec, dict) or spec.get("schema_version") != "finance_task_spec.v2":
        raise LabError("APPROVED_V2_SPEC_REQUIRED")
    if not isinstance(selection, dict):
        raise LabError("PREPARATION_SELECTION_REQUIRED")
    _reject_credentials(selection)
    selection, spec = copy.deepcopy(selection), copy.deepcopy(spec)
    selection_hash = digest(selection)
    gaps = []
    def gap(code, path, message, phase="binding"):
        record = {"code": code, "path": path, "message": message, "phase": phase}
        if record not in gaps:
            gaps.append(record)
    gap.items = gaps
    contracts = _contract(spec, gap)
    spec_hash = digest(spec)
    if not approval or approval.get("spec_hash") != spec_hash or not approval.get("candidate_id"):
        gap("TASK_ACCEPTANCE_REQUIRED", "approval", "须取得该精确规格通过程序检查和模型复核的登记版本；先运行task accept，不需要人工审批", "acceptance")
    start, stop = None, None
    try:
        # Complete-day split is the current runtime contract. Do not hide a
        # sub-day range change while converting SDK inclusive end dates.
        start, stop = iso(selection["real_start"]), iso(selection["real_stop"])
        if start >= stop or any(parse_time(x).time().isoformat() != "00:00:00" for x in (start, stop)):
            raise LabError("INVALID_PREPARATION_RANGE")
    except (LabError, KeyError, TypeError):
        gap("EXPLICIT_RANGE_REQUIRED", "real_start/real_stop", "显式填写真实完整日[start,stop)；不能代选时间范围", "selection")
        start, stop = None, None
    if start and stop:
        required_years = spec.get("bridge_contract", {}).get("minimum_years", spec.get("data_requirements", {}).get("minimum_years", 0))
        if not _finite(required_years):
            gap("INVALID_MINIMUM_YEARS", "bridge_contract.minimum_years", "已批准规格的数据年限不是有效非负数", "spec")
        elif (parse_time(stop) - parse_time(start)).days < float(required_years) * 365:
            gap("MINIMUM_RANGE_NOT_MET", "real_start/real_stop", "选择范围短于已批准规格的最低年限；修改规格须重新审核", "selection")
    runtime = copy.deepcopy(spec.get("runtime_profile", {}))
    override = selection.get("runtime", {})
    if not isinstance(override, dict):
        raise LabError("INVALID_RUNTIME_SELECTION")
    for key in ("engine", "engine_version", "strategy_api", "frequency", "fill_policy", "formal_segments"):
        if key in override and override[key] != runtime.get(key):
            gap("RUNTIME_DIFFERS_FROM_APPROVED_SPEC", "runtime." + key, "选择与已批准执行协议不一致", "selection")
    runtime.update(override)
    if override.get("resources") is not None:
        gap("RESOURCE_SELECTION_FORBIDDEN", "runtime.resources", "CPU/内存/时限由本人setup选择，不通过准备文件代填", "selection")
    runtime["resources"] = copy.deepcopy(operator_resources)
    runtime["resource_status"] = "human_selected" if operator_resources else "requires_human_setup"
    if not operator_resources:
        gap("SETUP_REQUIRED", "runtime.resources", "使用者本人执行finlab setup；准备草稿不会选择预算", "setup")
    instruments = runtime.get("instruments") or []
    if not isinstance(instruments, list) or any(not isinstance(x, dict) or not x.get("symbol") for x in instruments):
        gap("EXPLICIT_SYMBOLS_REQUIRED", "runtime.instruments", "显式选择交易标的及交易规则", "selection")
        instruments = []
    runtime["instruments"] = instruments
    selection.setdefault("runtime", {})["instruments"] = instruments
    symbols = [i["symbol"] for i in instruments]
    if not symbols:
        gap("EXPLICIT_SYMBOLS_REQUIRED", "runtime.instruments", "显式选择交易标的及交易规则", "selection")
    if len(set(symbols)) != len(symbols):
        gap("DUPLICATE_SYMBOL", "runtime.instruments", "交易代码重复", "selection")
    from .symbol_scope import validate_symbol_selection
    try:
        validate_symbol_selection(spec, symbols)
    except LabError as error:
        gap(error.code, "runtime.instruments", error.message, "selection")
    benchmark = runtime.get("benchmark")
    frozen_symbols = list(symbols)
    if isinstance(benchmark, dict) and benchmark.get("symbol"):
        frozen_symbols.append(benchmark["symbol"])
    collection, apis = _collection(contracts, selection, symbols, start, stop, gap)
    freeze_plan = {"real_start": start, "real_stop": stop, "symbols": sorted(set(frozen_symbols)), "apis": apis,
                   "synthetic": False, "year_offset": selection.get("year_offset", DEFAULT_YEAR_OFFSET)}
    binding = {"schema_version": "finlab.binding.v2", "frozen_dir": str(frozen_dir) if frozen_dir else None,
               "candidate_id": approval.get("candidate_id") if approval else None, "approved_spec_hash": spec_hash,
               "contract_review_confirmed": selection.get("contract_review_confirmed") is True,
               "research_fraction": selection.get("research_fraction", spec.get("protocol", {}).get("research_fraction", .3)),
               "feeds": copy.deepcopy(selection.get("feeds", [])), "runtime": runtime}
    if "stage_windows" in selection:
        binding["stage_windows"] = copy.deepcopy(selection["stage_windows"])
    reuse = {"status": "not_bound", "dataset_id": None, "manifest_hash": None}
    if frozen_dir:
        try:
            view = FrozenView(frozen_dir)
            manifest = view.manifest
            reuse.update(dataset_id=manifest["dataset_id"], manifest_hash=manifest["manifest_hash"])
            if manifest.get("synthetic"):
                gap("SYNTHETIC_DATA_NOT_REAL_DELIVERY", "frozen_dir", "合成夹具不能作为真实任务交付", "frozen")
            if start != manifest["real_start"] or stop != manifest["real_stop"]:
                gap("FROZEN_RANGE_MISMATCH", "frozen_dir", "冻结范围须与选择一致；需重新裁剪冻结，不能静默扩大运行范围", "frozen")
            if set(frozen_symbols) - set(manifest["symbols"]):
                gap("FROZEN_SYMBOLS_MISSING", "frozen_dir", "冻结版本缺少所选交易标的或基准", "frozen")
            actual = manifest["apis"]
            if set(actual) != set(contracts) or any(set(actual[n]["fields"]) != set(c["fields"])
                                                   for n, c in contracts.items() if n in actual):
                gap("FROZEN_CONTRACT_DIFFERS_FROM_APPROVED_SPEC", "frozen_dir", "接口及字段必须与批准规格一致；不能偷偷增加/丢弃接口", "frozen")
            for name, ep in actual.items():
                if (ep.get("binding", {}).get("coverage_confirmed") is not True
                        or ep.get("binding", {}).get("replayability") != "point_in_time"):
                    gap("FROZEN_EVIDENCE_MISSING", "frozen_dir." + name, "冻结覆盖或历史可见性依据缺失", "frozen")
                if "time_query_rules" not in ep:
                    gap("TIME_SELECTION_RULES_REQUIRED", "frozen_dir." + name, "冻结版本缺少统一时间参数门禁", "frozen")
            _derive_runtime(view, selection, runtime, gap)
            calendar = runtime.get("calendar") or []
            if len(calendar) < 16:
                gap("INSUFFICIENT_SEGMENT_DATES", "runtime.calendar", "研发及七段各至少两个交易日", "binding")
            if operator_resources:
                from .runtime import validate_binding
                try:
                    validate_binding(binding, view)
                except LabError as exc:
                    gap(exc.code, "binding", exc.message, "binding")
            reuse["status"] = "validated" if not any(x["phase"] == "frozen" for x in gaps) else "incompatible"
        except (LabError, OSError, KeyError, TypeError, ValueError) as exc:
            gap(getattr(exc, "code", "FROZEN_LOAD_FAILED"), "frozen_dir", "冻结版本无法通过完整性核验，保留原文件由运营方检查", "frozen")
            reuse["status"] = "invalid"
    else:
        gap("FROZEN_DATA_REQUIRED", "frozen_dir", "采集计划不是冻结数据；先完成数据核验冻结或选择已有版本", "frozen")
    if binding["contract_review_confirmed"] is not True:
        gap("BOUND_CONTRACT_REVIEW_REQUIRED", "contract_review_confirmed", "运行绑定的数据口径与规则依据尚未确认；移除题目人工审批不代表数据缺口已解决", "binding")
    # A proven existing immutable freeze does not require re-collecting data or
    # reproducing an obsolete collection configuration.
    for issue in gaps:
        issue["blocking"] = issue["phase"] != "collection" or reuse["status"] != "validated"
    blockers = [x for x in gaps if x["blocking"]]
    return {"schema_version": SCHEMA, "scope": "platform_private", "status": "ready" if not blockers else "blocked",
            "passed": not blockers, "spec_hash": spec_hash, "selection_hash": selection_hash, "gaps": gaps,
            "collection_ready": not any(x["phase"] in {"spec", "selection", "collection"} for x in gaps),
            "collection_plan": collection, "freeze_plan": freeze_plan, "binding_draft": binding,
            "dataset_reuse": reuse,
            "required_next_steps": sorted({x["phase"] for x in blockers}),
            "notes": ["准备通过仅表示可交给运行交付验收；episode仍核对真实接纳记录、本人资源和冻结哈希。",
                      "全部计划为平台私有，包含真实日期；不得混入参与者public目录。",
                      "上游地址与令牌使用TUSHARE_UPSTREAM_URL/TUSHARE_TOKEN环境变量；不切换其他数据源。"]}


def write_preparation(folder, report):
    """Write a new private, reviewable directory; never overwrite earlier work."""
    folder = Path(folder).resolve()
    if folder.exists():
        raise LabError("ALREADY_EXISTS")
    if report.get("schema_version") != SCHEMA or report.get("scope") != "platform_private":
        raise LabError("INVALID_PREPARATION_REPORT")
    _reject_credentials(report)
    folder.mkdir(parents=True)
    for name, key in (("collection.private.json", "collection_plan"), ("freeze.private.json", "freeze_plan"),
                      ("binding.private.json", "binding_draft")):
        write(folder / name, report[key])
    write(folder / "preparation.private.json", report)
    return {"status": report["status"], "passed": report["passed"], "folder": str(folder),
            "blocking_gaps": sum(x["blocking"] for x in report["gaps"])}
