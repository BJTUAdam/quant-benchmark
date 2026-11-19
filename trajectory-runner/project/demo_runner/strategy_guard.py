"""Trusted wrapper for backtest/simulation execution inside bubblewrap.

Prevents strategy code from opening files or using network/process APIs while the
engine remains able to load its own data. This is defense in depth, not a proof
of safety against adversarial native extensions or interpreter escapes.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import pathlib
import sys
import types

# Preload permitted libraries before installing the active-code audit guard.
for module_name in ("math", "statistics", "collections", "itertools", "functools", "typing",
                    "dataclasses", "numpy", "pandas"):
    importlib.import_module(module_name)

_active = False


def _audit(event, args):
    if not _active:
        return
    if event == "open":
        path = args[0]
        flags = args[2] if len(args) > 2 else 0
        # pandas/numpy lazily import their own installation files on first use.
        # Allow read-only library imports; never permit workspace/evaluator data I/O.
        if isinstance(path, (str, bytes, os.PathLike)):
            value = os.fsdecode(path)
            if value.startswith("/usr/") and not (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                return
        raise PermissionError("策略执行时不能打开行情文件；请只使用 bars 数据")
    if event.startswith(("os.", "socket.", "subprocess.", "ctypes.")):
        raise PermissionError("策略执行时不允许文件系统、进程或网络操作")


sys.addaudithook(_audit)


@contextlib.contextmanager
def no_io():
    global _active
    previous = _active
    _active = True
    try:
        yield
    finally:
        _active = previous


def patch_engine(engine):
    if hasattr(engine, "load_bars"):
        original_load_bars = engine.load_bars

        def uncached_load_bars(*args, **kwargs):
            # Reading directly avoids loading pickle files from a writable workspace.
            kwargs["use_cache"] = False
            return original_load_bars(*args, **kwargs)

        engine.load_bars = uncached_load_bars

    def guarded_load_strategy(strategy_path):
        path = pathlib.Path(strategy_path)
        source = path.read_text(encoding="utf-8")  # trusted engine reads strategy before executing it
        if hasattr(engine, "check_imports"):
            engine.check_imports(source, path)
        strategy_error = getattr(engine, "StrategyError", ValueError)
        # An independent AST check also blocks common interpreter/reflection bypasses.
        import ast
        tree = ast.parse(source, filename=str(path))
        allowed = {"math", "statistics", "collections", "itertools", "functools", "typing", "dataclasses", "numpy", "pandas"}
        banned = {"open", "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars", "input", "breakpoint"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
                if not all(n in allowed for n in names):
                    raise strategy_error("禁止的 import")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise strategy_error("禁止双下划线属性")
            if isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__file__"}:
                raise strategy_error("禁止访问解释器内部对象")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned:
                raise strategy_error("禁止直接调用文件或反射接口")
        module = types.ModuleType("agent_strategy")
        module.__file__ = str(path)
        with no_io():
            exec(compile(source, str(path), "exec"), module.__dict__)
        if not isinstance(getattr(module, "PARAMS", None), dict) or not callable(getattr(module, "decide", None)):
            raise strategy_error("策略必须定义 PARAMS 字典和 decide() 函数")
        if hasattr(engine, "MINUTE_POSITION"):
            times = list(getattr(module, "DECISION_TIMES", engine.DEFAULT_DECISION_TIMES))
            if not times or any(m not in engine.MINUTE_POSITION for m in times):
                raise strategy_error("DECISION_TIMES 不是合法的分钟标签")
            module.DECISION_TIMES = sorted(set(times))
        json.dumps(module.PARAMS, allow_nan=False)
        original_decide = module.decide

        def guarded_decide(*args, **kwargs):
            with no_io():
                return original_decide(*args, **kwargs)

        module.decide = guarded_decide
        return module

    engine.load_strategy = guarded_load_strategy


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"--backtest", "--simulate"}:
        raise SystemExit("用法：strategy_guard.py --backtest|--simulate [args]")
    mode = sys.argv.pop(1)
    if mode == "--backtest":
        sys.path.insert(0, "/workspace")
        from backtester import engine
        patch_engine(engine)
        import run_backtest
        run_backtest.main()
    else:
        sys.path.insert(0, "/eval")
        from backtester import engine
        patch_engine(engine)
        import simulate
        simulate.main()


if __name__ == "__main__":
    main()
