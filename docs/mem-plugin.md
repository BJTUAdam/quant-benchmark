# mem-plugin

为待测 Agent 提供可写入和检索的持久化记忆，通过 MCP Streamable HTTP 接入。目录是独立 Git 子模块。

## 文件结构

- `src/mem_plugin/` 包含记忆服务、MCP 接口和后台任务。
- `deploy/` 和 `migrations/` 分别保存 PostgreSQL 部署配置和数据库迁移。
- `start.sh` 和 `stop.sh` 启停服务。`skills/mem-plugin/` 提供 Agent 的记忆使用规范。

## 配置

首次从仓库根目录初始化子模块，然后进入 `mem-plugin/` 安装依赖并生成 `.env`。

```bash
git submodule update --init -- mem-plugin
cd mem-plugin
uv sync --locked --extra mcp --extra postgres
uv run --no-sync python scripts/init_env.py
```

记忆插件使用自己的 `mem-plugin/.env`。选用 `LLM_PROVIDER=openai_compatible` 时，在这里填写 `MEM_PLUGIN_OPENAI_BASE_URL`、`MEM_PLUGIN_OPENAI_MODEL`、`MEM_PLUGIN_OPENAI_API_KEY` 和 `MEM_PLUGIN_OPENAI_TEMPERATURE`。向量检索使用独立的 `EMBEDDING_API_KEY`。Agent 连接插件只需 MCP 地址和对应的 Token。[配置说明](../mem-plugin/README.md#2-配置服务与模型)

## 启动

启动 Docker 后，在仓库根目录执行。脚本准备插件依赖和 PostgreSQL、应用迁移并启动服务。终端出现 `"status": "ready"` 后即可运行 Agent。

```bash
./mem-plugin/start.sh --with-postgres
```

在仓库根目录执行 `./mem-plugin/stop.sh`，停止常驻插件的 API、Worker 和 Scheduler。

## 产物

记忆保存在 PostgreSQL 持久化卷。终端用颜色区分服务角色、时间、日志级别和事件名称，设定 `NO_COLOR=1` 可关闭颜色。`data/runtime/` 下的日志文件使用纯文本。

逐题隔离使用同一个 MCP URL，私有连接配置由 `trajectory-runner` 写入 `data/trajectory-credentials/`。[测试轮次说明](../mem-plugin/README.md#6-隔离测试轮次)
