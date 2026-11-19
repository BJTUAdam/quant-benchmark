"""Constrained tools for a strategy-writing agent.

The model sees only the workspace files returned by these functions. Code execution
uses bubblewrap with only that workspace mounted; API credentials are not passed in.
"""
from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any
from runtime_environment import sandbox_runtime_mounts


ALLOWED_WRITES = {"strategy.py", "CHANGELOG.md", "RESEARCH.md"}
ALLOWED_IMPORTS = {"math", "statistics", "collections", "itertools", "functools", "typing", "dataclasses", "numpy", "pandas"}
FORBIDDEN_CALLS = {"open", "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars", "input", "breakpoint"}
TOOLS = [
    {"name": "list_files", "description": "列出当前工作区中的可见文件，不包括缓存和密钥。", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_file", "description": "按行读取工作区内 .md/.py/.txt/.json/.csv 文件，支持分段阅读。", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}},
    {"name": "inspect_data", "description": "按 symbol 和 t 区间读取当前可用的股票日线/分钟线或期货 quotes/rules；每次最多 120 行。", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["daily", "minute", "quotes", "rules"]}, "symbol": {"type": "string"}, "start_t": {"type": "integer"}, "end_t": {"type": "integer"}, "max_rows": {"type": "integer"}}, "required": ["kind", "symbol", "start_t", "end_t"], "additionalProperties": False}},
    {"name": "write_file", "description": "写入 strategy.py / RESEARCH.md；CHANGELOG.md 仅允许保留全部旧内容后追加。", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}},
    {"name": "append_changelog", "description": "在 CHANGELOG.md 末尾追加本轮版本记录；只传新增文字，不要重发旧记录。", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False}},
    {"name": "run_backtest", "description": "在隔离工作区运行标准回测；可用 params 临时覆盖参数，或用 sweep 对当前可用数据做参数扫描。交付验证须不传 params/sweep/start/end，完整运行当前 strategy.py。", "parameters": {"type": "object", "properties": {"params": {"type": "object"}, "sweep": {"type": "object"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, "additionalProperties": False}},
    {"name": "run_research", "description": "在隔离环境运行数据研究代码，可用 numpy、pandas、scipy；只能读取当前可用资料，不能联网，工作区仅 output/ 可写。此工具不允许执行策略回测；策略评估请使用 run_backtest/sweep。通过 print 返回结果。", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False}},
]

FORBIDDEN_RESEARCH_IMPORTS = {"harness", "run_backtest", "simulate", "subprocess", "runpy", "importlib"}
FORBIDDEN_RESEARCH_CALLS = {"exec", "eval", "compile", "__import__"}


def safe_path(workspace: pathlib.Path, name: str) -> pathlib.Path:
    if not isinstance(name, str) or not name or "\\" in name or pathlib.PurePosixPath(name).is_absolute():
        raise ValueError("只允许工作区内的相对路径")
    parts = pathlib.PurePosixPath(name).parts
    if any(part in {"..", ".", "", ".cache", ".secrets", "__pycache__"} or part.startswith(".") for part in parts):
        raise ValueError("路径越界或隐藏文件")
    target = (workspace / name).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError("路径越界")
    return target


def validate_strategy(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"策略语法错误：第 {exc.lineno} 行") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name.split(".")[0] for alias in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
            if not all(m in ALLOWED_IMPORTS for m in modules):
                raise ValueError("策略包含禁止的 import")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("策略不可读取双下划线属性")
        if isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__file__"}:
            raise ValueError("策略不可访问解释器内部对象")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            raise ValueError(f"策略不可调用 {node.func.id}")


