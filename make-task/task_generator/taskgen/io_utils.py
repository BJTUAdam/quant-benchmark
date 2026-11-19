from __future__ import annotations
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    def invalid_constant(_):
        raise ValueError("JSON不能包含NaN或Infinity")
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid_constant)
    def finite(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON数值必须有限")
        if isinstance(item, dict):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)
    finite(value)
    return value


def write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".taskgen-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows antivirus/indexer or a concurrent reader may hold the destination
        # briefly. Retry only sharing/access errors; never truncate the original.
        for attempt in range(6):
            try:
                os.replace(temp, path)
                break
            except PermissionError as error:
                if attempt == 5 or getattr(error, "winerror", None) not in {5, 32, 33}:
                    raise
                time.sleep(min(0.025 * (2 ** attempt), 0.2))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def catalog(root, name):
    return read_json(Path(root) / "catalog" / (name + ".json"))
