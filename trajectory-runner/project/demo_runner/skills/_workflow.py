"""供内置专项 Skill 使用的受限核心工具适配器。"""
from __future__ import annotations

from typing import Callable


def definition(name: str, description: str, properties: dict, required: tuple[str, ...]) -> dict:
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }}


class CoreWorkflowSkill:
    """将一组专用模型工具映射到明确白名单中的既有核心工具。"""

    def __init__(self, schemas: list[dict], routes: dict[str, tuple[str, Callable[[dict], dict]]], guidance: str):
        self._schemas = schemas
        self._routes = routes
        self._guidance = guidance
        self.core_tools = frozenset(core for core, _ in routes.values())
        self._execute_core: Callable[[str, dict], str] | None = None

    def bind_core(self, execute: Callable[[str, dict], str]) -> None:
        self._execute_core = execute

    def discover(self) -> list[dict]:
        return self._schemas

    def guidance(self) -> str:
        return self._guidance

    def execute(self, name: str, arguments: dict, *, timeout: float | None = None) -> str:
        del timeout  # 核心工具自身负责 Docker 与阶段时间限制。
        if name not in self._routes:
            raise ValueError("专项 Skill 未处理该工具：" + name)
        if self._execute_core is None:
            raise RuntimeError("专项 Skill 尚未绑定核心工具")
        core_name, adapt = self._routes[name]
        return self._execute_core(core_name, adapt(arguments))
