"""Deterministic SYNTHETIC integration fixture, never financial evidence."""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from .common import write, textfile, LabError
from .data import init, ingest, freeze
from .tasks import templates
from .temporal import DEFAULT_YEAR_OFFSET


def build(folder, asset="STOCK", *, resources=None):
    folder = Path(folder).resolve()
    if folder.exists(): raise LabError("ALREADY_EXISTS")
    folder.mkdir(parents=True)
    symbol = {"STOCK": "000001.SZ", "FUND": "510300.SH", "FUTURES": "M2509.DCE"}[asset]
    api = {"STOCK": "daily", "FUND": "fund_daily", "FUTURES": "fut_daily"}[asset]
    days = []
    d = date(2024, 1, 2)
    while len(days) < 40:
        if d.weekday() < 5: days.append(d)
        d += timedelta(days=1)
    rows, rules = [], []
    for n, day in enumerate(days):
        price = 10 + n * .05
        row = {"ts_code": symbol, "trade_date": day.strftime("%Y%m%d"), "open": price,
               "high": price + .2, "low": price - .2, "close": price + .05, "vol": 10000,
               "_available_at": day.isoformat() + "T15:00:00+08:00"}
        if asset == "FUTURES": row["settle"] = price + .04
        rows.append(row)
        rules.append({"symbol": symbol, "trade_date": day.isoformat(), "tradable": True,
                      "up_limit": round(price * 1.1, 2), "down_limit": round(price * .9, 2)})
    source = folder / "source.private.sqlite"
    init(source)
    ingest(source, api, rows, {"timing_basis": "operator_supplied", "key_fields": ["ts_code", "trade_date"], "event_field": "trade_date"})
    index_rows = [{"ts_code": "000300.SH", "trade_date": day.strftime("%Y%m%d"), "close": 3000 + n,
                   "_available_at": day.isoformat() + "T15:00:00+08:00"} for n, day in enumerate(days)]
    ingest(source, "index_daily", index_rows, {"timing_basis": "operator_supplied", "key_fields": ["ts_code", "trade_date"], "event_field": "trade_date"})
    freeze(source, {"year_offset": DEFAULT_YEAR_OFFSET, "synthetic": True, "symbols": [symbol, "000300.SH"], "real_start": days[0].isoformat(),
                    "real_stop": (days[-1] + timedelta(days=1)).isoformat(),
                    "apis": {api: {"fields": [k for k in rows[0] if not k.startswith("_")], "coverage_confirmed": True, "replayability": "point_in_time"},
                             "index_daily": {"fields": ["ts_code", "trade_date", "close"], "coverage_confirmed": True, "replayability": "point_in_time"}}}, folder / "frozen")
    instrument = {"symbol": symbol, "asset_type": asset, "multiplier": 10 if asset == "FUTURES" else 1,
        "lot_size": 1 if asset == "FUTURES" else 100, "tick_size": .01, "sellable_after_days": 1 if asset == "STOCK" else 0,
        "commission_rate": .00025, "min_commission": 0 if asset != "STOCK" else 5,
        "stamp_tax_rate": .0005 if asset == "STOCK" else 0, "transfer_fee_rate": .00001 if asset == "STOCK" else 0,
        "slippage_bps": 5, "margin_ratio": .1 if asset == "FUTURES" else 1,
        "expiry_date": "2025-09-15" if asset == "FUTURES" else None}
    binding = {"schema_version": "finlab.binding.v2", "frozen_dir": str(folder / "frozen"), "candidate_id": None,
        "research_fraction": .3, "feeds": [{"api_name": api, "event_field": "trade_date", "close_time": "15:00",
                                           "volume_multiplier": 1 if asset == "FUTURES" else 100, "amount_multiplier": 1000}],
        "runtime": {"frequency": "daily", "fill_policy": "NextOpen", "market_rules_status": "bound",
            "initial_cash": 1000000, "instruments": [instrument], "calendar": [d.isoformat() for d in days],
            "sessions": {symbol: [["09:00", "16:00", "continuous"]]}, "execution_rules": rules,
            "corporate_actions": [], "corporate_actions_verified": True, "settlement_mode": "settlement_mark" if asset == "FUTURES" else None,
            "benchmark": {"api_name": "index_daily", "symbol": "000300.SH"},
            "resources": resources, "resource_status": "explicit_synthetic_fixture" if resources else "requires_human_setup"}}
    write(folder / "binding.private.json", binding)
    for name, value in templates().items(): textfile(folder / "submission" / name, value)
    # Test-only buy-and-hold is intentionally separate from task package templates.
    textfile(folder / "submission" / "strategy.py", f'''from finlab.sdk import Strategy as BaseStrategy
class Strategy(BaseStrategy):
    def on_bars(self, ctx):
        if not ctx.memory.get("sent"):
            ctx.order({symbol!r}, {1 if asset == 'FUTURES' else 100}, side="buy")
            ctx.memory["sent"] = True
        ctx.memory["bars_seen"] = ctx.memory.get("bars_seen", 0) + 1
''')
    textfile(folder / "README.md", "# 合成集成测试\n\n全部价格、规则和交易日为程序构造；不表示真实行情、实际权限或投资表现。\n")
    return {"status": "synthetic_fixture_created", "folder": str(folder), "asset": asset}
