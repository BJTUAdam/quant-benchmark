#!/usr/bin/env python3
"""离线检查数据请求元数据；输入 JSON 不会被发送到任何服务。"""
from __future__ import annotations

import argparse
import json
import pathlib


def check(payload: dict) -> dict:
    required = ("api", "params", "fields", "visible_only", "missing_data_reported")
    missing = [key for key in required if key not in payload]
    issues = []
    if payload.get("visible_only") is not True:
        issues.append("未确认只查询当前可见数据")
    if payload.get("missing_data_reported") is not True:
        issues.append("未确认报告缺失数据")
    if not isinstance(payload.get("params", {}), dict):
        issues.append("params 必须为对象")
    return {"passed": not missing and not issues, "missing": missing, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(check(json.loads(args.input.read_text(encoding="utf-8"))), ensure_ascii=False))


if __name__ == "__main__":
    main()
