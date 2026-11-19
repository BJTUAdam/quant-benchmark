"""Local resource choices entered by the operator, with no effective defaults.

The terminal guard and confirmation record enforce an operating workflow. They
do not authenticate a person's identity or distinguish a human from automation.
"""
from __future__ import annotations

from datetime import datetime
import math
import sys

from .common import ROOT, LabError, now, read, write

SCHEMA_VERSION = "finlab.operator_setup.v1"
RESOURCE_KEYS = {"cpus", "memory_mb", "timeout_seconds"}


def settings_path():
    return ROOT / ".finlab" / "operator.json"


def validate_resources(resources):
    """Return validated values; missing keys never acquire a fallback value."""
    if not isinstance(resources, dict) or set(resources) != RESOURCE_KEYS:
        raise LabError("INVALID_RESOURCES", "须明确选择CPU、内存和运行时限，不能使用缺省值")
    cpus = resources["cpus"]
    memory = resources["memory_mb"]
    seconds = resources["timeout_seconds"]
    try:
        valid_cpu = not isinstance(cpus, bool) and isinstance(cpus, (int, float)) and math.isfinite(cpus) and cpus > 0
    except OverflowError:
        valid_cpu = False
    if not valid_cpu:
        raise LabError("INVALID_RESOURCES", "CPU数量必须为有限的正数")
    if isinstance(memory, bool) or not isinstance(memory, int) or memory < 256:
        raise LabError("INVALID_RESOURCES", "内存须为至少256 MB的整数，供两个容器分配")
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0):
        raise LabError("INVALID_RESOURCES", "运行时限须为正整数秒，或明确选择none表示不限时")
    return {"cpus": float(cpus), "memory_mb": memory, "timeout_seconds": seconds}


def load_settings():
    path = settings_path()
    if not path.is_file():
        raise LabError("SETUP_REQUIRED", "请使用者本人在交互终端执行 finlab setup；AI不得代填配置")
    try:
        settings = read(path)
        if (not isinstance(settings, dict)
                or set(settings) != {"schema_version", "method", "resources", "configured_at"}
                or settings["schema_version"] != SCHEMA_VERSION
                or settings["method"] != "human_interactive"):
            raise ValueError()
        configured_at = datetime.fromisoformat(settings["configured_at"])
        if configured_at.tzinfo is None:
            raise ValueError()
        resources = validate_resources(settings["resources"])
    except (KeyError, TypeError, ValueError, OSError):
        raise LabError("INVALID_OPERATOR_SETUP", "配置无效，请使用者本人重新执行 finlab setup") from None
    return {**settings, "resources": resources}


def _ask(prompt, parse):
    while True:
        value = input(prompt).strip()
        if value.lower() in {"q", "quit", "exit", "取消"}:
            raise LabError("SETUP_CANCELLED", "配置已中止，未保存任何选择")
        if not value:
            print("必须明确输入；空输入不会采用推荐值或任何默认值。")
            continue
        try:
            return parse(value)
        except (TypeError, ValueError, OverflowError):
            print("输入无效，请重新输入；输入“取消”可中止。")


def interactive_setup():
    """Ask the operator in a TTY; no noninteractive selection API is exposed."""
    if sys.stdin is None or not sys.stdin.isatty():
        raise LabError("SETUP_REQUIRES_INTERACTIVE_TERMINAL", "须使用者本人在交互终端运行 finlab setup；不接受管道代填")
    print("首次使用须由使用者本人选择资源，AI不得代填或生成确认文件。")
    print("推荐总预算为2 CPU、4096 MB内存；推荐值不会自动采用。运行时限无默认值，也无推荐值。")
    print("两个容器分享所选CPU和内存。单回调协议限制独立于单次总运行时限。")
    print("这是交互门禁与操作规范，不是身份认证；输入“取消”可中止，已有配置不会因此改变。")

    def cpu_value(text):
        value = float(text)
        if not math.isfinite(value) or value <= 0:
            raise ValueError()
        return value

    def memory_value(text):
        value = int(text)
        if value < 256:
            raise ValueError()
        return value

    def timeout_value(text):
        if text.lower() == "none":
            return None
        value = int(text)
        if value <= 0:
            raise ValueError()
        return value

    try:
        resources = validate_resources({
            "cpus": _ask("请输入CPU总数量（正数）：", cpu_value),
            "memory_mb": _ask("请输入内存总量（整数MB，至少256）：", memory_value),
            "timeout_seconds": _ask("请输入单次最长运行秒数（正整数），或none表示不限时：", timeout_value),
        })
        limit = "不限时（本人明确选择none）" if resources["timeout_seconds"] is None else f'{resources["timeout_seconds"]}秒'
        print(f'所选总预算：{resources["cpus"]:g} CPU、{resources["memory_mb"]} MB，单次运行时限：{limit}。')
        if input("请本人输入“本人确认”保存；其他输入取消：").strip() != "本人确认":
            raise LabError("SETUP_CANCELLED", "未确认，未保存任何选择")
    except (EOFError, KeyboardInterrupt):
        raise LabError("SETUP_CANCELLED", "配置已中止，未保存任何选择") from None
    settings = {"schema_version": SCHEMA_VERSION, "method": "human_interactive", "resources": resources, "configured_at": now()}
    write(settings_path(), settings)
    return {"status": "configured", "path": str(settings_path()), **settings}
