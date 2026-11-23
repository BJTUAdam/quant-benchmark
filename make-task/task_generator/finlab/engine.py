"""Trusted AKQuant adapter. Submitted code is never imported here."""
from __future__ import annotations
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import uuid

import akquant as aq
import pandas as pd

from .common import LabError, canonical, file_hash, read, write, textfile
from .data import FrozenView, db_open
from .disclosure import AuditLog, check_policy, policy_scope
from .server import query_server
from .temporal import SH, TimeMap, iso, parse_time

RUN = {}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rpc(event):
    raw = canonical(event).encode()
    req = Request(RUN["config"]["worker_url"], data=raw,
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["FINLAB_RPC_TOKEN"]})
    try:
        with urlopen(req, timeout=RUN["config"]["callback_timeout"]) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000: raise LabError("STRATEGY_REPLY_TOO_LARGE")
        reply = json.loads(raw)
        if (reply.get("api_version") != "finlab.strategy.v2" or not isinstance(reply.get("orders"), list)
                or len(reply["orders"]) > 256 or not isinstance(reply.get("memory"), dict)):
            raise LabError("STRATEGY_PROTOCOL_ERROR")
        canonical(reply)  # reject NaN/Infinity, including nested memory
        return reply
    except (OSError, URLError, HTTPError, ValueError, KeyError, TypeError):
        raise LabError("STRATEGY_RPC_FAILED") from None


def portfolio(strategy):
    return {"cash": float(strategy.cash), "equity": float(strategy.equity),
            "positions": {k: float(v) for k, v in strategy.positions.items()},
            "available_positions": {k: float(strategy.get_available_position(k)) for k in strategy.positions},
            "open_orders": [{"id": RUN["view"].mapping.identifier(o.id), "symbol": o.symbol, "quantity": float(o.quantity),
                             "filled_quantity": float(o.filled_quantity), "side": str(o.side)} for o in strategy.get_open_orders()]}


class ControlledMatcher:
    """Next-open execution with daily limits, unit, liquidity and fee rules.

    AKQuant owns account changes, T+1, orders and margin checks. This adapter
    supplies the permitted fill price/quantity; no user-provided fill is trusted.
    """
    def match(self, order, event, instrument, bar_index):
        if (order.symbol, int(event.timestamp)) in RUN["settlement_marks"]:
            return None  # valuation-only event; pending orders survive to real market bars
        if not hasattr(event, "open") or float(event.volume) <= 0:
            return None
        rule = RUN["execution"].get((order.symbol, int(event.timestamp)))
        if rule is None or not rule.get("tradable", False): return None
        cfg = RUN["instruments"][order.symbol]
        buy = "buy" in str(order.side).lower()
        price = float(event.open) * (1 + cfg["slippage_bps"] / 10000 * (1 if buy else -1))
        tick = cfg["tick_size"]
        price = (math.ceil(price / tick - 1e-9) if buy else math.floor(price / tick + 1e-9)) * tick
        up, down = rule.get("up_limit"), rule.get("down_limit")
        # Conservative: orders on the blocked side at the daily boundary wait.
        if buy and up is not None and price >= up - tick / 10: return None
        if not buy and down is not None and price <= down + tick / 10: return None
        if up is not None and price > up or down is not None and price < down: return None
        if order.price is not None:
            if buy and price > float(order.price) or not buy and price < float(order.price): return None
        remaining = float(order.quantity) - float(order.filled_quantity)
        key = (order.symbol, int(event.timestamp))
        used = RUN["used_volume"].get(key, 0)
        cap = max(0, float(event.volume) * RUN["profile"].get("volume_limit_pct", .10) - used)
        lot = cfg["lot_size"]
        quantity = min(remaining, math.floor(cap / lot) * lot)
        if quantity <= 0: return None
        prior = float(order.filled_quantity)
        average = float(order.average_filled_price or 0)
        order.filled_quantity = prior + quantity
        order.average_filled_price = (average * prior + price * quantity) / (prior + quantity)
        order.status = aq.OrderStatus.Filled if quantity >= remaining else aq.OrderStatus.PartiallyFilled
        RUN["used_volume"][key] = used + quantity
        return aq.Trade(id=uuid.uuid4().hex, order_id=order.id, symbol=order.symbol, side=order.side,
                        quantity=quantity, price=price, commission=0,
                        timestamp=event.timestamp, bar_index=bar_index)


