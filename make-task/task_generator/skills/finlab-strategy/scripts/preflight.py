"""Static submission checks. This script never imports or runs submitted code.

Runtime acceptance, trading validity and isolation are verified by the platform,
not by AST checks. Warnings identify dynamic code that needs runtime inspection.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

FILES = {"strategy.py", "backtest.py", "params.json", "RESEARCH.md", "CHANGELOG.md"}
ALLOWED_IMPORTS = {"finlab", "pandas", "numpy", "math", "statistics", "collections", "datetime", "json", "typing",
                   "itertools", "functools", "decimal", "random", "re", "dataclasses", "enum", "copy", "bisect", "heapq"}
API_VERSION = "finlab.strategy.v2"
CALLBACKS = {"initialize", "on_bars", "on_order", "on_trade", "on_finish"}
CONTEXT_METHODS = {"query", "order", "cancel"}
KNOWN_WRONG_METHODS = {"history", "buy", "sell", "order_target", "order_target_percent", "target_percent", "get_position", "close_position", "subscribe"}


def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return _DYNAMIC


_DYNAMIC = object()


def reject_nonfinite(value):
    raise ValueError("non-finite JSON constant: " + value)


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_nonfinite)
    # Also catches values such as 1e10000 that parse as infinity.
    json.dumps(value, allow_nan=False)
    return value


def check(folder, *, task=None):
    folder = Path(folder)
    errors, warnings, hashes = [], [], {}

    def add(code, filename, node=None, detail="", warning=False):
        row = {"code": code, "file": filename, "line": getattr(node, "lineno", None), "detail": detail}
        target = warnings if warning else errors
        if row not in target:
            target.append(row)

    contract = None
    if task is not None:
        try:
            value = read_json(Path(task) / "bridge_contract.json")
            if value.get("schema_version") != "finlab.bridge_contract.v2" or not isinstance(value.get("endpoints"), list):
                raise ValueError("not a v2 bridge contract")
            contract = {}
            for endpoint in value["endpoints"]:
                name, fields = endpoint["api_name"], endpoint["fields"]
                if not isinstance(name, str) or not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
                    raise ValueError("invalid endpoint fields")
                contract[name] = set(fields)
            if value.get("binding_status") not in {"bound", "frozen"}:
                add("TASK_NOT_BOUND", "bridge_contract.json", detail="Static contract review only; data binding is not verified.", warning=True)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            add("TASK_CONTRACT_INVALID", "bridge_contract.json", detail=type(exc).__name__)

    trees = {}
    if not folder.is_dir() or folder.is_symlink():
        add("SUBMISSION_DIRECTORY_MISSING_OR_LINK", str(folder))
    for name in sorted(FILES):
        path = folder / name
        if not path.is_file() or path.is_symlink():
            add("REQUIRED_FILE_MISSING_OR_LINK", name)
            continue
        if path.stat().st_size > 1_000_000:
            add("FILE_TOO_LARGE", name)
            continue
        try:
            body = path.read_bytes()
            hashes[name] = hashlib.sha256(body).hexdigest()
            content = body.decode("utf-8-sig")
            if name.endswith(".py"):
                trees[name] = ast.parse(content, filename=name)
            elif name == "params.json":
                params = read_json(path)
                if not isinstance(params, dict):
                    add("PARAMS_MUST_BE_OBJECT", name)
            elif not content.strip():
                add("EMPTY_RESEARCH_DOCUMENT", name, warning=True)
        except (UnicodeError, SyntaxError, ValueError, RecursionError, OSError) as exc:
            add("INVALID_SYNTAX_ENCODING_OR_JSON", name, detail=type(exc).__name__)
    if folder.is_dir():
        extra = sorted(p.name for p in folder.iterdir() if p.name not in FILES)
        if extra:
            add("EXTRA_FILES_NOT_SUBMITTED", str(folder), detail=", ".join(extra), warning=True)

    def check_query(filename, call, api_node, fields_node):
        api = literal(api_node) if api_node is not None else _DYNAMIC
        if api is _DYNAMIC:
            add("DYNAMIC_API_NEEDS_RUNTIME_CHECK", filename, call, warning=True)
            return
        if not isinstance(api, str):
            add("API_NAME_MUST_BE_STRING", filename, call)
            return
        if contract is not None and api not in contract:
            add("API_NOT_IN_TASK_CONTRACT", filename, call, api)
            return
        if fields_node is not None and contract is not None:
            fields = literal(fields_node)
            if fields is _DYNAMIC:
                add("DYNAMIC_FIELDS_NEED_RUNTIME_CHECK", filename, call, warning=True)
            elif isinstance(fields, str) and fields.strip():
                requested = {f.strip() for f in fields.split(",") if f.strip()}
                missing = sorted(requested - contract[api])
                if missing:
                    add("FIELDS_NOT_IN_TASK_CONTRACT", filename, call, ", ".join(missing))
            elif fields is not _DYNAMIC and not isinstance(fields, str):
                add("FIELDS_SHOULD_BE_COMMA_SEPARATED_STRING", filename, call, warning=True)

    for filename, tree in trees.items():
        imported, clients, imports_seen = {}, set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                for alias in node.names:
                    imported[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                    imports_seen.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.level:
                    add("RELATIVE_IMPORT_NOT_PACKAGED", filename, node)
                for alias in node.names:
                    imported[alias.asname or alias.name] = (node.module or "") + "." + alias.name
                    imports_seen.add((node.module or "") + "." + alias.name)
            else:
                continue
            for name in names:
                if name.split(".")[0] not in ALLOWED_IMPORTS:
                    add("IMPORT_NOT_ALLOWED", filename, node, name)
        for full_name in imports_seen:
            if full_name.startswith("finlab.") and ".".join(full_name.split(".")[:2]) not in {"finlab.sdk", "finlab.tushare", "finlab.common"}:
                add("PLATFORM_PRIVATE_MODULE_UNAVAILABLE", filename, detail=full_name)
        # Record ordinary pro_api clients. Unknown arbitrary objects are not
        # interpreted as market clients just because they have a query method.
        def dotted(node):
            if isinstance(node, ast.Name):
                return imported.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return dotted(node.value) + "." + node.attr
            return ""
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                if dotted(node.value.func) in {"finlab.tushare.pro_api", "finlab.tushare.ProClient"}:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    clients.update(t.id for t in targets if isinstance(t, ast.Name))

        if filename == "strategy.py":
            classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy"]
            if not classes:
                dynamic_strategy = any(isinstance(n, (ast.Assign, ast.AnnAssign)) and any(
                    isinstance(t, ast.Name) and t.id == "Strategy" for t in (n.targets if isinstance(n, ast.Assign) else [n.target])) for n in tree.body)
                add("DYNAMIC_STRATEGY_CLASS" if dynamic_strategy else "STRATEGY_CLASS_MISSING", filename, warning=dynamic_strategy)
            for cls in classes:
                for node in cls.body:
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        if any(isinstance(t, ast.Name) and t.id == "api_version" for t in targets):
                            version = literal(node.value)
                            if version is _DYNAMIC:
                                add("DYNAMIC_API_VERSION", filename, node, warning=True)
                            elif version != API_VERSION:
                                add("STRATEGY_API_VERSION_MISMATCH", filename, node)
                if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "on_bars" for n in cls.body):
                    add("ON_BARS_INHERITED_OR_MISSING", filename, cls, warning=True)
                    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "on_bar" for n in cls.body):
                        add("RAW_AKQUANT_CALLBACK_NOT_SUPPORTED", filename, cls, "Implement on_bars(ctx), not the native engine on_bar(bar).")
                for method in cls.body:
                    if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if isinstance(method, ast.AsyncFunctionDef) and method.name in CALLBACKS:
                        add("ASYNC_CALLBACK_NOT_SUPPORTED", filename, method)
                    arguments = list(method.args.posonlyargs) + list(method.args.args)
                    if method.name in CALLBACKS:
                        supplied = 3 if method.name in {"on_order", "on_trade"} else 2
                        required = len(arguments) - len(method.args.defaults)
                        if required > supplied or (len(arguments) < supplied and method.args.vararg is None) or any(
                            default is None for default in method.args.kw_defaults):
                            add("CALLBACK_SIGNATURE_MISMATCH", filename, method, method.name)
                    context_names = {arguments[1].arg} if len(arguments) > 1 and (method.name in CALLBACKS or arguments[1].arg in {"ctx", "context"}) else set()
                    for node in ast.walk(method):
                        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in context_names:
                            context_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                    for node in ast.walk(method):
                        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                            continue
                        func = node.func
                        if not isinstance(func.value, ast.Name) or func.value.id not in context_names:
                            continue
                        if func.attr not in CONTEXT_METHODS:
                            add("UNKNOWN_CONTEXT_METHOD", filename, node, func.attr)
                            continue
                        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
                        if func.attr in {"order", "cancel"} and method.name in CALLBACKS - {"on_bars"}:
                            add("ORDER_ONLY_IN_ON_BARS", filename, node, method.name)
                        if func.attr == "order":
                            quantity = literal(node.args[1]) if len(node.args) > 1 else literal(kwargs["quantity"]) if "quantity" in kwargs else _DYNAMIC
                            if quantity is not _DYNAMIC and (not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or quantity <= 0):
                                add("ORDER_QUANTITY_MUST_BE_POSITIVE", filename, node)
                            for key, allowed in (("side", {"buy", "sell"}), ("tif", {"GTC", "DAY"})):
                                if key in kwargs:
                                    value = literal(kwargs[key])
                                    if value is not _DYNAMIC and (not isinstance(value, str) or value not in allowed):
                                        add("INVALID_ORDER_" + key.upper(), filename, node)
                        elif func.attr == "query":
                            check_query(filename, node, node.args[0] if node.args else kwargs.get("api_name"),
                                        kwargs.get("fields", node.args[1] if len(node.args) > 1 else None))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kwargs = {k.arg: k.value for k in node.keywords if k.arg}
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in clients:
                if func.attr == "query":
                    check_query(filename, node, node.args[0] if node.args else kwargs.get("api_name"), kwargs.get("fields", node.args[1] if len(node.args) > 1 else None))
                elif not func.attr.startswith("_"):
                    check_query(filename, node, ast.Constant(value=func.attr), kwargs.get("fields", node.args[0] if node.args else None))
            if dotted(func) == "finlab.tushare.pro_bar":
                add("PRO_BAR_DEPENDENCIES_NEED_TASK_REVIEW", filename, node,
                    "Check underlying quote, adjustment-factor and daily_basic endpoint permissions.", warning=True)
            if isinstance(func, ast.Name) and func.id in {"exec", "eval", "__import__"}:
                add("DYNAMIC_CODE_NOT_STATICALLY_VERIFIED", filename, node, warning=True)

        if filename == "backtest.py":
            entry = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and dotted(n.func) == "finlab.sdk.launch"]
            if not entry:
                add("SDK_LAUNCH_ENTRY_NOT_FOUND", filename)
            else:
                for call in entry:
                    kwargs = {k.arg: k.value for k in call.keywords if k.arg}
                    strategy_node = call.args[0] if call.args else kwargs.get("strategy_file")
                    params_node = call.args[1] if len(call.args) > 1 else kwargs.get("params_file")
                    if strategy_node is not None and literal(strategy_node) not in ("strategy.py", _DYNAMIC):
                        add("ENTRY_STRATEGY_FILE_NOT_SUBMITTED", filename, call)
                    if params_node is not None and literal(params_node) not in ("params.json", _DYNAMIC):
                        add("ENTRY_PARAMS_FILE_NOT_SUBMITTED", filename, call)

    return {"schema_version": "finlab.skill.preflight.v1", "passed": not errors, "static_only": True,
            "execution_verified": False, "security_verified": False, "errors": errors, "warnings": warnings,
            "files": hashes, "task_contract_checked": contract is not None,
            "note": "AST checks do not run code or establish trading correctness. Use the platform runner for runtime acceptance."}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("submission", type=Path)
    p.add_argument("--task", type=Path, help="Optional public task directory containing bridge_contract.json")
    args = p.parse_args(argv)
    result = check(args.submission, task=args.task)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
