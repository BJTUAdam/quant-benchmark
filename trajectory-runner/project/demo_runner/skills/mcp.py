"""将受配置约束的 MCP 服务作为一个可选 Skill 暴露给 Agent。"""
from __future__ import annotations

from pathlib import Path

from mcp_client import MCPTools, validate_servers


def memory_docs(runner):
    directory = runner.parents[2] / "mem-plugin/skills/mem-plugin"
    return [directory / name for name in (
        "SKILL.md", "references/retrieval.md", "references/writing.md", "references/privacy.md"
    )]


def configured_docs(skills, runner):
    for spec in skills or []:
        if (spec["name"] == "mcp" and spec.get("enabled", True)
                and spec["config"].get("load_mem_plugin_skill")):
            return memory_docs(runner)
    return []


def create(config: dict):
    if "servers" not in config or set(config) - {"servers", "load_mem_plugin_skill"}:
        raise ValueError("mcp Skill 需要 servers，可选 load_mem_plugin_skill")
    load_docs = config.get("load_mem_plugin_skill", False)
    if type(load_docs) is not bool:
        raise ValueError("load_mem_plugin_skill 须为布尔值")
    validate_servers(config["servers"])
    docs = memory_docs(Path(__file__).resolve().parents[1]) if load_docs else []
    return _McpSkill(config["servers"], docs)


class _McpSkill:
    def __init__(self, servers, docs):
        self._tools = MCPTools(servers)
        self._instructions = [doc.read_text(encoding="utf-8") for doc in docs]

    def discover(self) -> list[dict]:
        return self._tools.discover()

    def guidance(self) -> str:
        return "\n\n".join([*self._tools.guidance, *self._instructions])

    def execute(self, name: str, arguments: dict, *, timeout: float | None = None) -> str:
        if not self._tools.handles(name):
            raise ValueError("mcp Skill 未处理该工具：" + name)
        return self._tools.execute(name, arguments, timeout=30 if timeout is None else timeout)
