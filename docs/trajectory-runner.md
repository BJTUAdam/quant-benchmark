# trajectory-runner

运行待测 Agent 的单条 FinLab v2 评测轨迹，记录工具调用、策略交付和各阶段回测结果。

## 文件结构

- `benchmark/finlab_v2.py` 是单条轨迹入口，同目录下有示例配置。
- `project/demo_runner/` 包含 Agent、模型客户端、工具和 MCP 接入代码。
- `examples/` 保存旧 Matrix 评测的配置模板。

## 配置

- 将 `benchmark/finlab.v2.skills.example.json` 另存为同目录下的 `finlab.v2.skills.local.json`，设置 `run_id` 并核对研究镜像 ID。先按 [make-task 说明](make-task.md) 安装 FinLab、准备资源和镜像。正式题还要提供数据绑定文件。
- 从仓库根目录的 `.env.example` 创建 `.env`，填写 `OPENAI_API_KEY`。启动脚本从这里读取 OpenAI 兼容接口、模型和温度。当前接口使用 HTTP，传输的密钥和请求内容没有 TLS 加密。
- 如需逐题隔离记忆，在本地轨迹 JSON 设置 `"isolated_memory": true`，并启用仅含 `memory` 服务的 MCP Skill。新测试启动时，运行器检查并清空本题轨迹的记忆空间。续跑已有测试时沿用该轮记忆。私有凭证存放在 `mem-plugin/data/trajectory-credentials/`。[按标签签发凭证](../mem-plugin/README.md#6-隔离测试轮次)
- 若本机缺少 JSON 指定的研究镜像，在仓库根目录执行 `docker build -q trajectory-runner/project/demo_runner/docker`，将输出的镜像 ID 写入本地配置的 `docker_image`。更换模型、镜像或实验配置时使用新的 `run_id`。

## 启动

从仓库根目录运行以下命令。启用 MCP 时，先等 `mem-plugin` 服务就绪。

```bash
./trajectory-runner/start.sh
```

研发模型遇到可重试的网络或 HTTP 错误时，最多重试两次，依次等待 2 秒和 4 秒。正式回测遇到可反馈的策略错误时，Agent 在本段剩余预算内最多修复两次，已提交的段继续沿用。使用新 `run_id` 可以在保留已有结果的情况下重新做一轮测试。

## 产物

单题结果写入 `trajectory-runner/benchmark/results/<run_id>/`。`state.json` 记录进度，`stages/` 保存各阶段的 JSON 轨迹，`summary.json` 在完成后生成。运行配置存放在 `results/.configs/`，旧版 Matrix 的导入结果放在 `results/matrix/`。[运行器完整说明](../trajectory-runner/README.md)
