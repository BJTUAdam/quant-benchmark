"""Provider-neutral chat + tool-call interface. No project/strategy logic lives here.

Secrets are loaded at call time from an environment variable or a dedicated key file.
Do not put a literal API key in config.json or expose config/key files to the agent.
"""
from __future__ import annotations

import json
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class ModelError(RuntimeError):
    pass


def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path).resolve()
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    required = ("provider", "base_url", "model", "api_key_env")
    missing = [field for field in required if not cfg.get(field)]
    if missing:
        raise ModelError(f"模型配置缺少: {', '.join(missing)}")
    if cfg["provider"] not in {"openai_compatible", "anthropic"}:
        raise ModelError("provider 只支持 openai_compatible 或 anthropic")
    url = urllib.parse.urlparse(cfg["base_url"])
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}):
        raise ModelError("base_url 必须是 HTTPS（仅本机服务允许 HTTP）")
    if not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ModelError("base_url 不能含账号、密码、查询参数或片段")
    if "api_key" in cfg:
        raise ModelError("不要将明文 api_key 写入配置；请使用 api_key_env 或 api_key_file")
    if cfg.get("api_key_file"):
        cfg["api_key_file"] = str((p.parent / cfg["api_key_file"]).resolve())
    if "mcp_servers" in cfg or "skills" in cfg:
        from skill_registry import normalize_skills
        try:
            cfg["skills"] = normalize_skills(cfg.get("skills"), cfg.get("mcp_servers"))
        except ValueError as exc:
            raise ModelError(str(exc)) from None
    cfg["max_tokens"] = int(cfg.get("max_tokens", 8192))
    cfg["timeout_seconds"] = int(cfg.get("timeout_seconds", 120))
    cfg["max_model_calls"] = int(cfg.get("max_model_calls", 80))
    cfg["context_mode"] = cfg.get("context_mode", "fresh")
    if cfg["context_mode"] not in {"fresh", "continuous"}:
        raise ModelError("context_mode 只能是 fresh 或 continuous")
    cfg["interaction_protocol"] = cfg.get("interaction_protocol", "legacy")
    if cfg["interaction_protocol"] not in {"legacy", "bundled_review_v1", "guided_delivery_v1", "guided_delivery_v2", "environment_feedback_v1"}:
        raise ModelError("interaction_protocol 不受支持")
    backend = cfg.get("execution_backend", "bubblewrap")
    if backend not in {"bubblewrap", "docker"}:
        raise ModelError("execution_backend 只能是 bubblewrap 或 docker")
    if cfg["interaction_protocol"] == "environment_feedback_v1" and backend != "docker":
        raise ModelError("环境反馈协议要求 Docker 执行后端")
    if cfg["interaction_protocol"] == "environment_feedback_v1":
        defaults = {"stage_warning_seconds": 1440, "stage_urgent_seconds": 2040,
                    "stage_hard_seconds": 2400, "max_research_calls": 65,
                    "max_strategy_evaluations": 40, "max_sweep_evaluations": 12,
                    "reserved_final_evaluations": 2,
                    "research_timeout_seconds": 180}
        for key, value in defaults.items():
            if key == "reserved_final_evaluations" and type(cfg.get(key, value)) is not int:
                raise ModelError("最终验证预留额度须为整数")
            cfg[key] = int(cfg.get(key, value))
        if not (60 <= cfg["stage_warning_seconds"] < cfg["stage_urgent_seconds"] <
                cfg["stage_hard_seconds"] <= 14400):
            raise ModelError("研发时间提醒和硬上限无效")
        if not (1 <= cfg["max_research_calls"] <= 100 and
                1 <= cfg["max_strategy_evaluations"] <= 200 and
                1 <= cfg["reserved_final_evaluations"] <= cfg["max_strategy_evaluations"] and
                1 <= cfg["max_sweep_evaluations"] <= min(50, cfg["max_strategy_evaluations"]) and
                10 <= cfg["research_timeout_seconds"] <= 300 and
                cfg["research_timeout_seconds"] < cfg["stage_hard_seconds"]):
            raise ModelError("研发工具预算无效")
    if backend == "docker":
        image = cfg.get("docker_image")
        if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ModelError("Docker 后端须使用不可变的 sha256 镜像 ID")
    elif cfg.get("docker_image") is not None:
        raise ModelError("bubblewrap 后端不能登记 Docker 镜像")
    if cfg["context_mode"] == "continuous":
        if "context_window_tokens" not in cfg:
            raise ModelError("持续会话必须显式配置 context_window_tokens")
        cfg["context_window_tokens"] = int(cfg["context_window_tokens"])
        cfg["context_margin_tokens"] = int(cfg.get("context_margin_tokens", 4096))
        if not 1024 <= cfg["context_window_tokens"] <= 2000000 or cfg["context_margin_tokens"] < 0:
            raise ModelError("持续会话上下文窗口或余量不合法")
        if cfg["max_tokens"] + cfg["context_margin_tokens"] >= cfg["context_window_tokens"]:
            raise ModelError("持续会话窗口必须大于输出上限及预留余量")
    if not (128 <= cfg["max_tokens"] <= 131072 and 1 <= cfg["timeout_seconds"] <= 600):
        raise ModelError("max_tokens 或 timeout_seconds 超出允许范围")
    if not 1 <= cfg["max_model_calls"] <= 200:
        raise ModelError("max_model_calls 超出允许范围")
    if cfg.get("thinking") not in (None, "enabled", "disabled"):
        raise ModelError("thinking 只能是 enabled 或 disabled")
    if cfg.get("reasoning_effort") not in (None, "none", "low", "high", "max"):
        raise ModelError("reasoning_effort 只能是 none、low、high 或 max")
    if cfg.get("output_token_parameter", "max_tokens") not in {"max_tokens", "max_completion_tokens"}:
        raise ModelError("output_token_parameter 不受支持")
    if cfg.get("output_token_parameter") and cfg['provider'] != 'openai_compatible':
        raise ModelError("output_token_parameter 仅适用于 OpenAI 兼容协议")
    if cfg.get('cost_control') is not None:
        from usage_ledger import validate_policy
        validate_policy(cfg['cost_control'])
        if cfg['provider'] != 'openai_compatible' or cfg.get('output_token_parameter') != 'max_completion_tokens':
            raise ModelError('费用控制必须使用包含思考的 max_completion_tokens')
    if cfg.get("user_agent") is not None:
        from user_agent import validate_config, validate_model_budget
        try:
            validate_config(cfg["user_agent"], unlimited_calls=cfg.get("evidence_driven") is True)
            validate_model_budget(cfg)
        except ValueError as exc:
            raise ModelError(str(exc)) from None
        if cfg["interaction_protocol"] != "environment_feedback_v1" or cfg["context_mode"] != "fresh":
            raise ModelError("User Agent 第一版仅支持 environment_feedback_v1 和 fresh 文件交接")
    return cfg


