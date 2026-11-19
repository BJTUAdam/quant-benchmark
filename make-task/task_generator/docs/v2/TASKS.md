# Task 生产、自动接纳与公开包

完整包中的十二个修订规格位于`task_packages/20261110`，阅读、导入及状态说明见[TASK_PACKAGES.md](TASK_PACKAGES.md)。修复已进入生成器；生成和导出入口的相对路径统一以task_input.draft.json所在目录为基准。历史接纳快照保持原字节，导出副本仅记录入口元信息规范化，不改写研究内容或伪造复核。

## 六类选题

|方向|需要相互影响的研究决策|
|---|---|
|趋势与突破|确认、失败退出、跟踪、再入场|
|区间与均值回归|偏离、趋势否决、退出、冷却|
|横截面相对强弱|排序、持有缓冲、换仓、分配|
|状态与风险管理|状态切换、减仓、暂停、恢复|
|跨品种相对价值|对冲关系、多腿配比、失效、重估|
|同品种跨期结构|月份选择、期限匹配、移仓、流动性|

每题至少三项必需研究决策，说明依赖关系和后续观察如何促使维护。指标或参数数量不作为独立题目的充分依据。

## v2规格

保留核心机制、决策对象、决策图、来源、研究问题和维护对象。增加 bridge_contract、runtime_profile、submission_contract、protocol。内部 data_requirements 仍记录真实采集需求；公开包只出现虚拟时间范围，未绑定范围使用null。

`finlab task generate` 直接要求模型输出原生v2研究规格及桥接、运行、提交契约，不经过v1生成或迁移。只接受v2环境和finlab.strategy.v2接口，v1规格会被拒绝。历史题的机制指纹保留在对照库，改用新格式不自动计为独立母题。

公开时间必须说明为未来数百年的虚拟事件标签。不得依据虚拟年份套用现实股灾、政策或节假日，也不提供真实时间映射。未绑定数据时不填写日期；资源保持null、状态requires_human_setup。2CPU/4GB仅作推荐，无默认时限，使用者本人通过finlab setup决定，AI不得代填。

## 去重、模型复核与自动接纳

题目规格不再要求人工审批。程序检查和真实模型复核满足接纳规则后，系统保存不可变执行规格及证据；新生成与最终整批复核流程会执行自动接纳，已有有效复核材料也可以用`finlab task accept`接纳，不必重调模型。

新记录明确标注`approval_method=automated`、`status=spec_accepted`、`action=auto_accepted`；`novelty`保留distinct、variant或duplicate分类，`counts_new_question`只对没有结构重复的独立题为true。旧人工批准记录保持原来源，不伪装成自动记录，自动接纳也不记作人工批准。

结构指纹只依据核心机制、决策对象、必需决策及关系、维护对象。标题、措辞、参数值、样本年份和执行环境升级不改变任务身份。语义复核检索跨家族相近题，并输出重复、变体、独立或无法确定及逐项差异。接纳前核对规格、公开内容、查重上下文和真实模型调用记录，SQLite事务保存规格和证据。内容或比较上下文改变时，应重做受影响的模型复核，不能只改review.json。

静态有效的重复/变体候选持续参与同批对照，审核结论变化不会使它们反复退出、重入对照集。只有实际内容或有效比较记录发生变化才使已有证据过期；最终整批复核后再创建分发快照。

交付需求先查可复用版本。`finlab task reuse-options CANDIDATE_ID`解析查重近邻及实际可复用实体；`finlab task approved`沿用原命令名，列出可核验的接纳版本，并保留自动接纳与历史人工批准的来源区别。`finlab task reuse TASK_ID --output work_v2/reused_spec`读取不可变版本，不重复生成题目。

重复、变体的研究分类与执行版本接纳分开：它们不计入独立新题，但满足当前规格及模型复核要求时可作为执行版本入库。已有可复用版本时优先复用；只有旧历史对照、没有原生v2母题时，保存候选本身的规格与`related_task_id`历史关系，不为旧ID补造母题或人工批准。旧status仅因非独立分类显示needs_attention，不单独阻止接纳；但分类不确定、模型证据过期、`issues`或`validation_errors`非空、公开安全或长程有效性未通过仍会阻止接纳。完整流程见[DELIVERY.md](DELIVERY.md)。

```powershell
finlab task import-examples
finlab task check --batch BATCH_ID
finlab task review --batch BATCH_ID --api
finlab task accept --batch BATCH_ID
finlab task export CANDIDATE_ID
```

## 配置模型并持续生产

先复制或编辑config/oneapi.example.json、deepseek.example.json或glm.example.json，设置base_url、generation_model、review_model和api_key_env。generation_model与review_model可不同；调用失败、格式修复和单批调用总数均受配置上限限制。不要把密钥填进JSON。

