# mem-plugin

为待测 Agent 提供可写入和检索的持久化记忆，通过 MCP Streamable HTTP 接入。目录是独立 Git 子模块。

## 文件结构

- `src/mem_plugin/` 包含记忆服务、MCP 接口和后台任务。
- `deploy/` 和 `migrations/` 分别保存 PostgreSQL 部署配置和数据库迁移。
- `start.sh` 启动服务，`skills/mem-plugin/` 提供 Agent 的记忆使用规范。

## 配置

首次从仓库根目录初始化子模块，然后进入 `mem-plugin/` 安装依赖并生成 `.env`。

```bash
git submodule update --init -- mem-plugin
cd mem-plugin
uv sync --locked --extra mcp --extra postgres
uv run --no-sync python scripts/init_env.py
```

在 `.env` 补齐 Embedding 和 LLM 凭证。将 `MEM_PLUGIN_MCP_URL` 与对应的 Token 配给 Agent。[配置说明](../mem-plugin/README.md#2-配置服务与模型)

## 启动

在 `mem-plugin/` 启动 PostgreSQL 并完成迁移，再启动服务。首次使用时按 README 拉取所需的 PostgreSQL 镜像。

```bash
docker compose --env-file .env \
  -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml \
  up -d --wait --pull never postgres
uv run --no-sync alembic upgrade head
./start.sh
```

## 产物

记忆保存在 PostgreSQL 持久化卷，运行日志默认写到 `data/runtime/`。使用独立测试轮次时，连接配置位于 `data/test-runs/<run_id>/connection.json`。[测试轮次说明](../mem-plugin/README.md#6-隔离测试轮次)
