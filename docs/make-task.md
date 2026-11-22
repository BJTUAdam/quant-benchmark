# make-task

生成和复核金融任务规格，准备评测所需的行情数据、题包和运行绑定。

## 文件结构

- `task_generator/taskgen/` 与 `task_generator/finlab/` 分别负责产题流程和 FinLab 运行工具。
- `task_generator/config/` 保存模型、批次和数据准备配置。
- `task_generator/task_packages/` 保存随仓库交付的题包。

## 配置

- 从仓库根目录的 `.env.example` 创建 `.env`，填写 OpenAI 兼容模型的地址、名称、温度和密钥。将 `task_generator/.env.example` 另存为同目录的 `.env`。启动脚本先读根目录 `.env`，再用后者覆盖同名变量。终端已导出的变量优先级最高。通过启动脚本运行时，产题和复核共用 `OPENAI_MODEL` 和 `OPENAI_API_KEY`，配额见 `task_generator/config/batch.json`。
- 采集真实行情时设置 `TUSHARE_TOKEN` 和所需的 `TUSHARE_UPSTREAM_URL`。做回测或绑定真实题前，完成 `finlab setup` 并加载 FinLab Docker 镜像。[部署说明](../make-task/task_generator/docs/v2/OPERATIONS.md)

## 启动

从仓库根目录运行。脚本用 `uv` 准备 Python 3.12 环境，默认开始产题。首次运行回测时，先在交互终端设置资源，再从原始 FinLab 交付包取得镜像归档并放到 `make-task/images/finlab_images_v2.tar`。

```bash
./make-task/start.sh setup
./make-task/start.sh backtest load-images ../images/finlab_images_v2.tar
./make-task/start.sh task doctor
./make-task/start.sh task generate
```

## 产物

生成批次和候选位于 `task_generator/runs/<batch_id>/`，接纳记录写入 `task_generator/registry/tasks.sqlite`。冻结数据和绑定交付写入各命令的 `--output`，常用 `make-task/task_generator/work_v2/`。复现已有的正式题评测时，需要另行取得对应的绑定交付物。[交付流程](../make-task/task_generator/docs/v2/DELIVERY.md)
