# trajectory-runner

运行待测 Agent 的单条 FinLab v2 评测轨迹，记录工具调用、策略交付和各阶段回测结果。

## 文件结构

- `benchmark/finlab_v2.py` 是单条轨迹入口，同目录下有示例配置。
- `project/demo_runner/` 包含 Agent、模型客户端、工具和 MCP 接入代码。
- `examples/` 保存旧 Matrix 评测的配置模板。

## 配置

- 将 `benchmark/finlab.v2.skills.example.json` 另存为同目录下的 `finlab.v2.skills.local.json`，设置 `run_id` 并核对研究镜像 ID。先按 [make-task 说明](make-task.md) 安装 FinLab、准备资源和镜像。正式题还要提供数据绑定文件。
- 将 `benchmark/deepseek.env.example` 另存为 `benchmark/deepseek.private.env` 并填写模型密钥。使用记忆时，启用 JSON 中的 `mcp` Skill，填入服务 URL，并在私有 env 文件里设置配对的 `MEM_PLUGIN_MCP_TOKEN`。[MCP 服务说明](mem-plugin.md)

## 启动

从仓库根目录运行以下命令。启用 MCP 时，先等 `mem-plugin` 服务就绪。

```bash
cd trajectory-runner
uv sync --locked --extra mcp
uv run --locked --extra mcp --env-file benchmark/deepseek.private.env \
  python -I -B benchmark/finlab_v2.py start --config benchmark/finlab.v2.skills.local.json
```

## 产物

默认写到 `benchmark/v2_runs/<run_id>/`。`stages/` 保存轨迹，`deliveries/` 保存阶段交付，`summary.json` 和 `public_results.zip` 在完成后生成。[运行器完整说明](../trajectory-runner/README.md)
