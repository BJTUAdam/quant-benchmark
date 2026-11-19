#!/usr/bin/env python3
"""离线检查策略评估报告是否保留风险与统计量边界。"""
from __future__ import annotations

import argparse
import json
import pathlib


def check(payload: dict) -> dict:
    required = ("period_metrics", "drawdown", "cost_stress", "position_attribution", "risks")
    missing = [key for key in required if key not in payload]
    issues = []
    if payload.get("multi_period_consistent") is not True:
        issues.append("未确认多区间一致")
    if "dsr" in payload and not payload.get("dsr_inputs"):
        issues.append("DSR 已填写但缺少输入说明")
    if "pbo" in payload and not payload.get("pbo_inputs"):
        issues.append("PBO 已填写但缺少输入说明")
    return {"passed": not missing and not issues, "missing": missing, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(check(json.loads(args.input.read_text(encoding="utf-8"))), ensure_ascii=False))


if __name__ == "__main__":
    main()
