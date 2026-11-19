"""Private append-only data store, immutable task snapshots and scoped queries."""
from __future__ import annotations
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid
from .catalog import load_catalog, endpoint
from .common import LabError, canonical, digest, file_hash, now, read, write
from .temporal import DEFAULT_YEAR_OFFSET, TimeMap, parse_time, iso
from .disclosure import (AuditLog, DisclosureScope, QueryContext, RANGE_MESSAGE, AUDIT_MESSAGE,
                         clean_params, compile_time_rules, map_query, selections, row_matches, time_ns, ns_iso,
                         value_range, opaque_nested_roots)

MARKET_SERIES = {"daily", "weekly", "monthly", "fund_daily", "index_daily", "index_weekly", "index_monthly",
                 "fut_daily", "stk_mins", "etf_mins", "idx_mins", "ft_mins"}
INSTRUMENT_FIELDS = {"ts_code", "symbol", "con_code", "index_code", "stock_code", "fund_code", "bond_code", "underlying_code"}
VISIBLE_HISTORY_APIS = {"stock_basic", "fund_basic", "index_basic", "fut_basic", "opt_basic",
                        "cb_basic", "bond_basic", "stock_company"}


def db_open(path, *, write_access=False):
    path = Path(path).resolve()
    return sqlite3.connect(path if write_access else path.as_uri() + "?mode=ro", uri=not write_access)