def _key(cfg: dict[str, Any]) -> str:
    key = ""
    key_file = cfg.get("api_key_file")
    if not key_file and cfg["api_key_env"] == "DEEPSEEK_API_KEY":
        # Matrix jobs do not accept a credential path in their public spec.
        # Resolve the private local config from this checkout, outside runs.
        candidate = Path(__file__).resolve().parents[2] / "benchmark" / "deepseek.private.env"
        if candidate.is_file():
            key_file = candidate
    if key_file:
        try:
            lines = Path(key_file).read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            lines = []
        selected = [line.split("=", 1)[1].strip().strip('"\'') for line in lines
                    if line.strip().startswith(cfg["api_key_env"] + "=")]
        key = (selected[-1] if selected else "").strip()
    if not key:
        key = os.environ.get(cfg["api_key_env"], "").strip()
    if not key:
        raise ModelError(f"没有 API Key：请设置环境变量 {cfg['api_key_env']}，或填写 api_key_file 所指 .env 文件中的同名键")
    return key


def _endpoint(base: str, suffix: str) -> str:
    trimmed = base.rstrip("/")
    if trimmed.endswith(suffix):
        return trimmed
    if suffix.startswith("/v1/") and trimmed.endswith("/v1"):
        return trimmed + suffix[3:]
    return trimmed + suffix


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            converted.append({"role": "tool", "tool_call_id": msg["tool_call_id"], "content": msg["content"]})
        elif role == "assistant":
            item = {"role": "assistant", "content": msg.get("content") or None}
            if "reasoning_content" in msg:
                item["reasoning_content"] = msg["reasoning_content"]
            if msg.get("tool_calls"):
                item["tool_calls"] = [{"id": c["id"], "type": "function", "function": {
                    "name": c["name"], "arguments": c.get("raw_arguments", json.dumps(c["arguments"], ensure_ascii=False))}}
                    for c in msg["tool_calls"]]
            converted.append(item)
        else:
            converted.append({"role": role, "content": msg.get("content", "")})
    return converted


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = "\n\n".join(m.get("content", "") for m in messages if m["role"] == "system")
    converted = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            continue
        if role == "tool":
            item = {"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}
            new_role, content = "user", [item]
        elif role == "assistant":
            content = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            content += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                        for c in msg.get("tool_calls", [])]
            new_role = "assistant"
        else:
            new_role, content = "user", [{"type": "text", "text": msg.get("content", "")}]
        if not content:
            continue
        if converted and converted[-1]["role"] == new_role:
            converted[-1]["content"].extend(content)
        else:
            converted.append({"role": new_role, "content": content})
    return system, converted


