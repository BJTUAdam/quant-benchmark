#!/usr/bin/env python3
"""离线检查本地回测的可复现元数据。"""
from __future__ import annotations

import argparse
import json
import pathlib


def check(payload: dict) -> dict:
    required = ("data_files", "periods", "fees", "slippage", "parameters", "outputs", "independent_accounts")
    missing = [key for key in required if key not in payload]
    issues = []
    if payload.get("independent_accounts") is not True:
        issues.append("未确认分段使用独立账户")
    if not payload.get("fees") or not payload.get("slippage"):
        issues.append("手续费或滑点未登记")
    return {"passed": not missing and not issues, "missing": missing, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(check(json.loads(args.input.read_text(encoding="utf-8"))), ensure_ascii=False))


if __name__ == "__main__":
    main()
