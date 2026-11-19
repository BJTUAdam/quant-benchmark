"""Untrusted-side platform strategy API v2. No engine or private data access."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
from .common import LabError, canonical

API_VERSION = "finlab.strategy.v2"


class Context:
    def __init__(self, event, params, memory):
        self.now = event.get("now")
        self.bars = event.get("bars", {})
        self.portfolio = event.get("portfolio", {})
        self.params, self.memory = params, memory
        self.orders = []

    def query(self, api_name, fields="", **params):
        from .tushare import pro_api
        return pro_api().query(api_name, fields, **params)

    def order(self, symbol, quantity, *, side="buy", limit_price=None, tif="GTC"):
        """quantity is shares for STOCK/FUND and contracts for FUTURES."""
        self.orders.append({"symbol": symbol, "quantity": quantity, "side": side,
                            "limit_price": limit_price, "tif": tif})

    def cancel(self, order_id):
        self.orders.append({"cancel": str(order_id)})


class Strategy:
    api_version = API_VERSION
    def initialize(self, ctx): pass
    def on_bars(self, ctx): pass
    def on_order(self, ctx, event): pass
    def on_trade(self, ctx, event): pass
    def on_finish(self, ctx): pass


def launch(strategy_file="strategy.py", params_file="params.json"):
    """backtest.py entry: host invocation prints usage; Docker runs RPC worker."""
    if os.environ.get("FINLAB_WORKER") != "1":
        print(json.dumps({"api_version": API_VERSION, "status": "use_platform_runner",
                          "command": "finlab backtest run --episode EPISODE --submission . --research"}))
        return
    path = Path("/submission") / strategy_file
    spec = importlib.util.spec_from_file_location("submitted_strategy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strategy = module.Strategy()
    if getattr(strategy, "api_version", None) != API_VERSION:
        raise LabError("STRATEGY_API_VERSION_MISMATCH")
    params = json.loads((Path("/submission") / params_file).read_text(encoding="utf-8-sig"))
    token = os.environ["FINLAB_RPC_TOKEN"]
    state = {"memory": {}, "initialized": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            try:
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    raise LabError("FORBIDDEN")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8_000_000: raise LabError("RPC_SIZE")
                event = json.loads(self.rfile.read(size))
                kind = event["kind"]
                if kind == "ping":
                    raw = canonical({"api_version": API_VERSION, "orders": [], "memory": state["memory"]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
                    return
                if kind == "initialize":
                    state["memory"] = event.get("memory", {})
                ctx = Context(event, params, state["memory"])
                if kind == "initialize":
                    strategy.initialize(ctx); state["initialized"] = True
                elif not state["initialized"]:
                    raise LabError("NOT_INITIALIZED")
                elif kind == "bars": strategy.on_bars(ctx)
                elif kind == "order": strategy.on_order(ctx, event.get("event", {}))
                elif kind == "trade": strategy.on_trade(ctx, event.get("event", {}))
                elif kind == "finish": strategy.on_finish(ctx)
                else: raise LabError("UNKNOWN_EVENT")
                if not isinstance(ctx.memory, dict): raise LabError("MEMORY_MUST_BE_OBJECT")
                state["memory"] = ctx.memory
                reply = {"api_version": API_VERSION, "orders": ctx.orders, "memory": ctx.memory}
                raw = canonical(reply).encode()
                if len(raw) > 2_000_000: raise LabError("REPLY_TOO_LARGE")
                self.send_response(200)
            except Exception:
                # Source exceptions and stdout are private operator diagnostics.
                raw = b'{"error_code":"STRATEGY_CALLBACK_FAILED"}'
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
