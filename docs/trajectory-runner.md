# trajectory-runner

运行待测 Agent 的单条 FinLab v2 评测轨迹，记录工具调用、策略交付和各阶段回测结果。

## 文件结构

- `benchmark/finlab_v2.py` 是单条轨迹入口，同目录下有示例配置。
- `project/demo_runner/` 包含 Agent、模型客户端、工具和 MCP 接入代码。
- `examples/` 保存旧 Matrix 评测的配置模板。

## 配置

- 在 `trajectory-runner/benchmark/` 下将 `finlab.v2.skills.example.json` 复制为 `finlab.v2.skills.local.json`，用于合成联调。正式题使用 `finlab.v2.bound.example.json` 创建本地配置，并将 `task.binding` 指向取得的 `binding.private.json`。相对路径以配置文件所在目录为准。运行前按 [make-task 说明](make-task.md) 安装 FinLab、设置资源并加载镜像。
- 从仓库根目录的 `.env.example` 创建 `.env`，填写 `OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_TEMPERATURE` 和 `OPENAI_API_KEY`。启动脚本先读根目录 `.env`，再从 `trajectory-runner/.env`（如存在）覆盖同名变量。终端已导出的变量优先级最高。使用 HTTP 地址时，密钥和请求内容在传输中没有 TLS 加密。
- 若本机缺少 JSON 指定的研究镜像，在仓库根目录执行 `docker build -q trajectory-runner/project/demo_runner/docker`，将输出的镜像 ID 写入本地配置的 `docker_image`。更换模型、镜像或实验配置时使用新的 `run_id`。

### 记忆对照配置

| 测试条件 | `isolated_memory` | `skills` 中 `name: mcp` 的 `enabled` |
| --- | --- | --- |
| 逐题隔离记忆 | `true` | `true`，只配置一个名为 `memory` 的服务 |
| 无记忆 | `false` | `false` |

`isolated_memory` 只控制是否使用逐题隔离凭证。启用隔离记忆时，新测试会清空本题轨迹的记忆空间，续跑会沿用该轮记忆。私有凭证存放在 `mem-plugin/data/trajectory-credentials/`。[按标签签发凭证](../mem-plugin/README.md#6-隔离测试轮次)

## 启动

从仓库根目录运行。启用 MCP 时，先等 `mem-plugin` 服务就绪。

```bash
./trajectory-runner/start.sh prepare benchmark/finlab.v2.skills.local.json
./trajectory-runner/start.sh start benchmark/finlab.v2.skills.local.json
./trajectory-runner/start.sh status benchmark/finlab.v2.skills.local.json
```

`prepare` 准备本地运行目录，`start` 启动或续跑并调用模型，`status` 查看进度。对照实验显式传入每道题的配置路径。省略路径时优先使用 `benchmark/finlab.v2.work-v2-t01.local.json`，该文件缺失时使用 `benchmark/finlab.v2.skills.local.json`。

研发模型的每次请求遇到可重试的网络或 HTTP 错误时，最多重试两次，依次等待 2 秒和 4 秒。正式回测遇到可反馈的策略错误时，Agent 在本段剩余预算内最多修复两次，已提交的段继续沿用。使用新 `run_id` 可以在保留已有结果的情况下重新做一轮测试。

## 产物

默认输出到 `trajectory-runner/benchmark/results/<run_id>/`。`state.json` 记录进度，`stages/` 保存阶段 JSON 轨迹，`workspace/reports/` 保存各段回测明细。成功完成后可查看 `summary.json`、`audit.json` 和 `public_results.zip`。生成的运行配置存放在 `results/.configs/`，旧版 Matrix 的导入结果放在 `results/matrix/`。[轨迹 HTML 导出说明](../trajectory-runner/README.md#导出轨迹)
