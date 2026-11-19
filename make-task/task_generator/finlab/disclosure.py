"""Controller-owned disclosure capabilities, typed selections and private audit."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re
import sqlite3
import threading

from .common import LabError, canonical, digest, now
from .temporal import DATE_TOKEN, SH, iso, parse_time

TIME_KINDS = {"date", "datetime", "year", "month", "quarter", "period", "temporal",
              "epoch_s", "epoch_ms", "epoch_us", "epoch_ns"}
RANGE_MESSAGE = "查询时间超出本阶段允许披露范围，本次请求未返回数据。"
AUDIT_MESSAGE = "桥接审计日志不可用，本次请求未返回数据，请联系平台运营方。"
PUBLIC_DISCLOSURE = {"source": "platform_controller", "interval": "[start,stop)",
                     "release": "cumulative_committed_segments", "strategy_clock": "current_event",
                     "out_of_range": "reject_entire_request", "error_code": "TIME_RANGE_EXCEEDED",
                     "audit": "platform_private_sqlite"}
EPOCH = datetime(1970, 1, 1, tzinfo=SH)


def time_ns(value):
    """Exact ISO instant on an integer timeline; do not round nanosecond inputs."""
    dt = parse_time(value)
    delta = dt - EPOCH
    result = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000
    fraction = re.search(r"[T ]\d\d:\d\d:\d\d\.(\d+)", str(value))
    if fraction:
        digits = fraction[1]
        if len(digits) > 9 and any(x != "0" for x in digits[9:]):
            raise LabError("TIME_SELECTION_UNCLASSIFIED", "超过已登记的纳秒精度")
        result += int((digits + "000000000")[6:9])
    return result


def ns_iso(point):
    seconds, fraction = divmod(point, 10**9)
    dt = EPOCH + timedelta(seconds=seconds, microseconds=fraction // 1000)
    result = dt.isoformat(timespec="microseconds")
    if fraction % 1000:
        result = result[:-6] + f"{fraction % 1000:03d}" + result[-6:]
    return result


@dataclass(frozen=True)
class DisclosureScope:
    start: str
    stop: str
    episode_id: str
    stage: int
    policy_hash: str
    job_id: str | None = None
    submission_hash: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "start", iso(self.start))
        object.__setattr__(self, "stop", iso(self.stop))
        if (time_ns(self.start) >= time_ns(self.stop) or not self.episode_id or not self.policy_hash or
                isinstance(self.stage, bool) or not isinstance(self.stage, int) or not 0 <= self.stage <= 7):
            raise LabError("INVALID_DISCLOSURE_SCOPE")

    def validate(self, manifest):
        if (time_ns(self.start) < time_ns(manifest["real_start"]) or
                time_ns(self.stop) > time_ns(manifest["real_stop"])):
            raise LabError("DISCLOSURE_SCOPE_NOT_FROZEN")
        return self


@dataclass(frozen=True)
class QueryContext:
    scope: DisclosureScope
    clock: str

    def __post_init__(self):
        object.__setattr__(self, "clock", iso(self.clock))
        if time_ns(self.clock) < time_ns(self.scope.start):
            raise LabError("INVALID_DISCLOSURE_CLOCK")

    def record_visible(self, event_at, available_at, event_scope):
        start, stop, clock = time_ns(self.scope.start), time_ns(self.scope.stop), time_ns(self.clock)
        at = time_ns(available_at)
        return (at <= clock and at < stop and
                (event_scope == "visible_history" or start <= time_ns(event_at) < stop and time_ns(event_at) <= clock))


def make_policy(windows, manifest, episode_id):
    if not isinstance(windows, list) or len(windows) != 8:
        raise LabError("DISCLOSURE_POLICY_REQUIRED")
    stages, last = [], None
    for index, window in enumerate(windows):
        if window.get("segment") != index or set(window) != {"segment", "start", "stop"}:
            raise LabError("INVALID_DISCLOSURE_POLICY")
        start, stop = iso(window["start"]), iso(window["stop"])
        if time_ns(start) >= time_ns(stop) or last is not None and start != last:
            raise LabError("INVALID_DISCLOSURE_POLICY")
        scope = DisclosureScope(windows[0]["start"], stop, episode_id, index, "validation")
        scope.validate(manifest)
        stages.append({"stage": index, "start": scope.start, "stop": scope.stop})
        last = stop
    policy = {"schema_version": "finlab.disclosure.v1", "episode_id": episode_id,
              "dataset_id": manifest["dataset_id"], "freeze_hash": manifest["manifest_hash"],
              "interval": "[start,stop)", "stages": stages}
    policy["policy_hash"] = digest(policy)
    return policy


def check_policy(policy, windows, manifest, episode_id, expected_hash):
    expected = make_policy(windows, manifest, episode_id)
    if policy != expected or policy.get("policy_hash") != expected_hash:
        raise LabError("DISCLOSURE_POLICY_CHANGED")
    return policy


def policy_scope(policy, stage, job_id=None, submission_hash=None):
    if isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= 7:
        raise LabError("INVALID_DISCLOSURE_STAGE")
    item = policy["stages"][stage]
    return DisclosureScope(item["start"], item["stop"], policy["episode_id"], stage, policy["policy_hash"], job_id, submission_hash)


def _leaves(rules, prefix=""):
    for key, kind in rules.items():
        path = prefix + key
        if isinstance(kind, dict):
            if kind.get("type") == "array":
                if "fields" in kind:
                    yield from _leaves(kind["fields"], path + "[].")
                else:
                    yield path + "[]", kind.get("items")
            else:
                yield from _leaves(kind.get("fields", kind), path + ".")
        else:
            yield path, kind


def compile_time_rules(ep, overrides=None):
    """Freeze parameter meaning with its type; unknown meanings stay blocked."""
    supplied = {**ep.get("query_time_rules", {}), **(overrides or {})}
    result = {}
    for path, kind in _leaves(ep["params"]):
        if kind not in TIME_KINDS:
            continue
        key = path.rsplit(".", 1)[-1].replace("[]", "")
        docs = ep.get("param_docs", {}).get(key, {}).get("description", "")
        role = "lower" if key.startswith(("start_", "begin_")) else "upper" if key.startswith(("end_", "stop_")) else "select"
        target = "event_at" if role in {"lower", "upper"} else key if key in ep["fields"] else None
        # Published date ranges have different semantics from report/event dates.
        if role in {"lower", "upper"} and "公告" in docs:
            target = "ann_date" if "ann_date" in ep["fields"] else None
        if "开始" in docs and "公告" not in docs and key in ep["fields"]:
            role, target = "lower", key
        if (key in ep["fields"] and re.search(r"报告期|财报周期", docs) and
                not re.search(r"开始|结束|范围", docs)):
            role, target = "select", key
        item = {"role": role if target else "unclassified", "target": target, "kind": kind}
        if path in supplied:
            override = supplied[path]
            if not isinstance(override, dict) or set(override) - {"role", "target"}:
                raise LabError("INVALID_TIME_SELECTION_RULE")
            item.update(override)
        if item["role"] not in {"lower", "upper", "select", "unclassified"}:
            raise LabError("INVALID_TIME_SELECTION_RULE")
        if item["target"] not in {None, "event_at"} and item["target"] not in ep["fields"]:
            raise LabError("INVALID_TIME_SELECTION_RULE")
        result[path] = item
    if set(supplied) - set(result):
        raise LabError("INVALID_TIME_SELECTION_RULE")
    return result


def map_query(params, rules, mapping):
    """Tushare comma selections also pass the typed time mapper, one value at a time."""
    if not isinstance(params, dict):
        raise LabError("INVALID_QUERY")
    unknown = set(params) - set(rules)
    if unknown:
        raise LabError("UNCLASSIFIED_FIELD")
    output = {}
    for key, value in params.items():
        rule = rules[key]
        if isinstance(rule, dict) and value is not None:
            if rule.get("type") == "array":
                if not isinstance(value, list):
                    raise LabError("INVALID_RECORD")
                output[key] = [map_query(v, rule["fields"], mapping) if "fields" in rule
                               else mapping.shift(v, rule["items"], inverse=True) for v in value]
            else:
                output[key] = map_query(value, rule.get("fields", rule), mapping)
        elif isinstance(rule, str) and rule in TIME_KINDS and isinstance(value, str) and "," in value:
            parts = value.split(",")
            if len(parts) > 1000 or any(not v.strip() for v in parts):
                raise LabError("INVALID_TIME_SELECTION")
            output[key] = ",".join(str(mapping.shift(v.strip(), rule, inverse=True)) for v in parts)
        elif value is None:
            output[key] = None
        else:
            output[key] = mapping.shift(value, rule, inverse=True)
    return output


def value_range(value, kind):
    """Return [low,high) and whether the selection has calendar-day precision."""
    raw = str(value)
    try:
        if kind.startswith("epoch_"):
            if isinstance(value, bool) or not re.fullmatch(r"-?\d+", raw):
                raise ValueError()
            scale = {"epoch_s": 10**9, "epoch_ms": 10**6, "epoch_us": 1000, "epoch_ns": 1}[kind]
            point = int(raw) * scale + time_ns("1970-01-01T00:00:00Z")
            return point, point + 1, False
        if kind == "date":
            day = parse_time(value).replace(hour=0, minute=0, second=0, microsecond=0)
            return time_ns(day.isoformat()), time_ns((day + timedelta(days=1)).isoformat()), True
        if kind == "datetime":
            point = time_ns(value)
            return point, point + 1, False
        if kind == "temporal":
            if re.fullmatch(r"\d{14}", raw):
                return value_range(datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=SH).isoformat(), "datetime")
            if re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", raw):
                return value_range(raw, "date")
            if re.match(r"\d{4}-\d{2}-\d{2}[T ]", raw):
                return value_range(raw, "datetime")
            raise ValueError()
        if kind == "period":
            inferred = "year" if re.fullmatch(r"\d{4}", raw) else "month" if re.fullmatch(r"\d{6}|\d{4}[-/]\d{2}", raw) else "quarter" if "Q" in raw.upper() or "季度" in raw else "date"
            return value_range(value, inferred)
        if kind == "year":
            if not re.fullmatch(r"\d{4}", raw): raise ValueError()
            start = datetime(int(raw), 1, 1, tzinfo=SH)
            stop = start.replace(year=start.year + 1)
        elif kind == "month":
            match = re.fullmatch(r"(\d{4})[-/]?(\d{2})", raw)
            if not match: raise ValueError()
            start = datetime(int(match[1]), int(match[2]), 1, tzinfo=SH)
            stop = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        elif kind == "quarter":
            match = re.fullmatch(r"(\d{4})(?:[Qq]([1-4])|年第?([1-4])季度)", raw)
            if not match: raise ValueError()
            start = datetime(int(match[1]), (int(match[2] or match[3]) - 1) * 3 + 1, 1, tzinfo=SH)
            stop = start.replace(year=start.year + 1, month=1) if start.month == 10 else start.replace(month=start.month + 3)
        else:
            raise ValueError()
        return time_ns(start.isoformat()), time_ns(stop.isoformat()), True
    except (ValueError, OverflowError, KeyError, TypeError):
        raise LabError("TIME_SELECTION_UNCLASSIFIED", "时间选择格式或精度未登记") from None


def _values(params, rules, prefix=""):
    for key, value in params.items():
        rule, path = rules[key], prefix + key
        if value is None:
            continue
        if isinstance(rule, dict):
            if rule.get("type") == "array":
                for item in value:
                    if "fields" in rule:
                        yield from _values(item, rule["fields"], path + "[].")
                    else:
                        yield path + "[]", rule["items"], item
            else:
                yield from _values(value, rule.get("fields", rule), path + ".")
        else:
            yield path, rule, value


def opaque_nested_roots(params, rules):
    # Nested time checks do not authorize ignoring a sibling filter. Until a
    # nested non-time filter has frozen partition provenance, it cannot replay.
    return {path.split(".")[0].replace("[]", "") for path, kind, _ in _values(params, rules)
            if ("." in path or "[]" in path) and kind not in TIME_KINDS}


def selections(params, ep, context, evidence):
    chosen = []
    rules = ep.get("time_query_rules")
    if rules is None:
        raise LabError("TIME_SELECTION_RULES_REQUIRED")
    start, stop, clock = time_ns(context.scope.start), time_ns(context.scope.stop), time_ns(context.clock)
    clock_day = parse_time(context.clock).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for path, kind, value in _values(params, ep["params"]):
        if kind == "text" and (DATE_TOKEN.search(str(value)) or re.search(r"\d{10,}", str(value))):
            raise LabError("TIME_SELECTION_UNCLASSIFIED")
        if kind not in TIME_KINDS:
            continue
        rule = rules.get(path)
        if not rule or rule["kind"] != kind or rule["role"] == "unclassified":
            raise LabError("TIME_SELECTION_UNCLASSIFIED")
        values = str(value).split(",") if isinstance(value, str) and "," in value else [value]
        if rule["role"] in {"lower", "upper"} and len(values) != 1:
            raise LabError("INVALID_TIME_SELECTION")
        for value in values:
            low, high, calendar = value_range(value, kind)
            item = {"path": path, "kind": kind, "role": rule["role"], "target": rule["target"],
                    "start_ns": low, "stop_ns": high}
            evidence.append(item)
            clock_stop = time_ns(clock_day.isoformat()) if calendar else clock + 1
            if low < start or high > stop or high > clock_stop:
                raise LabError("TIME_RANGE_EXCEEDED", RANGE_MESSAGE)
            chosen.append(item)
    for target in {x["target"] for x in chosen}:
        lower = [v["start_ns"] for v in chosen if v["role"] == "lower" and v["target"] == target]
        upper = [v["stop_ns"] for v in chosen if v["role"] == "upper" and v["target"] == target]
        if lower and upper and max(lower) >= min(upper):
            raise LabError("INVALID_TIME_SELECTION", "查询开始时间不得晚于结束时间")
    return chosen


def row_matches(row, event_at, ep, chosen):
    groups = defaultdict(list)
    for item in chosen:
        groups[(item["path"], item["role"], item["target"])].append(item)
    for (_, role, target), items in groups.items():
        if target == "event_at":
            low, high, _ = value_range(event_at, "datetime")
        else:
            if row.get(target) in (None, ""):
                return False
            low, high, _ = value_range(row[target], ep["all_field_types"].get(target, "unclassified"))
        if role == "lower" and low < items[0]["start_ns"]:
            return False
        if role == "upper" and high > items[0]["stop_ns"]:
            return False
        if role == "select" and not any(low >= x["start_ns"] and high <= x["stop_ns"] for x in items):
            return False
    return True


def clean_params(params, rules):
    """Bounded, allowlisted metadata only; the wire token never enters this function."""
    if not isinstance(params, dict): return {"invalid_type": type(params).__name__}
    output = {}
    for key, value in params.items():
        if key not in rules or re.search(r"token|secret|password|authorization|api_key", key, re.I):
            continue
        if isinstance(value, dict) and isinstance(rules[key], dict):
            output[key] = clean_params(value, rules[key].get("fields", rules[key]))
        elif isinstance(value, list):
            output[key] = "[array: see normalized time selections]"
        elif isinstance(value, float) and not math.isfinite(value):
            output[key] = "[non-finite value]"
        elif value is None or isinstance(value, (bool, int, float)):
            output[key] = value
        else:
            text = str(value)[:256]
            text = re.sub(r"sk-[A-Za-z0-9_-]+|(?i:Bearer)\s+\S+", "[redacted]", text)
            output[key] = text.replace("\r", " ").replace("\n", " ")
    return output


class AuditLog:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._secrets = set()
        self._secret_lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._open()) as con, con:
                con.execute("pragma journal_mode=WAL")
                con.execute("""create table if not exists query_events(
                    request_id text primary key,recorded_at text,episode_id text,stage integer,
                    job_id text,policy_hash text,outcome text,error_code text,body text not null)""")
        except (OSError, sqlite3.Error):
            raise LabError("BRIDGE_AUDIT_UNAVAILABLE", AUDIT_MESSAGE) from None

    def _open(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("pragma busy_timeout=10000")
        con.execute("pragma synchronous=FULL")
        return con

    def register_secret(self, secret):
        if isinstance(secret, str) and secret:
            with self._secret_lock:
                self._secrets.add(secret)

    def _redact(self, value, secrets):
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {k: self._redact(v, secrets) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v, secrets) for v in value]
        return value

    def append(self, record):
        try:
            with self._secret_lock:
                secrets = sorted(self._secrets, key=len, reverse=True)
            record = self._redact(record, secrets)
            body = canonical(record)
            with closing(self._open()) as con, con:
                con.execute("insert into query_events values(?,?,?,?,?,?,?,?,?)", (
                    record["request_id"], now(), record.get("episode_id"), record.get("stage"),
                    record.get("job_id"), record.get("policy_hash"), record["outcome"], record.get("error_code"), body))
        except (OSError, ValueError, TypeError, sqlite3.Error):
            raise LabError("BRIDGE_AUDIT_UNAVAILABLE", AUDIT_MESSAGE) from None

    def checkpoint(self):
        try:
            with closing(self._open()) as con:
                if con.execute("pragma wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                    raise sqlite3.OperationalError("audit busy")
        except (OSError, sqlite3.Error):
            raise LabError("BRIDGE_AUDIT_UNAVAILABLE", AUDIT_MESSAGE) from None


def read_audit(path, limit=100):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise LabError("INVALID_AUDIT_LIMIT")
    path = Path(path).resolve()
    if not path.is_file():
        raise LabError("BRIDGE_AUDIT_NOT_FOUND")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
            rows = con.execute("select body from query_events order by rowid desc limit ?", (limit,)).fetchall()
        return {"scope": "operator_private", "events": [json.loads(x[0]) for x in rows]}
    except (sqlite3.Error, ValueError):
        raise LabError("BRIDGE_AUDIT_UNAVAILABLE", AUDIT_MESSAGE) from None
