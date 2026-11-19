"""Untrusted strategy process. The operator sends only each decision's visible view.

This file runs inside a networkless Docker container with no task or operator
directories mounted. Host-to-container frames use pickle for pandas views;
container-to-host frames are bounded JSON and are never unpickled by the host.
"""
from __future__ import annotations

import contextlib
import json
import os
import pickle
import sys
import types

import numpy as np
import pandas as pd

from guard import no_io


MAX_REQUEST = 1024 * 1024 * 1024


def read_exact(count: int) -> bytes:
    parts = []
    while count:
        chunk = sys.stdin.buffer.read(count)
        if not chunk:
            raise EOFError("strategy input closed")
        parts.append(chunk)
        count -= len(chunk)
    return b"".join(parts)


def receive() -> dict:
    header = read_exact(8)
    size = int.from_bytes(header, "big")
    if not 0 < size <= MAX_REQUEST:
        raise ValueError("strategy request too large")
    value = pickle.loads(read_exact(size))
    if not isinstance(value, dict):
        raise ValueError("invalid strategy request")
    return value


def send(value: dict) -> None:
    data = json.dumps(value, ensure_ascii=False, allow_nan=True).encode("utf-8")
    sys.stdout.buffer.write(len(data).to_bytes(8, "big") + data)
    sys.stdout.buffer.flush()


def json_target(value):
    if isinstance(value, pd.Series):
        value = value.to_dict()
    if not isinstance(value, dict) and value is not None:
        return value  # The trusted engine will reject a non-dict target.
    if value is None:
        return None
    return {key: (item.item() if isinstance(item, np.generic) else item)
            for key, item in value.items()}


def main() -> None:
    init = receive()
    if init.get("kind") != "init" or not isinstance(init.get("source"), str):
        raise ValueError("missing strategy source")
    module = types.ModuleType("agent_strategy")
    module.__file__ = "/strategy.py"
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), no_io():
                exec(compile(init["source"], "/strategy.py", "exec"), module.__dict__)
        if not isinstance(getattr(module, "PARAMS", None), dict) or not callable(getattr(module, "decide", None)):
            raise ValueError("strategy requires PARAMS and decide")
        params = module.PARAMS
        json.dumps(params, allow_nan=False)
        times = getattr(module, "DECISION_TIMES", None)
        send({"ok": True, "params": params, "decision_times": times})
    except Exception as exc:
        send({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]})
        return

    while True:
        try:
            request = receive()
        except EOFError:
            return
        if request.get("kind") == "stop":
            return
        if request.get("kind") != "decide" or not isinstance(request.get("args"), tuple):
            send({"ok": False, "error": "invalid decide request"})
            continue
        args = request["args"]
        memory = args[-1] if args and isinstance(args[-1], dict) else None
        try:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), no_io():
                    target = module.decide(*args)
            send({"ok": True, "target": json_target(target), "memory": memory})
        except Exception as exc:
            try:
                send({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500],
                      "memory": memory})
            except (TypeError, ValueError):
                send({"ok": False, "error": "strategy result is not JSON serializable"})


if __name__ == "__main__":
    main()
