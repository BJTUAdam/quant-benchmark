# 金融长轨迹运行器

源码源自 2026-11-05 的 `benchmark/docker_batch_lab` 快照。历史轨迹保留登记时的源码和配置哈希，修改代码后使用新的 `run_id` 或 `matrix_id` 登记实验。

## 主要调用链

FinLab v2 单题从 `start.sh` 进入 `benchmark/finlab_v2.py`。旧 Matrix 的调用链为
`benchmark/matrix.py` → `batch.py` → `worker.py` →
`project/demo_runner/environment_controller.py` →
`autonomous_harness.py`。`agent_view.py` 构造被测 Agent 可见内容，
`user_agent.py` 负责启动、复盘与催交付的用户话语；
`agent_tools.py` 管理研究和回测工具，`model_client.py` 请求模型，
`docker_sandbox.py` 等文件负责隔离执行。阶段仿真和审计由可信控制端完成。

## FinLab v2 单题适配

`project/demo_runner/finlab_v2_adapter.py` 提供公开数据查询、Agent 自写的本地研究回测和交付校验。
`finlab_v2_worker.py` 调用随题目系统提供的
FinLab v2 运行时。研发 Agent 交付 `strategy.py`、`backtest.py`、`params.json`、
`RESEARCH.md` 和 `CHANGELOG.md`；平台只向 Agent 返回提交是否可执行，
独立完成七段正式运行并返回六次复盘反馈。
旧题包仍使用 Matrix 入口，两套题包格式不能混用。

`benchmark/finlab.v2.demo.example.json` 配置合成股票联调；
`benchmark/finlab.v2.bound.example.json` 需要产题系统交付的真实
`binding.private.json`。仓库随附的 12 份题包是未绑定草案。已有正式题评测使用的绑定交付物和行情放在本地 `make-task/task_generator/work_v2/`，需另行取得。
在 `trajectory-runner` 目录安装 Agent 的 Python 3.12 环境并准备本地配置。

```sh
uv sync --locked --extra mcp
cp benchmark/finlab.v2.skills.example.json benchmark/finlab.v2.skills.local.json
```

