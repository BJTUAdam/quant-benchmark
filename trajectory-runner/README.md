# 金融长轨迹：当前跑轨迹源码快照

源码源自 2026-11-05 的 `benchmark/docker_batch_lab` 快照。历史轨迹保留登记时的源码和配置哈希，修改代码后使用新的 `run_id` 或 `matrix_id` 登记实验。

## 主要调用链

`benchmark/matrix.py` → `batch.py` → `worker.py` →
`project/demo_runner/environment_controller.py` →
`autonomous_harness.py`。`agent_view.py` 构造被测 Agent 可见内容，
`user_agent.py` 负责启动、复盘与催交付的用户话语；
`agent_tools.py` 管理研究和回测工具，`model_client.py` 请求模型，
`docker_sandbox.py` 等文件负责隔离执行。阶段仿真和审计由可信控制端完成。

## FinLab v2 单题适配

`benchmark/finlab_v2.py` 是新增的单条轨迹入口；`project/demo_runner/finlab_v2_adapter.py`
提供公开数据查询、Agent 自写的本地研究回测、交付校验，`finlab_v2_worker.py` 调用随题目系统提供的
FinLab v2 运行时。研发 Agent 交付 `strategy.py`、`backtest.py`、`params.json`、
`RESEARCH.md` 和 `CHANGELOG.md`；平台只向 Agent 返回提交是否可执行，
独立完成七段正式运行并返回六次复盘反馈。
旧题包仍使用 Matrix 入口，两套题包格式不能混用。

`benchmark/finlab.v2.demo.example.json` 配置合成股票联调；
`benchmark/finlab.v2.bound.example.json` 需要产题系统交付的真实
`binding.private.json`。本次 12 题包只有未绑定草案，尚不能作为正式七段运行输入。
在 `trajectory-runner` 目录安装 Agent 的 Python 3.12 环境并准备本地配置。

```sh
uv sync --locked --extra mcp
cp benchmark/finlab.v2.skills.example.json benchmark/finlab.v2.skills.local.json
```

在仓库根目录 `.env` 填写评测模型配置。要启用记忆，在本地 JSON 中启用 `mcp` Skill
并设置 `"isolated_memory": true`。单题脚本为每个 `run_id` 签发独立 Token，连接常驻
`mem-plugin`。启用 `load_mem_plugin_skill` 会加载子模块的记忆使用规范。

在 `make-task/task_generator` 安装 FinLab v2 到独立环境，以匹配示例的 `finlab_python`。

```sh
uv venv --python 3.12
uv pip install -e .
```

按 FinLab 的[部署说明](../make-task/task_generator/docs/v2/OPERATIONS.md)加载镜像并完成
`finlab setup`。镜像归档 `make-task/images/finlab_images_v2.tar` 可从原始 FinLab 交付包取得。
核对本地配置的研究镜像 ID 后，回到运行器目录执行。

```sh
./start.sh prepare benchmark/finlab.v2.skills.local.json
```

`prepare` 准备合成联调，`start` 会实际调用模型并启动 Docker 作业。启用记忆时先启动
`mem-plugin`，等 MCP 服务就绪后再执行 `start`。
正式题须先完成系统自动接纳、真实数据冻结与绑定，再把交付包的
`platform_private/binding.private.json` 路径填入 bound 配置。

从外层 `quant-benchmark` 根目录预检批跑示例。

```sh
uv run --project trajectory-runner --locked --extra mcp \
  python batch-runner/batchctl.py check batch-runner/batch.example.json
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

## 使用前准备

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

## 当前模板的阶段额度

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

已完成运行可用 `python project/demo_runner/export_trajectory_html.py <运行目录>`
导出单文件 `trajectory.html`。从仓库根目录提供 v2 运行路径 `trajectory-runner/benchmark/results/<run_id>`，页面按 `stages/` 展开完整对话、工具参数与结果。
旧版运行目录仍按 `events/` 导出，并保留用户侧事件及回退来源。
导出不调用模型，也不修改原始轨迹。

## 分享边界

本包只含源码、代码测试与脱敏模板；不含密钥、`.env`、原题 ZIP、
`benchmark/packages/`、`benchmark/matrices/`、检查点或运行日志。