class RemoteStrategy(aq.Strategy):
    """Stable platform-owned class is the only Python Strategy in checkpoints."""
    def setup(self):
        self.slice = {}
        self.slice_ts = None
        self.virtual_now = RUN["view"].mapping.shift(iso(RUN["config"]["window"]["start"]), "datetime")

    def on_start(self): self.setup()
    def on_resume(self): self.setup()

    def notify(self, kind, *, allow_orders=True, **kwargs):
        reply = rpc({"kind": kind, "now": self.virtual_now, "portfolio": portfolio(self), **kwargs})
        RUN["memory"] = reply["memory"]
        if reply["orders"] and not allow_orders:
            raise LabError("ORDERS_OUTSIDE_BAR_CALLBACK")
        for order in reply["orders"]:
            self.place(order)

    def place(self, order):
        if not isinstance(order, dict): raise LabError("INVALID_ORDER")
        if set(order) == {"cancel"}:
            active = {RUN["view"].mapping.identifier(o.id): o.id for o in self.get_open_orders()}
            if str(order["cancel"]) not in active:
                raise LabError("ORDER_NOT_ACTIVE")
            self.cancel_order(active[str(order["cancel"])]); return
        if set(order) - {"symbol", "quantity", "side", "limit_price", "tif"}: raise LabError("INVALID_ORDER")
        symbol, quantity = order.get("symbol"), order.get("quantity")
        if symbol not in RUN["instruments"] or not finite(quantity) or quantity <= 0:
            raise LabError("INVALID_ORDER")
        cfg = RUN["instruments"][symbol]
        if abs(quantity / cfg["lot_size"] - round(quantity / cfg["lot_size"])) > 1e-8: raise LabError("ORDER_LOT_SIZE")
        if order.get("side") not in {"buy", "sell"} or order.get("tif", "GTC") not in {"DAY", "GTC"}: raise LabError("INVALID_ORDER")
        price = order.get("limit_price")
        if price is not None and (not finite(price) or price <= 0): raise LabError("INVALID_LIMIT_PRICE")
        if cfg["asset_type"] != "FUTURES" and order["side"] == "sell" and quantity > self.get_available_position(symbol):
            raise LabError("SHORT_OR_T_PLUS_ONE_VIOLATION")
        tif = aq.TimeInForce.GTC if order.get("tif", "GTC") == "GTC" else aq.TimeInForce.Day
        method = self.buy if order["side"] == "buy" else self.sell
        method(symbol=symbol, quantity=quantity, price=price, time_in_force=tif)

    def on_bar(self, bar):
        timestamp = int(bar.timestamp)
        if (bar.symbol, timestamp) in RUN["settlement_marks"]:
            return
        real_at = pd.Timestamp(timestamp, unit="ns", tz="UTC").tz_convert("Asia/Shanghai").isoformat()
        RUN["view"].clock = iso(real_at)
        self.virtual_now = RUN["view"].mapping.shift(iso(real_at), "datetime")
        if self.slice_ts != timestamp:
            if self.slice: raise LabError("INCOMPLETE_SYNCHRONOUS_SLICE")
            self.slice_ts = timestamp
        self.slice[bar.symbol] = {"open": float(bar.open), "high": float(bar.high), "low": float(bar.low),
                                  "close": float(bar.close), "volume": float(bar.volume), "now": self.virtual_now}
        if len(self.slice) == RUN["counts"][timestamp]:
            current = self.slice
            self.slice = {}
            self.notify("bars", bars=current)

    def on_order(self, order):
        # Match events can occur before the new on_bar callback. Advance only
        # to the actual event timestamp (never the next segment boundary).
        at = getattr(order, "updated_at", None)
        if isinstance(at, int) and at > 0:
            real = pd.Timestamp(at, unit="ns", tz="UTC").tz_convert("Asia/Shanghai").isoformat()
            if iso(real) > RUN["view"].clock:
                RUN["view"].clock = iso(real)
                self.virtual_now = RUN["view"].mapping.shift(iso(real), "datetime")
        if not hasattr(self, "virtual_now"): return
        self.notify("order", allow_orders=False, event={"id": RUN["view"].mapping.identifier(order.id), "symbol": order.symbol,
                    "status": str(order.status), "quantity": float(order.quantity), "filled_quantity": float(order.filled_quantity)})

    def on_trade(self, trade):
        real = pd.Timestamp(int(trade.timestamp), unit="ns", tz="UTC").tz_convert("Asia/Shanghai").isoformat()
        RUN["view"].clock = iso(real)
        self.virtual_now = RUN["view"].mapping.shift(iso(real), "datetime")
        self.notify("trade", allow_orders=False, event={"symbol": trade.symbol, "quantity": float(trade.quantity),
                    "price": float(trade.price), "side": str(trade.side), "now": self.virtual_now})

    def on_stop(self): pass  # explicit finalization below must not be swallowed by AKQuant


