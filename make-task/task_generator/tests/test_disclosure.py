"""Black-box stage-disclosure tests using explicit synthetic fixtures only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from finlab.common import LabError, digest, read, write
from finlab.data import FrozenView, freeze, ingest, init
from finlab.demo import build
from finlab.disclosure import AuditLog, DisclosureScope, QueryContext
from finlab.runtime import EpisodeResearchView, create_episode, status
from finlab.server import query_server
from finlab.temporal import TimeMap, iso, parse_time
from finlab.tushare import pro_api, pro_bar


@contextmanager
def serving(view, token="synthetic-disclosure-token"):
    server = query_server(view, token, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(url, token, api="daily", params=None, **extra):
    payload = {"api_name": api, "token": token, "params": params or {}, "fields": "ts_code,trade_date,close", **extra}
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        finally:
            error.close()


def audit_text(path):
    """Read logical rows, including committed WAL data, without relying on table layout."""
    with closing(sqlite3.connect(path)) as connection:
        tables = [row[0] for row in connection.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        values = []
        for name in tables:
            escaped = name.replace('"', '""')
            values.extend(connection.execute(f'select * from "{escaped}"').fetchall())
    return json.dumps(values, ensure_ascii=False)


class StageDisclosureBlackBoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / "synthetic"
        build(self.fixture, "STOCK", resources={"cpus": 2, "memory_mb": 4096, "timeout_seconds": 120})
        self.episode = self.root / "episode"
        create_episode(read(self.fixture / "binding.private.json"), self.episode)
        self.view = EpisodeResearchView(self.episode)
        self.binding = read(self.episode / "binding.private.json")
        self.mapping = self.view.view.mapping
        self.first = self.mapping.shift(self.binding["windows"][0]["start"], "date").replace("-", "")
        self.stop = self.mapping.shift(self.binding["windows"][0]["stop"], "date").replace("-", "")
        previous = parse_time(self.binding["windows"][0]["start"]) - timedelta(days=1)
        self.before = self.mapping.shift(previous.date().isoformat(), "date").replace("-", "")
        last = parse_time(self.binding["windows"][0]["stop"]) - timedelta(days=1)
        self.last = self.mapping.shift(last.date().isoformat(), "date").replace("-", "")

    def tearDown(self):
        self.temp.cleanup()

    def test_http_partial_range_is_rejected_without_partial_data_and_is_audited(self):
        with serving(self.view) as (url, token):
            http_status, reply = post(url, token, params={"start_date": self.first, "end_date": self.stop})
        self.assertEqual(http_status, 403)
        self.assertEqual(reply["code"], -1)
        self.assertEqual(reply["error_code"], "TIME_RANGE_EXCEEDED")
        self.assertIn("超出", reply["msg"])
        self.assertTrue(reply["request_id"])
        self.assertNotIn("data", reply)
        audit = audit_text(self.episode / "bridge_audit.private.sqlite")
        self.assertIn(reply["request_id"], audit)
        self.assertIn("TIME_RANGE_EXCEEDED", audit)
        self.assertNotIn(token, audit)
        self.assertNotIn(str(self.root), json.dumps(reply))
        self.assertNotIn("year_offset", json.dumps(reply))
        self.assertNotIn(self.binding["windows"][0]["start"], json.dumps(reply))
        self.assertEqual(status(self.episode)["completed_segment"], 0)

    def test_start_boundary_allowed_and_stop_boundary_rejected(self):
        with serving(self.view) as (url, token):
            allowed_status, allowed = post(url, token, params={"trade_date": self.first})
            denied_status, denied = post(url, token, params={"trade_date": self.stop})
        self.assertEqual(allowed_status, 200)
        self.assertEqual(len(allowed["data"]["items"]), 1)
        self.assertEqual(denied_status, 403)
        self.assertEqual(denied["error_code"], "TIME_RANGE_EXCEEDED")
        self.assertNotIn("data", denied)

    def test_lower_partial_range_and_entirely_future_range_rejected(self):
        with serving(self.view) as (url, token):
            for params in ({"start_date": self.before, "end_date": self.first},
                           {"start_date": self.stop, "end_date": self.stop},
                           {"trade_date": self.before}):
                with self.subTest(params=params):
                    http_status, reply = post(url, token, params=params)
                    self.assertEqual(http_status, 403)
                    self.assertEqual(reply["error_code"], "TIME_RANGE_EXCEEDED")
                    self.assertNotIn("data", reply)

    def test_omitted_dates_and_pagination_stay_in_disclosed_range(self):
        values = []
        with serving(self.view) as (url, token):
            offset = 0
            while True:
                http_status, reply = post(url, token, params={"limit": 2, "offset": offset})
                self.assertEqual(http_status, 200)
                values.extend(reply["data"]["items"])
                if not reply["meta"]["has_more"]:
                    break
                self.assertGreater(reply["meta"]["next_offset"], offset)
                offset = reply["meta"]["next_offset"]
        self.assertEqual(len(values), 12)
        self.assertTrue(all(self.first <= row[1] < self.stop for row in values))
        self.assertEqual(len({row[1] for row in values}), len(values))

    def test_sdk_query_dynamic_and_wrapper_keep_error_details(self):
        with serving(self.view) as (url, token):
            client = pro_api(token, url=url)
            operations = [lambda: client.daily(trade_date=self.stop),
                          lambda: client.query("daily", trade_date=self.stop),
                          lambda: pro_bar("000001.SZ", api=client, start_date=self.first, end_date=self.stop)]
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(LabError) as caught:
                        operation()
                    error = caught.exception
                    self.assertEqual(error.code, "TIME_RANGE_EXCEEDED")
                    self.assertIn("超出", error.message)
                    self.assertTrue(error.request_id)
                    self.assertIn(error.request_id, audit_text(self.episode / "bridge_audit.private.sqlite"))

    def test_wrapper_entire_disclosed_daily_range_succeeds(self):
        with serving(self.view) as (url, token):
            frame = pro_bar("000001.SZ", api=pro_api(token, url=url), start_date=self.first, end_date=self.last)
        self.assertEqual(len(frame), 12)
        self.assertTrue(all(self.first <= value < self.stop for value in frame["trade_date"]))

    def test_direct_queries_apply_the_same_gate_and_audit(self):
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"trade_date": self.stop}, "")
        self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")
        self.assertTrue(caught.exception.request_id)
        self.assertIn(caught.exception.request_id, audit_text(self.episode / "bridge_audit.private.sqlite"))

    def test_failed_job_artifacts_do_not_unlock_data(self):
        orphan = self.episode / "jobs" / "synthetic_failed_probe"
        write(orphan / "failure.private.json", {"status": "not_committed"})
        write(orphan / "artifacts" / "success.private.json", {"status": "ready_to_commit", "hashes": {}})
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"trade_date": self.stop}, "")
        self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")
        self.assertEqual(status(self.episode)["completed_segment"], 0)

    def test_research_scope_expands_cumulatively_after_trusted_commit_state_update(self):
        # Isolated controller-state fixture only; this does not claim a real
        # container execution or create a production approval/checkpoint.
        with self.assertRaises(LabError):
            self.view.query("daily", {"trade_date": self.stop}, "")
        state = read(self.episode / "state.private.json")
        state["completed_segment"] = 1
        state["commits"] = [{"segment": 1, "job_id": "synthetic_committed_state_fixture"}]
        write(self.episode / "state.private.json", state)
        self.assertEqual(len(self.view.query("daily", {"trade_date": self.first}, "")["data"]["items"]), 1)
        self.assertEqual(len(self.view.query("daily", {"trade_date": self.stop}, "")["data"]["items"]), 1)
        next_stop = self.mapping.shift(self.binding["windows"][1]["stop"], "date").replace("-", "")
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"trade_date": next_stop}, "")
        self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")
        self.assertEqual(status(self.episode)["completed_segment"], 1)

    def test_client_cannot_supply_a_larger_stage_or_range(self):
        with serving(self.view) as (url, token):
            for extra in ({"stage": 7}, {"clock": "2499-12-31"},
                          {"scope": {"start": self.first, "stop": "24991231"}}):
                with self.subTest(extra=extra):
                    http_status, reply = post(url, token, params={"trade_date": self.stop}, **extra)
                    self.assertNotEqual(http_status, 200)
                    self.assertNotIn("data", reply)
            http_status, reply = post(url, token, params={"trade_date": self.stop})
            self.assertEqual(http_status, 403)

    def test_parallel_requests_have_distinct_persistent_audit_ids(self):
        with serving(self.view) as (url, token):
            with ThreadPoolExecutor(max_workers=8) as pool:
                replies = list(pool.map(lambda _: post(url, token, params={"trade_date": self.stop}), range(24)))
        ids = []
        for http_status, reply in replies:
            self.assertEqual(http_status, 403)
            self.assertNotIn("data", reply)
            ids.append(reply["request_id"])
        self.assertEqual(len(set(ids)), 24)
        persisted = audit_text(self.episode / "bridge_audit.private.sqlite")
        self.assertTrue(all(ident in persisted for ident in ids))

    def test_policy_tampering_is_detected_after_research_service_starts(self):
        policy_path = self.episode / "disclosure.private.json"
        policy = read(policy_path)
        policy["synthetic_tamper"] = True
        write(policy_path, policy)
        with self.assertRaises(LabError):
            self.view.query("daily", {"trade_date": self.first}, "")

    def test_wire_token_is_redacted_even_when_copied_into_an_allowed_parameter(self):
        with serving(self.view) as (url, token):
            http_status, reply = post(url, token, params={"ts_code": token})
        self.assertNotEqual(http_status, 200)
        self.assertNotIn("data", reply)
        self.assertNotIn(token, audit_text(self.episode / "bridge_audit.private.sqlite"))


class TypedDisclosureTests(unittest.TestCase):
    """Custom explicit catalog types exercise selectors beyond common date names."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mapping = TimeMap()
        self.fields = {"ts_code": "identity", "trade_date": "date", "close": "number"}
        self.params = {"ts_code": "identity", "trade_date": "date", "start_date": "date", "end_date": "date",
                       "limit": "number", "offset": "number", "unusual_date": "date", "ann_date": "date",
                       "report_year": "year", "report_month": "month", "report_quarter": "quarter",
                       "event_s": "epoch_s", "event_ms": "epoch_ms", "event_us": "epoch_us", "event_ns": "epoch_ns",
                       "selection": {"fields": {"as_of": "datetime"}},
                       "check_dates": {"type": "array", "items": "date"}, "opaque_text": "text"}
        self.catalog = self.root / "catalog.json"
        write(self.catalog, {"endpoints": {"daily": {"api_name": "daily", "fields": self.fields, "params": self.params}}})
        self.source = self.root / "source.sqlite"
        init(self.source)
        from finlab.catalog import load_catalog
        catalog = load_catalog(self.catalog)
        rows = [{"ts_code": "000001.SZ", "trade_date": f"202401{day:02d}", "close": day,
                 "_available_at": f"2024-01-{day:02d}T15:00:00+08:00"} for day in (2, 10, 15, 20)]
        ingest(self.source, "daily", rows, {"timing_basis": "operator_supplied", "key_fields": ["ts_code", "trade_date"],
                                          "event_field": "trade_date"}, catalog=catalog)
        self.frozen = self.root / "frozen"
        time_rules = {field: {"role": "select", "target": "event_at"} for field in
                      ("unusual_date", "ann_date", "report_year", "report_month", "report_quarter",
                       "event_s", "event_ms", "event_us", "event_ns", "selection.as_of", "check_dates[]")}
        freeze(self.source, {"synthetic": True, "catalog_path": str(self.catalog), "symbols": ["000001.SZ"],
                             "real_start": "2024-01-01", "real_stop": "2024-02-01",
                             "apis": {"daily": {"fields": list(self.fields), "coverage_confirmed": True,
                                                   "query_time_rules": time_rules,
                                                   "replayability": "point_in_time"}}}, self.frozen)
        self.audit = AuditLog(self.root / "audit.private.sqlite")
        self.policy_hash = digest({"synthetic_test_policy": True})
        self.scope = DisclosureScope("2024-01-01", "2024-01-11", "synthetic-typed", 0, self.policy_hash)
        self.view = FrozenView(self.frozen, clock="2024-01-10T15:00:00+08:00", scope=self.scope, audit=self.audit)

    def tearDown(self):
        self.temp.cleanup()

    def test_query_and_server_require_scope_and_audit(self):
        for kwargs in ({}, {"scope": self.scope}, {"audit": self.audit}):
            with self.subTest(kwargs=kwargs):
                view = FrozenView(self.frozen, clock="2024-01-10T15:00:00+08:00", **kwargs)
                with self.assertRaises(LabError):
                    view.query("daily")
                with self.assertRaises(LabError):
                    query_server(view, "synthetic-token", port=0)

    def test_uncommon_date_and_announcement_parameters_are_checked_before_replay(self):
        for field in ("unusual_date", "ann_date"):
            with self.subTest(field=field):
                with self.assertRaises(LabError) as caught:
                    self.view.query("daily", {field: "24000115"})
                self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")

    def test_year_month_and_quarter_cannot_select_a_partly_hidden_period(self):
        for field, value in (("report_year", 2400), ("report_month", "240001"), ("report_quarter", "2400Q1")):
            with self.subTest(field=field):
                with self.assertRaises(LabError) as caught:
                    self.view.query("daily", {field: value})
                self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")

    def test_nested_datetime_parameter_does_not_bypass_scope(self):
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"selection": {"as_of": "2400-01-15T12:00:00+08:00"}})
        self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")

    def test_mixed_comma_and_array_selections_are_rejected_as_whole_queries(self):
        for params in ({"trade_date": "24000102,24000115"},
                       {"check_dates": ["24000102", "24000115"]}):
            with self.subTest(params=params):
                with self.assertRaises(LabError) as caught:
                    self.view.query("daily", params)
                self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")
                self.assertTrue(caught.exception.request_id)

    def test_opaque_text_with_unregistered_time_semantics_is_rejected(self):
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"opaque_text": "period=2400-01-02"})
        self.assertEqual(caught.exception.code, "TIME_SELECTION_UNCLASSIFIED")

    def test_unregistered_temporal_parameter_fails_closed(self):
        self.view.manifest["apis"]["daily"]["time_query_rules"]["unusual_date"] = {
            "kind": "date", "role": "unclassified", "target": None}
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"unusual_date": "24000102"})
        self.assertEqual(caught.exception.code, "TIME_SELECTION_UNCLASSIFIED")

    def test_each_epoch_unit_checks_precise_event_clock(self):
        self.view.clock = "2024-01-10T15:00:00+08:00"
        seconds = int(parse_time(self.view.clock).timestamp())
        for field, kind, factor in (("event_s", "epoch_s", 1), ("event_ms", "epoch_ms", 1000),
                                     ("event_us", "epoch_us", 10**6), ("event_ns", "epoch_ns", 10**9)):
            with self.subTest(field=field):
                virtual = self.mapping.shift(seconds * factor + 1, kind)
                with self.assertRaises(LabError) as caught:
                    self.view.query("daily", {field: virtual})
                self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")

    def test_daily_current_date_is_allowed_but_unpublished_rows_are_hidden(self):
        self.view.clock = "2024-01-10T14:59:59+08:00"
        reply = self.view.query("daily", {"trade_date": "24000110"})
        self.assertEqual(reply["data"]["items"], [])
        self.view.clock = "2024-01-10T15:00:00+08:00"
        self.assertEqual(len(self.view.query("daily", {"end_date": "24000110"})["data"]["items"]), 2)

    def test_audit_write_failure_returns_503_and_no_data(self):
        with serving(self.view) as (url, token):
            with patch.object(self.audit, "append", side_effect=sqlite3.OperationalError("synthetic disk full")):
                http_status, reply = post(url, token, params={"trade_date": "24000102"})
        self.assertEqual(http_status, 503)
        self.assertEqual(reply["error_code"], "BRIDGE_AUDIT_UNAVAILABLE")
        self.assertTrue(reply["request_id"])
        self.assertNotIn("data", reply)
        self.assertNotIn("synthetic disk full", json.dumps(reply))

    def test_same_view_uses_the_supplied_immutable_context_per_query(self):
        broad = DisclosureScope("2024-01-01", "2024-01-21", "synthetic-typed", 1, self.policy_hash)
        narrow_context = QueryContext(self.scope, "2024-01-10T15:00:00+08:00")
        broad_context = QueryContext(broad, "2024-01-20T15:00:00+08:00")
        self.view.clock = "2024-01-10T23:00:00+08:00"
        def query(context):
            try:
                return self.view.query("daily", {"trade_date": "24000115"}, _context=context)
            except LabError as error:
                return error
        contexts = [narrow_context, broad_context] * 12
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(query, contexts))
        for context, reply in zip(contexts, replies):
            if context is narrow_context:
                self.assertIsInstance(reply, LabError)
                self.assertEqual(reply.code, "TIME_RANGE_EXCEEDED")
            else:
                self.assertEqual(len(reply["data"]["items"]), 1)
                self.assertEqual(reply["data"]["items"][0][1], "24000115")

    def test_nanosecond_clock_and_exclusive_stop_are_not_rounded(self):
        from finlab.disclosure import time_ns
        clock = "2024-01-10T15:00:00.123456789+08:00"
        stop = "2024-01-10T15:00:00.123456790+08:00"
        scope = DisclosureScope("2024-01-01", stop, "synthetic-ns", 0, self.policy_hash)
        context = QueryContext(scope, clock)
        self.assertEqual(time_ns(context.clock), time_ns(clock))
        self.assertEqual(time_ns(context.scope.stop), time_ns(stop))
        exact = self.mapping.shift(time_ns(clock) - time_ns("1970-01-01T00:00:00Z"), "epoch_ns")
        reply = self.view.query("daily", {"event_ns": exact}, _context=context)
        self.assertEqual(reply["code"], 0)
        with self.assertRaises(LabError) as caught:
            self.view.query("daily", {"event_ns": exact + 1}, _context=context)
        self.assertEqual(caught.exception.code, "TIME_RANGE_EXCEEDED")

    def test_nanosecond_publication_revision_is_visible_only_at_exact_clock(self):
        from finlab.catalog import load_catalog
        revised = {"ts_code": "000001.SZ", "trade_date": "20240110", "close": 999,
                   "_available_at": "2024-01-10T15:00:00.123456789+08:00"}
        ingest(self.source, "daily", [revised], {"timing_basis": "operator_supplied", "key_fields": ["ts_code", "trade_date"],
                                                "event_field": "trade_date"}, catalog=load_catalog(self.catalog))
        nano = self.root / "nano_frozen"
        freeze(self.source, {"synthetic": True, "catalog_path": str(self.catalog), "symbols": ["000001.SZ"],
                             "real_start": "2024-01-01", "real_stop": "2024-02-01",
                             "apis": {"daily": {"fields": list(self.fields), "coverage_confirmed": True,
                                                   "replayability": "point_in_time"}}}, nano)
        view = FrozenView(nano, clock="2024-01-10T15:00:00.123456788+08:00", scope=self.scope, audit=self.audit)
        self.assertEqual(view.query("daily", {"trade_date": "24000110"})["data"]["items"][0][-1], 10)
        view.clock = "2024-01-10T15:00:00.123456789+08:00"
        self.assertEqual(view.query("daily", {"trade_date": "24000110"})["data"]["items"][0][-1], 999)

    def test_invalid_scope_and_outside_frozen_scope_refuse_startup(self):
        with self.assertRaises(LabError):
            DisclosureScope("2024-01-02", "2024-01-02", "synthetic", 0, self.policy_hash)
        scope = DisclosureScope("2024-01-01", "2024-03-01", "synthetic", 0, self.policy_hash)
        with self.assertRaises(LabError):
            view = FrozenView(self.frozen, clock="2024-01-10", scope=scope, audit=self.audit)
            query_server(view, "synthetic-token", port=0)


if __name__ == "__main__":
    unittest.main()
