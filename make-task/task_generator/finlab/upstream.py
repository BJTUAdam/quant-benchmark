from __future__ import annotations
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .common import LabError, ROOT, now, read


def upstream_settings():
    """Local endpoint preference contains no credential; environment overrides it."""
    from pathlib import Path
    path = Path(os.environ.get('FINLAB_UPSTREAM_CONFIG', ROOT / '.finlab/upstream.private.json'))
    config = read(path) if path.is_file() else {}
    if not isinstance(config, dict) or set(config) - {'url', 'token_env'}:
        raise LabError('INVALID_UPSTREAM_CONFIG')
    variable = config.get('token_env', 'TUSHARE_TOKEN')
    if not isinstance(variable, str) or not variable:
        raise LabError('INVALID_UPSTREAM_CONFIG')
    return {'url': os.environ.get('TUSHARE_UPSTREAM_URL') or config.get('url') or 'https://api.tushare.pro',
            'token_env': variable, 'token_present': bool(os.environ.get(variable))}


def official_pro():
    """Official SDK object with a strict protocol transport for its query method.

    SDK 1.4.29 can return an empty frame on HTTP errors. Routing the object's
    query through the existing strict transport preserves status/code/message.
    Standard dynamic calls and ts.pro_bar(api=pro) keep using this same object.
    """
    config = upstream_settings()
    client = TushareUpstream(config['url'])  # validates address and credential presence
    try:
        import tushare as ts
    except ImportError:
        raise LabError('TUSHARE_SDK_MISSING', '安装项目可选依赖：pip install -e .[tushare]') from None
    pro = ts.pro_api(client.token, timeout=client.timeout)
    pro._DataApi__http_url = client.url
    def query(api_name, fields='', **params):
        import pandas as pd
        response = client.query(api_name, params, fields)
        return pd.DataFrame(response['rows'], columns=response['fields'])
    pro.query = query
    return pro


def safe_upstream_message(message, token):
    """Operator-only diagnostics. Never use this to respond to a strategy."""
    import re
    value = str(message).replace(str(token), '[REDACTED]') if token else str(message)
    value = re.sub(r'(?i)(?:bearer\s+|(?:token|api[_-]?key|authorization)[\s:=\"\']+)[^\s,;\"\'}]+', '[REDACTED]', value)
    value = re.sub(r'(?:sk-|qdl_)[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
    return value[:1000]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TushareUpstream:
    """Operator-only transport. Never sends credentials through redirects."""
    def __init__(self, url=None, token=None, *, timeout=30, retries=2):
        settings = upstream_settings()
        self.url = url or settings['url']
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LabError("INVALID_UPSTREAM_URL")
        self.token = token if token is not None else os.environ.get(settings['token_env'])
        if not self.token:
            raise LabError("UPSTREAM_TOKEN_MISSING")
        self.timeout, self.retries = timeout, retries
        self.opener = build_opener(NoRedirect())

    def query(self, api_name, params=None, fields=""):
        body = json.dumps({"api_name": api_name, "token": self.token, "params": params or {}, "fields": fields}, allow_nan=False).encode()
        for attempt in range(self.retries + 1):
            try:
                req = Request(self.url, data=body, headers={"Content-Type": "application/json"})
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read(50_000_001)
                if len(raw) > 50_000_000:
                    raise LabError("UPSTREAM_RESPONSE_TOO_LARGE")
                reply = json.loads(raw)
                if not isinstance(reply, dict):
                    raise ValueError()
                if reply.get("code") != 0:
                    if reply.get("code") in {-2001, 429} and attempt < self.retries:
                        time.sleep(min(2 ** attempt, 4))
                        continue
                    error = LabError("UPSTREAM_REJECTED", safe_upstream_message(reply.get('msg', '上游拒绝请求'), self.token))
                    error.upstream_code = reply.get('code')
                    raise error
                data = reply["data"]
                names, items = data["fields"], data["items"]
                if (not isinstance(names, list) or not names or len(set(names)) != len(names) or
                        any(not isinstance(n, str) for n in names) or not isinstance(items, list) or
                        any(not isinstance(row, list) or len(row) != len(names) for row in items)):
                    raise ValueError()
                return {"fields": names, "rows": [dict(zip(names, row)) for row in items]}
            except HTTPError as exc:
                retry = exc.code in {429, 500, 502, 503, 504}
                status = exc.code
                try:
                    details = json.loads(exc.read(8192))
                    message = safe_upstream_message(details.get('msg', 'HTTP请求失败'), self.token)
                except (ValueError, AttributeError, OSError):
                    message = 'HTTP请求失败'
                exc.close()
                if not retry or attempt == self.retries:
                    error = LabError("UPSTREAM_HTTP_FAILED", f'HTTP {status}: {message}')
                    error.http_status = status
                    raise error from None
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise LabError("UPSTREAM_TIMEOUT_OR_NETWORK") from None
            except LabError:
                raise
            except (ValueError, KeyError, TypeError):
                raise LabError("UPSTREAM_FORMAT_ERROR") from None
            time.sleep(min(2 ** attempt, 4))
        raise LabError("UPSTREAM_FAILED")

    def pages(self, api_name, params, fields, *, page_size=2000, max_pages=100, mode="offset"):
        if mode not in {"offset", "single"} or not 1 <= page_size <= 10000 or not 1 <= max_pages <= 10000:
            raise LabError("INVALID_PAGINATION")
        rows, seen = [], set()
        for page in range(max_pages):
            args = dict(params)
            if mode == "offset":
                args.update(limit=page_size, offset=page * page_size)
            reply = self.query(api_name, args, fields)
            batch = reply["rows"]
            marker = json.dumps(batch, sort_keys=True, ensure_ascii=False)
            if batch and marker in seen:
                raise LabError("PAGINATION_STALLED", "上游可能不支持offset，改用日期/标的分区")
            seen.add(marker)
            rows.extend(batch)
            if mode == "single" or len(batch) < page_size:
                return rows
        raise LabError("PAGINATION_LIMIT", "未证明分区完整，不能冻结")


def probe(plan, client=None):
    """Bounded, operator-only capability evidence, never a coverage assertion."""
    client = client or TushareUpstream(timeout=15, retries=0)
    queries = plan.get('queries', [])
    if not isinstance(queries, list) or not 1 <= len(queries) <= 20:
        raise LabError('INVALID_PROBE_PLAN')
    results = []
    for query in queries:
        name = query['api_name']
        try:
            result = client.query(name, query.get('params', {}), ','.join(query.get('fields', [])))
            rows = result['rows']
            results.append({'api_name': name, 'status': 'nonempty' if rows else 'empty_success',
                            'rows': len(rows), 'fields': result['fields'], 'connection_tested': True,
                            'coverage_proven': False})
        except LabError as error:
            results.append({'api_name': name, 'status': 'failed', 'error_code': error.code,
                            'message': error.message, 'upstream_code': getattr(error, 'upstream_code', None),
                            'http_status': getattr(error, 'http_status', None)})
    return {'schema_version': 'finlab.upstream_probe.v1', 'checked_at': now(),
            'passed': all(r['status'] == 'nonempty' for r in results), 'results': results,
            'scope': 'operator_only_sample_queries_not_complete_interface_or_historical_validation',
            'source_fallback': False, 'credentials_included': False}
