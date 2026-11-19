# make-task

生成和复核金融任务规格，准备评测所需的行情数据、题包和运行绑定。

## 文件结构

- `task_generator/taskgen/` 与 `task_generator/finlab/` 分别负责产题流程和 FinLab 运行工具。
- `task_generator/config/` 保存模型、批次和数据准备配置。
- `task_generator/task_packages/` 保存随仓库交付的题包。

## 配置

- 按 `task_generator/.env.example` 导出所选模型服务的密钥。默认从 `config/default.json` 读取模型设置，产题配额见 `config/batch.json`。
- 采集真实行情时设置 `TUSHARE_TOKEN` 和所需的 `TUSHARE_UPSTREAM_URL`。做回测或绑定真实题前，完成 `finlab setup` 并加载 FinLab Docker 镜像。[部署说明](../make-task/task_generator/docs/v2/OPERATIONS.md)

## 启动

从仓库根目录进入产题项目。安装依赖并完成环境变量配置后，运行产题命令。

```bash
cd make-task/task_generator
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .
finlab task generate --batch-config config/batch.json
```

## 产物

生成批次和候选位于 `task_generator/runs/<batch_id>/`，接纳记录写入 `task_generator/registry/tasks.sqlite`。冻结数据和绑定交付写入各命令的 `--output`，常用 `task_generator/work_v2/`。[交付流程](../make-task/task_generator/docs/v2/DELIVERY.md)
