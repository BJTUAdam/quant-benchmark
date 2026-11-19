"""Full conversation replay and a conservative context budget check.

No summarization, truncation, strategy selection, or provider calls occur here.
The provider's last observed input-token count anchors subsequent estimates.
"""
from __future__ import annotations

import copy
import hashlib
import json

from model_client import ModelError, prepare_request


class ContextLimitError(ModelError):
    pass


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def new_session(system: str) -> dict:
    return {"schema_version": 1, "messages": [{"role": "system", "content": system}],
            "turns": [], "requests": [], "last_request": None}


def validate_history(messages: list[dict]) -> None:
    if not messages or messages[0].get("role") != "system":
        raise RuntimeError("持续会话缺少最初的系统消息")
    pending = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"} or (role == "system" and index != 0):
            raise RuntimeError("持续会话的消息角色或系统消息位置不正确")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise RuntimeError("持续会话的工具结果没有对应的调用")
            pending.remove(call_id)
        else:
            if pending:
                raise RuntimeError("持续会话存在未完成的工具调用")
            if role == "assistant":
                calls = message.get("tool_calls", [])
                ids = [call.get("id") for call in calls]
                if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
                    raise RuntimeError("持续会话的工具调用 ID 不正确")
                pending = set(ids)
    if pending:
        raise RuntimeError("持续会话存在未完成的工具调用")


def validate_session(session: dict) -> None:
    if session.get("schema_version") != 1:
        raise RuntimeError("不支持的持续会话格式")
    validate_history(session["messages"])
    previous_end = 1  # the single system message precedes the first task
    for turn in session["turns"]:
        start, end = turn["start"], turn["end"]
        if start != previous_end or not start < end <= len(session["messages"]):
            raise RuntimeError("持续会话的阶段边界不连续")
        if session["messages"][start]["role"] != "user" or session["messages"][end - 1]["role"] != "assistant":
            raise RuntimeError("持续会话的阶段未完整交付")
        previous_end = end
    if previous_end != len(session["messages"]):
        raise RuntimeError("持续会话含有阶段记录之外的消息")


def begin_turn(session: dict, prompt: str) -> tuple[dict, int]:
    validate_session(session)
    working = copy.deepcopy(session)  # never mutate the preceding checkpoint's state
    start = len(working["messages"])
    working["messages"].append({"role": "user", "content": prompt})
    return working, start


def input_budget(cfg: dict, messages: list[dict], tools: list[dict], last_request: dict | None) -> dict:
    """Bound additional input by UTF-8 bytes; anchor old input to API usage.

    This is a conservative engineering estimate, not an exact tokenizer. A
    separate margin and output reserve protect the model's context limit.
    If usage is unavailable, the entire serialized request is counted in bytes.
    """
    validate_history(messages)
    _, _, payload = prepare_request(cfg, messages, tools)
    projected = payload["messages"]
    static = {key: value for key, value in payload.items() if key != "messages"}
    count = last_request.get("message_count", -1) if last_request else -1
    anchored = (last_request is not None and 0 <= count <= len(projected)
                and last_request.get("prefix_sha256") == digest(projected[:count])
                and last_request.get("static_sha256") == digest(static)
                and isinstance(last_request.get("prompt_tokens"), int))
    if anchored:
        added = projected[count:]
        estimate = last_request["prompt_tokens"] + len(encoded(added)) + len(added) * 32 + 256
    else:
        estimate = len(encoded(payload)) + len(projected) * 32 + 256
    window = cfg["context_window_tokens"]
    reserve = cfg["max_tokens"] + cfg.get("context_margin_tokens", 4096)
    result = {"estimated_input_tokens": estimate, "context_window_tokens": window,
              "reserved_tokens": reserve, "usage_anchored": anchored}
    if estimate + reserve > window:
        raise ContextLimitError(f"上下文预算不足：输入估算 {estimate}，输出及余量 {reserve}，窗口 {window}；"
                                "本阶段不提交，不裁剪或重置历史")
    return result


def observe_request(session: dict, cfg: dict, tools: list[dict], budget: dict,
                    message_count: int, usage: dict | None, turn_name: str) -> None:
    usage = usage or {}
    record = {"turn": turn_name, "message_count": message_count, **budget}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            record[key] = value
    session["requests"].append(record)
    if "prompt_tokens" in record:
        _, _, payload = prepare_request(cfg, session["messages"][:message_count], tools)
        session["last_request"] = {
            "message_count": message_count, "prompt_tokens": record["prompt_tokens"],
            "prefix_sha256": digest(payload["messages"]),
            "static_sha256": digest({k: v for k, v in payload.items() if k != "messages"})}


def finish_turn(session: dict, start: int, turn_name: str, visible_part: int) -> dict:
    session["turns"].append({"name": turn_name, "start": start, "end": len(session["messages"]),
                             "visible_part": visible_part})
    validate_session(session)
    return session
