"""Budgeted, standard-library JSON client for trusted provider configurations.

Provider plug-ins must expose ``Adapter(config)`` with
``build_request(messages, *, model, api_key, json_mode, schema) -> Request`` and
``extract_content(response_object) -> str | dict``. Only this module performs
HTTP requests, so plug-ins share retry, timeout and batch-call accounting.
Plug-ins are trusted executable Python code, never model-generated input.
"""
from __future__ import annotations

import importlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import socket
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    """Safe error: messages never include keys, headers or response bodies."""


class BudgetExceeded(APIError):
    """The configured maximum number of HTTP attempts has been reached."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A misconfigured endpoint must not forward authentication elsewhere.
        return None


def _json_object(text: str) -> dict:
    def reject_constant(_value):
        raise ValueError('Non-finite JSON value')
    def finite_float(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError('Non-finite JSON number')
        return value
    value = json.loads(text, parse_constant=reject_constant, parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


class _ChatCompletionsAdapter:
    def __init__(self, config: dict):
        base = config.get('base_url', '')
        if not isinstance(base, str) or not base.strip():
            raise APIError('Configure base_url before calling the API.')
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
            raise APIError('base_url must be an HTTP(S) URL without embedded credentials.')
        if parsed.query or parsed.fragment:
            raise APIError('base_url cannot contain a query string or fragment.')
        # Both a versioned API base and the complete endpoint are accepted.
        self.url = base.rstrip('/')
        if not self.url.endswith('/chat/completions'):
            self.url += '/chat/completions'
        self.options = {name: config[name] for name in ('max_tokens', 'temperature') if name in config}
        if 'thinking' in config:
            self.options['thinking'] = {'type': config['thinking']}

    def build_request(self, messages, *, model, api_key, json_mode, schema):
        payload: dict[str, Any] = {'model': model, 'messages': messages}
        payload.update(self.options)
        if json_mode == 'json_object':
            payload['response_format'] = {'type': 'json_object'}
        elif json_mode == 'json_schema':
            if not isinstance(schema, dict):
                raise APIError('json_schema mode requires a schema object.')
            payload['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'task_output', 'strict': True, 'schema': schema}}
        return urllib.request.Request(self.url,
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + api_key,
                     'Content-Type': 'application/json', 'Accept': 'application/json'},
            method='POST')

    def extract_content(self, response_object):
        choice = response_object['choices'][0]
        message = choice['message']
        if message.get('refusal') or choice.get('finish_reason') == 'content_filter':
            raise APIError('The provider refused this request.')
        content = message.get('content')
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces = [p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
            if pieces and all(isinstance(p, str) for p in pieces):
                return ''.join(pieces)
        raise APIError('The provider returned no usable text content.')


class APIClient:
    """One instance represents one batch's shared HTTP-attempt budget.

    ``schema`` is forwarded in json_schema mode and included in format repairs.
    This client validates a finite JSON object, not a complete JSON Schema;
    callers must apply the TaskSpec/review validators after completion.
    """
    def __init__(self, config: dict, log_path: Path | None = None, reserve_attempt=None):
        if not isinstance(config, dict):
            raise APIError('API configuration must be an object.')
        if reserve_attempt is not None and not callable(reserve_attempt):
            raise APIError('reserve_attempt must be callable when provided.')
        self.config = dict(config)
        self.log_path = Path(log_path) if log_path is not None else None
        self._reserve_attempt = reserve_attempt
        self._calls_used = 0
        self._budget = self._integer('max_calls_per_batch', 36, minimum=1)
        self._max_retries = self._integer('max_retries', 2, minimum=0)
        self._format_retries = self._integer('format_retries', 1, minimum=0)
        if 'max_tokens' in self.config:
            self._integer('max_tokens', None, minimum=1)
        if 'temperature' in self.config:
            temperature = self.config['temperature']
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
                raise APIError('temperature must be a finite number between zero and two.')
        if 'thinking' in self.config:
            thinking = self.config['thinking']
            if not isinstance(thinking, str) or thinking not in {'enabled', 'disabled'}:
                raise APIError('thinking must be enabled or disabled when provided.')
        timeout = self.config.get('timeout_seconds', 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
            raise APIError('timeout_seconds must be greater than zero and at most 3600.')
        self._timeout = timeout
        mode = self.config.get('json_mode', 'json_object')
        if isinstance(mode, bool):
            mode = 'json_object' if mode else 'off'
        if mode not in {'off', 'json_object', 'json_schema'}:
            raise APIError('json_mode must be off, json_object or json_schema.')
        self._mode = mode
        adapter = self.config.get('adapter', 'chat_completions')
        self._is_plugin = adapter == 'provider_plugin'
        if adapter == 'chat_completions':
            self._adapter = _ChatCompletionsAdapter(self.config)
        elif adapter == 'provider_plugin':
            path = self.config.get('provider_plugin')
            if not isinstance(path, str) or ':' not in path:
                raise APIError('provider_plugin must use module.path:AdapterClass syntax.')
            module_name, class_name = path.rsplit(':', 1)
            try:
                cls = getattr(importlib.import_module(module_name), class_name)
                self._adapter = cls(dict(self.config))
                if not callable(getattr(self._adapter, 'build_request', None)) or not callable(getattr(self._adapter, 'extract_content', None)):
                    raise TypeError
            except Exception:
                raise APIError('The trusted provider plug-in could not be loaded.') from None
        else:
            raise APIError('Unsupported adapter; use chat_completions or provider_plugin.')
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _integer(self, name, default, *, minimum):
        value = self.config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            constraint = 'a non-negative integer.' if minimum == 0 else 'a positive integer.'
            raise APIError(name + ' must be ' + constraint)
        return value

    @property
    def calls_used(self):
        return self._calls_used

    @staticmethod
    def _response_metadata(response, key):
        """Allowlisted audit fields only; never message content or provider errors."""
        metadata = {}
        for source, target in (('id', 'response_id'), ('model', 'response_model')):
            value = response.get(source)
            if isinstance(value, str):
                value = value.replace(key, '[redacted]')
                # Identifiers cannot smuggle multiline/free-form response bodies into logs.
                if re.fullmatch(r'[A-Za-z0-9_.:/\[\]-]{1,512}', value):
                    metadata[target] = value[:256]
        usage = response.get('usage')
        if isinstance(usage, dict):
            counts = {}
            for name in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                         'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
                value = usage.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    counts[name] = value
            for group, names in (
                ('prompt_tokens_details', ('cached_tokens', 'audio_tokens')),
                ('completion_tokens_details', ('reasoning_tokens', 'audio_tokens',
                                              'accepted_prediction_tokens', 'rejected_prediction_tokens')),
            ):
                details = usage.get(group)
                if isinstance(details, dict):
                    clean = {name: details[name] for name in names if isinstance(details.get(name), int)
                             and not isinstance(details[name], bool) and details[name] >= 0}
                    if clean:
                        counts[group] = clean
            if counts:
                metadata['usage'] = counts
        choices = response.get('choices')
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get('finish_reason')
            if isinstance(reason, str):
                metadata['finish_reason'] = reason if reason in {
                    'stop', 'length', 'tool_calls', 'function_call', 'content_filter',
                    'insufficient_system_resource'} else 'other'
        return metadata

    def _log(self, stage, model, status, started, metadata=None):
        if self.log_path is None:
            return
        # No request/response body, URL, headers, key or provider exception text.
        record = {'stage': stage, 'model': model, 'status': status,
                  'duration_seconds': round(time.monotonic() - started, 6),
                  'ended_at_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}
        if metadata:
            record.update(metadata)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            raise APIError('Could not write the safe API attempt log.') from None

    def _http(self, request, stage, model, key):
        for attempt in range(self._max_retries + 1):
            if self._calls_used >= self._budget:
                raise BudgetExceeded('The configured batch API-call budget is exhausted.')
            if self._reserve_attempt is not None:
                try:
                    self._reserve_attempt()
                except Exception:
                    raise APIError('Could not reserve an API attempt; no request was sent.') from None
            self._calls_used += 1
            started = time.monotonic()
            retry = False
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    status = response.status
                    # Bounded envelope size prevents accidental unbounded reads.
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    self._log(stage, model, status, started, {'completion_status': 'response_too_large'})
                    raise APIError('The provider response exceeded the size limit.')
                try:
                    envelope = _json_object(raw.decode('utf-8'))
                except (ValueError, UnicodeError):
                    self._log(stage, model, status, started, {'completion_status': 'invalid_envelope'})
                    raise APIError('The provider returned an invalid JSON response envelope.') from None
                metadata = self._response_metadata(envelope, key)
                if metadata.get('finish_reason') == 'length':
                    metadata['completion_status'] = 'truncated'
                    self._log(stage, model, status, started, metadata)
                    raise APIError('The provider truncated the completion at a token or context limit; increase the limit or reduce the request.')
                self._log(stage, model, status, started, metadata)
                return envelope
            except urllib.error.HTTPError as error:
                code = int(error.code)
                error.close()
                self._log(stage, model, code, started)
                retry = code == 429 or 500 <= code <= 599
                if not retry or attempt == self._max_retries:
                    raise APIError('Provider HTTP request failed with status ' + str(code) + '.') from None
            except (TimeoutError, socket.timeout):
                self._log(stage, model, 'timeout', started)
                retry = True
                if attempt == self._max_retries:
                    raise APIError('The provider request timed out after the configured retries.') from None
            except urllib.error.URLError as error:
                timed_out = isinstance(error.reason, (TimeoutError, socket.timeout))
                self._log(stage, model, 'timeout' if timed_out else 'network_error', started)
                retry = timed_out
                if not retry or attempt == self._max_retries:
                    raise APIError('The provider network request failed.') from None
            except APIError:
                raise
            except Exception:
                self._log(stage, model, 'transport_error', started)
                raise APIError('The provider transport failed.') from None
            if retry:
                time.sleep(min(0.25 * (2 ** attempt), 2.0))
        raise APIError('Provider request failed.')

    def complete_json(self, messages: list[dict], stage: str = 'generate', schema: dict | None = None) -> dict:
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) and m.get('role') in {'system','developer','user','assistant'} and isinstance(m.get('content'), str) for m in messages):
            raise APIError('messages must be a nonempty list of role/content objects.')
        if not isinstance(stage, str) or not stage or len(stage) > 80 or not all(ch.isalnum() or ch in '_-' for ch in stage):
            raise APIError('stage must be a short identifier.')
        model = self.config.get('review_model' if stage == 'review' else 'generation_model')
        if stage == 'review' and (model is None or isinstance(model, str) and not model.strip()):
            model = self.config.get('generation_model')
        if not isinstance(model, str) or not model.strip():
            raise APIError('Configure the model for this stage before calling the API.')
        env_name = self.config.get('api_key_env')
        if not isinstance(env_name, str) or not env_name:
            raise APIError('Configure api_key_env before calling the API.')
        key = os.environ.get(env_name)
        if not key:
            raise APIError('The configured API-key environment variable is not set.')
        # Prevent accidentally configured secret-like model/stage strings from logs.
        safe_model = model.replace(key, '[redacted]')[:256]
        safe_stage = stage.replace(key, '[redacted]')
        work = [dict(m) for m in messages]
        if self._mode != 'off' and not any('json' in m['content'].lower() for m in work):
            work.insert(0, {'role':'system','content':'Return only a valid JSON object.'})
        for repair_index in range(self._format_retries + 1):
            try:
                request = self._adapter.build_request(work, model=model, api_key=key,
                    json_mode=self._mode, schema=schema)
                if not isinstance(request, urllib.request.Request):
                    raise TypeError
            except APIError:
                if self._is_plugin:
                    raise APIError('The provider adapter could not construct a request.') from None
                raise
            except Exception:
                raise APIError('The provider adapter could not construct a request.') from None
            response_object = self._http(request, safe_stage, safe_model, key)
            try:
                content = self._adapter.extract_content(response_object)
            except APIError:
                if self._is_plugin:
                    raise APIError('The provider adapter could not read the response envelope.') from None
                raise
            except Exception:
                raise APIError('The provider adapter could not read the response envelope.') from None
            try:
                if isinstance(content, dict):
                    return _json_object(json.dumps(content, ensure_ascii=False, allow_nan=False))
                if not isinstance(content, str):
                    raise ValueError
                return _json_object(content)
            except (ValueError, TypeError):
                if repair_index == self._format_retries:
                    raise APIError('The model did not return a finite JSON object within the format-repair limit.') from None
                original = content if isinstance(content, str) else 'Invalid non-object output'
                # Do not echo a provider-returned API key back as conversational text.
                original = original.replace(key, '[redacted]')
                work.append({'role':'assistant','content':original[:64000]})
                instruction = '上次输出不是合法JSON对象。仅修复格式，保留原意，返回单个JSON对象，不要代码围栏、前后解释或NaN/Infinity。'
                if schema is not None:
                    instruction += '\n目标结构：' + json.dumps(schema, ensure_ascii=False, allow_nan=False)
                work.append({'role':'user','content':instruction})
        raise APIError('JSON completion failed.')
