"""Preparation uses explicit synthetic fixtures, never market evidence/approval."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from finlab.common import LabError, digest, read, write
from finlab.data import init, ingest, freeze
from finlab.preparation import prepare, write_preparation


RESOURCES = {"cpus": 2, "memory_mb": 4096, "timeout_seconds": 120}


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.days = []
        day = date(2024, 1, 2)
        while len(self.days) < 40:
            if day.weekday() < 5:
                self.days.append(day)
            day += timedelta(days=1)
        self.symbol = "000001.SZ"
        self.rows = []
        self.apis = {
            "daily": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
            "trade_cal": ["exchange", "cal_date", "is_open", "pretrade_date"],
            "stk_limit": ["ts_code", "trade_date", "up_limit", "down_limit"],
            "dividend": ["ts_code", "ann_date", "div_proc", "ex_date", "stk_div", "stk_bo_rate", "stk_co_rate", "cash_div_tax"]}
        self.spec = {
            "schema_version": "finance_task_spec.v2",
            "bridge_contract": {"symbols": [self.symbol], "minimum_years": 0,
                                "endpoints": [{"api_name": n, "fields": fs} for n, fs in self.apis.items()]},
            "runtime_profile": {"frequency": "daily", "fill_policy": "NextOpen", "initial_cash": 100000,
                                "formal_segments": 7, "resources": None},
            "protocol": {"research_fraction": .3}}
        # This is a unit-test only registry-record stand-in. No real registry
        # is written or candidate approved by preparation.
        self.approval = {"candidate_id": "test-only-approved-reference", "task_id": "test-only-task", "version": 1,
                         "spec_hash": digest(self.spec), "action": "approved"}
        instrument = {"symbol": self.symbol, "asset_type": "STOCK", "multiplier": 1, "lot_size": 100,
                      "tick_size": .01, "sellable_after_days": 1, "commission_rate": .00025,
                      "min_commission": 5, "stamp_tax_rate": .0005, "transfer_fee_rate": .00001, "slippage_bps": 0}
        self.selection = {
            "real_start": self.days[0].isoformat(), "real_stop": (self.days[-1] + timedelta(days=1)).isoformat(),
            "contract_review_confirmed": True,
            "feeds": [{"api_name": "daily", "event_field": "trade_date", "close_time": "15:00",
                       "volume_multiplier": 100, "amount_multiplier": 1000}],
            "runtime": {"instruments": [instrument], "sessions": {self.symbol: [["09:30", "15:00", "continuous"]]},
                        "market_rules_status": "bound", "corporate_actions": None},
            "derive_from_frozen": {"calendar_exchange": "SSE", "execution_rules": True,
                                   "corporate_actions": {"accounting": "ex_date_cash_credit", "cash_field": "cash_div_tax",
                                                         "confirmed": True, "evidence": "synthetic test fixture only"}}}

    def frozen(self, *, missing_price=False, missing_limit=False, stock_dividend=False, late_dividend=False, synthetic=False):
        db = self.root / "source.private.sqlite"
        init(db)
        daily, limits, cal = [], [], []
        for n, day in enumerate(self.days):
            text = day.strftime("%Y%m%d")
            at = day.isoformat() + "T15:00:00+08:00"
            if not (missing_price and n == 10):
                daily.append({"ts_code": self.symbol, "trade_date": text, "open": 10, "high": 11,
                              "low": 9, "close": 10, "vol": 1000, "amount": 1000, "_available_at": at})
            if not (missing_limit and n == 10):
                limits.append({"ts_code": self.symbol, "trade_date": text, "up_limit": 11, "down_limit": 9,
                               "_available_at": day.isoformat() + "T08:00:00+08:00"})
            cal.append({"exchange": "SSE", "cal_date": text, "is_open": "1", "pretrade_date": text,
                        "_available_at": self.days[0].isoformat() + "T00:00:00+08:00"})
        dividends = [{"ts_code": self.symbol, "ann_date": self.days[1].strftime("%Y%m%d"), "div_proc": "实施",
                      "ex_date": self.days[20].strftime("%Y%m%d"), "cash_div_tax": .1,
                      "stk_div": .1 if stock_dividend else 0, "stk_bo_rate": 0, "stk_co_rate": 0,
                      "_available_at": self.days[25 if late_dividend else 1].isoformat() + "T23:59:00+08:00"}]
        for api, rows, event, keys in (
            ("daily", daily, "trade_date", ["ts_code", "trade_date"]),
            ("trade_cal", cal, "cal_date", ["exchange", "cal_date"]),
            ("stk_limit", limits, "trade_date", ["ts_code", "trade_date"]),
            ("dividend", dividends, "ann_date", ["ts_code", "ann_date"])):
            ingest(db, api, rows, {"event_field": event, "key_fields": keys, "timing_basis": "operator_supplied"})
        plan = {"real_start": self.selection["real_start"], "real_stop": self.selection["real_stop"], "symbols": [self.symbol],
                "synthetic": synthetic, "apis": {n: {"fields": fields, "coverage_confirmed": True, "replayability": "point_in_time"}
                                                for n, fields in self.apis.items()}}
        frozen = self.root / "frozen"
        freeze(db, plan, frozen)
        return frozen

    def run_prepare(self, **kwargs):
        return prepare(self.spec, self.selection, approval=self.approval, operator_resources=RESOURCES, **kwargs)

    @staticmethod
    def codes(result):
        return {x["code"] for x in result["gaps"] if x["blocking"]}

    def test_reuses_exact_freeze_and_derives_runtime_without_recollecting(self):
        frozen = self.frozen()
        result = self.run_prepare(frozen_dir=frozen)
        self.assertTrue(result["passed"], result["gaps"])
        self.assertFalse(result["collection_ready"])
        self.assertEqual(result["dataset_reuse"]["status"], "validated")
        runtime = result["binding_draft"]["runtime"]
        self.assertEqual(len(runtime["calendar"]), 40)
        self.assertEqual(len(runtime["execution_rules"]), 40)
        self.assertEqual(runtime["corporate_actions"], [{"symbol": self.symbol, "date": self.days[20].isoformat(),
                                                       "type": "dividend", "value": .1}])
        self.assertTrue(runtime["corporate_actions_verified"])

    def test_no_approval_is_not_replaced_by_a_complete_freeze(self):
        result = prepare(self.spec, self.selection, frozen_dir=self.frozen(), operator_resources=RESOURCES)
        self.assertIn("TASK_ACCEPTANCE_REQUIRED", self.codes(result))
        self.assertIsNone(result["binding_draft"]["candidate_id"])

    def test_changed_spec_cannot_reuse_old_approval(self):
        self.spec["new_research_wording"] = "changed"
        result = self.run_prepare(frozen_dir=self.frozen())
        self.assertIn("TASK_ACCEPTANCE_REQUIRED", self.codes(result))

    def test_resources_remain_unselected_without_operator_setup(self):
        result = prepare(self.spec, self.selection, approval=self.approval, frozen_dir=self.frozen())
        self.assertIn("SETUP_REQUIRED", self.codes(result))
        self.assertIsNone(result["binding_draft"]["runtime"]["resources"])
        self.assertFalse((self.root / ".finlab").exists())

    def test_model_budget_in_selection_is_rejected(self):
        self.selection["runtime"]["resources"] = RESOURCES
        self.assertIn("RESOURCE_SELECTION_FORBIDDEN", self.codes(self.run_prepare(frozen_dir=self.frozen())))

    def test_data_gap_is_not_automatically_suspension(self):
        result = self.run_prepare(frozen_dir=self.frozen(missing_price=True))
        self.assertIn("PRICE_GAP_NOT_SUSPENSION", self.codes(result))
        self.assertFalse(any(x.get("tradable") is False for x in result["binding_draft"]["runtime"]["execution_rules"]))

    def test_explicit_nontradable_evidence_resolves_absent_price(self):
        self.selection["derive_from_frozen"]["nontradable_days"] = [
            {"symbol": self.symbol, "trade_date": self.days[10].isoformat(), "evidence": "synthetic known full suspension"}]
        result = self.run_prepare(frozen_dir=self.frozen(missing_price=True))
        self.assertTrue(result["passed"], result["gaps"])
        self.assertEqual(sum(not r["tradable"] for r in result["binding_draft"]["runtime"]["execution_rules"]), 1)

    def test_missing_daily_limits_not_filled_with_ten_percent(self):
        result = self.run_prepare(frozen_dir=self.frozen(missing_limit=True))
        self.assertIn("DAILY_PRICE_LIMIT_MISSING", self.codes(result))

    def test_stock_dividend_does_not_disappear_into_cash_only_actions(self):
        result = self.run_prepare(frozen_dir=self.frozen(stock_dividend=True))
        self.assertIn("STOCK_DIVIDEND_REQUIRES_ADAPTER", self.codes(result))
        self.assertFalse(result["binding_draft"]["runtime"]["corporate_actions_verified"])

    def test_dividend_accounting_requires_explicit_acknowledgement(self):
        self.selection["derive_from_frozen"]["corporate_actions"]["confirmed"] = False
        result = self.run_prepare(frozen_dir=self.frozen())
        self.assertIn("CORPORATE_ACTION_ACCOUNTING_REQUIRED", self.codes(result))

    def test_dividend_after_exdate_cannot_be_treated_as_contemporaneous(self):
        result = self.run_prepare(frozen_dir=self.frozen(late_dividend=True))
        self.assertIn("DIVIDEND_AVAILABLE_AFTER_EVENT", self.codes(result))

    def test_exact_frozen_contract_and_range_required(self):
        frozen = self.frozen()
        self.selection["real_start"] = self.days[1].isoformat()
        result = self.run_prepare(frozen_dir=frozen)
        self.assertIn("FROZEN_RANGE_MISMATCH", self.codes(result))
        self.spec["bridge_contract"]["endpoints"][0]["fields"].remove("amount")
        result = self.run_prepare(frozen_dir=frozen)
        self.assertIn("FROZEN_CONTRACT_DIFFERS_FROM_APPROVED_SPEC", self.codes(result))

    def test_unfrozen_draft_preserves_missing_evidence_and_inclusive_api_end(self):
        result = self.run_prepare()
        self.assertFalse(result["passed"])
        self.assertIn("FROZEN_DATA_REQUIRED", self.codes(result))
        self.assertIn("VISIBILITY_EVIDENCE_REQUIRED", self.codes(result))
        daily = next(q for q in result["collection_plan"]["queries"] if q["api_name"] == "daily")
        self.assertEqual(daily["params"]["end_date"], self.days[-1].strftime("%Y%m%d"))
        self.assertIsNone(result["collection_plan"]["upstream_url"])
        self.assertFalse(result["freeze_plan"]["apis"]["daily"]["coverage_confirmed"])

    def test_no_explicit_dates_no_default_time_is_selected(self):
        self.selection["real_start"] = self.selection["real_stop"] = None
        result = self.run_prepare()
        self.assertIn("EXPLICIT_RANGE_REQUIRED", self.codes(result))
        self.assertIsNone(result["freeze_plan"]["real_start"])

    def test_tampered_snapshot_rejected(self):
        frozen = self.frozen()
        with (frozen / "snapshot.sqlite").open("ab") as f:
            f.write(b"altered")
        result = self.run_prepare(frozen_dir=frozen)
        self.assertIn("FROZEN_VERSION_CHANGED", self.codes(result))

    def test_synthetic_not_counted_as_real_delivery(self):
        result = self.run_prepare(frozen_dir=self.frozen(synthetic=True))
        self.assertIn("SYNTHETIC_DATA_NOT_REAL_DELIVERY", self.codes(result))

    def test_secret_keys_refused_before_any_write(self):
        for field in ("token", "TUSHARE_TOKEN", "api_key"):
            selection = copy.deepcopy(self.selection)
            selection[field] = "synthetic-secret-not-a-live-credential"
            with self.assertRaisesRegex(LabError, "CREDENTIAL_FORBIDDEN"):
                prepare(self.spec, selection)

    def test_private_output_has_new_folder_and_never_overwrites(self):
        result = self.run_prepare()
        folder = self.root / "prepared"
        written = write_preparation(folder, result)
        self.assertEqual(written["status"], "blocked")
        self.assertEqual({p.name for p in folder.iterdir()}, {
            "preparation.private.json", "collection.private.json", "freeze.private.json", "binding.private.json"})
        self.assertEqual(read(folder / "binding.private.json")["approved_spec_hash"], digest(self.spec))
        with self.assertRaisesRegex(LabError, "ALREADY_EXISTS"):
            write_preparation(folder, result)


if __name__ == "__main__":
    unittest.main()
