"""Host-side Skill registry for optional Agent capabilities.

Skills are discovered only from this runner's ``skills`` package.  A run config
may select and configure those named Skills, but can never import an arbitrary
module or replace a core tool.  Skills run on the host and must not receive the
Agent workspace or private task data unless their own explicit contract allows
it.
"""
from __future__ import annotations

import importlib
import json
import pkgutil
import re
from typing import Any, Callable


_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


class SkillError(ValueError):
    pass


def available_skills() -> set[str]:
    """Return packaged Skill names without searching user-controlled paths."""
    package = importlib.import_module("skills")
    return {item.name for item in pkgutil.iter_modules(package.__path__)
            if not item.name.startswith("_")}


def normalize_skills(value: Any, legacy_mcp_servers: Any = None) -> list[dict[str, Any]]:
    """Validate public configuration and translate the legacy MCP switch.

    ``mcp_servers`` remains supported as a compatibility alias for the built-in
    ``mcp`` Skill.  Enabling both forms is deliberately rejected so a run has
    one unambiguous, lockable source of tool configuration.
    """
    if value is None:
        value = []
    if not isinstance(value, list):
        raise SkillError("skills 须为列表")
    installed = available_skills()
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        where = f"skills[{index}]"
        if not isinstance(item, dict) or set(item) - {"name", "enabled", "config"}:
            raise SkillError(f"{where} 只能包含 name、enabled、config")
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise SkillError(f"{where}.name 无效")
        if name not in installed:
            raise SkillError(f"未发现已安装的 Skill：{name}")
        if name in names:
            raise SkillError(f"Skill 重复配置：{name}")
        enabled = item.get("enabled", True)
        config = item.get("config", {})
        if type(enabled) is not bool or not isinstance(config, dict):
            raise SkillError(f"{where}.enabled 须为布尔值，config 须为对象")
        names.add(name)
        result.append({"name": name, "enabled": enabled, "config": config})
    if legacy_mcp_servers is not None:
        if "mcp" in names:
            raise SkillError("mcp_servers 与 skills 中的 mcp 不能同时配置")
        # Keep validation lazy: an inactive configuration must not require the
        # optional MCP SDK, while the mcp Skill validates server details itself.
        result.append({"name": "mcp", "enabled": True, "config": {"servers": legacy_mcp_servers}})
    return result


class SkillRegistry:
    """Loads enabled packaged Skills and exposes one model-tool surface."""

    def __init__(self, skills: Any = None, *, legacy_mcp_servers: Any = None):
        self.specs = normalize_skills(skills, legacy_mcp_servers)
        self._skills: list[tuple[str, Any]] = []
        for spec in self.specs:
            if not spec["enabled"]:
                continue
            module = importlib.import_module("skills." + spec["name"])
            factory = getattr(module, "create", None)
            if not callable(factory):
                raise SkillError(f"Skill 缺少 create 工厂：{spec['name']}")
            self._skills.append((spec["name"], factory(spec["config"])))
        self._routes: dict[str, Any] = {}
        self._guidance: list[str] = []

    def discover(self, reserved_names: set[str] | None = None) -> list[dict]:
        reserved = set(reserved_names or ())
        schemas: list[dict] = []
        self._routes, self._guidance = {}, []
        for name, skill in self._skills:
            found = skill.discover()
            if not isinstance(found, list):
                raise SkillError(f"Skill 返回的工具定义无效：{name}")
            for schema in found:
                tool_name = schema.get("name") if isinstance(schema, dict) else None
                if not isinstance(tool_name, str) or tool_name in reserved or tool_name in self._routes:
                    raise SkillError(f"Skill 工具名与核心工具冲突或重复：{tool_name}")
                self._routes[tool_name] = skill
                schemas.append(schema)
            prompt = skill.guidance()
            if prompt:
                if not isinstance(prompt, str):
                    raise SkillError(f"Skill 指引无效：{name}")
                self._guidance.append(prompt)
        return schemas

    def system_prompt(self, base: str) -> str:
        return "\n\n".join([base, *self._guidance])

    def handles(self, tool_name: str) -> bool:
        return tool_name in self._routes

    def bind_core(self, execute: Callable[[str, dict], str], core_tool_names: set[str]) -> None:
        """向 Skill 注入受限的核心工具入口。

        Skill 只能拿到这个闭包，不能取得工作区、后端或 ``Tools`` 实例。每个
        Skill 声明自己的 ``core_tools`` 白名单；即使其实现有误，也不能借此调用
        未声明或当前 Harness 不存在的核心工具。
        """
        if not callable(execute):
            raise SkillError("核心工具执行器无效")
        available = set(core_tool_names)
        for name, skill in self._skills:
            binder = getattr(skill, "bind_core", None)
            if binder is None:
                continue
            if not callable(binder):
                raise SkillError(f"Skill 核心绑定接口无效：{name}")
            allowed = getattr(skill, "core_tools", None)
            if not isinstance(allowed, (set, frozenset)) or not all(isinstance(item, str) for item in allowed):
                raise SkillError(f"Skill 核心工具白名单无效：{name}")

            def restricted(tool_name: str, arguments: dict, *, _allowed=frozenset(allowed)) -> str:
                if tool_name not in _allowed:
                    return json.dumps({"error": "Skill 不允许调用该核心工具"}, ensure_ascii=False)
                if tool_name not in available:
                    return json.dumps({"error": "当前 Harness 不提供该 Skill 所需的核心工具"}, ensure_ascii=False)
                return execute(tool_name, arguments)

            binder(restricted)

    def execute(self, tool_name: str, arguments: dict, *, timeout: float | None = None) -> str:
        if tool_name not in self._routes:
            raise SkillError("Skill 未处理该工具：" + tool_name)
        return self._routes[tool_name].execute(tool_name, arguments, timeout=timeout)
