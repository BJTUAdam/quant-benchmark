# 部署、使用与恢复

整合版已包含主机端修复、固定镜像及十二题包。解压使用[TASK_PACKAGES.md](TASK_PACKAGES.md)的目录与安装步骤；本版不提供需要再次应用的补丁ZIP。

题目规格不再要求人工审批。需要交付可批跑任务时，先按[DELIVERY.md](DELIVERY.md)查询已接纳版本和已有冻结数据；已有新鲜真实模型证据的候选可直接`task accept`，再执行准备、预检、研发验收与平台私有交接。不要反复生成同义题目来获得状态。原分发快照和历史人工记录保留原事实，新的机器接纳另行登记。

## 安装与诊断

要求Docker Desktop处于Linux容器模式且引擎运行，推荐Python3.12。镜像固定Python基础摘要、AKQuant/Tushare和全部传递依赖；build-images记录本机镜像ID。

```powershell
python -m pip install -e .
finlab setup
finlab doctor
finlab backtest load-images ../images/finlab_images_v2.tar
```

`finlab setup`必须由使用者本人在交互终端输入CPU、内存和单次运行时限，并输入“本人确认”保存。2CPU/4GB只作推荐，输入留空会重新询问；时限无默认值，显式输入none才代表不限时。AI不能代填或创建确认文件，也没有--timeout覆盖入口。配置保存在本机.finlab/operator.json，不进入交付包。SETUP_REQUIRED表示需要本人完成此步骤；这是操作门禁，不宣称能够识别操作者身份。详见[首次使用与本人配置](SETUP.md)。

## 先跑合成演示

以下命令构造明确标记的合成行情和交易规则，仅检验协议，不代表真实市场数据或成绩。每个演示目录必须不存在。

```powershell
finlab demo build --asset STOCK --output work_v2/demo_stock
finlab backtest init --binding work_v2/demo_stock/binding.private.json --episode work_v2/demo_stock/episode
finlab submission check work_v2/demo_stock/submission
finlab backtest run --episode work_v2/demo_stock/episode --submission work_v2/demo_stock/submission --research
finlab backtest run --episode work_v2/demo_stock/episode --submission work_v2/demo_stock/submission
finlab backtest status --episode work_v2/demo_stock/episode
```

不加--research执行下一段；连续执行七次完成正式期。FUND和FUTURES用同样命令更换asset及目录。`finlab test`执行程序测试并记录报告；`finlab verify`实际创建多个容器，自动完成三类资产、七段恢复、部分成交、跨段订单、股票/ETF分钟可卖规则与隔离/失败重试检查，并更新验收报告。

## 绑定真实任务

先取得自动接纳的不可变规格版本，或复用真实历史接纳版本。配置采集plan，准备唯一键、事件字段、每行可见性、单位、历史成分和交易规则，再采集、冻结。取消题目审批不取消数据完整性与历史可见性检查。

```powershell
finlab task accept CANDIDATE_ID
finlab task accept --batch BATCH_ID
finlab task approved
```

```powershell
finlab data init work_v2/source.sqlite
finlab data collect --db work_v2/source.sqlite --plan config/v2/collection.example.json
finlab data freeze --db work_v2/source.sqlite --plan config/v2/freeze.example.json --output work_v2/frozen
finlab backtest init --binding work_v2/binding.private.json --episode work_v2/episode
```

binding包含frozen_dir、candidate_id、approved_spec_hash、contract_review_confirmed、feeds及runtime。兼容字段名保留，但真实数据绑定可以引用机器自动接纳的v2规格，不再要求人工审批。规格哈希、模型证据、接口字段和运行契约仍须匹配；contract_review_confirmed是绑定核验状态，不应仅为绕过缺口而改成true。合成测试有单独synthetic标记。

runtime必须填写instrument完整规格、真实交易日calendar、每个标的sessions、逐日execution_rules、公司行为清单及其核验、初始资金。缺少lot_size、费用、保证金、结算价或价格限制时禁止启动。

