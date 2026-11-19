from __future__ import annotations
import ast
import json
from pathlib import Path
from .common import LabError, file_hash, digest, canonical
from .tasks import SUBMISSION_FILES

ALLOWED_IMPORTS = {"finlab", "pandas", "numpy", "math", "statistics", "collections", "datetime", "json", "typing",
                   "itertools", "functools", "decimal", "random", "re", "dataclasses", "enum", "copy", "bisect", "heapq"}


def check(folder):
    root = Path(folder).resolve()
    errors = []
    if not root.is_dir(): raise LabError("SUBMISSION_MISSING")
    hashes = {}
    for name in sorted(SUBMISSION_FILES):
        p = root / name
        if not p.is_file() or p.is_symlink():
            errors.append("缺少普通文件：" + name); continue
        if p.stat().st_size > 1_000_000:
            errors.append("文件过大：" + name); continue
        hashes[name] = file_hash(p)
        if p.suffix == ".py":
            try:
                tree = ast.parse(p.read_text(encoding="utf-8-sig"), filename=name)
                for node in ast.walk(tree):
                    names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                             [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                    if any(n.split(".")[0] not in ALLOWED_IMPORTS for n in names):
                        errors.append("未声明依赖：" + name)
            except (SyntaxError, UnicodeError): errors.append("语法或编码错误：" + name)
    try:
        params = json.loads((root / "params.json").read_text(encoding="utf-8-sig"))
        canonical(params)
        if not isinstance(params, dict): errors.append("params.json必须为对象")
    except (OSError, ValueError): errors.append("params.json无效")
    return {"schema_version": "finlab.submission_check.v2", "passed": not errors, "errors": sorted(set(errors)),
            "files": hashes, "submission_hash": digest(hashes), "execution_verified": False,
            "note": "静态检查不构成安全边界；真实代码只在隔离Docker内执行"}
