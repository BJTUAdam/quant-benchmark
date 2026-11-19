"""Future virtual dates and frozen replay, using explicitly synthetic rows."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest

from finlab.common import LabError, ROOT, read, digest
from finlab.data import FrozenView as PrivateFrozenView, freeze, ingest, init
from finlab.disclosure import AuditLog, DisclosureScope
from finlab.demo import build
from finlab.server import query_server
from finlab.temporal import DEFAULT_YEAR_OFFSET, TimeMap, parse_time
from finlab.tushare import pro_api, pro_bar


def FrozenView(folder, **kwargs):
    """Grant a bounded, audited capability only to explicitly synthetic test data."""
    folder = Path(folder)
    manifest = read(folder / 'manifest.private.json')
    scope = DisclosureScope(manifest['real_start'], manifest['real_stop'], 'synthetic-test', 0,
                            digest({'test_fixture_only': True, 'manifest_hash': manifest['manifest_hash']}))
    return PrivateFrozenView(folder, scope=scope,
                             audit=AuditLog(folder.parent / (folder.name + '_audit.private.sqlite')), **kwargs)


class FutureTemporalTests(unittest.TestCase):
    def test_default_is_a_nonround_future_offset(self):
        mapping = TimeMap()
        self.assertEqual(mapping.years, DEFAULT_YEAR_OFFSET)
        self.assertEqual(mapping.shift("2024-02-29"), "2400-02-29")
        self.assertGreater(DEFAULT_YEAR_OFFSET, 300)
        self.assertNotEqual(DEFAULT_YEAR_OFFSET % 100, 0)
        plan = read(ROOT / "config/v2/freeze.example.json")
        self.assertEqual(plan["year_offset"], DEFAULT_YEAR_OFFSET)

    def test_dates_and_source_timezone_precision_roundtrip(self):
        mapping = TimeMap()
        values = [("20240229", "date"), (20240229, "date"),
                  ("2024-02-29", "date"),
                  ("2023-12-31T20:00:00.123456789Z", "datetime")]
        for value, kind in values:
            with self.subTest(value=value):
                virtual = mapping.shift(value, kind)
                self.assertEqual(mapping.shift(virtual, kind, inverse=True), value)
        self.assertEqual(mapping.shift(values[-1][0], "datetime"),
                         "2399-12-31T20:00:00.123456789Z")

    def test_registered_future_text_years_and_periods_roundtrip(self):
        mapping = TimeMap()
        source = "2024-02-29 / 20240229 / 2024年2月29日 / 2024Q1 / 2024 / 202402 / 20240229120102"
        virtual = mapping.text(source)
        self.assertNotIn("2024", virtual)
        self.assertIn("2400 / 240002", virtual)
        self.assertEqual(mapping.text(virtual, inverse=True), source)
        for value, kind in [(2024, "year"), ("202402", "month"),
                            ("2024-12", "month"), ("2024Q3", "quarter"),
                            ("2024年第3季度", "quarter")]:
            with self.subTest(value=value):
                virtual = mapping.shift(value, kind)
                self.assertEqual(mapping.shift(virtual, kind, inverse=True), value)

    def test_epoch_precisions_beyond_nanosecond_int64_roundtrip(self):
        mapping = TimeMap()
        seconds = int(parse_time("2024-02-29T12:34:56+08:00").timestamp())
        for kind, scale in [("epoch_s", 1), ("epoch_ms", 1000),
                            ("epoch_us", 10**6), ("epoch_ns", 10**9)]:
            source = seconds * scale + scale - 1
            with self.subTest(kind=kind):
                virtual = mapping.shift(source, kind)
                self.assertEqual(mapping.shift(virtual, kind, inverse=True), source)
                self.assertEqual(virtual % scale, source % scale)
                self.assertEqual(json.loads(json.dumps(virtual)), virtual)
                if kind == "epoch_ns":
                    self.assertGreater(virtual, 2**63 - 1)

    def test_nested_rules_keep_identity_and_durations(self):
        rules = {"rows": {"type": "array", "fields": {
            "at": "datetime", "name": "identity", "duration": "duration", "note": "text"}}}
        source = {"rows": [{"at": "2024-02-29T15:00:00+08:00",
                            "name": "2025年9月豆粕 M2509.DCE", "duration": 2024,
                            "note": "年度2024；报告期2024Q1"}]}
        mapping = TimeMap()
        virtual = mapping.record(source, rules)
        self.assertEqual(virtual["rows"][0]["name"], source["rows"][0]["name"])
        self.assertEqual(virtual["rows"][0]["duration"], 2024)
        self.assertEqual(mapping.record(virtual, rules, inverse=True), source)
        self.assertEqual(mapping.identifier("expiry:M2509.DCE:2025-09-15"),
                         "expiry:M2509.DCE:2401-09-15")

    def test_century_leap_mismatch_is_rejected_in_both_directions(self):
        mapping = TimeMap()
        with self.assertRaisesRegex(LabError, "INVALID_TIME_MAPPING"):
            mapping.shift("1924-02-29")  # 2300 has no February 29.
        with self.assertRaisesRegex(LabError, "INVALID_TIME_MAPPING"):
            mapping.shift("2476-02-29", inverse=True)  # 2100 is not a leap year.
        with self.assertRaisesRegex(LabError, "INVALID_TIME_MAPPING"):
            mapping.text("确认于1924年2月29日")

    def test_explicit_offsets_are_still_configurable(self):
        self.assertEqual(TimeMap(-20).shift("2024-02-29"), "2004-02-29")
        self.assertEqual(TimeMap(128).shift("2024-02-29"), "2152-02-29")
        with self.assertRaisesRegex(LabError, "INVALID_TIME_MAPPING"):
            TimeMap(100).shift("2000-02-29")

    def test_future_unknown_timestamp_text_does_not_pass(self):
        with self.assertRaisesRegex(LabError, "UNCLASSIFIED_TIME_TEXT"):
            TimeMap().text("timestamp=13574400000123456789")
        with self.assertRaisesRegex(LabError, "UNCLASSIFIED_FIELD"):
            TimeMap().record({"unknown_time": "2400-02-29"}, {})

    def test_temporal_timestamp_requires_explicit_precision(self):
        mapping = TimeMap()
        for value in [1709180000, 13574400000, 1709180000123, 13574400000123,
                      1709180000123456, 13574400000123456,
                      1709180000123456789, 13574400000123456789]:
            for encoded in (value, str(value)):
                with self.subTest(value=encoded):
                    with self.assertRaises(LabError):
                        mapping.shift(encoded, "temporal")
        for source in ["20240229120102", "2024-02-29T12:01:02+08:00"]:
            virtual = mapping.shift(source, "temporal")
            self.assertEqual(mapping.shift(virtual, "temporal", inverse=True), source)

    def test_period_range_stays_within_supported_iso_years(self):
        with self.assertRaisesRegex(LabError, "INVALID_TIME_MAPPING"):
            TimeMap().shift("999901", "month")


class FutureFrozenReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.sqlite"
        init(self.source)
        self.rows = [{"ts_code": "000001.SZ", "trade_date": "20240228",
                      "open": 10, "high": 11, "low": 9, "close": 10.1, "vol": 100,
                      "_available_at": "2024-02-28T16:00:00+08:00"},
                     {"ts_code": "000001.SZ", "trade_date": "20240229",
                      "open": 10.1, "high": 11, "low": 10, "close": 10.2, "vol": 200,
                      "_available_at": "2024-02-29T16:00:00+08:00"}]
        ingest(self.source, "daily", self.rows,
               {"timing_basis": "operator_supplied", "key_fields": ["ts_code", "trade_date"],
                "event_field": "trade_date"})
        self.plan = {"synthetic": True, "symbols": ["000001.SZ"],
                     "real_start": "2024-02-28", "real_stop": "2024-03-02",
                     "apis": {"daily": {"fields": [k for k in self.rows[0] if not k.startswith("_")],
                                          "coverage_confirmed": True, "replayability": "point_in_time"}}}
        self.frozen = self.root / "frozen"
        freeze(self.source, self.plan, self.frozen)

    def tearDown(self):
        self.tmp.cleanup()

    def test_omitted_freeze_offset_pins_new_default(self):
        self.assertEqual(read(self.frozen / "manifest.private.json")["year_offset"], DEFAULT_YEAR_OFFSET)
        view = FrozenView(self.frozen, clock="2024-02-29T16:00:00+08:00")
        reply = view.query("daily", {"trade_date": "24000229"})
        fields = reply["data"]["fields"]
        row = dict(zip(fields, reply["data"]["items"][0]))
        self.assertEqual(row["ts_code"], "000001.SZ")
        self.assertEqual(row["trade_date"], "24000229")
        self.assertEqual(reply["meta"]["visible_until"], "2400-02-29T16:00:00.000000+08:00")

    def test_future_visibility_uses_event_clock_not_era_number(self):
        view = FrozenView(self.frozen, clock="2024-02-28T16:00:00+08:00")
        with self.assertRaisesRegex(LabError, "TIME_RANGE_EXCEEDED"):
            view.query("daily", {"trade_date": "24000229"})
        self.assertEqual(len(view.query("daily", {"trade_date": "24000228"})["data"]["items"]), 1)

    def test_explicit_freeze_offset_overrides_default(self):
        folder = self.root / "explicit"
        freeze(self.source, {**self.plan, "year_offset": -20}, folder)
        view = FrozenView(folder, clock="2024-02-29T16:00:00+08:00")
        self.assertEqual(view.mapping.years, -20)
        self.assertEqual(len(view.query("daily", {"trade_date": "20040229"})["data"]["items"]), 1)

    def test_dataframe_and_wrapper_keep_future_date_strings(self):
        view = FrozenView(self.frozen, clock="2024-02-29T16:00:00+08:00")
        server = query_server(view, "synthetic-test-token", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = pro_api("synthetic-test-token", url=f"http://127.0.0.1:{server.server_port}")
            frame = client.daily(ts_code="000001.SZ", trade_date="24000229")
            self.assertEqual(frame.iloc[0]["trade_date"], "24000229")
            wrapped = pro_bar("000001.SZ", api=client, start_date="24000228", end_date="24000229", ma=[2])
            self.assertEqual(wrapped["trade_date"].tolist(), ["24000229", "24000228"])
            self.assertAlmostEqual(wrapped.iloc[0]["ma2"], 10.15)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_synthetic_fixture_uses_future_default(self):
        fixture = self.root / "demo"
        build(fixture, "FUND")
        manifest = read(fixture / "frozen/manifest.private.json")
        self.assertTrue(manifest["synthetic"])
        self.assertEqual(manifest["year_offset"], DEFAULT_YEAR_OFFSET)
        view = FrozenView(fixture / "frozen", clock="2024-01-10T16:00:00+08:00")
        self.assertEqual(len(view.query("fund_daily", {"trade_date": "24000110"})["data"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