def load_bars(config, view):
    profile = config["binding"]["runtime"]
    start, stop = iso(config["window"]["start"]), iso(config["window"]["stop"])
    instruments = {i["symbol"]: i for i in profile["instruments"]}
    rules = {(r["symbol"], r["trade_date"]): r for r in profile.get("execution_rules", [])}
    frames, execution, marks, counts = {}, {}, set(), Counter()
    with closing(db_open(view.folder / "snapshot.sqlite")) as con:
        for feed in config["binding"]["feeds"]:
            sql = """select body,available_at from (select body,available_at,row_number() over
                     (partition by row_key order by available_at asc) rn from records where api=?) where rn=1"""
            for body, available in con.execute(sql, (feed["api_name"],)):
                row = json.loads(body); symbol = row.get("ts_code")
                if symbol not in instruments: continue
                event = parse_time(row[feed["event_field"]])
                if feed.get("close_time"):
                    h, m = map(int, feed["close_time"].split(":")); event = event.replace(hour=h, minute=m)
                at = iso(event)
                if not start <= at < stop: continue
                if available > at: raise LabError("FEED_NOT_VISIBLE_AT_EVENT", "调整采集可见性证据或事件时间")
                for k in ("open", "high", "low", "close", "vol"):
                    if not finite(row.get(k)) or row[k] < 0: raise LabError("INVALID_MARKET_BAR")
                if not 0 < row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                    raise LabError("INVALID_OHLC")
                trading_day = str(row.get("trade_date", event.date().isoformat()))
                trading_day = parse_time(trading_day).date().isoformat()
                rule = rules.get((symbol, trading_day))
                if rule is None: raise LabError("EXECUTION_RULE_MISSING", symbol)
                cfg = instruments[symbol]
                if cfg["asset_type"] in {"STOCK", "FUND"} and not cfg.get("no_daily_price_limit", False) and not {"up_limit", "down_limit"} <= rule.keys():
                    raise LabError("DAILY_PRICE_LIMIT_MISSING")
                ts = pd.Timestamp(event).value
                execution[(symbol, ts)] = rule
                counts[ts] += 1
                record = {"date": event, **{k: float(row[k]) for k in ("open", "high", "low", "close")},
                          "volume": float(row["vol"]) * feed["volume_multiplier"]}
                frames.setdefault(symbol, []).append(record)
                if cfg["asset_type"] == "FUTURES" and profile.get("settlement_mode") == "settlement_mark":
                    settle = row.get("settle")
                    if not finite(settle) or settle <= 0: raise LabError("SETTLEMENT_PRICE_MISSING")
                    settle_at = event + timedelta(seconds=1)
                    st = pd.Timestamp(settle_at).value
                    # AKQuant treats zero-volume bars as suspensions and rejects
                    # queued orders before custom matching. A positive sentinel
                    # avoids that path; ControlledMatcher never fills this mark.
                    frames[symbol].append({"date": settle_at, "open": settle, "high": settle, "low": settle, "close": settle, "volume": 1.0})
                    marks.add((symbol, st))
    if not frames: raise LabError("NO_BARS_IN_WINDOW")
    return {k: pd.DataFrame(v).sort_values("date") for k, v in frames.items()}, execution, marks, counts


