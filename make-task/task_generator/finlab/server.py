from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import uuid
from .common import LabError


def query_server(view, token, host="127.0.0.1", port=8766):
    if not token:
        raise LabError("BRIDGE_TOKEN_REQUIRED")
    view.assert_ready()
    view.register_secret(token)
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "FinlabBridge/2", ""
        def log_message(self, *args):
            pass
        def send(self, status, obj):
            raw = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            self.send(200 if self.path == "/health" else 404, {"status": "scoped_frozen_bridge", "upstream_access": False})
        def do_POST(self):
            req = {}
            try:
                if self.path not in {"/", "/query"}:
                    raise LabError("NO_ROUTE")
                size = self.headers.get("Content-Length", "")
                if not size.isdigit() or not 0 < int(size) <= 65536:
                    raise LabError("REQUEST_TOO_LARGE")
                req = json.loads(self.rfile.read(int(size)))
                if not isinstance(req, dict) or set(req) - {"api_name", "token", "params", "fields"}:
                    raise LabError("INVALID_REQUEST")
                supplied = req.get("token")
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), token.encode()):
                    raise LabError("FORBIDDEN")
                self.send(200, view.query(req.get("api_name"), req.get("params"), req.get("fields", "")))
            except LabError as exc:
                self.fail(exc, req)
            except (ValueError, TypeError, KeyError):
                self.fail(LabError("INVALID_REQUEST", "请求格式无效"), req)
            except Exception:
                self.fail(LabError("QUERY_FAILED", "查询失败"), req)
        def fail(self, exc, req):
            if not exc.request_id:
                try:
                    safe = req if isinstance(req, dict) else {}
                    exc.request_id = view.audit_rejection(safe.get("api_name"), safe.get("params"), exc.code)
                except LabError as audit_exc:
                    exc = audit_exc
            request_id = exc.request_id or uuid.uuid4().hex
            status = (503 if exc.code == "BRIDGE_AUDIT_UNAVAILABLE" else
                      403 if exc.code in {"FORBIDDEN", "TIME_RANGE_EXCEEDED"} else
                      500 if exc.code == "QUERY_FAILED" else 400)
            self.send(status, {"code": -1, "error_code": exc.code,
                               "msg": exc.message or "查询被拒绝：" + exc.code, "request_id": request_id})
    class Server(ThreadingHTTPServer):
        # Close waits for active requests, so an engine cannot hash/commit an
        # audit database which a late query is still updating.
        daemon_threads = False
        block_on_close = True
        def get_request(self):
            sock, addr = super().get_request()
            sock.settimeout(15)
            return sock, addr
    return Server((host, port), Handler)