def init(path):
    path = Path(path)
    if path.exists():
        raise LabError("ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(db_open(path, write_access=True)) as con, con:
        con.executescript("""
        create table records(api text, row_key text, available_at text, event_at text, body text, basis text,
          primary key(api,row_key,available_at));
        create index visibility on records(api,available_at,event_at);
        create table partitions(id text primary key, api text, request text, evidence text, collected_at text);
        create table partition_records(partition_id text, api text, row_key text, available_at text,
          primary key(partition_id,api,row_key,available_at));
        create table meta(key text primary key,value text);
        insert into meta values('kind','finlab.source.v2');
        """)
    return {"status": "empty_store", "rows": 0}


def ingest(path, api, rows, rule, *, catalog=None, partition=None):
    ep = endpoint(api, catalog)
    mapping = TimeMap(rule.get("validation_year_offset", DEFAULT_YEAR_OFFSET))
    basis = rule.get("timing_basis")
    if basis not in {"operator_supplied", "publication_field", "market_close_assumption", "download_time"}:
        raise LabError("TIMING_BASIS_REQUIRED")
    key_fields = rule.get("key_fields", [])
    event_field = rule.get("event_field")
    if not key_fields or not event_field or any(x not in ep["fields"] for x in [*key_fields, event_field]):
        raise LabError("KEY_AND_EVENT_RULE_REQUIRED")
    added = 0
    with closing(db_open(path, write_access=True)) as con, con:
        con.execute("create table if not exists partition_records(partition_id text,api text,row_key text,available_at text,primary key(partition_id,api,row_key,available_at))")
        for row in rows:
            clean = {k: v for k, v in row.items() if k != "_available_at"}
            mapping.record(clean, ep["fields"])  # fail closed before private import too
            if any(clean.get(k) in {None, ""} for k in [*key_fields, event_field]):
                raise LabError("REQUIRED_DATA_FIELD_MISSING")
            event_low, _, _ = value_range(clean[event_field], ep["fields"][event_field])
            event_value = ns_iso(event_low)
            event = parse_time(event_value)
            at = row.get("_available_at") or rule.get("available_at")
            if basis == "publication_field":
                field = rule.get("availability_field")
                if not field or clean.get(field) is None:
                    raise LabError("AVAILABILITY_FIELD_MISSING")
                at = str(clean[field])
                if ep["fields"].get(field) == "date":
                    at = parse_time(at).replace(hour=23, minute=59, second=59).isoformat()
            elif basis == "market_close_assumption":
                if api not in {"daily", "fund_daily", "index_daily", "fut_daily", "stk_mins", "ft_mins"}:
                    raise LabError("CLOSE_ASSUMPTION_NOT_ALLOWED")
                hour, minute = (int(x) for x in rule.get("close_time", "16:00").split(":"))
                at = event.replace(hour=hour, minute=minute).isoformat()
            elif basis == "download_time":
                at = rule.get("acquired_at", now())
            if not at:
                raise LabError("AVAILABILITY_REQUIRED")
            at, event_at = iso(at), event_value
            mapping.shift(at, "datetime")
            for k in ep["fields"]:
                if k in {"ann_date", "f_ann_date", "publish_date"} and clean.get(k) and parse_time(at) < parse_time(clean[k]):
                    raise LabError("AVAILABLE_BEFORE_PUBLICATION")
            key, body = canonical([clean[k] for k in key_fields]), canonical(clean)
            previous = con.execute("select body,basis from records where api=? and row_key=? and available_at=?", (api, key, at)).fetchone()
            if previous and previous != (body, basis):
                raise LabError("REVISION_CONFLICT", "修订须使用新的可见时点")
            if not previous:
                con.execute("insert into records values(?,?,?,?,?,?)", (api, key, at, event_at, body, basis))
                added += 1
            if partition:
                con.execute("insert or ignore into partition_records values(?,?,?,?)", (digest(partition), api, key, at))
        if partition:
            ident = digest(partition)
            evidence = {"complete": rule.get("coverage_confirmed") is True, "timing_basis": basis, "rule": rule}
            con.execute("insert or replace into partitions values(?,?,?,?,?)", (ident, api, canonical(partition), canonical(evidence), now()))
    return {"inserted": added, "api_name": api, "timing_basis": basis, "live_tested": False}


def collect(path, plan, upstream=None):
    from .upstream import TushareUpstream
    client = upstream or TushareUpstream(plan.get("upstream_url"))
    cat = load_catalog(plan.get("catalog_path"))
    reports = []
    for query in plan["queries"]:
        name, params = query["api_name"], query.get("params", {})
        ep = endpoint(name, cat)
        # Validate classifications without shifting real upstream parameters.
        TimeMap().record(params, ep["params"])
        fields = query.get("fields") or list(ep["fields"])
        if set(fields) - set(ep["fields"]) or any(ep["fields"][k] == "unclassified" for k in fields):
            raise LabError("UNCLASSIFIED_FIELD")
        paging = query.get("pagination", {})
        rows = client.pages(name, params, ",".join(fields), page_size=paging.get("page_size", 2000),
                            max_pages=paging.get("max_pages", 100), mode=paging.get("mode", "offset"))
        report = ingest(path, name, rows, query["rule"], catalog=cat,
                        partition={"api_name": name, "params": params, "fields": fields})
        report["live_tested"] = True
        reports.append(report)
    return {"queries": reports, "scope": "operator_only"}


def freeze(source, plan, destination):
    """Freeze a declared scope. This is private; public packages contain no cache."""
    dest = Path(destination).resolve()
    if dest.exists():
        raise LabError("ALREADY_EXISTS")
    mapping = TimeMap(plan.get("year_offset", DEFAULT_YEAR_OFFSET))
    start, stop = iso(plan["real_start"]), iso(plan["real_stop"])
    if start >= stop or not plan.get("symbols") or not plan.get("apis"):
        raise LabError("INVALID_SCOPE")
    cat = load_catalog(plan.get("catalog_path"))
    dest.mkdir(parents=True)
    try:
        # Validate the exact snapshot, not a source which may change between
        # validation and backup while acquisition continues.
        with closing(db_open(source)) as src, closing(db_open(dest / "snapshot.sqlite", write_access=True)) as dst:
            src.backup(dst)
        contracts = {}
        with closing(db_open(dest / "snapshot.sqlite")) as con:
            for api, contract in plan["apis"].items():
                ep = endpoint(api, cat)
                fields = contract.get("fields", [])
                if not fields or set(fields) - set(ep["fields"]) or any(ep["fields"][k] == "unclassified" for k in fields):
                    raise LabError("FIELD_CONTRACT_REQUIRED")
                if contract.get("coverage_confirmed") is not True:
                    raise LabError("COVERAGE_NOT_CONFIRMED", api)
                if contract.get("replayability") != "point_in_time":
                    raise LabError("HISTORICAL_VISIBILITY_NOT_PROVEN", api)
                if contract.get("event_scope", "task_range") not in {"task_range", "visible_history"}:
                    raise LabError("INVALID_EVENT_SCOPE")
                if contract.get("event_scope") == "visible_history" and api in MARKET_SERIES:
                    raise LabError("MARKET_SERIES_REQUIRES_TASK_RANGE")
                if contract.get("event_scope") == "visible_history" and api not in VISIBLE_HISTORY_APIS:
                    raise LabError("VISIBLE_HISTORY_REQUIRES_REVIEWED_METADATA", api)
                if not con.execute("select 1 from records where api=? limit 1", (api,)).fetchone():
                    raise LabError("DATA_NOT_FROZEN", api)
                if con.execute("select 1 from records where api=? and basis='download_time' limit 1", (api,)).fetchone():
                    raise LabError("DOWNLOAD_TIME_NOT_HISTORICAL", api)
                contracts[api] = {**ep, "fields": {k: ep["fields"][k] for k in fields}, "binding": contract,
                                  "all_field_types": ep["fields"],
                                  "time_query_rules": compile_time_rules(ep, contract.get("query_time_rules"))}
                for body, at, event_at in con.execute("select body,available_at,event_at from records where api=?", (api,)):
                    row = json.loads(body)
                    mapping.record({k: v for k, v in row.items() if k in fields}, contracts[api]["fields"])
                    mapping.shift(at, "datetime"); mapping.shift(event_at, "datetime")
        mapping.shift(start, "datetime"); mapping.shift(stop, "datetime")
        manifest = {"schema_version": "finlab.freeze.v2", "dataset_id": "ds_" + uuid.uuid4().hex,
                    "created_at": now(), "real_start": start, "real_stop": stop,
                    "year_offset": mapping.years, "symbols": plan["symbols"], "apis": contracts,
                    "snapshot_sha256": file_hash(dest / "snapshot.sqlite"), "synthetic": plan.get("synthetic", False)}
        manifest["manifest_hash"] = digest(manifest)
        write(dest / "manifest.private.json", manifest)
    except Exception:
        # Preserve an incomplete private directory for diagnosis; never mark it frozen.
        write(dest / "FAILED.json", {"status": "freeze_failed"})
        raise
    return {"dataset_id": manifest["dataset_id"], "status": "frozen_private", "synthetic": manifest["synthetic"]}


class FrozenView:
    def __init__(self, folder, *, clock=None, allowed_symbols=None, verify=True, scope=None, audit=None):
        self.folder = Path(folder)
        self.manifest = read(self.folder / "manifest.private.json")
        m = dict(self.manifest); saved = m.pop("manifest_hash")
        if digest(m) != saved or verify and file_hash(self.folder / "snapshot.sqlite") != m["snapshot_sha256"]:
            raise LabError("FROZEN_VERSION_CHANGED")
        self.mapping = TimeMap(m["year_offset"])
        self._context_lock = threading.RLock()
        self._clock = iso(clock or m["real_start"])
        self.scope, self.audit = scope, audit
        if scope is not None:
            scope.validate(self.manifest)
        if allowed_symbols is not None and not set(allowed_symbols) <= set(m["symbols"]):
            raise LabError("SYMBOL_NOT_ALLOWED")
        self.symbols = m["symbols"] if allowed_symbols is None else allowed_symbols

    @property
    def clock(self):
        with self._context_lock:
            return self._clock

    @clock.setter
    def clock(self, value):
        with self._context_lock:
            self._clock = iso(value)

    def assert_ready(self):
        if not isinstance(self.scope, DisclosureScope):
            raise LabError("DISCLOSURE_SCOPE_REQUIRED")
        self.scope.validate(self.manifest)
        if not isinstance(self.audit, AuditLog):
            raise LabError("BRIDGE_AUDIT_REQUIRED")
        if any("time_query_rules" not in ep for ep in self.manifest["apis"].values()):
            raise LabError("TIME_SELECTION_RULES_REQUIRED", "冻结数据需要重新绑定时间选择规则")

    def context(self):
        self.assert_ready()
        with self._context_lock:
            return QueryContext(self.scope, self._clock)

    def register_secret(self, secret):
        self.audit.register_secret(secret)

    def audit_rejection(self, api, params, code, *, request_id=None, _context=None):
        context = _context or self.context()
        request_id = request_id or uuid.uuid4().hex
        ep = self.manifest["apis"].get(api, {}) if isinstance(api, str) else {}
        record = {"request_id": request_id, "episode_id": context.scope.episode_id,
                  "stage": context.scope.stage, "job_id": context.scope.job_id,
                  "policy_hash": context.scope.policy_hash, "submission_hash": context.scope.submission_hash,
                  "api_name": api[:100] if isinstance(api, str) else None,
                  "params": clean_params(params or {}, ep.get("params", {})), "requested_ranges": [],
                  "effective_range": {"start": context.scope.start, "stop": context.scope.stop},
                  "event_clock": context.clock, "outcome": "rejected", "error_code": code}
        try:
            self.audit.append(record)
        except Exception:
            raise LabError("BRIDGE_AUDIT_UNAVAILABLE", AUDIT_MESSAGE, request_id=request_id) from None
        return request_id

    def query(self, api, params=None, fields="", *, _context=None):
        context = _context or self.context()
        self.assert_ready()
        if not isinstance(context, QueryContext):
            raise LabError("DISCLOSURE_SCOPE_REQUIRED")
        context.scope.validate(self.manifest)
        request_id = uuid.uuid4().hex
        ep = self.manifest["apis"].get(api, {}) if isinstance(api, str) else {}
        evidence = []
        record = {"request_id": request_id, "episode_id": context.scope.episode_id,
                  "stage": context.scope.stage, "job_id": context.scope.job_id,
                  "policy_hash": context.scope.policy_hash, "submission_hash": context.scope.submission_hash,
                  "api_name": api[:100] if isinstance(api, str) else None,
                  "params": clean_params(params or {}, ep.get("params", {})),
                  "requested_ranges": evidence,
                  "effective_range": {"start": context.scope.start, "stop": context.scope.stop},
                  "event_clock": context.clock, "outcome": "rejected", "error_code": None}
        error, result = None, None
        try:
            result = self._query(api, params, fields, context, evidence)
            record.update(outcome="allowed", returned_rows=len(result["data"]["items"]))
        except LabError as exc:
            error = exc
            record["error_code"] = exc.code
        except Exception:
            error = LabError("QUERY_FAILED", "查询失败")
            record["error_code"] = error.code
        try:
            self.audit.append(record)
        except Exception:
            raise LabError("BRIDGE_AUDIT_UNAVAILABLE", AUDIT_MESSAGE, request_id=request_id) from None
        if error is not None:
            raise LabError(error.code, error.message, request_id=request_id) from None
        result["meta"]["request_id"] = request_id
        return result

    def _query(self, api, params, fields, context, evidence):
        if not isinstance(api, str):
            raise LabError("INVALID_QUERY")
        if api not in self.manifest["apis"]:
            raise LabError("API_NOT_ALLOWED_OR_NOT_FROZEN")
        ep = self.manifest["apis"][api]
        params = {} if params is None else params
        if not isinstance(params, dict) or not isinstance(fields, str):
            raise LabError("INVALID_QUERY")
        args = map_query(params, ep["params"], self.mapping)
        selected_times = selections(args, ep, context, evidence)
        chosen = [f.strip() for f in fields.split(",")] if fields else list(ep["fields"])
        if not chosen or set(chosen) - set(ep["fields"]) or len(set(chosen)) != len(chosen):
            raise LabError("FIELD_NOT_ALLOWED")
        for field in INSTRUMENT_FIELDS:
            if field in args and any(s not in self.symbols for s in str(args[field]).split(",")):
                raise LabError("SYMBOL_NOT_ALLOWED")
        limit, offset = args.get("limit", 2000), args.get("offset", 0)
        if (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000 or
                isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
            raise LabError("INVALID_PAGINATION")
        selected = []
        # Visibility is applied before choosing a revision, never after it.
        sql = """select body,event_at,row_key,available_at from (select body,event_at,row_key,available_at,row_number() over
                 (partition by row_key order by available_at desc) rn
                 from records where api=? and available_at<=?) where rn=1 order by event_at desc"""
        with closing(db_open(self.folder / "snapshot.sqlite")) as con:
            visible_clock = min(context.clock, ns_iso(time_ns(context.scope.stop) - 1), key=time_ns)
            event_scope = ep["binding"].get("event_scope", "task_range")
            time_keys = {v["path"].split(".")[0].replace("[]", "") for v in selected_times}
            nested_opaque = opaque_nested_roots(args, ep["params"])
            opaque_params = {k:v for k,v in args.items() if (k in nested_opaque or k not in ep["fields"] and k not in time_keys) and k not in {"limit","offset"}}
            if opaque_params:
                # Future partition existence must not change today's errors.
                # Only provenance of currently visible, task-scoped records can
                # establish replayability for otherwise opaque parameters.
                partitions = []
                for request, at, event_at, body in con.execute("""select p.request,r.available_at,r.event_at,r.body
                    from partitions p join partition_records pr on p.id=pr.partition_id
                    join records r on r.api=pr.api and r.row_key=pr.row_key and r.available_at=pr.available_at
                    where p.api=? and r.available_at<=?""", (api, visible_clock)):
                    if not context.record_visible(event_at, at, event_scope):
                        continue
                    source_row = json.loads(body)
                    keys = INSTRUMENT_FIELDS - ({"symbol"} if "ts_code" in source_row else set())
                    if any(source_row.get(k) not in (None, "") and source_row[k] not in self.symbols for k in keys):
                        continue
                    partitions.append(json.loads(request).get("params", {}))
                if any(not any(k in p for p in partitions) for k in opaque_params): raise LabError("PARAMETER_NOT_REPLAYABLE")
                if not any(all(canonical(p.get(k)) == canonical(v) if isinstance(v, (dict, list)) else str(p.get(k)) == str(v)
                               for k,v in opaque_params.items()) for p in partitions):
                    raise LabError("PARAMETER_NOT_FROZEN")
            for body, event_at, row_key, available_at in con.execute(sql, (api, visible_clock)):
                row = json.loads(body)
                if not context.record_visible(event_at, available_at, event_scope):
                    continue
                # A bare stock_basic.symbol may be the short form of ts_code.
                # Relationship endpoints (e.g. index_weight) must scope both ends.
                keys = INSTRUMENT_FIELDS - ({"symbol"} if "ts_code" in row else set())
                identities = [row[k] for k in keys if k in row and row[k] not in (None, "")]
                if identities and not all(s in self.symbols for s in identities):
                    continue
                # The source database may cover many tasks. An omitted range
                # must not widen a market-series query beyond this task.
                if not row_matches(row, event_at, ep, selected_times):
                    continue
                valid = True
                opaque = {}
                for k, value in args.items():
                    if k in nested_opaque:
                        opaque[k] = value
                        continue
                    if k in time_keys or k in {"limit", "offset"}:
                        continue
                    if k not in row:
                        opaque[k] = value
                        continue
                    if str(row[k]) not in str(value).split(","):
                        valid = False
                        break
                if valid and opaque:
                    try:
                        requests = [json.loads(x[0])["params"] for x in con.execute("""select p.request from partitions p join partition_records r
                            on p.id=r.partition_id where r.api=? and r.row_key=? and r.available_at=?""", (api, row_key, available_at))]
                    except sqlite3.OperationalError:
                        requests = []  # old snapshots cannot establish partition provenance
                    if not requests or any(not any(k in p for p in requests) for k in opaque):
                        raise LabError("PARAMETER_NOT_REPLAYABLE", ",".join(sorted(opaque)))
                    valid = any(all(canonical(p.get(k)) == canonical(v) if isinstance(v, (dict, list)) else str(p.get(k)) == str(v)
                                    for k, v in opaque.items()) for p in requests)
                if valid:
                    safe = self.mapping.record({k: row.get(k) for k in chosen}, ep["fields"])
                    selected.append([safe[k] for k in chosen])
        page = selected[offset:offset + limit]
        return {"code": 0, "msg": "", "data": {"fields": chosen, "items": page},
                "meta": {"dataset_id": self.manifest["dataset_id"], "visible_until": self.mapping.shift(visible_clock, "datetime"),
                         "stage": context.scope.stage, "disclosure_policy": context.scope.policy_hash,
                         "disclosure_range": {"start": self.mapping.shift(context.scope.start, "datetime"),
                                              "stop": self.mapping.shift(context.scope.stop, "datetime"), "interval": "[start,stop)"},
                         "has_more": offset + len(page) < len(selected), "identity_policy": "real_names_and_codes",
                         "next_offset": offset + len(page) if offset + len(page) < len(selected) else None}}