已接纳任务使用`finlab delivery prepare`将规格与明确选择转换成相互引用的采集、冻结和绑定材料。已有冻结版本用`--frozen`复用，仍检查哈希和契约。缺失数据、可见性依据和运行规则继续列为阻塞，不把下载成功自动记为覆盖完整或历史时点可见。

自建Tushare地址与令牌分别设置`TUSHARE_UPSTREAM_URL`和`TUSHARE_TOKEN`。先执行`finlab data probe --plan work_v2/probe.private.json --output work_v2/probe_report.private.json`，只把实际探测的接口及参数记为已实测。使用标准SDK时建立pro对象后必须设置`pro._DataApi__http_url`，`ts.pro_bar`显式传`api=pro`。请求错误保留明确分类和经脱敏说明，不当成空数据，不擅自切源。具体调用范式见DELIVERY.md。

平台本机也可使用`.finlab/upstream.private.json`，只存`url`和`token_env`，环境地址优先，令牌值不落盘。标准SDK对象可由`finlab.upstream.official_pro()`工厂创建；该工厂设置自建地址并使用严格HTTP查询，避免SDK把非2xx误变空DataFrame。本轮`daily`、`trade_cal`、`stk_limit`及标准SDK日线样本的真实探测见`docs/upstream_probe_v2.json`；分红带报告期查询4502错误保持失败，另一125条样本不能宣称覆盖完整或PIT已证明。

逐标的逐日规则必须解释每个日历日；可交易日缺行情会阻止创建。停牌、未上市或其他不能交易日期须显式tradable=false，不能只删掉行情行。benchmark可设置为已冻结的index_daily和真实指数代码，需覆盖全部账户估值日。数值为null、NaN、负费用或无效交易单位均不作为已绑定规则。

sessions每段写`["09:30","11:30","continuous"]`；夜盘可以写第四项`"prev"`表示前一个交易日，例如`["21:00","23:00","continuous","prev"]`。真实交易日归属由引擎结合日历处理，不能直接用夜盘自然日替代。

启动研发数据服务：

```powershell
$env:MARKET_BRIDGE_TOKEN = "单独设置访问令牌"
finlab bridge serve --episode work_v2/episode --host 127.0.0.1 --port 8766
```

这只提供研发可见范围。服务校验episode的disclosure.private.json，并按已成功提交段号累计开放；未配置有效阶段范围不启动。正式策略的桥接服务由可信引擎按事件创建，不复用研发边界。正式任务启动前还需将公开契约、交易规则与实际绑定逐项对照，执行一次研发验收。范围与审计说明见[DISCLOSURE.md](DISCLOSURE.md)。

平台按请求编号排查越界时，可只读查看私有记录：

```powershell
finlab bridge audit --episode work_v2/episode --limit 100
finlab bridge audit --episode work_v2/episode --job JOB_ID --limit 100
```

不传job查看研发日志；传job查看该次容器日志。输出含平台私有时间和权限依据，不复制给策略。审计文件在清理容器后继续保留，不能为解决查询错误而删除或关闭审计。

## 每段生命周期

控制器取得episode锁 → 校验冻结数据与绑定哈希 → 校验并复制提交文件 → 新建内部网络与两个容器 → 读取上一段可信检查点和memory → 执行本段 → 校验系统结果 → 原子写入提交状态 → 释放已完成数据 → 删除临时容器与网络。

研发回测始终从初始账户重跑当前已解锁范围，不改变正式账户。正式第一段从初始资金开始；后续段恢复正式检查点。代码与参数可以在段间替换，账户规则和冻结数据版本不能修改。

## 批跑预检与平台交接

```powershell
finlab delivery check --binding work_v2/binding.private.json --submission work_v2/submission --output work_v2/preflight.private.json
finlab delivery batch work_v2/batch.private.json --output work_v2/batch_preflight.private.json
finlab delivery batch work_v2/batch.private.json --output work_v2/batch_run.private.json --execute
```

