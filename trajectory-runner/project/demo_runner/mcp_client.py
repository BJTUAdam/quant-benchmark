"""Optional host-side MCP tools for the FinLab v2 agent."""
from __future__ import annotations

import asyncio
import json
import os
import re


def validate_servers(servers):
    if not isinstance(servers, list):
        raise ValueError("mcp_servers 须为列表")
    names = set()
    for server in servers:
        if not isinstance(server, dict) or any(
            not isinstance(server.get(key), str) or not server[key].strip()
            for key in ("name", "url", "token_env")
        ):
            raise ValueError("MCP 服务须填写 name、url、token_env")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", server["name"]) or server["name"] in names:
            raise ValueError("MCP name 须为唯一的短名称（小写字母、数字、下划线）")
        names.add(server["name"])
        if "allowed_tools" in server and (
            not isinstance(server["allowed_tools"], list)
            or any(not isinstance(name, str) for name in server["allowed_tools"])
        ):
            raise ValueError("allowed_tools 须为工具名称列表；省略时加载全部工具")


class MCPTools:
    def __init__(self, servers=()):
        self.servers = list(servers)
        validate_servers(self.servers)
        self.schemas, self.routes, self.guidance = [], {}, []

    async def _request(self, server, action, name=None, arguments=None, timeout=30):
        try:
            from fastmcp import Client
        except ImportError:
            raise RuntimeError("启用 MCP 需在运行器目录执行 uv sync --locked --extra mcp") from None
        token = os.environ.get(server["token_env"], "").strip()
        if not token:
            raise RuntimeError("未配置 MCP 令牌环境变量：" + server["token_env"])
        try:
            async with asyncio.timeout(timeout), Client(
                server["url"], auth=token, timeout=timeout, init_timeout=timeout
            ) as client:
                if action == "list":
                    return await client.list_tools(), client.instructions
                return await client.call_tool(name, arguments, raise_on_error=False)
        except Exception as exc:
            raise RuntimeError(f"MCP 请求失败：{server['name']} ({type(exc).__name__})") from None

    def discover(self):
        self.schemas, self.routes, self.guidance = [], {}, []
        for server in self.servers:
            tools, instructions = asyncio.run(self._request(server, "list"))
            allowed = server.get("allowed_tools")
            if allowed is not None and set(allowed) - {tool.name for tool in tools}:
                raise RuntimeError("MCP 服务缺少指定工具：" + server["name"])
            aliases = []
            for tool in tools:
                if allowed is not None and tool.name not in allowed:
                    continue
                alias = "mcp_" + server["name"] + "__" + re.sub(r"[^a-zA-Z0-9_-]", "_", tool.name)
                if len(alias) > 64 or alias in self.routes:
                    raise ValueError("MCP 工具别名重复或超过 64 字符：" + alias)
                self.routes[alias] = (server, tool.name)
                self.schemas.append({"name": alias, "description": tool.description or tool.name,
                                     "parameters": tool.input_schema})
                aliases.append(tool.name + " -> " + alias)
            self.guidance.append(f"MCP 服务 {server['name']}（在本任务约束下使用）：\n"
                                 + (instructions or "") + "\n工具名映射：" + "; ".join(aliases))
        return self.schemas

    def system_prompt(self, base):
        return "\n\n".join([base, *self.guidance])

    def handles(self, name):
        return name in self.routes

    def execute(self, name, arguments, *, timeout=30):
        server, remote = self.routes[name]
        try:
            result = asyncio.run(self._request(server, "call", remote, arguments, timeout=timeout))
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        content = result.structured_content
        if content is None:
            content = "\n".join(item.text for item in result.content if item.type == "text")
        return json.dumps({"error" if result.is_error else "result": content}, ensure_ascii=False)
