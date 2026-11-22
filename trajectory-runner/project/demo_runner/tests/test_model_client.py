import json
import http.client
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from model_client import ModelClient, ModelError, _key, load_config, parse_response, prepare_request


TOOLS = [{"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]
HISTORY = [
    {"role": "system", "content": "只看已知数据"},
    {"role": "user", "content": "研究"},
    {"role": "assistant", "content": "先读任务", "tool_calls": [{"id": "call_1", "name": "read_file", "arguments": {"path": "TASK.md"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "任务内容"},
]


class AdapterTests(unittest.TestCase):
    def test_remote_http_requires_operator_url_and_sends_zero_temperature(self):
        url = "http://model.example.test:3425/v1"
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = pathlib.Path(temp) / "cfg.json"
            path.write_text(json.dumps({"provider": "openai_compatible", "base_url": url,
                                        "model": "example", "api_key_env": "OPENAI_API_KEY",
                                        "temperature": 0}))
            with self.assertRaises(ModelError):
                load_config(path)
            with patch.dict(os.environ, {"OPENAI_BASE_URL": url}):
                endpoint, _, body = prepare_request(load_config(path), HISTORY, TOOLS)
                self.assertEqual(endpoint, url + "/chat/completions")
                self.assertEqual(body["temperature"], 0)

    def test_openai_roundtrip(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "max_tokens": 1024}
        url, headers, data = prepare_request(cfg, HISTORY, TOOLS)
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(data["messages"][2]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(data["messages"][3]["tool_call_id"], "call_1")
        result = parse_response("openai_compatible", {"choices": [{"message": {"content": None, "tool_calls": [{"id": "x", "function": {"name": "read_file", "arguments": '{"path":"TASK.md"}'}}]}}]})
        self.assertEqual(result["tool_calls"][0]["arguments"], {"path": "TASK.md"})

    def test_thinking_tool_roundtrip(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://api.deepseek.com",
               "model": "deepseek-v4-pro", "max_tokens": 32768,
               "thinking": "enabled", "reasoning_effort": "high"}
        raw = {"choices": [{"message": {"content": None, "reasoning_content": "先读取任务",
                             "tool_calls": [{"id": "x", "function": {"name": "read_file",
                                                                 "arguments": '{"path":"TASK.md"}'}}]}}]}
        reply = parse_response("openai_compatible", raw)
        _, _, payload = prepare_request(cfg, HISTORY[:2] + [{"role": "assistant", **reply},
                                                         {"role": "tool", "tool_call_id": "x", "content": "任务内容"}], TOOLS)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["messages"][2]["reasoning_content"], "先读取任务")
        self.assertEqual(payload["messages"][2]["tool_calls"][0]["id"], "x")

    def test_anthropic_roundtrip(self):
        cfg = {"provider": "anthropic", "base_url": "https://api.anthropic.com", "model": "later-model", "max_tokens": 1024}
        url, headers, data = prepare_request(cfg, HISTORY, TOOLS)
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertIn("只看已知数据", data["system"])
        self.assertEqual(data["messages"][1]["content"][1]["type"], "tool_use")
        self.assertEqual(data["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        result = parse_response("anthropic", {"content": [{"type": "text", "text": "开始"}, {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "TASK.md"}}]})
        self.assertEqual(result["tool_calls"][0]["name"], "read_file")

    def test_no_literal_key_and_missing_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "cfg.json"
            path.write_text(json.dumps({"provider": "openai_compatible", "base_url": "https://api.example.com", "model": "m", "api_key_env": "TEST_MISSING_KEY"}))
            cfg = load_config(path)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ModelError, "没有 API Key"):
                    ModelClient(cfg).complete(HISTORY, TOOLS)
            path.write_text(json.dumps({**cfg, "api_key": "literal"}))
            with self.assertRaisesRegex(ModelError, "不要将明文"):
                load_config(path)

    def test_dotenv_key_file(self):
        with tempfile.TemporaryDirectory() as temp:
            keyfile = pathlib.Path(temp) / ".env"
            keyfile.write_text("# private\nTEST_FILE_KEY=placeholder-for-unit-test\n")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(_key({"api_key_env": "TEST_FILE_KEY", "api_key_file": str(keyfile)}), "placeholder-for-unit-test")

    def test_missing_optional_key_file_falls_back_to_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = {"api_key_env": "TEST_FILE_KEY", "api_key_file": str(pathlib.Path(temp) / "missing.env")}
            with patch.dict(os.environ, {"TEST_FILE_KEY": "local-test-key"}, clear=True):
                self.assertEqual(_key(cfg), "local-test-key")

    def test_invalid_arguments_are_preserved_for_model_correction(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://example.test", "model": "fake", "max_tokens": 1024}
        invalid = '{"path":"TASK.md"}{"path":"strategy.py"}'
        raw = {"choices": [{"message": {"content": None, "reasoning_content": "read task",
            "tool_calls": [
                {"id": "bad", "function": {"name": "read_file", "arguments": invalid}},
                {"id": "good", "function": {"name": "read_file", "arguments": '{"path":"BACKTEST.md"}'}}]}}]}
        reply = parse_response("openai_compatible", raw)
        bad, good = reply["tool_calls"]
        self.assertIsNone(bad["arguments"])
        self.assertIn("未执行", bad["argument_error"])
        self.assertEqual(good["arguments"], {"path": "BACKTEST.md"})
        history = HISTORY[:2] + [{"role": "assistant", **reply},
            {"role": "tool", "tool_call_id": "bad", "content": bad["argument_error"]},
            {"role": "tool", "tool_call_id": "good", "content": "rules"}]
        _, _, payload = prepare_request(cfg, history, TOOLS)
        self.assertEqual(payload["messages"][2]["tool_calls"][0]["function"]["arguments"], invalid)
        self.assertEqual(payload["messages"][3]["tool_call_id"], "bad")
        self.assertEqual(payload["messages"][2]["reasoning_content"], "read task")

    def test_non_object_arguments_are_rejected_as_tool_errors(self):
        for arguments in ('[]', 'null', '"text"'):
            with self.subTest(arguments=arguments):
                reply = parse_response("openai_compatible", {"choices": [{"message": {"tool_calls": [
                    {"id": "x", "function": {"name": "list_files", "arguments": arguments}}]}}]})
                self.assertEqual(reply["tool_calls"][0]["raw_arguments"], arguments)
                self.assertIn("JSON object", reply["tool_calls"][0]["argument_error"])

    def test_bad_protocol_response(self):
        with self.assertRaises(ModelError):
            parse_response("openai_compatible", {"choices": []})

    def test_usage_is_retained_for_context_budget(self):
        reply = parse_response("openai_compatible", {"choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}})
        self.assertEqual(reply["usage"]["prompt_tokens"], 123)
        _, _, payload = prepare_request({"provider": "openai_compatible", "base_url": "https://example.test",
                                         "model": "fake", "max_tokens": 1024},
                                        HISTORY[:2] + [{"role": "assistant", **reply}], TOOLS)
        self.assertNotIn("usage", payload["messages"][-1])

    def test_incomplete_http_response_retries_identical_request(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://example.test", "model": "fake",
               "max_tokens": 1024, "timeout_seconds": 30, "api_key_env": "UNUSED_TEST_KEY"}
        broken = MagicMock()
        broken.__enter__.return_value = broken
        broken.read.side_effect = http.client.IncompleteRead(b"")
        healthy = MagicMock()
        healthy.__enter__.return_value = healthy
        healthy.read.return_value = json.dumps({"choices": [{"message": {"content": "done"}}]}).encode()
        opener = MagicMock()
        opener.open.side_effect = [broken, healthy]
        with patch("model_client._key", return_value="placeholder"), \
             patch("model_client.urllib.request.build_opener", return_value=opener), \
             patch("model_client.time.sleep"):
            reply = ModelClient(cfg).complete(HISTORY, TOOLS)
        self.assertEqual(reply["content"], "done")
        self.assertEqual(opener.open.call_count, 2)
        self.assertIs(opener.open.call_args_list[0].args[0], opener.open.call_args_list[1].args[0])
        self.assertEqual(opener.open.call_args_list[0].args[0].data, opener.open.call_args_list[1].args[0].data)

    def test_trajectory_http_retries_with_exponential_backoff(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://example.test", "model": "fake",
               "max_tokens": 1024, "timeout_seconds": 30, "_retry_http": True}
        for codes, expected in [([502, 429, 503], [call(2), call(4)]), ([401], [])]:
            with self.subTest(codes=codes):
                opener = MagicMock()
                opener.open.side_effect = [HTTPError(None, code, "private", {}, None) for code in codes]
                with patch("model_client._key", return_value="placeholder"), \
                     patch("model_client.urllib.request.build_opener", return_value=opener), \
                     patch("model_client.time.sleep") as sleep, \
                     self.assertRaises(ModelError) as caught:
                    ModelClient(cfg).complete(HISTORY, TOOLS)
                self.assertEqual(sleep.call_args_list, expected)
                self.assertEqual(opener.open.call_count, len(codes))
                self.assertEqual(caught.exception.code, f"MODEL_HTTP_{codes[-1]}")
                self.assertNotIn("private", str(caught.exception))

    def test_repeated_http_disconnect_becomes_safe_model_error(self):
        cfg = {"provider": "openai_compatible", "base_url": "https://example.test", "model": "fake",
               "max_tokens": 1024, "timeout_seconds": 30, "api_key_env": "UNUSED_TEST_KEY"}
        opener = MagicMock()
        opener.open.side_effect = http.client.RemoteDisconnected("private provider detail")
        with patch("model_client._key", return_value="placeholder"), \
             patch("model_client.urllib.request.build_opener", return_value=opener), \
             patch("model_client.time.sleep"), \
             self.assertRaisesRegex(ModelError, "3 次尝试.*RemoteDisconnected") as caught:
            ModelClient(cfg).complete(HISTORY, TOOLS)
        self.assertEqual(opener.open.call_count, 3)
        self.assertNotIn("private provider detail", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
