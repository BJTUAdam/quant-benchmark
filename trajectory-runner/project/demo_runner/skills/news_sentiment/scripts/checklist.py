#!/usr/bin/env python3
"""离线检查情绪数据的时间边界和可复核性。"""
from __future__ import annotations

import argparse
import json
import pathlib


def check(payload: dict) -> dict:
    required = ("published_before_visible_stop", "record_ids", "scoring_rule", "aggregation_window", "out_of_sample_checked")
    missing = [key for key in required if key not in payload]
    issues = []
    if payload.get("published_before_visible_stop") is not True:
        issues.append("未确认文本发布时间早于可见边界")
    if payload.get("out_of_sample_checked") is not True:
        issues.append("未确认样本外检验")
    return {"passed": not missing and not issues, "missing": missing, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(check(json.loads(args.input.read_text(encoding="utf-8"))), ensure_ascii=False))


if __name__ == "__main__":
    main()
