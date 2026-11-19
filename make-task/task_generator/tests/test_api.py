"""Contract tests use loopback fake HTTP only; no provider account is required."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskgen.api import APIClient, APIError, BudgetExceeded


ENV_NAME = 'TASKGEN_TEST_API_KEY'
FAKE_KEY = 'fake-unit-test-key-never-a-real-secret'
MESSAGES = [{'role': 'user', 'content': 'Produce the specified JSON object.'}]


def envelope(content='{"ok":true}', **message_fields):
    return {'choices': [{'message': {'content': content, **message_fields}, 'finish_reason': 'stop'}]}


@contextmanager
def fake_service(*responses):
    """Each response is (status, JSON-serializable payload or raw bytes, headers)."""
    pending = deque(responses)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            calls.append({'path': self.path, 'headers': dict(self.headers), 'body': json.loads(body)})
            status, payload, headers = pending.popleft() if pending else (200, envelope(), {})
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield types.SimpleNamespace(url='http://127.0.0.1:' + str(server.server_port), calls=calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def config(endpoint, **changes):
    result = {
        'adapter': 'chat_completions', 'base_url': endpoint,
        'generation_model': 'fake-generator', 'review_model': 'fake-reviewer',
        'api_key_env': ENV_NAME, 'timeout_seconds': 1,
        'max_retries': 0, 'format_retries': 0,
        'max_calls_per_batch': 10, 'json_mode': 'json_object',
    }
    result.update(changes)
    return result


class APIClientTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {ENV_NAME: FAKE_KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sleep = patch('taskgen.api.time.sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_endpoint_variants_and_model_selection(self):
        for suffix in ('', '/', '/v1', '/v1/', '/v1/chat/completions', '/v1/chat/completions/'):
            with self.subTest(suffix=suffix), fake_service() as server:
                client = APIClient(config(server.url + suffix))
                self.assertEqual(client.complete_json(MESSAGES), {'ok': True})
                client.complete_json(MESSAGES, stage='review')
                client.complete_json(MESSAGES, stage='repair')
                expected = '/v1/chat/completions' if '/v1' in suffix else '/chat/completions'
                self.assertEqual([r['path'] for r in server.calls], [expected] * 3)
                self.assertEqual([r['body']['model'] for r in server.calls],
                                 ['fake-generator', 'fake-reviewer', 'fake-generator'])
                self.assertEqual(server.calls[0]['headers']['Authorization'], 'Bearer ' + FAKE_KEY)
                self.assertEqual(client.calls_used, 3)

    def test_json_modes_and_prompt_instruction(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
        for mode in ('off', 'json_object', 'json_schema', True, False):
            with self.subTest(mode=mode), fake_service() as server:
                client = APIClient(config(server.url, json_mode=mode))
                client.complete_json([{'role': 'user', 'content': 'Return a result.'}], schema=schema)
                payload = server.calls[0]['body']
                if mode in ('off', False):
                    self.assertNotIn('response_format', payload)
                    self.assertEqual(len(payload['messages']), 1)
                else:
                    self.assertEqual(payload['messages'][0]['role'], 'system')
                    self.assertIn('JSON', payload['messages'][0]['content'])
                    self.assertEqual(payload['response_format']['type'],
                                     'json_schema' if mode == 'json_schema' else 'json_object')
                    if mode == 'json_schema':
                        self.assertEqual(payload['response_format']['json_schema']['schema'], schema)

    def test_empty_review_model_falls_back_to_generation_model_for_separate_call(self):
        for empty in ('', None):
            with self.subTest(empty=empty), fake_service() as server:
                client = APIClient(config(server.url, review_model=empty))
                self.assertEqual(client.complete_json(MESSAGES, stage='review'), {'ok': True})
                self.assertEqual(server.calls[0]['body']['model'], 'fake-generator')
                self.assertEqual(client.calls_used, 1)

    def test_json_schema_missing_schema_fails_before_http(self):
        with fake_service() as server:
            client = APIClient(config(server.url, json_mode='json_schema'))
            with self.assertRaisesRegex(APIError, 'requires a schema'):
                client.complete_json(MESSAGES)
            self.assertEqual(client.calls_used, 0)
            self.assertEqual(server.calls, [])

    def test_optional_output_limits_are_validated_and_forwarded(self):
        with fake_service() as server:
            APIClient(config(server.url, max_tokens=7000, temperature=0.2)).complete_json(MESSAGES)
            self.assertEqual(server.calls[0]['body']['max_tokens'], 7000)
            self.assertEqual(server.calls[0]['body']['temperature'], 0.2)
            APIClient(config(server.url)).complete_json(MESSAGES)
            self.assertNotIn('max_tokens', server.calls[1]['body'])
            self.assertNotIn('temperature', server.calls[1]['body'])
        for changes in ({'max_tokens': 0}, {'max_tokens': True}, {'max_tokens': 1.2},
                        {'max_tokens': None}, {'temperature': -0.1}, {'temperature': 2.1},
                        {'temperature': float('nan')}, {'temperature': float('inf')},
                        {'temperature': True}, {'temperature': '0.2'}):
            with self.subTest(changes=changes), self.assertRaises(APIError):
                APIClient(config('http://127.0.0.1:1', **changes))

    def test_documented_deepseek_thinking_option_is_explicit_and_enum_checked(self):
        for mode in ('disabled', 'enabled'):
            with self.subTest(mode=mode), fake_service() as server:
                APIClient(config(server.url, thinking=mode)).complete_json(MESSAGES)
                self.assertEqual(server.calls[0]['body']['thinking'], {'type': mode})
                APIClient(config(server.url)).complete_json(MESSAGES)
                self.assertNotIn('thinking', server.calls[1]['body'])
                self.assertNotIn('reasoning_effort', server.calls[1]['body'])
        for value in (None, True, {}, {'type': 'disabled'}, 'off', 'DISABLED'):
            with self.subTest(value=value), self.assertRaises(APIError):
                APIClient(config('http://127.0.0.1:1', thinking=value))

    def test_missing_key_models_and_invalid_input_fail_before_http(self):
        with fake_service() as server:
            cases = [({'generation_model': ''}, MESSAGES, 'generate'),
                     ({'review_model': '', 'generation_model': ''}, MESSAGES, 'review'),
                     ({'api_key_env': ''}, MESSAGES, 'generate'),
                     ({}, [], 'generate'),
                     ({}, [{'role': 'tool', 'content': 'x'}], 'generate'),
                     ({}, MESSAGES, 'bad stage')]
            for changes, messages, stage in cases:
                with self.subTest(changes=changes, stage=stage):
                    client = APIClient(config(server.url, **changes))
                    with self.assertRaises(APIError):
                        client.complete_json(messages, stage)
                    self.assertEqual(client.calls_used, 0)
            with patch.dict(os.environ, {ENV_NAME: ''}):
                with self.assertRaisesRegex(APIError, 'not set'):
                    APIClient(config(server.url)).complete_json(MESSAGES)
            self.assertEqual(server.calls, [])

    def test_invalid_client_configuration(self):
        cases = [
            {'base_url': ''}, {'base_url': 'file:///tmp/data'},
            {'base_url': 'https://name:password@example.com/v1'},
            {'base_url': 'https://example.com/v1?key=bad'},
            {'base_url': 'https://example.com/v1#bad'},
            {'max_calls_per_batch': 0}, {'max_retries': -1},
            {'format_retries': True}, {'timeout_seconds': 0},
            {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')},
            {'timeout_seconds': True}, {'json_mode': 'unknown'},
            {'adapter': 'unknown'}, {'adapter': 'provider_plugin', 'provider_plugin': 'bad'},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(APIError):
                APIClient(config('http://127.0.0.1:1', **changes))

    def test_retryable_statuses_count_every_attempt(self):
        with fake_service((429, {}, {}), (503, {}, {}), (200, envelope(), {})) as server:
            client = APIClient(config(server.url, max_retries=2))
            self.assertEqual(client.complete_json(MESSAGES), {'ok': True})
            self.assertEqual(client.calls_used, 3)
            self.assertEqual(len(server.calls), 3)

    def test_nonretryable_statuses_are_safe_and_single_attempt(self):
        for status in (302, 400, 401, 403, 404):
            with self.subTest(status=status), fake_service((status, {'error': FAKE_KEY}, {})) as server:
                client = APIClient(config(server.url, max_retries=3))
                with self.assertRaises(APIError) as caught:
                    client.complete_json(MESSAGES)
                self.assertNotIn(FAKE_KEY, str(caught.exception))
                self.assertIn(str(status), str(caught.exception))
                self.assertEqual(client.calls_used, 1)
                self.assertEqual(len(server.calls), 1)

    def test_timeout_retries_but_other_network_failure_does_not(self):
        for error, expected_calls in [
            (TimeoutError(FAKE_KEY), 2),
            (urllib.error.URLError(socket.timeout(FAKE_KEY)), 2),
            (urllib.error.URLError(FAKE_KEY), 1),
        ]:
            with self.subTest(error=type(error).__name__):
                client = APIClient(config('http://127.0.0.1:1', max_retries=1))
                client._opener = Mock()
                client._opener.open.side_effect = error
                with self.assertRaises(APIError) as caught:
                    client.complete_json(MESSAGES)
                self.assertNotIn(FAKE_KEY, str(caught.exception))
                self.assertEqual(client.calls_used, expected_calls)
                self.assertEqual(client._opener.open.call_count, expected_calls)

    def test_budget_stops_http_retry_and_subsequent_calls(self):
        with fake_service((429, {}, {}), (503, {}, {})) as server:
            client = APIClient(config(server.url, max_retries=5, max_calls_per_batch=2))
            with self.assertRaises(BudgetExceeded):
                client.complete_json(MESSAGES)
            with self.assertRaises(BudgetExceeded):
                client.complete_json(MESSAGES)
            self.assertEqual(client.calls_used, 2)
            self.assertEqual(len(server.calls), 2)

    def test_attempt_reservation_runs_per_retry_and_failure_prevents_http(self):
        reserve = Mock()
        with fake_service((429, {}, {}), (503, {}, {}), (200, envelope(), {})) as server:
            client = APIClient(config(server.url, max_retries=2), reserve_attempt=reserve)
            self.assertEqual(client.complete_json(MESSAGES), {'ok': True})
            self.assertEqual(reserve.call_count, 3)
            self.assertEqual(client.calls_used, 3)
        reserve = Mock(side_effect=RuntimeError(FAKE_KEY))
        with fake_service() as server:
            client = APIClient(config(server.url), reserve_attempt=reserve)
            with self.assertRaisesRegex(APIError, 'no request was sent') as caught:
                client.complete_json(MESSAGES)
            self.assertNotIn(FAKE_KEY, str(caught.exception))
            self.assertEqual(client.calls_used, 0)
            self.assertEqual(server.calls, [])

    def test_format_repair_uses_same_budget_redacts_echoed_key(self):
        bad = 'Here is malformed JSON and a key ' + FAKE_KEY
        with fake_service((200, envelope(bad), {}), (200, envelope('{"fixed":true}'), {})) as server:
            client = APIClient(config(server.url, format_retries=1))
            original = [dict(m) for m in MESSAGES]
            schema = {'type': 'object'}
            self.assertEqual(client.complete_json(original, schema=schema), {'fixed': True})
            repaired_messages = server.calls[1]['body']['messages']
            self.assertNotIn(FAKE_KEY, json.dumps(repaired_messages))
            self.assertIn('[redacted]', repaired_messages[-2]['content'])
            self.assertIn('目标结构', repaired_messages[-1]['content'])
            self.assertEqual(original, MESSAGES)
            self.assertEqual(client.calls_used, 2)

    def test_format_repair_cannot_exceed_batch_budget(self):
        with fake_service((200, envelope('bad'), {})) as server:
            client = APIClient(config(server.url, format_retries=2, max_calls_per_batch=1))
            with self.assertRaises(BudgetExceeded):
                client.complete_json(MESSAGES)
            self.assertEqual(len(server.calls), 1)

    def test_invalid_model_json_exhausts_format_limit(self):
        for bad in ('[]', '{"n":NaN}', '{"n":Infinity}', '{"n":1e999}', '```json\n{}\n```'):
            with self.subTest(bad=bad), fake_service((200, envelope(bad), {})) as server:
                client = APIClient(config(server.url))
                with self.assertRaisesRegex(APIError, 'finite JSON object'):
                    client.complete_json(MESSAGES)
                self.assertEqual(client.calls_used, 1)

    def test_invalid_provider_envelope_is_not_a_model_format_repair(self):
        for body in (b'not-json', b'[]', b'{"x":NaN}', b'{"x":1e999}', b'\xff'):
            with self.subTest(body=body), fake_service((200, body, {})) as server:
                client = APIClient(config(server.url, format_retries=2))
                with self.assertRaisesRegex(APIError, 'invalid JSON response envelope'):
                    client.complete_json(MESSAGES)
                self.assertEqual(client.calls_used, 1)

    def test_missing_content_and_refusal_are_safe(self):
        responses = [{}, {'choices': []}, envelope(None), envelope('', refusal=FAKE_KEY)]
        filtered = envelope('{}')
        filtered['choices'][0]['finish_reason'] = 'content_filter'
        responses.append(filtered)
        for payload in responses:
            with self.subTest(payload=payload), fake_service((200, payload, {})) as server:
                with self.assertRaises(APIError) as caught:
                    APIClient(config(server.url)).complete_json(MESSAGES)
                self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_text_content_blocks(self):
        content = [{'type': 'text', 'text': '{"ok":'}, {'type': 'text', 'text': 'true}'}]
        with fake_service((200, envelope(content), {})) as server:
            self.assertEqual(APIClient(config(server.url)).complete_json(MESSAGES), {'ok': True})

    def test_logs_are_allowlisted_and_hide_request_and_response(self):
        with tempfile.TemporaryDirectory() as folder, fake_service((500, {'error': FAKE_KEY}, {})) as server:
            path = Path(folder) / 'logs' / 'api.jsonl'
            client = APIClient(config(server.url), log_path=path)
            with self.assertRaises(APIError):
                client.complete_json([{'role': 'user', 'content': 'private prompt ' + FAKE_KEY}])
            raw = path.read_text(encoding='utf-8')
            record = json.loads(raw)
            self.assertEqual(set(record), {'stage', 'model', 'status', 'duration_seconds', 'ended_at_utc'})
            self.assertEqual(record['status'], 500)
            self.assertNotIn(FAKE_KEY, raw)
            self.assertNotIn('private prompt', raw)
            self.assertNotIn(server.url, raw)

    def test_success_audit_logs_have_safe_response_metadata_and_integer_usage(self):
        response = envelope('{"private_body":"must not be logged"}')
        response.update({'id': 'completion-' + FAKE_KEY, 'model': 'served-' + FAKE_KEY,
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140,
                      'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 80,
                      'completion_tokens_details': {'reasoning_tokens': 12, 'other': FAKE_KEY},
                      'prompt_tokens_details': {'cached_tokens': 20, 'audio_tokens': True},
                      'untrusted': FAKE_KEY, 'prompt': 'must not be logged'}})
        with tempfile.TemporaryDirectory() as folder, fake_service((200, response, {})) as server:
            path = Path(folder) / 'audit.jsonl'
            result = APIClient(config(server.url), log_path=path).complete_json(MESSAGES)
            self.assertEqual(result['private_body'], 'must not be logged')
            raw = path.read_text(encoding='utf-8')
            record = json.loads(raw)
            self.assertEqual(record['response_id'], 'completion-[redacted]')
            self.assertEqual(record['response_model'], 'served-[redacted]')
            self.assertEqual(record['finish_reason'], 'stop')
            self.assertEqual(record['usage'], {
                'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140,
                'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 80,
                'completion_tokens_details': {'reasoning_tokens': 12},
                'prompt_tokens_details': {'cached_tokens': 20}})
            self.assertTrue(record['ended_at_utc'].endswith('Z'))
            datetime.fromisoformat(record['ended_at_utc'].replace('Z', '+00:00'))
            self.assertNotIn(FAKE_KEY, raw)
            self.assertNotIn('private_body', raw)
            self.assertNotIn('must not be logged', raw)

    def test_response_metadata_is_allowlisted_and_bounded(self):
        response = envelope()
        response.update({'id': 'x' * 300, 'model': 'text with spaces and\n' + FAKE_KEY,
                         'usage': {'prompt_tokens': True, 'completion_tokens': -1,
                                   'total_tokens': '123', 'other': 10}})
        response['choices'][0]['finish_reason'] = FAKE_KEY
        with tempfile.TemporaryDirectory() as folder, fake_service((200, response, {})) as server:
            path = Path(folder) / 'audit.jsonl'
            APIClient(config(server.url), log_path=path).complete_json(MESSAGES)
            raw = path.read_text(encoding='utf-8')
            record = json.loads(raw)
            self.assertEqual(len(record['response_id']), 256)
            self.assertNotIn('response_model', record)
            self.assertNotIn('usage', record)
            self.assertEqual(record['finish_reason'], 'other')
            self.assertNotIn(FAKE_KEY, raw)

    def test_length_completion_is_logged_and_never_format_repaired(self):
        response = envelope('{"partial":')
        response.update({'id': 'truncated-test-id', 'model': 'test-served-model',
                         'usage': {'completion_tokens': 7000}})
        response['choices'][0]['finish_reason'] = 'length'
        with tempfile.TemporaryDirectory() as folder, fake_service((200, response, {})) as server:
            path = Path(folder) / 'audit.jsonl'
            client = APIClient(config(server.url, format_retries=3, max_retries=3), log_path=path)
            with self.assertRaisesRegex(APIError, 'truncated'):
                client.complete_json(MESSAGES)
            self.assertEqual(client.calls_used, 1)
            self.assertEqual(len(server.calls), 1)
            record = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 200)
            self.assertEqual(record['completion_status'], 'truncated')
            self.assertEqual(record['finish_reason'], 'length')
            self.assertEqual(record['usage'], {'completion_tokens': 7000})

    def test_redirect_does_not_forward_authorization(self):
        with fake_service() as target:
            with fake_service((302, {}, {'Location': target.url + '/chat/completions'})) as source:
                with self.assertRaisesRegex(APIError, '302'):
                    APIClient(config(source.url, max_retries=2)).complete_json(MESSAGES)
                self.assertEqual(len(source.calls), 1)
                self.assertEqual(target.calls, [])

    def test_native_plugin_uses_shared_transport_and_model_budget(self):
        with fake_service((503, {}, {}), (200, {'output': {'text': '{"native":true}'}}, {})) as server:
            client = APIClient(config(server.url + '/native', adapter='provider_plugin',
                provider_plugin='taskgen.provider_example:ExampleNativeAdapter', max_retries=1))
            self.assertEqual(client.complete_json(MESSAGES, stage='review'), {'native': True})
            self.assertEqual(client.calls_used, 2)
            self.assertEqual(server.calls[0]['path'], '/native')
            self.assertEqual(server.calls[0]['headers']['X-Api-Key'], FAKE_KEY)
            self.assertEqual(server.calls[0]['body']['model_id'], 'fake-reviewer')
            self.assertEqual(server.calls[0]['body']['output_format'], 'json')
            self.assertNotIn('Authorization', server.calls[0]['headers'])

    def test_plugin_errors_cannot_echo_secrets(self):
        module = types.ModuleType('taskgen_test_plugin')

        class LeakyAdapter:
            def __init__(self, settings):
                self.delegate = None

            def build_request(self, *args, **kwargs):
                raise APIError(FAKE_KEY)

            def extract_content(self, data):
                raise APIError(FAKE_KEY)

        module.Adapter = LeakyAdapter
        with patch.dict(sys.modules, {'taskgen_test_plugin': module}):
            client = APIClient(config('http://127.0.0.1:1', adapter='provider_plugin',
                provider_plugin='taskgen_test_plugin:Adapter'))
            with self.assertRaises(APIError) as caught:
                client.complete_json(MESSAGES)
            self.assertNotIn(FAKE_KEY, str(caught.exception))
            self.assertEqual(client.calls_used, 0)
            with fake_service() as server:
                native = APIClient(config(server.url))
                client._adapter.build_request = native._adapter.build_request
                with self.assertRaises(APIError) as caught:
                    client.complete_json(MESSAGES)
                self.assertNotIn(FAKE_KEY, str(caught.exception))
                self.assertEqual(client.calls_used, 1)

    def test_provider_presets_share_protocol_and_distinct_key_names(self):
        root = Path(__file__).resolve().parents[1] / 'config'
        expected = {
            'oneapi.example.json': ('http://127.0.0.1:3000/v1', 'TASKGEN_API_KEY'),
            'deepseek.example.json': ('https://api.deepseek.com', 'DEEPSEEK_API_KEY'),
            'glm.example.json': ('https://open.bigmodel.cn/api/paas/v4', 'GLM_API_KEY'),
        }
        for name, (url, env) in expected.items():
            with self.subTest(name=name):
                preset = json.loads((root / name).read_text(encoding='utf-8'))
                self.assertEqual(preset['adapter'], 'chat_completions')
                self.assertEqual(preset['base_url'], url)
                self.assertEqual(preset['api_key_env'], env)
                self.assertEqual(preset['generation_model'], '')
                self.assertEqual(preset['review_model'], '')
                self.assertNotIn('api_key', preset)


if __name__ == '__main__':
    unittest.main()
