"""Behavioral regressions found during the independent disclosure review.

All data is synthetic. No upstream calls, Docker runs or operator setup occurs.
"""
from __future__ import annotations

import copy
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from finlab.catalog import load_catalog
from finlab.common import LabError, read, write
from finlab.data import FrozenView, db_open, freeze, ingest, init
from finlab.demo import build
from finlab.disclosure import AuditLog, DisclosureScope, time_ns
from finlab.runtime import create_episode
from finlab.temporal import TimeMap, parse_time


class DisclosureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mapping = TimeMap()

    def tearDown(self):
        self.temp.cleanup()

    def frozen(self, name, api, rows, *, event_field, key_fields=None,
               catalog=None, event_scope="task_range", symbols=None,
               start="2024-01-01", stop="2024-09-01", partition=None):
        source = self.root / (name + ".sqlite")
        init(source)
        loaded = load_catalog(catalog) if catalog else None
        ingest(source, api, rows, {"timing_basis": "operator_supplied",
                                  "key_fields": key_fields or ["ts_code", event_field],
                                  "event_field": event_field},
               catalog=loaded, partition=partition)
        plan = {"synthetic": True, "symbols": symbols or ["000001.SZ"],
                "real_start": start, "real_stop": stop,
                "apis": {api: {"fields": [k for k in rows[0] if not k.startswith("_")],
                               "coverage_confirmed": True, "replayability": "point_in_time",
                               "event_scope": event_scope}}}
        if catalog:
            plan["catalog_path"] = str(catalog)
        destination = self.root / name
        freeze(source, plan, destination)
        return destination, source

    def view(self, folder, *, start="2024-01-01", stop="2024-08-01",
             clock="2024-07-31T18:00:00+08:00"):
        scope = DisclosureScope(start, stop, "synthetic-independent-review", 0, "synthetic-policy-hash")
        return FrozenView(folder, clock=clock, scope=scope,
                          audit=AuditLog(self.root / (folder.name + ".audit.private.sqlite")))

    def test_financial_end_date_selects_one_report_period_instead_of_all_prior_reports(self):
        for api in ("disclosure_date", "stk_rewards"):
            with self.subTest(api=api):
                rows = [{"ts_code": "000001.SZ", "end_date": "20240331", "ann_date": "20240420",
                         "_available_at": "2024-04-20T16:00:00+08:00"},
                        {"ts_code": "000001.SZ", "end_date": "20240630", "ann_date": "20240720",
                         "_available_at": "2024-07-20T16:00:00+08:00"}]
                folder, _ = self.frozen(api, api, rows, event_field="end_date")
                reply = self.view(folder).query(api, {"end_date": "24000630"}, "ts_code,end_date")
                self.assertEqual(reply["data"]["items"], [["000001.SZ", "24000630"]])

    def test_announcement_range_uses_announcement_date_instead_of_event_start(self):
        for api in ("namechange", "pledge_detail"):
            with self.subTest(api=api):
                rows = [{"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240201",
                         "ann_date": "20240120", "_available_at": "2024-01-20T16:00:00+08:00"},
                        {"ts_code": "000001.SZ", "start_date": "20240124", "end_date": "20240201",
                         "ann_date": "20240125", "_available_at": "2024-01-25T16:00:00+08:00"}]
                folder, _ = self.frozen(api, api, rows, event_field="start_date")
                reply = self.view(folder).query(api, {"start_date": "24000120", "end_date": "24000120"},
                                                "ts_code,start_date,ann_date")
                self.assertEqual(reply["data"]["items"], [["000001.SZ", "24000102", "24000120"]])

    def test_time_series_cannot_enable_metadata_history_exception(self):
        samples = {
            "adj_factor": {"ts_code": "000001.SZ", "trade_date": "20240120", "adj_factor": 1.0},
            "daily_basic": {"ts_code": "000001.SZ", "trade_date": "20240120", "close": 10},
            "stk_limit": {"ts_code": "000001.SZ", "trade_date": "20240120", "up_limit": 11, "down_limit": 9},
            "index_weight": {"index_code": "000300.SH", "con_code": "000001.SZ",
                             "trade_date": "20240120", "weight": 1.0},
        }
        for api, sample in samples.items():
            with self.subTest(api=api):
                row = {**sample, "_available_at": "2024-01-02T16:00:00+08:00"}
                keys = ["con_code", "trade_date"] if api == "index_weight" else ["ts_code", "trade_date"]
                with self.assertRaisesRegex(LabError, "VISIBLE_HISTORY_REQUIRES_REVIEWED_METADATA"):
                    self.frozen(api, api, [row], event_field="trade_date", key_fields=keys,
                                symbols=["000001.SZ", "000300.SH"], event_scope="visible_history")

    def test_published_future_maturity_remains_available_without_widening_query_range(self):
        rows = [{"ts_code": "M2509.DCE", "name": "豆粕2025年9月", "list_date": "20231220",
                 "delist_date": "20250915", "_available_at": "2023-12-20T16:00:00+08:00"}]
        folder, _ = self.frozen("metadata", "fut_basic", rows, event_field="list_date",
                                key_fields=["ts_code"], symbols=["M2509.DCE"], event_scope="visible_history")
        view = self.view(folder, stop="2024-02-01", clock="2024-01-10T16:00:00+08:00")
        reply = view.query("fut_basic", fields="ts_code,name,list_date,delist_date")
        self.assertEqual(reply["data"]["items"], [["M2509.DCE", "豆粕2025年9月", "23991220", "24010915"]])
        with self.assertRaisesRegex(LabError, "TIME_RANGE_EXCEEDED"):
            view.query("fut_basic", {"list_date": "23991220"})

    def test_future_partition_existence_does_not_change_current_error(self):
        catalog = self.root / "opaque.catalog.json"
        fields = {"ts_code": "identity", "trade_date": "date", "close": "number"}
        params = {"ts_code": "identity", "trade_date": "date", "opaque_text": "text",
                  "limit": "number", "offset": "number"}
        write(catalog, {"endpoints": {"daily": {"api_name": "daily", "fields": fields, "params": params}}})
        source = self.root / "opaque.sqlite"
        init(source)
        rule = {"timing_basis": "operator_supplied", "key_fields": ["ts_code", "trade_date"],
                "event_field": "trade_date"}
        loaded = load_catalog(catalog)
        ingest(source, "daily", [{"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10,
                                   "_available_at": "2024-01-02T15:00:00+08:00"}], rule, catalog=loaded)
        ingest(source, "daily", [{"ts_code": "000001.SZ", "trade_date": "20240120", "close": 20,
                                   "_available_at": "2024-01-20T15:00:00+08:00"}], rule, catalog=loaded,
               partition={"api_name": "daily", "params": {"opaque_text": "future_value"}})
        folder = self.root / "opaque"
        freeze(source, {"synthetic": True, "catalog_path": str(catalog), "symbols": ["000001.SZ"],
                        "real_start": "2024-01-01", "real_stop": "2024-02-01",
                        "apis": {"daily": {"fields": list(fields), "coverage_confirmed": True,
                                           "replayability": "point_in_time"}}}, folder)
        view = self.view(folder, stop="2024-01-11", clock="2024-01-10T15:00:00+08:00")
        errors = []
        for opaque in ("future_value", "never_seen"):
            with self.assertRaises(LabError) as caught:
                view.query("daily", {"opaque_text": opaque, "trade_date": "24000102"})
            errors.append((caught.exception.code, caught.exception.message))
        self.assertEqual(errors[0], errors[1])
        self.assertNotEqual(errors[0][0], "TIME_RANGE_EXCEEDED")

    def test_event_and_publication_nanoseconds_survive_ingestion_and_exclusive_stop(self):
        catalog = self.root / "ns.catalog.json"
        fields = {"ts_code": "identity", "at": "datetime", "value": "number"}
        params = {"ts_code": "identity", "start_date": "datetime", "end_date": "datetime",
                  "limit": "number", "offset": "number"}
        write(catalog, {"endpoints": {"ns_feed": {"api_name": "ns_feed", "fields": fields, "params": params}}})
        base = "2024-01-10T09:30:00.000000"
        rows = [{"ts_code": "000001.SZ", "at": base + f"{ns:03d}+08:00", "value": ns,
                 "_available_at": base + f"{ns if ns < 2 else 0:03d}+08:00"} for ns in range(3)]
        folder, source = self.frozen("nanoseconds", "ns_feed", rows, event_field="at", catalog=catalog,
                                      start="2024-01-10", stop="2024-01-11")
        with closing(db_open(source)) as con:
            actual = con.execute("select event_at,available_at from records order by event_at").fetchall()
        self.assertEqual([time_ns(r[0]) for r in actual], [time_ns(r["at"]) for r in rows])
        self.assertEqual([time_ns(r[1]) for r in actual], [time_ns(r["_available_at"]) for r in rows])
        view = self.view(folder, start=base + "000+08:00", stop=base + "002+08:00", clock=base + "000+08:00")
        self.assertEqual(view.query("ns_feed", fields="value")["data"]["items"], [[0]])
        view.clock = base + "001+08:00"
        self.assertEqual(view.query("ns_feed", fields="value")["data"]["items"], [[1], [0]])
        view.clock = base + "002+08:00"
        self.assertEqual(view.query("ns_feed", fields="value")["data"]["items"], [[1], [0]])
        with self.assertRaisesRegex(LabError, "TIME_RANGE_EXCEEDED"):
            view.query("ns_feed", {"end_date": self.mapping.shift(base + "002+08:00", "datetime")})


class ExplicitStageWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixture = self.root / "fixture"
        build(fixture, "STOCK", resources={"cpus": 2, "memory_mb": 4096, "timeout_seconds": 120})
        self.binding = read(fixture / "binding.private.json")
        create_episode(self.binding, self.root / "baseline")
        self.windows = read(self.root / "baseline/binding.private.json")["windows"]

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_stage_ranges_are_frozen_as_cumulative_disclosure(self):
        binding = copy.deepcopy(self.binding)
        binding["stage_windows"] = copy.deepcopy(self.windows)
        create_episode(binding, self.root / "explicit")
        policy = read(self.root / "explicit/disclosure.private.json")
        self.assertEqual([s["stage"] for s in policy["stages"]], list(range(8)))
        self.assertEqual({s["start"][:10] for s in policy["stages"]}, {self.windows[0]["start"]})
        self.assertEqual([s["stop"][:10] for s in policy["stages"]], [w["stop"] for w in self.windows])

    def test_inverted_gapped_and_outside_frozen_ranges_are_rejected(self):
        for error in ("inverted", "gap", "outside"):
            with self.subTest(error=error):
                windows = copy.deepcopy(self.windows)
                if error == "inverted":
                    windows[2]["stop"] = windows[2]["start"]
                elif error == "gap":
                    windows[2]["start"] = (parse_time(windows[2]["start"]) + timedelta(days=1)).date().isoformat()
                else:
                    windows[0]["start"] = (parse_time(windows[0]["start"]) - timedelta(days=1)).date().isoformat()
                binding = copy.deepcopy(self.binding)
                binding["stage_windows"] = windows
                with self.assertRaises(LabError):
                    create_episode(binding, self.root / error)
                self.assertFalse((self.root / error / "state.private.json").exists())


if __name__ == "__main__":
    unittest.main()