def safe_frame(frame, mapping):
    if isinstance(frame, pd.Series): frame = frame.to_frame()
    frame = frame.copy().reset_index()
    for name in frame.columns:
        def clean(x):
            if x is None or not isinstance(x, (list, dict)) and pd.isna(x): return None
            if name == "reject_reason" and isinstance(x, str):
                return "ENGINE_ORDER_REJECTED" if x else ""
            if name in {"id", "order_id", "owner_strategy_id", "execution_id", "trade_id"}:
                return mapping.identifier(x)
            if name in {"timestamp", "created_at", "updated_at", "entry_time", "exit_time", "created_at_iso", "updated_at_iso"} and str(x).isdigit():
                digits = len(str(x))
                kind = {10: "epoch_s", 13: "epoch_ms", 16: "epoch_us", 19: "epoch_ns"}.get(digits)
                if kind:
                    return mapping.shift(int(x), kind)
                if digits == 8: return mapping.shift(str(x), "date")
                raise LabError("RESULT_TIMESTAMP_PRECISION")
            if isinstance(x, (pd.Timestamp, datetime, date)):
                return mapping.shift(str(x.isoformat()), "datetime" if isinstance(x, (datetime, pd.Timestamp)) else "date")
            if isinstance(x, pd.Timedelta): return str(x)
            if isinstance(x, str) and name not in {"symbol", "ts_code", "name"}:
                try: return mapping.text(x)
                except LabError:
                    raise LabError("RESULT_FIELD_TIME_RULE", str(name)) from None
            if hasattr(x, "item"): return x.item()
            return x
        frame[name] = frame[name].map(clean)
    return frame


def benchmark_curve(config, view, equity):
    """Align a frozen, visible index close to each cumulative account mark."""
    cfg = config["binding"]["runtime"].get("benchmark")
    if not cfg: return None
    rows = {}
    with closing(db_open(view.folder / "snapshot.sqlite")) as con:
        for body, available in con.execute("select body,available_at from records where api=?", (cfg["api_name"],)):
            row = json.loads(body)
            if row.get("ts_code") == cfg["symbol"]:
                rows.setdefault(str(row["trade_date"]), []).append((available, row["close"]))
    records, base = [], None
    for point in equity.to_dict("records"):
        real = view.mapping.shift(str(point["timestamp"]), "datetime", inverse=True)
        day = parse_time(real).strftime("%Y%m%d")
        visible = sorted((at, price) for at, price in rows.get(day, []) if at <= iso(real))
        if not visible or not finite(visible[-1][1]) or visible[-1][1] <= 0:
            raise LabError("BENCHMARK_PRICE_NOT_VISIBLE", "基准应覆盖全部账户估值日期并有可见性依据")
        price = float(visible[-1][1])
        if base is None: base = price
        records.append({"timestamp": point["timestamp"], "symbol": cfg["symbol"], "close": price,
                        "nav": price / base, "equity": config["binding"]["runtime"]["initial_cash"] * price / base})
    return pd.DataFrame(records)