def validate_research_code(source: str) -> None:
    """Keep exploratory Python separate from the metered strategy evaluator."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"研究代码语法错误：第 {exc.lineno} 行") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
            if roots & FORBIDDEN_RESEARCH_IMPORTS:
                raise ValueError("run_research 只用于数据研究；策略评估请使用 run_backtest/sweep")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_RESEARCH_IMPORTS:
                raise ValueError("run_research 只用于数据研究；策略评估请使用 run_backtest/sweep")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_RESEARCH_CALLS:
            raise ValueError("研究代码不能动态执行其他代码")


def sandbox_command(workspace: pathlib.Path, script: str, args: list[str], timeout: int = 300,
                    backend: str = "bubblewrap", docker_image: str | None = None) -> subprocess.CompletedProcess[str]:
    if script != "run_backtest.py":
        raise RuntimeError("只允许运行标准回测脚本")
    if backend == "docker":
        from docker_sandbox import execute
        return execute(docker_image, "backtest", workspace, args=args, timeout=timeout)
    if backend != "bubblewrap":
        raise RuntimeError("未知策略执行后端")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("未安装 bubblewrap，拒绝不隔离地运行模型生成的 Python 代码")
    guard = pathlib.Path(__file__).resolve().parent / "strategy_guard.py"
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
               "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
               "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
               "--dev-bind", "/dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--ro-bind", str(guard), "/guard.py",
               "--bind", str(workspace.resolve()), "/workspace", "--chdir", "/workspace",
               "--", "/usr/bin/python3", "/guard.py", "--backtest", *args]
    command[command.index("--"):command.index("--")] = sandbox_runtime_mounts()
    clean_env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONNOUSERSITE": "1"}
    return subprocess.run(command, cwd="/tmp", env=clean_env, capture_output=True, text=True, timeout=timeout)


def sandbox_research(workspace: pathlib.Path, code: str, backend: str = "bubblewrap",
                     docker_image: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    validate_research_code(code)
    if backend == "docker":
        from docker_sandbox import execute
        return execute(docker_image, "research", workspace, code=code, timeout=timeout)
    if backend != "bubblewrap":
        raise RuntimeError("未知研究执行后端")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("未安装 bubblewrap，拒绝不隔离地运行研究代码")
    output = workspace / "output"
    output.mkdir(exist_ok=True)
    runner = pathlib.Path(__file__).resolve().parent / "research_runner.py"
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
               "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
               "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
               "--dev-bind", "/dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--ro-bind", str(workspace), "/workspace",
               "--bind", str(output), "/workspace/output", "--chdir", "/workspace",
               "--ro-bind", str(runner), "/research.py",
               "--", "/usr/bin/python3", "/research.py", code]
    command[command.index("--"):command.index("--")] = sandbox_runtime_mounts()
    clean_env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONNOUSERSITE": "1"}
    return subprocess.run(command, cwd="/tmp", env=clean_env, capture_output=True, text=True, timeout=timeout)


class AgentTools:
    def __init__(self, workspace: pathlib.Path, max_backtests: int | None = None, max_visible_t: int = 143,
                 execution_backend: str = "bubblewrap", docker_image: str | None = None,
                 public_view: bool = False, max_research_calls: int | None = None,
                 max_strategy_evaluations: int | None = None,
                 max_sweep_evaluations: int | None = None,
                 research_timeout_seconds: int = 300, reserved_final_evaluations: int = 0):
        self.workspace = workspace.resolve()
        self.max_backtests = max_backtests
        self.max_visible_t = max_visible_t
        self.execution_backend = execution_backend
        self.docker_image = docker_image
        self.public_view = public_view
        self.max_research_calls = max_research_calls
        self.max_strategy_evaluations = max_strategy_evaluations
        if (type(reserved_final_evaluations) is not int or reserved_final_evaluations < 0 or
                (max_strategy_evaluations is not None and reserved_final_evaluations > max_strategy_evaluations)):
            raise ValueError("最终验证预留额度无效")
        self.reserved_final_evaluations = reserved_final_evaluations
        self.closeout_only = False
        self.max_sweep_evaluations = max_sweep_evaluations
        self.research_timeout_seconds = research_timeout_seconds
        self.backtests = 0
        self.research_calls = 0
        self.strategy_evaluations = 0
        self.passed_backtest = False
        self.passed_strategy_hash = None

    def execute(self, name: str, args: dict[str, Any]) -> str:
        try:
            closing = self.closeout_only or (self.max_strategy_evaluations is not None and
                self.strategy_evaluations >= self.max_strategy_evaluations - self.reserved_final_evaluations)
            if closing and name == "run_research":
                raise ValueError("本轮已进入收尾：请完成记录和最终默认参数验证，不再扩展研究")
            if name == "list_files":
                files = [str(p.relative_to(self.workspace)).replace(os.sep, "/") for p in self.workspace.rglob("*")
                         if p.is_file() and not ({".cache", "__pycache__", "output"} & set(p.relative_to(self.workspace).parts))
                         and not p.name.startswith(".")]
                return json.dumps(sorted(files)[:100], ensure_ascii=False)
            if name == "read_file":
                p = safe_path(self.workspace, args["path"])
                if not p.is_file() or p.suffix not in {".md", ".py", ".txt", ".json", ".csv"}:
                    raise ValueError("该文件不存在或不是可读的文本格式；分钟线请用 inspect_data")
                start = int(args.get("start_line", 1))
                count = int(args.get("max_lines", 140))
                if not (1 <= start <= 10000000 and 1 <= count <= 250):
                    raise ValueError("start_line/max_lines 超出范围")
                with p.open(encoding="utf-8") as handle:
                    selected = [f"{n}: {line.rstrip()}" for n, line in enumerate(handle, 1) if start <= n < start + count]
                return "\n".join(selected)[:30000] or "（该行范围无内容）"
            if name == "inspect_data":
                kind, symbol = args["kind"], args["symbol"]
                start, end = int(args["start_t"]), int(args["end_t"])
                limit = int(args.get("max_rows", 80))
                if not (0 <= start <= end <= self.max_visible_t and 1 <= limit <= 120):
                    raise ValueError("请求范围超出当前可用数据或 max_rows 不合法" if self.public_view else
                                     "t 区间尚未解锁或 max_rows 不合法")
                if (self.workspace / "data/market.json").is_file():
                    if kind not in ("quotes", "rules") or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", symbol):
                        raise ValueError("期货行情类型或合约代码不合法")
                    result = []
                    pattern = f"history/{kind}.csv" if self.public_view else f"part_*/{kind}.csv"
                    for p in sorted((self.workspace / "data").glob(pattern)):
                        with p.open("r", encoding="utf-8", newline="") as handle:
                            for row in csv.DictReader(handle):
                                t = int(row["t"])
                                if t > end:
                                    break
                                if start <= t <= end and row["symbol"] == symbol:
                                    result.append(row)
                                    if len(result) >= limit:
                                        return json.dumps(result, ensure_ascii=False)
                    return json.dumps(result, ensure_ascii=False)
                if kind not in ("daily", "minute") or not re.fullmatch(r"S(?:0[1-9]|[1-3][0-9]|4[0-5])", symbol):
                    raise ValueError("行情类型或代码不合法")
                directory = self.workspace / "data" / kind
                pattern = (("history.csv.gz" if kind == "minute" else "history.csv") if self.public_view else
                           "part_*.csv" + (".gz" if kind == "minute" else ""))
                result = []
                for p in sorted(directory.glob(pattern)):
                    open_fn = gzip.open if kind == "minute" else open
                    with open_fn(p, "rt", encoding="utf-8", newline="") as handle:
                        for row in csv.DictReader(handle):
                            t = int(row["t"])
                            if t > end:
                                break
                            if start <= t <= end and row["symbol"] == symbol:
                                result.append(row)
                                if len(result) >= limit:
                                    return json.dumps(result, ensure_ascii=False)
                return json.dumps(result, ensure_ascii=False)
            if name == "append_changelog":
                content = args["content"]
                if not isinstance(content, str) or not content.lstrip().startswith("## ") or len(content.encode("utf-8")) > 15000:
                    raise ValueError("新增记录必须以 ## 标题开头，且不超过 15 KB")
                with (self.workspace / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
                    handle.write("\n" + content.strip() + "\n")
                return "已在 CHANGELOG.md 末尾追加版本记录（旧内容未改动）"
            if name == "write_file":
                p = safe_path(self.workspace, args["path"])
                if p.name not in ALLOWED_WRITES or p.parent != self.workspace:
                    raise ValueError("只允许写工作区根目录的 strategy.py、CHANGELOG.md、RESEARCH.md")
                content = args["content"]
                if not isinstance(content, str) or len(content.encode("utf-8")) > 100000:
                    raise ValueError("内容不是文本或超过 100 KB")
                if p.name == "strategy.py":
                    validate_strategy(content)
                    if (self.max_strategy_evaluations is not None and
                            self.strategy_evaluations >= self.max_strategy_evaluations and
                            hashlib.sha256(content.encode("utf-8")).hexdigest() != self.passed_strategy_hash):
                        raise ValueError("评估额度已用尽，不能写入需要重新验证的策略；请保留已验证版本并完成记录")
                if p.name == "CHANGELOG.md" and p.is_file() and not content.startswith(p.read_text(encoding="utf-8")):
                    raise ValueError("CHANGELOG 只能追加，不能压缩、删除或改写历史；请用 append_changelog")
                p.write_text(content, encoding="utf-8")
                return f"已写入 {p.name}（{len(content)} 字符）"
            if name == "run_backtest":
                if self.max_backtests is not None and self.backtests >= self.max_backtests:
                    raise ValueError("本轮回测次数已用尽，请总结当前结果")
                strategy = self.workspace / "strategy.py"
                if not strategy.is_file():
                    raise ValueError("请先写 strategy.py")
                validate_strategy(strategy.read_text(encoding="utf-8"))
                params = args.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params 必须是对象")
                sweep = args.get("sweep")
                # Only an argument-free call qualifies for reserved final validation.
                full_default = not any(key in args for key in ("params", "sweep", "start", "end"))
                if sweep is not None and (not isinstance(sweep, dict) or not sweep or
                                          any(not isinstance(v, list) or not v for v in sweep.values())):
                    raise ValueError("sweep 必须是参数名到非空候选列表的映射")
                evaluations = math.prod(len(values) for values in sweep.values()) if sweep is not None else 1
                if closing and not full_default:
                    raise ValueError("本轮已进入收尾；仅允许不传 params/sweep/start/end 的完整默认参数验证")
                if self.max_sweep_evaluations is not None and sweep is not None and evaluations > self.max_sweep_evaluations:
                    raise ValueError(f"单次 sweep 最多 {self.max_sweep_evaluations} 组；请缩小候选范围")
                if (self.max_strategy_evaluations is not None and
                        self.strategy_evaluations + evaluations > self.max_strategy_evaluations):
                    remaining = max(0, self.max_strategy_evaluations - self.strategy_evaluations)
                    raise ValueError(f"本次研发只剩 {remaining} 次策略评估额度；请根据已有结果完成交付")
                if (not full_default and self.max_strategy_evaluations is not None and
                        self.strategy_evaluations + evaluations >
                        self.max_strategy_evaluations - self.reserved_final_evaluations):
                    remaining = max(0, self.max_strategy_evaluations - self.reserved_final_evaluations -
                                    self.strategy_evaluations)
                    raise ValueError(f"探索可用额度只剩 {remaining} 组；最后 {self.reserved_final_evaluations} 组"
                                     "仅用于完整默认参数验证及必要修正，请缩小扫描或选定策略交付")
                cli = ["--out", f"/workspace/output/check_{self.backtests + 1}"]
                if params:
                    cli += ["--params", json.dumps(params, ensure_ascii=False)]
                if sweep is not None:
                    cli += ["--sweep", json.dumps(sweep, ensure_ascii=False)]
                start = int(args.get("start", 0))
                end = int(args.get("end", self.max_visible_t))
                if not 0 <= start <= end <= self.max_visible_t:
                    raise ValueError("回测区间超出当前可用数据" if self.public_view else
                                     "回测区间包含尚未解锁的行情")
                if "start" in args:
                    cli += ["--start", str(start)]
                if "end" in args:
                    cli += ["--end", str(end)]
                self.backtests += 1
                self.strategy_evaluations += evaluations
                try:
                    result = sandbox_command(self.workspace, "run_backtest.py", cli,
                                             backend=self.execution_backend, docker_image=self.docker_image)
                except subprocess.TimeoutExpired:
                    return "回测超时（300 秒）；请简化策略"
                if result.returncode != 0:
                    return "回测失败，退出码 " + str(result.returncode) + "\n" + (result.stderr[-8000:] or result.stdout[-8000:])
                if full_default:
                    self.passed_backtest = True
                    self.passed_strategy_hash = hashlib.sha256(strategy.read_bytes()).hexdigest()
                label = (("完整默认参数研发回测成功" if full_default else "研发回测成功（不计为交付验证）")
                         if self.public_view else
                         ("完整默认参数回测成功" if full_default else "研究回测成功（不计为交付验证）"))
                quota = (f"；策略评估累计 {self.strategy_evaluations}/{self.max_strategy_evaluations}"
                         if self.max_strategy_evaluations is not None else "")
                certification = ("\n当前文件版本已通过完整默认参数验证；不代表收益最优，"
                                 "修改策略后需重新验证，仍须补齐研究与变更记录并回复交付。" if full_default else "")
                return label + quota + "，输出目录 output/check_" + str(self.backtests) + certification + "\n" + result.stdout[-9000:]
            if name == "run_research":
                code = args["code"]
                if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 30000:
                    raise ValueError("研究代码必须是非空文本，且不超过 30 KB")
                validate_research_code(code)
                if self.max_research_calls is not None and self.research_calls >= self.max_research_calls:
                    raise ValueError("数据研究调用额度已用尽；请根据已有研究完成当前最佳策略的交付")
                self.research_calls += 1
                try:
                    result = sandbox_research(self.workspace, code, backend=self.execution_backend,
                                              docker_image=self.docker_image,
                                              timeout=self.research_timeout_seconds)
                except subprocess.TimeoutExpired:
                    return f"研究代码超时（{self.research_timeout_seconds} 秒）；请缩小单次研究范围"
                if result.returncode:
                    return "研究代码失败，退出码 " + str(result.returncode) + "\n" + result.stderr[-5000:]
                quota = (f"（数据研究调用 {self.research_calls}/{self.max_research_calls}）"
                         if self.max_research_calls is not None else "")
                return "研究代码完成" + quota + "：\n" + (result.stdout[-12000:] or "（无标准输出）")
            raise ValueError(f"不存在的工具: {name}")
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
            return "工具错误：" + str(exc)[:1500]
