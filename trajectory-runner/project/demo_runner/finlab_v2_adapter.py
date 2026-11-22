"""v2 task tools and trusted backend bridge; no legacy task imports."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import uuid

from agent_tools import sandbox_research

SUBMISSION = ("strategy.py", "backtest.py", "params.json", "RESEARCH.md", "CHANGELOG.md")
EXECUTABLE = SUBMISSION[:3]
PUBLIC = (*SUBMISSION, "TASK.md", "DATA_API.md", "SUBMISSION.md", "bridge_contract.json",
          "runtime_profile.json", "task_input.draft.json")


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def fingerprint(folder, names=EXECUTABLE):
    return {name: digest(folder / name) for name in names if (folder / name).is_file()}


def snapshot_submission(source, dest):
    dest.mkdir(parents=True, exist_ok=False)
    for name in SUBMISSION:
        p = source / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 1_000_000:
            raise ValueError("SUBMISSION_INVALID")
        shutil.copyfile(p, dest / name)
    return fingerprint(dest, SUBMISSION)


class BackendError(RuntimeError):
    def __init__(self, code, strategy_code=None):
        self.code = code
        self.strategy_code = strategy_code
        super().__init__("FINLAB_" + code + (f" ({strategy_code})" if strategy_code else ""))


class Backend:
    def __init__(self, cfg, root):
        self.cfg, self.root = cfg, root
        self.episode = root / "private" / "episode"

    def call(self, action, **kwargs):
        request = {"action": action, "episode": str(self.episode), **kwargs}
        script = pathlib.Path(__file__).with_name("finlab_v2_worker.py")
        # The backend never needs either the Qwen key or the upstream data token.
        env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "SYSTEMROOT",
               "TEMP", "TMP", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "FINLAB_BATCH_OWNER") if k in os.environ}
        result = subprocess.run([self.cfg["finlab_python"], "-I", "-B", "-X", "utf8", str(script), self.cfg["finlab_root"]],
            input=json.dumps(request), capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=self.cfg["finlab_root"], timeout=None if action in {"run", "prepare"} else 180)
        try:
            reply = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("FINLAB_RESPONSE_INVALID；请核对 v2 Python 环境") from None
        if result.returncode or not reply.get("ok"):
            raise BackendError(reply.get("code", "PROCESS_FAILED"), reply.get("strategy_code"))
        return reply["result"]


def definition(name, description, properties, required=()):
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False}}


STRING = {"type": "string"}
TOOLS = [
    definition("list_files", "列出当前公开工作区文件。", {}),
    definition("read_file", "读取公开文件；大文件用 offset 分页，每页最多 24000 字符。",
               {"path": STRING, "offset": {"type": "integer", "minimum": 0}}, ["path"]),
    definition("write_file", "写入 strategy.py、backtest.py、params.json 或 RESEARCH.md。",
               {"path": STRING, "content": STRING}, ["path", "content"]),
    definition("append_changelog", "追加本轮研究与维护决定。", {"content": STRING}, ["content"]),
    definition("query_data", "通过平台受限接口查询当前可见行情，结果保存到 data/；越界整次拒绝。",
               {"api": STRING, "params": {"type": "object"}, "fields": STRING}, ["api"]),
    definition("run_research", "在禁网 Docker 中分析公开资料；读取 query_data 保存的 JSON。不得执行策略回测。",
               {"code": STRING}, ["code"]),
    definition("run_local_backtest", "自己编写完整研究回测 Python 代码，只读取 query_data 保存的公开数据；"
               "禁网 Docker 执行，输出由你自己计算的统计结果，不能调用平台评测器。",
               {"code": STRING}, ["code"]),
    definition("run_backtest", "仅验证当前三个执行文件是否能在平台运行；不返回研究期收益。"
               "交付前不带参数调用一次。", {}),
]


class Tools:
    def __init__(self, backend, workspace, cfg, stage, persist):
        self.backend, self.workspace, self.cfg = backend, workspace, cfg
        self.stage, self.persist = stage, persist

    def status(self):
        return {"research_calls": self.stage["research_calls"],
                "data_queries": self.stage["data_queries"],
                "evaluations": self.stage["evaluations"],
                "remaining_evaluations": (None if self.cfg.get("evidence_driven")
                    else self.cfg["max_strategy_evaluations"] - self.stage["evaluations"]),
                "execution_verified": self.stage.get("verified") == fingerprint(self.workspace)
                    and len(fingerprint(self.workspace)) == 3}

    def closing(self):
        if self.cfg.get("evidence_driven"):
            return False
        return (self.stage["calls"] >= self.cfg["max_model_calls"] or
                self.stage["evaluations"] >= self.cfg["max_strategy_evaluations"] - self.cfg["reserved_final_evaluations"])

    def path(self, name):
        if not isinstance(name, str) or "\\" in name:
            raise ValueError("公开文件路径无效")
        rel = pathlib.PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("文件不在公开工作区")
        target = self.workspace / rel
        if not target.resolve().is_relative_to(self.workspace.resolve()):
            raise ValueError("文件不在公开工作区")
        if any(p.is_symlink() for p in [target, *target.parents] if p != self.workspace.parent):
            raise ValueError("禁止符号链接")
        return target

    def reserve(self, field, limit):
        if not self.cfg.get("evidence_driven") and self.stage[field] >= limit:
            raise ValueError(field + " 额度已用尽")
        self.stage[field] += 1
        self.persist()  # Persist before dispatch; timeouts do not refund budget.

    def execute(self, name, args):
        try:
            if not isinstance(args, dict):
                raise ValueError("工具参数必须为对象")
            schema = next((t["parameters"] for t in TOOLS if t["name"] == name), None)
            if schema is None or set(args) - set(schema["properties"]) or not set(schema["required"]) <= set(args):
                raise ValueError("未知工具或参数")
            if self.closing() and name in {"query_data", "run_research", "run_local_backtest"}:
                raise ValueError("已进入收尾，只允许整理交付和完整默认参数验证")
            result = self.dispatch(name, args)
            return json.dumps({"result": result, "budget": self.status()}, ensure_ascii=False, allow_nan=False)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            # Tool inputs may be corrected; backend exception bodies are sanitized.
            return json.dumps({"error": str(exc)[:400] if isinstance(exc, (ValueError, RuntimeError))
                               else type(exc).__name__, "budget": self.status()}, ensure_ascii=False)

    def dispatch(self, name, args):
        if name == "list_files":
            return sorted(p.relative_to(self.workspace).as_posix() for p in self.workspace.rglob("*")
                          if p.is_file() and not p.is_symlink())[:2000]
        if name == "read_file":
            offset = args.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise ValueError("offset 必须为非负整数")
            path = self.path(args["path"])
            if path.stat().st_size > 50_000_000:
                raise ValueError("请使用研究工具分块读取大文件")
            text = path.read_text(encoding="utf-8")
            return {"text": text[offset:offset + 24000], "total_chars": len(text)}
        if name in {"write_file", "append_changelog"}:
            target = "CHANGELOG.md" if name == "append_changelog" else args["path"]
            if target not in SUBMISSION or (target == "CHANGELOG.md" and name != "append_changelog"):
                raise ValueError("只允许四个交付文件与追加 CHANGELOG.md")
            content = args["content"]
            if not isinstance(content, str) or len(content.encode()) > 900_000:
                raise ValueError("文件内容过大或类型错误")
            path = self.path(target)
            if name == "append_changelog":
                content = path.read_text(encoding="utf-8") + "\n" + content + "\n"
            if len(content.encode()) > 1_000_000:
                raise ValueError("交付文件不能超过 1 MB")
            path.write_text(content, encoding="utf-8")
            return "已写入；修改执行文件后须重新验证。"
        if name == "query_data":
            self.reserve("data_queries", self.cfg["max_data_queries"])
            result = self.backend.call("query", api=args["api"], params=args.get("params", {}), fields=args.get("fields", ""))
            rel = f"data/query_{self.stage['index']:02d}_{self.stage['data_queries']:03d}.json"
            save(self.path(rel), result)
            preview = json.dumps(result, ensure_ascii=False)
            return {"saved": rel, "preview": preview[:12000], "truncated": len(preview) > 12000}
        if name in {"run_research", "run_local_backtest"}:
            self.reserve("research_calls", self.cfg["max_research_calls"])
            result = sandbox_research(self.workspace, args["code"], backend="docker",
                docker_image=self.cfg["docker_image"], timeout=self.cfg["research_timeout_seconds"])
            if name == "run_local_backtest" and result.returncode == 0 and result.stdout.strip():
                self.stage["local_backtest_evidence"] = {
                    "call": self.stage["calls"],
                    "output_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                }
            return {"returncode": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-4000:]}
        if name == "run_backtest":
            if not self.cfg.get("evidence_driven") and self.stage["evaluations"] >= self.cfg["max_strategy_evaluations"]:
                raise ValueError("执行验证额度已用尽")
            dest = self.backend.root / "private" / "evaluations" / uuid.uuid4().hex
            snapshot_submission(self.workspace, dest)
            checked = self.backend.call("check", submission=str(dest))
            if not checked["passed"]:
                return {"submission_errors": checked["errors"], "execution_verified": False}
            self.reserve("evaluations", self.cfg["max_strategy_evaluations"])
            job = self.backend.call("run", submission=str(dest), research=True)
            # The platform's research job is used only as an execution check.
            # Its score artifacts stay private; strategy feedback comes from
            # the Agent's own local backtest or committed formal segments.
            self.stage["verified"] = fingerprint(dest)
            self.stage["verification_job"] = job["job_id"]
            self.persist()
            return {"execution_verified": True, "note": "仅确认提交可执行；未披露平台研发期评分。"}
        raise ValueError("未知工具")