def main():
    import faulthandler
    faulthandler.enable()  # crash diagnostics stay in the operator's private log
    config = read("/input/job.private.json")
    binding, profile = config["binding"], config["binding"]["runtime"]
    view = FrozenView("/frozen", clock=config["window"]["start"])
    policy = config["disclosure_policy"]
    check_policy(policy, binding["windows"], view.manifest, policy["episode_id"], binding["disclosure_policy_hash"])
    view.scope = policy_scope(policy, config["disclosure_stage"], config["job_id"], config["submission_hash"])
    view.audit = AuditLog("/out/bridge_audit.private.sqlite")
    view.audit.register_secret(os.environ.get("FINLAB_RPC_TOKEN"))
    view.assert_ready()
    data, execution, marks, counts = load_bars(config, view)
    RUN.update(config=config, profile=profile, view=view, execution=execution, settlement_marks=marks, counts=counts,
               instruments={i["symbol"]: i for i in profile["instruments"]}, used_volume={},
               memory=read("/input/memory.json") if config["resume"] else {})
    server = query_server(view, os.environ["MARKET_BRIDGE_TOKEN"], "0.0.0.0", 8766)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for n in range(40):
        try:
            rpc({"kind": "ping"})
            break
        except LabError:
            if n == 39: raise
            time.sleep(.25)
    initial_portfolio = (read("/input/account.json") if config["resume"] else
                         {"cash": profile["initial_cash"], "equity": profile["initial_cash"], "positions": {}, "available_positions": {}, "open_orders": []})
    initialized = rpc({"kind": "initialize", "now": view.mapping.shift(iso(config["window"]["start"]), "datetime"),
                       "portfolio": initial_portfolio, "memory": RUN["memory"]})
    if initialized["orders"]: raise LabError("ORDERS_OUTSIDE_BAR_CALLBACK")
    RUN["memory"] = initialized["memory"]
    inst = []
    for item in profile["instruments"]:
        keys = {"symbol", "asset_type", "multiplier", "margin_ratio", "tick_size", "lot_size", "sellable_after_days", "expiry_date",
                "commission_rate", "min_commission", "stamp_tax_rate", "transfer_fee_rate"}
        values = {k: v for k, v in item.items() if k in keys}
        if values.get("expiry_date"): values["expiry_date"] = parse_time(values["expiry_date"]).date()
        inst.append(aq.InstrumentConfig(**values))
    risk = aq.config.RiskConfig(**profile.get("risk", {"account_mode": "margin" if any(i["asset_type"] == "FUTURES" for i in profile["instruments"]) else "cash",
                                                    "enable_short_sell": True, "financing_rate_annual": 0, "borrow_rate_annual": 0}))
    cfg = aq.BacktestConfig(strategy_config=aq.StrategyConfig(initial_cash=profile["initial_cash"], exit_on_last_bar=False, risk=risk,
                           commission_rate=0, stamp_tax_rate=0, transfer_fee_rate=0, min_commission=0,
                           volume_limit_pct=0, slippage={"type": "zero"}),
                           instruments=list(RUN["instruments"]), instruments_config=inst, timezone="Asia/Shanghai", show_progress=False)
    kwargs = {"config": cfg, "show_progress": False, "fill_policy": aq.NextOpen(),
              "t_plus_one": any(i["sellable_after_days"] == 1 for i in profile["instruments"]),
              "trading_calendar": profile["calendar"], "sessions": profile["sessions"], "symbols": list(RUN["instruments"])}
    # AKQuant 0.3.76 does not expose matcher re-registration on warm start.
    # This pinned adapter injects platform rules at engine construction/restore,
    # before any event is executed. No submitted object participates.
    import importlib
    engine_module = importlib.import_module("akquant.backtest.engine")
    checkpoint_module = importlib.import_module("akquant.checkpoint")
    original_engine, original_load = engine_module.Engine, checkpoint_module.load_checkpoint
    def configure(engine):
        for kind in (aq.AssetType.Stock, aq.AssetType.Fund, aq.AssetType.Futures):
            engine.register_custom_matcher(kind, ControlledMatcher())
        for action in profile["corporate_actions"]:
            d = parse_time(action["date"]).date()
            if parse_time(config["window"]["start"]).date() <= d < parse_time(config["window"]["stop"]).date():
                typ = aq.CorporateActionType.Dividend if action["type"] == "dividend" else aq.CorporateActionType.Split
                engine.add_corporate_action(aq.CorporateAction(action["symbol"], d, typ, action["value"]))
        return engine
    def factory(*args, **kwargs): return configure(original_engine(*args, **kwargs))
    def load(*args, **kwargs):
        engine, strategy = original_load(*args, **kwargs)
        return configure(engine), strategy
    engine_module.Engine, checkpoint_module.load_checkpoint = factory, load
    try:
        if config["resume"]:
            result = aq.run_from_checkpoint("/input/checkpoint.pkl", data=data, **kwargs)
        else:
            if profile["frequency"] == "daily":
                kwargs["freq"] = "1d"
            result = aq.run_backtest(data=data, strategy=RemoteStrategy, **kwargs)
    finally:
        engine_module.Engine, checkpoint_module.load_checkpoint = original_engine, original_load
    result.strategy.notify("finish", allow_orders=False)
    # Only the platform class exists in this pickle. It never contains a submitted object.
    aq.save_checkpoint(result.engine, result.strategy, "/out/checkpoint.pkl", config=result)
    write("/out/memory.json", RUN["memory"])
    write("/out/account.private.json", portfolio(result.strategy))
    out = Path("/out/public"); out.mkdir()
    result.orders_df.to_csv("/out/orders.private.csv", index=False)
    orders = result.orders_df.copy()
    # The custom matcher cannot write Order.updated_at. Its authoritative fill
    # time is the engine's execution ledger, not the original creation time.
    executions = result.executions_df
    if not executions.empty and {"order_id", "timestamp"} <= set(executions.columns) and "id" in orders:
        fill_times = executions.assign(fill_time=pd.to_datetime(executions["timestamp"], utc=True)).groupby("order_id")["fill_time"].max()
        for index, row in orders.iterrows():
            if row["id"] in fill_times.index:
                at = fill_times[row["id"]]
                orders.loc[index,"updated_at"] = at.tz_convert("Asia/Shanghai")
                if "updated_at_iso" in orders: orders.loc[index,"updated_at_iso"] = at.isoformat()
                if "duration" in orders: orders.loc[index,"duration"] = at - pd.Timestamp(row["created_at"])
    combined = {}
    for name, frame in (("equity", result.equity_curve), ("orders", orders), ("trades", result.executions_df)):
        clean = safe_frame(frame, view.mapping).drop(columns=["index"], errors="ignore")
        previous = Path("/input") / (name + ".csv")
        if previous.is_file():
            try: clean = pd.concat([pd.read_csv(previous), clean], ignore_index=True)
            except pd.errors.EmptyDataError: pass
        key = "timestamp" if name == "equity" else "id"
        if key in clean: clean = clean.drop_duplicates(subset=[key], keep="last")
        if name == "equity": clean = clean.sort_values("timestamp")
        clean.to_csv(out / (name + ".csv"), index=False)
        combined[name] = clean
    eq = combined["equity"]
    # Public virtual years may exceed pandas' nanosecond range. Statistics use
    # the private, real trading clock; only numeric metrics leave this process.
    real_index = [parse_time(view.mapping.shift(str(at), "datetime", inverse=True)) for at in eq["timestamp"]]
    curve = pd.Series(eq["equity"].to_numpy(), index=pd.DatetimeIndex(real_index))
    daily = curve.groupby(curve.index.date).last()
    initial = profile["initial_cash"]
    returns = daily.pct_change(); returns.iloc[0] = daily.iloc[0] / initial - 1
    returns = returns.dropna()
    metrics = {"total_return": float(curve.iloc[-1] / initial - 1),
               "max_drawdown": float((curve / curve.cummax().clip(lower=initial) - 1).min()),
               "daily_sharpe": float(returns.mean() / returns.std() * math.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else None,
               "final_equity": float(curve.iloc[-1]), "synthetic": binding["synthetic"], "formal_segment": config["segment"],
               "settlement_mode": profile.get("settlement_mode"), "scope": "cumulative_checkpoint_history"}
    benchmark = benchmark_curve(config, view, eq)
    if benchmark is not None:
        benchmark.to_csv(out / "benchmark.csv", index=False)
        metrics["benchmark_total_return"] = float(benchmark["nav"].iloc[-1] - 1)
        metrics["excess_return"] = metrics["total_return"] - metrics["benchmark_total_return"]
        metrics["benchmark_basis"] = "first_visible_close_in_account_window"
    write(out / "metrics.json", metrics)
    write(out / "run_manifest.json", {"schema_version": "finlab.run.v2", "job_id": config["job_id"], "engine": "AKQuant 0.3.76",
        "segment": config["segment"], "research": config["research"], "submission_hash": config["submission_hash"],
        "dataset_id": binding["dataset_id"], "synthetic": binding["synthetic"], "time_domain": "virtual",
        "resource_budget": profile["resources"],
        "window": {k: view.mapping.shift(v, "date") for k, v in config["window"].items() if k in {"start", "stop"}},
        "multi_leg_execution": "per_leg", "identity_policy": "real_names_and_codes"})
    server.shutdown(); server.server_close()
    view.audit.checkpoint()
    hashes = {p.relative_to(Path("/out")).as_posix(): file_hash(p) for p in Path("/out").rglob("*") if p.is_file()}
    write("/out/success.private.json", {"status": "ready_to_commit", "hashes": hashes})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("TRUSTED_ENGINE_FAILED", type(exc).__name__, str(exc), flush=True)
        raise
