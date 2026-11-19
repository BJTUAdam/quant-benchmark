# 插件式 Skill 架构

运行器将可选能力组织为宿主侧 Skill。核心 Agent、题包、数据桥接、Docker 沙箱和轨迹审计仍由原有 Harness 控制；Skill 只负责提供额外的模型工具及其使用指引。

## 边界

- Skill 只能从 `project/demo_runner/skills/` 内随运行器发布的模块中发现。配置不能导入任意文件或 Python 模块；网络连接只可由已注册 Skill 的已验证配置建立。
- 注册表拒绝与核心工具同名的 Skill 工具，因此 `query_data`、`run_backtest`、`write_file` 等原有接口不会被替换。
- Skill 不会自动获得 Agent 工作区、私有绑定、行情缓存或 Docker 容器权限。任何需要这些资源的能力仍必须经过既有工具、数据桥接和沙箱边界。
- 运行前会把注册表、所有内置 Skill 源码和配置纳入 v2 源码锁；旧 Matrix 批跑也会锁定这些源码。

## 配置

在 FinLab v2 配置中使用 `skills` 列表启停已发现的 Skill。内置 Skill 包括 `mcp`、`data_access`、`backtesting`、`strategy_evaluation` 和 `news_sentiment`。后四者没有外部配置项，必须显式启用：

```json
"skills": [
  {"name": "data_access", "enabled": true, "config": {}},
  {"name": "backtesting", "enabled": true, "config": {}},
  {"name": "strategy_evaluation", "enabled": true, "config": {}},
  {"name": "news_sentiment", "enabled": true, "config": {}}
]
```

四个专项 Skill 的每个目录都包含中文 `SKILL.md`、可离线运行的 `scripts/checklist.py` 和插件实现。它们提供的工具分别是：

- `data_access`：`data_query_market`，只转发至 `query_data`。
- `backtesting`：`backtest_run_local`，只转发至 `run_local_backtest`。
- `strategy_evaluation`：`strategy_evaluate`，只转发至 `run_research`，不能运行策略回测。
- `news_sentiment`：`news_query_text` 与 `news_score_sentiment`，分别只转发至 `query_data` 和 `run_research`。

注册表仅将按 Skill 声明白名单收窄后的核心执行闭包注入插件。插件无法取得核心 `Tools`、工作区、后端、私有绑定或 Docker 控制权；每次专用工具调用仍通过原有 `Tools.execute()` 计入配额并写入阶段轨迹。当前 Harness 不提供某项所需核心工具时，调用会明确失败，不会尝试降级或绕过限制。

`mcp` Skill 用于连接由运营方明确配置的 MCP 服务：

```json
"skills": [
  {
    "name": "mcp",
    "enabled": true,
    "config": {
      "servers": [
        {
          "name": "memory",
          "url": "http://127.0.0.1:8000/mcp",
          "token_env": "MEM_PLUGIN_MCP_TOKEN",
          "allowed_tools": ["memory.search", "memory.record"]
        }
      ]
    }
  }
]
```

`enabled: false` 时该 Skill 不加载、不发现工具，也不会导入 MCP SDK。`allowed_tools` 省略时会暴露该服务返回的全部工具；空数组表示不暴露工具。令牌只从 `token_env` 指定的宿主环境变量读取，绝不写入配置、提示词或轨迹。

旧配置中的 `mcp_servers` 仍然可用，运行器会将其转换为同一个 `mcp` Skill。一次运行不能同时写 `mcp_servers` 和 `skills` 中的 `mcp`，避免产生两个不一致的工具来源。

## 扩展 Skill

新增 Skill 时，在 `project/demo_runner/skills/` 增加一个仅小写名称的 Python 模块，并提供 `create(config)` 工厂。工厂返回的对象须实现：

- `discover()`：返回模型工具定义列表。
- `guidance()`：返回可选的模型使用指引。
- `execute(name, arguments, timeout=...)`：执行已发现的工具并返回文本结果。

如果 Skill 需要使用既有核心能力，可选实现 `bind_core(execute)`，并声明不可变集合 `core_tools`。注册表会把执行器限制在该集合与当前 Harness 实际工具的交集内。Skill 不得接收核心工具对象、工作区、后端、私有数据或 Docker 控制对象。

Skill 必须验证自己的配置，不能复用核心工具名，也不能绕过现有的数据可见性、策略校验、执行镜像或审计记录。新增后应将其加入相应源码锁定清单并补充离线测试。