默认只读检查，集中列出接纳记录、冻结、交易规则、提交、本人资源和Docker镜像各层阻塞；不执行策略代码。`--execute`只有整批预检通过才运行研发验收，不推进正式阶段。未运行、失败、通过分别计数。

平台私有交接用`finlab delivery export`；它要求与现有规格、数据、镜像和提交一致的成功研发记录。接收方先确认可信来源，用`finlab delivery import`校验白名单、路径与逐成员哈希，再在本机完成资源选择和研发验收。导入保留自动接纳或旧人工批准的原始来源，不把无证据草稿变成已接纳，不自动开始回测。

旧人工变体链继续携带真实母题批准证据，导入先验证母子关系再转移原事实。机器接纳的variant/duplicate是候选自身的执行规格，保留历史关系及不计独立新题标记，不补造历史母题批准。源公开目录是发送端证据；接收方本机验收后使用新episode重新生成公开包，避免沿用源机器预算。

该私有ZIP包含真实数据和映射，只给平台运营者。原`finlab backtest task-package`仍只生成策略参与者可见的十一文件公开包。两种包不能互相替代，也不能把私有ZIP复制到参与者工作目录。

## 失败恢复

|错误|处理|
|---|---|
|UPSTREAM_TOKEN_MISSING|在运营进程配置令牌，不写进plan|
|UPSTREAM_HTTP_FAILED / PAGINATION_LIMIT|核查权限、频率和分区；不声明采集完成|
|UNCLASSIFIED_FIELD / UNCLASSIFIED_TIME_FORMAT|登记字段或精度规则后重新采集/冻结|
|TIME_RANGE_EXCEEDED|本次无任何行情返回；按request_id查私有审计，修正虚拟查询范围或等待阶段成功提交，不改写披露策略|
|BRIDGE_AUDIT_UNAVAILABLE|服务已停止返回行情；检查私有审计目录可写性、磁盘空间和存储故障，再重试|
|DOWNLOAD_TIME_NOT_HISTORICAL|补充历史可见性证据，不能改标签冒充|
|BINDING_INCOMPLETE|补齐交易规则、日历和单位|
|接纳引用无法解析或缺少当前模型证据|先用task approved/reuse-options查看版本；已有新鲜证据则task accept，缺失或过期则重新模型复核，不等待人工审批|
|只有历史草稿、没有v2执行实体|接纳满足条件的当前v2候选，记录历史关系与非独立分类；不为旧ID补造批准|
|交付研发记录与代码/数据不一致|保持旧记录，使用当前固定组合重新研发；不能复制或修改receipt|
|Windows审查文件写入失败|检查短目录、文件占用与可写性；保留原审查文件并明确报错，不伪造模型证据或写成功状态|
|BACKTEST_FAILED|查看job中的engine.private.log和worker.private.log；修改提交后重试同段|
|DOCKER_UNAVAILABLE_OR_TIMEOUT|检查Docker和资源；本轮未提交|
|BUSY|确认无运行进程后，由运营方处理遗留锁；不得并发推进|
|FROZEN_VERSION_CHANGED / CHECKPOINT_CHANGED|停止复用已改变版本，恢复原文件或创建新episode|

jobs中失败或未提交的结果不作为正式成绩。不要通过复制成功文件或手改段号恢复；重复运行会使用上一个已提交检查点。运行中断可能留下临时Docker对象，由运营方按该job名称清理，避免操作其他项目的容器。

## 镜像与源码归档

```powershell
finlab backtest save-images finlab_images_v2.tar
finlab backtest load-images finlab_images_v2.tar
finlab docs build
finlab bundle --output dist
```

源码压缩包不含镜像、真实行情、环境变量、work_v2、运行日志和候选API响应。三个分工具包与完整源码包共用同一版本清单。镜像单独导出后，接收方使用相应images.lock.json验证镜像ID。完整工具包适用于平台开发者，参赛agent只接收public包。
