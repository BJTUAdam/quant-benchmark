"""Public file memory and tool-linked research evidence. No model or hidden data access."""
from __future__ import annotations

import hashlib
import json
import re
import pathlib

import agent_view


def digest(text):
    # Canonical text hashes are portable across Windows CRLF and Linux LF files.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def public_excerpt(text, limit=4000):
    # Agent-authored files are data, not instructions. Drop sensitive/control lines.
    lines = [line for line in text.splitlines()
             if not agent_view.FORBIDDEN.search(line)
             and not re.search(r"sk-|api[_ -]?key|密钥|https?://", line, re.I)]
    return "\n".join(lines)[-limit:]


def memory_from_files(files):
    """The append-only changelog carries decisions across stages; retain source hashes."""
    return {"schema_version": 1, "sources": [
        {"file": name, "sha256": digest(files[name]), "excerpt": public_excerpt(files[name]),
         "truncated": len(files[name]) > 4000}
        for name in ("RESEARCH.md", "CHANGELOG.md") if isinstance(files.get(name), str)]}


def tool_failed(text):
    return str(text).startswith(("工具错误：", "错误：", "回测失败", "回测超时", "研究代码失败", "研究代码超时"))


def evidence_record(call, output, strategy_hash, max_visible_t, number):
    """One experiment references exact call, parameters, file version and public result.

    A sweep remains one experiment with its full parameter grid, never inflated
    into claims of independent research. Success is established by tool output.
    """
    if call["name"] not in {"run_backtest", "run_research"}:
        return None
    args = call.get("arguments", {})
    if not isinstance(args, dict):
        args = {}
    is_backtest = call["name"] == "run_backtest"
    success = str(output).startswith(("完整默认参数", "研发回测成功", "研究回测成功", "研究代码完成"))
    return {"id": f"experiment-{number}", "tool_call_id": call["id"], "tool": call["name"],
            "strategy_sha256": strategy_hash if is_backtest else None,
            "arguments_sha256": digest(json.dumps(args, sort_keys=True, ensure_ascii=False)),
            "parameters": {k: args[k] for k in ("params", "sweep") if k in args},
            "range": [args.get("start", 0), args.get("end", max_visible_t)] if is_backtest else None,
            "status": "failed" if tool_failed(output) or call.get("argument_error") else "succeeded" if success else "unknown",
            "full_default_validation": bool(is_backtest and success and not call.get("argument_error")
                                            and not any(k in args for k in ("params", "sweep", "start", "end"))),
            "result_sha256": digest(str(output)), "result_excerpt": public_excerpt(str(output), 2500)}


def collect(calls):
    return [tool["evidence"] for call in calls for tool in call.get("tools", []) if tool.get("evidence")]


def submission_context(prompt, memory, files, evidence, reply):
    return {"public_task": prompt, "memory": memory,
            "documents": [{"file": name, "sha256": digest(files[name]),
                           "excerpt": public_excerpt(files[name])}
                          for name in ("RESEARCH.md", "CHANGELOG.md") if isinstance(files.get(name), str)],
            "strategy_sha256": digest(files["strategy.py"]) if isinstance(files.get("strategy.py"), str) else None,
            "evidence": evidence[-12:], "evidence_total": len(evidence),
            "submission": public_excerpt(reply or "", 2000),
            "scope": "Only public excerpts and actual research results; not a financial performance certification."}


def audit_evidence(transcript, initial_files, max_visible_t):
    """Rebuild evidence from transcript and file writes; reject invented measurements."""
    if "research_evidence" not in transcript:  # Historical transcripts remain readable.
        return
    files, pending, found = dict(initial_files), {}, []
    review_inputs = {}
    memory = memory_from_files(initial_files)
    actor_calls = 0
    for msg in transcript["messages"]:
        if msg["role"] == "assistant":
            actor_calls += 1
            pending = {c["id"]: c for c in msg.get("tool_calls", [])}
            if not pending:
                review_inputs[actor_calls] = submission_context(transcript["messages"][1]["content"],
                    memory, files, found, msg.get("content"))
        elif msg["role"] == "tool":
            call = pending.pop(msg["tool_call_id"])
            args, output = call.get("arguments", {}), msg["content"]
            current = files.get("strategy.py")
            record = evidence_record(call, output, digest(current) if isinstance(current, str) else None,
                                     max_visible_t, len(found) + 1)
            if record:
                found.append(record)
            if call["name"] == "write_file" and str(output).startswith("已写入"):
                path = str(pathlib.PurePosixPath(args.get("path", "")))
                if path in files:
                    files[path] = args["content"].replace("\r\n", "\n").replace("\r", "\n")
            elif call["name"] == "append_changelog" and str(output).startswith("已在 CHANGELOG.md"):
                content = args["content"].strip().replace("\r\n", "\n").replace("\r", "\n")
                files["CHANGELOG.md"] = (files.get("CHANGELOG.md") or "") + "\n" + content + "\n"
    if transcript["research_evidence"] != found or transcript.get("memory_input") != memory:
        raise ValueError("研究证据或历史记忆与工具轨迹不一致")
    if collect(transcript.get("call_history", [])) != found:
        raise ValueError("调用记录与研究证据不一致")
    for review in transcript.get("user_agent", {}).get("submission_reviews", []):
        if review["context"] != review_inputs.get(review["actor_call"]):
            raise ValueError("语义审阅输入与交付记录不一致")
        final = transcript.get("delivery_review", {})
        if final.get("semantic_assessment") != review["assessment"] or final.get("semantic_quality") != review["assessment"]["decision"]:
            raise ValueError("最终语义审阅结果与原始记录不一致")