def prepare_request(cfg: dict[str, Any], messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]]) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return URL, non-secret headers, JSON body. Authorization is added by ModelClient."""
    if cfg["provider"] == "openai_compatible":
        payload = {"model": cfg["model"], "messages": _openai_messages(messages),
                   "tools": [{"type": "function", "function": t} for t in tools],
                   "tool_choice": "auto", cfg.get('output_token_parameter', 'max_tokens'): cfg["max_tokens"]}
        if not tools:
            payload.pop("tools")
            payload.pop("tool_choice")
        if cfg.get("thinking"):
            payload["thinking"] = {"type": cfg["thinking"]}
        if cfg.get("reasoning_effort"):
            payload["reasoning_effort"] = cfg["reasoning_effort"]
        return _endpoint(cfg["base_url"], "/chat/completions"), {}, payload
    if cfg["provider"] == "anthropic":
        system, transformed = _anthropic_messages(messages)
        payload = {"model": cfg["model"], "system": system, "messages": transformed,
                   "tools": [{"name": t["name"], "description": t["description"],
                              "input_schema": t["parameters"]} for t in tools],
                   "max_tokens": cfg["max_tokens"]}
        if not tools:
            payload.pop("tools")
        return _endpoint(cfg["base_url"], "/v1/messages"), {"anthropic-version": "2023-06-01"}, payload
    raise ModelError("不支持的 provider")


def parse_response(provider: str, raw: dict[str, Any]) -> dict[str, Any]:
    try:
        if provider == "openai_compatible":
            if raw["choices"][0].get("finish_reason") == "length":
                raise ModelError("模型输出被 max_tokens 截断；请提高上限或缩短单轮任务")
            msg = raw["choices"][0]["message"]
            calls = []
            for call in msg.get("tool_calls", []):
                function = call["function"]
                arguments = function["arguments"] or "{}"
                if not isinstance(arguments, str):
                    raise ValueError("function.arguments 必须是字符串")
                normalized = {"id": call["id"], "name": function["name"], "arguments": None}
                try:
                    parsed = json.loads(arguments)
                    if not isinstance(parsed, dict):
                        raise ValueError("工具参数必须是 JSON object")
                    normalized["arguments"] = parsed
                except (ValueError, json.JSONDecodeError) as error:
                    # Retain the model's exact invalid string in history. Do not
                    # infer arguments or execute any part of a malformed call.
                    normalized["raw_arguments"] = arguments
                    normalized["argument_error"] = "工具参数格式错误：必须提供单个合法 JSON object；本次调用未执行，请重新发起。" + str(error)
                calls.append(normalized)
            text = msg.get("content") or ""
        elif provider == "anthropic":
            if raw.get("stop_reason") == "max_tokens":
                raise ModelError("模型输出被 max_tokens 截断；请提高上限或缩短单轮任务")
            blocks = raw["content"]
            text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
            calls = [{"id": b["id"], "name": b["name"], "arguments": b["input"]}
                     for b in blocks if b["type"] == "tool_use"]
        else:
            raise ModelError("不支持的 provider")
        if any(not isinstance(c["arguments"], dict) and not c.get("argument_error") for c in calls):
            raise ValueError("工具参数必须是 JSON object")
        ids = [c["id"] for c in calls]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("工具调用 ID 缺失或重复")
        result = {"content": text, "tool_calls": calls}
        if provider == "openai_compatible" and "reasoning_content" in msg:
            result["reasoning_content"] = msg["reasoning_content"]
        if isinstance(raw.get("usage"), dict):
            result["usage"] = raw["usage"]
        return result
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ModelError(f"模型响应格式不符合 {provider} 协议：{exc}") from exc


class ModelClient:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        url, headers, body = prepare_request(self.cfg, messages, tools)
        ledger = None
        if self.cfg.get('cost_control') is not None:
            from usage_ledger import UsageLedger
            if not self.cfg.get('_usage_ledger_path'):
                raise ModelError('费用控制缺少正式 run 的台账路径，拒绝调用模型')
            ledger = UsageLedger(self.cfg['_usage_ledger_path'], self.cfg)
        key = _key(self.cfg)
        headers["Content-Type"] = "application/json"
        if self.cfg["provider"] == "anthropic":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers=headers, method="POST")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                return None  # Do not forward authorization to another host.

        opener = urllib.request.build_opener(NoRedirect)
        attempts = self.cfg.get("_request_attempts", 3)
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise ModelError("模型请求重试设置无效")
        for attempt in range(attempts):
            reservation = ledger.reserve(self.cfg.get('_usage_turn', 'unknown'), attempt+1) if ledger else None
            try:
                with opener.open(request, timeout=self.cfg["timeout_seconds"]) as response:
                    raw = json.load(response)
                if ledger:
                    ledger.settle(reservation, raw.get('usage'))
                break
            except urllib.error.HTTPError as exc:
                if ledger:
                    ledger.failed(reservation, 'HTTP_'+str(exc.code))
                # Never show a provider response body: it may echo parts of the request.
                raise ModelError(f"模型 API 返回 HTTP {exc.code}；请检查地址、模型名、密钥与额度") from None
            except (urllib.error.URLError, TimeoutError, http.client.HTTPException,
                    ConnectionError, json.JSONDecodeError) as exc:
                if ledger:
                    ledger.failed(reservation, type(exc).__name__)
                if attempt == attempts - 1:
                    raise ModelError(f"模型 API 连接失败（{attempts} 次尝试，不显示凭据）：{type(exc).__name__}") from None
                time.sleep((2, 5)[attempt])
        return parse_response(self.cfg["provider"], raw)