在仓库根目录 `.env` 填写评测模型配置。单题和批跑同时加载 `trajectory-runner/.env`（如存在），
本地文件会覆盖根目录文件中的同名变量。终端已导出的变量优先级最高。启用逐题隔离记忆时，
设置 `"isolated_memory": true` 并启用 `mcp` Skill。做无记忆对照时，将两项都设为 `false`。
启用 `load_mem_plugin_skill` 会加载子模块的记忆使用规范。[单题配置说明](../docs/trajectory-runner.md#配置)

在 `make-task/task_generator` 安装 FinLab v2 到独立环境，以匹配示例的 `finlab_python`。

```sh
uv venv --python 3.12
uv pip install -e .
```

按[产题与数据准备](../docs/make-task.md#启动)导入当前 FinLab 镜像归档并完成 `finlab setup`。
核对本地配置的研究镜像 ID 后，回到运行器目录执行。

```sh
./start.sh prepare benchmark/finlab.v2.skills.local.json
./start.sh start benchmark/finlab.v2.skills.local.json
```

`prepare` 准备合成联调，`start` 会实际调用模型并启动 Docker 作业。启用记忆时先启动
`mem-plugin`，等 MCP 服务就绪后再执行 `start`。
评测正式题时，将交付包的 `binding.private.json` 路径填入 `task.binding`，并显式将该题配置传给 `start.sh`。从草案接入新题时，先完成自动接纳、真实数据冻结与绑定。

从外层 `quant-benchmark` 根目录预检批跑示例。

```sh
./batch-runner/start.sh check batch.example.json
```

批跑使用配置中的 Agent 解释器。创建自己的批次时，将示例另存为 `batch.local.json`，
将 `template` 指向所需轨迹配置，并使用新的 `batch_id`。逐题签发的隔离记忆凭证
通过单题入口使用。

## 目录

- `benchmark/*.py`：Matrix、批次调度、题包暂存和审计。
- `project/demo_runner/*.py`：当前 harness、User Agent、工具和隔离运行代码。
- `project/demo_runner/docker/Dockerfile`：执行镜像定义。
- `project/demo_runner/tests/` 与 `benchmark/tests/`：源码测试；仅作为代码附带。
- `examples/`：T01、T02、A01 的 DeepSeek 配置模板。保留当前额度设置，
  但已把本机路径替换为占位路径；这些模板不能直接启动。
- 本开源包已移除私有配置和运行产物。

## 旧 Matrix 使用前准备

1. 在 WSL/Linux 环境准备 Docker，按上面的 `uv sync` 命令建立 Python 3.12 环境。旧 Matrix 若使用 bubblewrap，沙箱内的系统 Python 必须与挂载依赖的 Python 小版本一致。
2. 取得**另行授权提供**的原题 ZIP；本包不含题目、隐藏行情、评分器、
   已暂存题包或任何轨迹产物。导入源码无需默认题目数据；worker 根据登记的
   `package_root` 绑定题目，并检查研发区间与七个仿真区间连续覆盖全部行情。
3. 按实际 WSL 绝对路径修改模板中的 `project_root` 和 `packages`，
   核对 Docker 镜像 ID，并为新运行指定未登记的 `matrix_id`。
4. 在本地设置 `DEEPSEEK_API_KEY` 环境变量；如配置使用 `api_key_file`，
   请在本地创建对应的私有 `.env` 文件。切勿把真实密钥提交到版本库。

准备齐全且明确要运行真实模型后，命令形式为：

```sh
uv run --locked --extra mcp python -I benchmark/matrix.py start --config examples/matrix.t01.example.json
```

此命令会发起真实模型请求和 Docker 执行。仅查看已有批次状态时使用
`uv run --locked --extra mcp python -I benchmark/matrix.py status --matrix benchmark/matrices/<matrix_id>`。
矩阵登记后，配置和源码受哈希锁定；变更代码或额度须建立新 `matrix_id`。

## 旧 Matrix 模板的阶段额度

每轮研发 Agent 正常 80 次模型回复，最多再给 16 次收尾；`run_research`
最多 65 次，每次最长 180 秒；策略评估共 40 组，其中 2 组保留给最终验证；
单次 sweep 最多 12 组；约 24、34 分钟提醒，40 分钟为阶段上限。
User Agent 每轮最多 3 次模型调用。真实执行以配置和源码为准。

User Agent 使用近期公开对话澄清疑问、催交，并在工具报错时介入一次。
复盘时引导读取已有研究和决策日志；是否保持策略仍由研发 Agent 决定。
用户侧请求重复处理或中断恢复不会重复调用 API。旧格式对话仍支持审计。
交付记录新增 `delivery_review` 与 `call_history`，记录机械校验、策略指纹、
模型和工具耗时；不将交付通过等同于策略盈利或研究质量通过。

User Agent 会从上一检查点的 `RESEARCH.md`、`CHANGELOG.md` 获取结构化记忆，
保存来源指纹和有长度限制的公开摘录。每次研究/回测形成 `research_evidence`，
关联工具调用、参数或扫描网格、回测区间、策略文本指纹和实际输出；完整结果仍在对话中。
文本证据指纹统一换行符，交付文件指纹仍保留原有字节级校验。

每轮预留一次 User Agent 调用做提交语义审阅，仍包含在最多 3 次的总额度内。
检查决策理由、证据支持和前后矛盾，结果为 `pass`、`concern`、`uncertain`；
超时、预算不足、中断或无效回复明确标记 `not_assessed`。
审阅意见独立记录，不改变原有机械交付门槛、策略保持权利或评分规则。

默认沿用研发模型。需独立模型时，在现有 `policy.user_agent` 内增加：

```json
"model_config": {
  "provider": "openai_compatible",
  "base_url": "https://YOUR_PROVIDER/v1",
  "model": "YOUR_USER_MODEL",
  "api_key_env": "USER_AGENT_API_KEY"
}
```

主模型启用 `cost_control` 时，独立模型也须填写自己的完整 `cost_control` 单价配置，
其 `budget_cny` 与主模型相同，并使用 `max_completion_tokens` 输出参数。
两种单价使用同一台账和同一预算上限，不会分成两份预算；独立模型不继承主模型密钥文件。

## 导出轨迹

从仓库根目录执行，将 `RUN_ID` 替换为实际运行标识。

```sh
python3 trajectory-runner/project/demo_runner/export_trajectory_html.py \
  trajectory-runner/benchmark/results/RUN_ID
```

导出器在运行目录内生成单文件 `trajectory.html`，从 v2 的 `stages/` 或旧版的 `events/` 读取对话和工具调用。导出可离线完成。

## 分享边界

本包只含源码、代码测试与脱敏模板；不含密钥、`.env`、原题 ZIP、
`benchmark/packages/`、`benchmark/matrices/`、检查点或运行日志。
