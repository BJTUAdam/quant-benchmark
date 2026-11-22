# quant-benchmark

用于评测金融研究 Agent 的长程任务基准，支持对比接入记忆 MCP 前后的策略交付和回测结果。

| 目录 | 用途 | 文档 |
| --- | --- | --- |
| `make-task/` | 生成金融任务、准备行情和绑定数据 | [产题与数据准备](docs/make-task.md) |
| `trajectory-runner/` | 运行单题评测，保存轨迹和回测结果 | [单题评测](docs/trajectory-runner.md) |
| `batch-runner/` | 并发运行多道题并汇总结果 | [批跑评测](docs/batch-runner.md) |
| `mem-plugin/` | 为 Agent 提供持久化记忆的 MCP 服务，作为 Git 子模块引入 | [记忆插件接入](docs/mem-plugin.md) |

首次运行时，先按产题说明用 `uv` 和 Docker 准备 FinLab 环境及镜像，再按单题说明运行合成示例。正式评测还需取得对应的绑定数据交付物。开启记忆的实验需先启动记忆插件。
