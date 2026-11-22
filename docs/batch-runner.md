# batch-runner

按任务清单批量调用 `trajectory-runner`，控制并发、重试和断点续跑，并汇总结果。

## 文件结构

- `batchctl.py` 提供预检、运行、状态查询和报表入口。
- `spec.py`、`scheduler.py`、`store.py` 和 `report.py` 管理配置、调度、状态和汇总。
- `batch.example.json` 是批次配置模板。

## 配置

将 `batch.example.json` 另存为同目录下的 `batch.local.json`，设置 `batch_id`、`concurrency` 和 `jobs`。批跑使用根目录的模型配置。逐题签发的隔离记忆凭证通过单题入口使用。更换实验配置时使用新的 `batch_id`。[单条轨迹配置](trajectory-runner.md)

## 启动

从仓库根目录执行。`check` 校验批次，`run` 启动或续跑。

```bash
./batch-runner/start.sh check
./batch-runner/start.sh
```

## 产物

示例写入 `trajectory-runner/benchmark/results/batches/<batch_id>/`。`runs/` 保存各条轨迹，`logs/` 保存日志，`batch.db` 保存调度状态，`report.json` 和 `report.csv` 提供批次汇总。