```powershell
$env:DEEPSEEK_API_KEY = "在本机环境设置有效密钥"
finlab task doctor --config config/deepseek.example.json
finlab task generate --batch-config config/batch.json --config config/deepseek.example.json
finlab task review --batch BATCH_ID --api --config config/deepseek.example.json
```

doctor只检查配置是否齐备，不据此宣称联网成功。生成命令会真实调用配置的服务。examples/v2里的六类种子仍是离线规格示例；2026-11-05生成批次的原始API证据、逐题状态和差异见[生成任务历史](GENERATED_TASKS.md)。该历史页不随自动接纳试跑改写；当前状态以登记库和本轮报告为准。有效模型复核可以用于自动接纳，但不能替代真实行情回测。

持续生产时先看report和上一批缺口，再调整config/batch.json方向配额和来源卡。每批默认六题、最多十八个候选；接纳了重复或变体执行版本也不能把它们计入独立题配额。

生成与最终整批初筛完成后，可保存只读的可分发快照：

```powershell
finlab task snapshot --batch BATCH_ID
```

快照写入examples/generated/BATCH_ID，不覆盖已有目录，不包含原始API请求、响应正文、环境变量、真实数据或私有登记库。完整包和Task生产包会携带该目录；后续生成自动将有效快照作为去重对照。修改或损坏快照会被拒绝，活动runs与登记库状态优先。快照本身不是接纳记录；需要继续修订时用import-spec导入其中的spec.json，重新检查、模型复核并自动接纳。原快照的审核状态不被新登记覆盖。

单独生产任务规格不要求Docker或本人资源配置；开始数据绑定回测前，仍必须由本人执行finlab setup。不得为生成任务而擅自写入CPU、内存和时限。

接纳单题可运行：

```powershell
finlab task accept CANDIDATE_ID
```

命令复用当前有效证据，不联网生成新的审查结论；缺少或过期时明确报错。历史人工批准与人工变体登记继续保留原事实，旧approve入口不再是生产必经步骤。自动接纳版本需要保存自身规格、公开内容和模型证据，不能仅凭一个历史ID导出。

## 公开白名单

|文件|内容|
|---|---|
|TASK.md|客观首轮任务、研究目标、长程协议|
|DATA_API.md|桥接接口、字段单位、缺失与可见性约定|
|bridge_contract.json|机器可读接口和虚拟范围|
|runtime_profile.json|运行版本、资产、撮合、资源与待绑定规则|
|SUBMISSION.md|提交、回调、订单和状态协议|
|strategy.py、backtest.py、params.json|仅演示调用的空仓模板|
|RESEARCH.md、CHANGELOG.md|研究与逐段维护记录模板|
|task_input.draft.json|草案input入口、初始消息、三个独立状态|

没有data/目录。生产状态或task_input变化不改变研究身份；策略接口和数据契约变化会使当前公开内容审核过期。

公开数据契约说明平台按阶段累计披露，并受策略当前事件时钟约束。已绑定的任务总范围不代表现在可查全部数据；范围采用开始包含、结束不包含。显式查询部分越界也整次报TIME_RANGE_EXCEEDED，不返回行情；审计数据库、真实阶段配置与时间映射不属于公开白名单。平台侧协议见[DISCLOSURE.md](DISCLOSURE.md)。

## 绑定后的任务包

真实episode创建时要求可核验的不可变接纳版本、相同接口字段白名单、冻结版本和通过运行契约检查。人工审批不再是启动条件；已接纳任务后续更新，也不能以当前草稿替代先前接纳内容。然后生成仅包含公开白名单的已绑定任务包：

```powershell
finlab backtest task-package --episode work_v2/episode --output exports/bound_task_public
```

该命令将虚拟范围、标的、dataset_id及正式窗口写入机器契约；合约到期日期转换为虚拟日期。逐日价格限制、未来公司行为表、真实日历和原始缓存不进入任务包，按事件由可信引擎使用。绑定接口或字段变更必须先修改规格并重新检查和模型复核，不静默扩大权限。没有有效接纳记录、冻结数据和绑定的规格不能由此命令直接变成可运行任务。

参考答案、内部差异证据、数据准备prompt、真实时间映射和未来数据不得加入公开白名单。内部材料单独导出。

批跑平台接收的是另一个明确标记为私有的交接包：由`finlab delivery export`核对接纳记录、数据、镜像、代码与成功研发记录后生成。该包包含平台运行所需冻结行情和私有映射，不交给策略参与者。接收方导入后重新选择本机资源并执行完整预检；哈希验证通过不等于本机Docker运行通过。
