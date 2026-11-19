# 从题目规格到可批跑任务的交付

本文件供出题、数据准备和批跑平台的 AI 阅读。题目规格已取消人工审批，默认由程序检查和真实模型复核触发自动接纳入库。任务交付仍依据不可变接纳规格、冻结数据、运行绑定及实际研发验收；候选生成成功、模型指出相似题、HTTP查询成功或规格已接纳，都不能单独表示任务可以批跑。

## 当前存档的实际状态

取消人工审批前，本机登记库只有六份v1历史对照任务，没有人工批准或变体批准记录；原有`work_v2`冻结版本均为合成测试。十五份DeepSeek分发快照保留原来的草案、重复或变体状态。新自动接纳记录单独保存，不回写旧快照、不伪造旧人工批准，也不把重复和变体计为独立新题。

反馈中提到的“三只股票、569个交易日、11次现金分红”真实冻结数据未在这份本地工作区中找到。取消题目审批不会补出这些文件；系统将其记为待接入数据，不能用相同规模的合成数据代替。

本轮实际自动接纳6份规格：3份重复、3份变体；人工批准记录新增0，独立新题新增0。其余12份因规格错误或模型仍有未解决问题而未接纳。两份股票任务继续执行准备和批跑预检时，接纳检查均通过，Docker与镜像也可用；正式冻结、历史可见性及资源配置仍未齐备。

本包`examples/accepted`携带这6份不可变接纳记录。新解压后执行`finlab task init`核验并恢复，再用`finlab task approved`查看。它们不要求重新生成或人工审批；原15份候选快照保持历史状态。

本轮自动接纳及重试结果见`docs/automatic_acceptance_retry_v2.json`；真实上游区间查询见`docs/automatic_upstream_retry_v2.json`。后者记录选定股票2024—2025区间的日线485行、复权因子485行、涨跌停485行、交易日历731行和指数日线485行，是实际查询样本，不是冻结完整性或历史可见性认证。是否已接纳、是否已冻结、是否通过研发，分别读取各阶段报告。

下面的流程适用于已有接纳版本复用，以及新候选完成自动接纳后的交付。没有有效记录时，先尝试接纳当前新鲜证据；缺模型证据才重新复核，缺真实数据则补数据，不为获得一个状态反复重写题面。

## 三种记录必须分开

|记录|证明的内容|不能证明的内容|
|---|---|---|
|历史查重记录|这道题与哪个历史研究机制相近|该历史ID已有可复用v2实体、可用数据或运行验收|
|规格接纳版本|确定的规格与公开内容通过程序检查、有效模型复核并入库；来源可区分自动接纳和旧人工批准|绑定数据、本人资源配置和Docker已就绪；它必然是独立新题|
|交付验收|确定规格、数据、镜像与提交代码组合完成预检及实际研发运行|其他代码版本或其他机器自动通过；任务拥有策略盈利能力|

版本引用使用任务ID、版本、候选ID和规格哈希。取版本时读取登记库的不可变规格与证据，不把同名草稿当成已接纳实体。修改规格后应重新检查和模型复核；原版本继续保存。为便于已有运行绑定对接，部分兼容命令和字段仍使用approved命名，但机器记录必须保留`approval_method=automated`，不能据字段名宣称发生过人工审批。

## 1. 先查复用，再决定是否生成

```powershell
finlab task approved
finlab task reuse-options CANDIDATE_ID
finlab task accept CANDIDATE_ID
finlab task accept --batch BATCH_ID
finlab task approved-show TASK_ID --version 1
finlab task reuse TASK_ID --output work_v2/reused_spec
```

`approved`沿用原命令名，展示可核验的接纳实体及来源；`reuse-options`读取查重近邻及可复用版本；`approved-show`读取确定版本和证据。`accept`在不调用新模型的情况下核验并接纳已有有效审查材料；生成和最终整批复核也会自动接纳。历史引用无法解析时只记录研究关系；可接纳当前候选本身，但不能把旧ID变成不存在的母题。`reuse`提取真实存在且哈希匹配的版本，不新增独立题目计数。

重复、变体可以作为执行规格接纳，其`novelty`仍保留原分类、`counts_new_question=false`。机器接纳不是人工变体登记，不要求先把一个历史v1母题变成已批准v2母题；它保存候选自身的原生v2内容及`related_task_id`。已有真正的母题—变体批准链仍按原证据保留。不要改写分类、母题ID或review.json来制造新颖性或有效模型证据。

自动接纳要求规格检查通过、真实model_api复核事件存在，规格／公开内容／比较上下文哈希匹配，无未解决issues或validation_errors，公开安全和长程有效性通过。分类uncertain、内容改变或证据过期仍会失败。缺少可用模型密钥时不能补造新审查；只有已保存且仍有效的证据能直接接纳。

复用和交付不需要再调用生成模型。只有存在实际研究差异和方向缺口时，才回到 `finlab task generate`。

## 2. 平台侧接入自建 Tushare 服务

```powershell
$env:TUSHARE_UPSTREAM_URL = "在本机设置自建服务地址"
$env:TUSHARE_TOKEN = "在本机设置实际令牌"
finlab data probe --plan work_v2/probe.private.json --output work_v2/probe_report.private.json
```

服务地址、令牌分别由平台环境提供，不写进任务包、源码、提示词、普通日志或示例 JSON。`probe` 按计划中的具体接口和字段检查实际响应；成功只覆盖执行过的查询，不宣称整个官方目录都可用。本服务未支持的接口、鉴权失败、权限不足、分页不支持或数据缺口均须保留为明确失败，不转换为空行情，不切换其他数据源。

可选平台本机配置`.finlab/upstream.private.json`只保存`url`和`token_env`，不保存令牌值。`TUSHARE_UPSTREAM_URL`环境变量优先于本机地址配置；令牌从指定环境变量读取。当前机器只保存服务地址与令牌变量名，未保存令牌。该私有配置不进入源码工具包或任务交接包。

平台采集器支持 Tushare Pro HTTP 协议。需要在平台脚本使用官方 SDK 时，每次建立对象后设置自建地址；使用包装函数必须显式传入同一个对象：

```python
import os
import tushare as ts

pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
pro._DataApi__http_url = os.environ["TUSHARE_UPSTREAM_URL"]
data = pro.daily(ts_code="000001.SZ", trade_date="运营方明确选择的真实YYYYMMDD")
# 如需包装：ts.pro_bar(..., api=pro)
```

平台也提供`finlab.upstream.official_pro()`工厂：它创建官方SDK对象并设置自建地址，将对象查询路由到平台严格HTTP传输，以保留非2xx状态和服务端错误。该防护用于避免固定SDK 1.4.29遇到非2xx响应时返回空DataFrame。需要该对象的环境可安装可选依赖：

```powershell
python -m pip install -e ".[tushare]"
```

```python
from finlab.upstream import official_pro
pro = official_pro()
# 查询沿用pro.daily/pro.query等官方对象入口；包装仍须显式api=pro。
```

上述对象只属于平台采集侧。策略仍使用 `from finlab import tushare as ts`，访问任务桥接，不能把上游令牌或自建服务地址传入策略容器。接口通过不表示历史可见性已经成立；公告时点、修订版本和缺失范围仍须单独核验。

本轮实际连通性摘要位于`docs/upstream_probe_v2.json`：HTTP样本中`daily`返回1行、`trade_cal`返回3行、`stk_limit`返回1行；标准SDK的`daily`也实际返回1行。`dividend`带报告期过滤的样本返回服务错误4502，已保留错误；另一无报告期过滤查询返回125条记录。严格SDK工厂的后续分红查询实际遇到40203限频错误，保留了服务端“一小时一次”的说明，没有变成空DataFrame，也没有追加查询绕过限制。上述次数和行数仅描述这批实际探测，不能证明全标的覆盖、分红完整性或历史可见性，更不是反馈中569日冻结数据的接管验收。

## 3. 从接纳规格产生准备材料

```powershell
finlab delivery prepare --task TASK_ID --selection work_v2/selection.private.json --output work_v2/prepared
finlab delivery prepare --task TASK_ID --version 1 --selection work_v2/selection.private.json --frozen work_v2/existing_frozen --output work_v2/prepared_reuse
```

输入是一个确定的接纳版本和明确选择的标的、范围及规则。输出把采集、冻结和运行绑定连接成同一组可核对材料，并列出缺口。选择已有冻结目录时，检查内容哈希、接口和字段、标的、时间覆盖以及可见性规则；不重新采集已确认的冻结版本。移除题目审批不等于替未验证的数据打上coverage或point_in_time标签。

准备命令不会把未核验项自动改成 `true`。日历、停牌和未上市状态、逐日价格限制、费用与单位、公司行为和信息可见时间必须有数据或明确的实验规则依据。现金分红与拆股转换后仍需核验适用日期和金额单位；其他未支持的公司行为应阻止正式启动。缺失行情不能直接解释为停牌，当前下载时间不能充当历史公告时间。

选择文件的完整模板为`config/v2/preparation.selection.example.json`。必填`real_start`、`real_stop`，按完整日的`[start,stop)`解释。`runtime`保存明确的标的规格、日历、交易时段、逐日规则、公司行为及基准；`feeds`说明接口、事件字段和单位倍率。`api_policies`给出采集时的唯一键、事件和可见性规则、分区、分页、覆盖证明；复用已验证冻结版本时无需重填采集规则。`contract_review_confirmed`保留为显式确认。

输出目录包含`collection.private.json`、`freeze.private.json`、`binding.private.json`及`preparation.private.json`；目录必须不存在，避免覆盖已有证据。准备报告逐项列出`phase/code/path/message/blocking`，`ready`只表示准备检查通过，不能替代实际研发运行。

当前优先支持股票、ETF日线。可以显式填写已有期货运行规则，但不会根据品种名猜合约乘数、保证金、到期或费用。可选`derive_from_frozen`仅从明确冻结的日历、涨跌停和分红字段机械转换；它要求运营方指定交易所、缺失行情不可交易的证据，以及现金分红核算方式和单位。送股、转增等未支持事件不能强制当成纯现金分红。

资源仍保持待本人配置。使用者本人在交互终端执行 `finlab setup`；AI 不得代选 CPU、内存或时限，不得复制别人的本人确认记录。数据的真实时间和采集范围只存在私有准备材料，公开日期由桥接映射。

## 4. 完整预检，集中列出阻塞

```powershell
finlab delivery check --binding work_v2/prepared/binding.private.json --submission work_v2/submission --output work_v2/preflight.private.json
```

检查围绕同一次交付组合执行：

|检查层|要核对的内容|
|---|---|
|规格与接纳|自动接纳或旧人工批准的真实来源、模型证据、版本、不可变规格哈希及原生v2协议|
|数据与契约|冻结清单和数据库哈希、接口字段、覆盖、标的、时间类型与历史可见性|
|运行绑定|频率、行情单位、交易日历、每标的规则、公司行为、资金和分段要求|
|提交代码|五份必需文件、协议、依赖、静态可运行入口和提交哈希|
|本机运行|本人资源选择、Docker 服务、固定镜像是否可用|

报告必须区分通过、缺失与尚未执行，集中列出阻塞。规格通过后发现Docker不可用，不应丢失规格检查结果；Docker可用也不能掩盖缺少有效模型证据或未完成接纳。预检不执行参与者代码，不能写成“回测已通过”。

全部预检通过后创建 episode 并实际研发运行：

```powershell
finlab backtest init --binding work_v2/prepared/binding.private.json --episode work_v2/episode
finlab backtest run --episode work_v2/episode --submission work_v2/submission --research
```

每次运行创建新的策略与可信引擎容器。研发验收不推进正式段号，也不提前开放未提交阶段的数据。正式七段运行和失败恢复继续采用 [CONTROLLER.md](CONTROLLER.md) 与 [DISCLOSURE.md](DISCLOSURE.md)。

## 5. 导出平台私有交接包

```powershell
finlab delivery export --episode work_v2/episode --submission work_v2/submission --output work_v2/platform_handoff.private.zip
```

导出要求找到与当前规格、冻结版本、固定镜像和提交代码完全匹配的成功研发记录；修改过其中任意一项，须重新预检和研发验收。不能拿另一题、另一份代码、另一组数据或合成演示的通过记录替代。

该包给受信任的批跑平台运营方，包含运行必需的私有冻结数据、真实时间映射、绑定、接纳版本及其来源证据、提交代码和交接清单。逐成员哈希用于检测损坏或改动，不能证明发送者身份。它与不含凭证和行情的公开任务包是两个不同收件范围。

旧人工变体登记的交接仍额外携带真实母题记录`platform_private/parent_approval.json`及其哈希，保持原母子批准关系。新的机器接纳规格即使分类为variant或duplicate，也以自身不可变执行版本交付，历史相似引用不是一份母题批准，不要求或补造parent_approval。两种来源不能混淆，均不因交付而新增独立题计数。

私有交接包不包含上游令牌、模型密钥、宿主机资源确认文件、任意目录或用户提供的序列化检查点。交接新任务不继承已运行账户，接收方建立新的 episode；已有七段账户迁移不属于此命令的承诺。

## 6. 接收方核验与启动

```powershell
finlab delivery import work_v2/platform_handoff.private.zip --output work_v2/imported_task --trust-note "运营方填写可信来源核验说明"
```

导入前由接收方运营人员判断来源可信；不要让AI根据一个ZIP的自述自动填写信任说明。导入校验ZIP路径、成员白名单、全部哈希和接纳实体一致性，拒绝绝对路径、目录穿越、重复条目、额外私有文件及不匹配版本。校验通过后重定位相对路径，保留原机器接纳或历史人工批准来源，不把一份无证据草稿直接改成已接纳。

包内结构为：

```text
HANDOFF_MANIFEST.json
platform_private/
  approval.json
  parent_approval.json    仅原人工变体链包含；不为机器分类补造母题
  binding.private.json
  research_acceptance.json
  preflight.private.json
  frozen/
    manifest.private.json
    snapshot.sqlite
participant_public/    仅十一文件公开任务契约
submission/            五份提交文件
```

导入保留交接材料，并在输出根生成重定位后的`binding.imported.private.json`和`IMPORT_STATUS.private.json`。后续check/init使用此绑定，不能继续使用源机器的绝对`frozen_dir`。原`participant_public`标记为`origin_snapshot_do_not_dispatch`，保留为发送端证据，不直接派发；接收方在本人资源配置和本机研发验收后，使用新episode重新生成公开包。其余材料及整个ZIP始终只留在平台侧。

接收方本人的运行预算不从源机器继承。导入不会自动启动 episode 或 Docker，也不会把发送端的研发通过当成本机通过。接收方应使用本人 `finlab setup` 配置、核对固定镜像，再对导入绑定执行 `delivery check` 并完成本机研发运行。

对原人工变体链，临时独立登记库先核验母题和变体证据及结构关系，再转移原事实。机器接纳版本核验自身规格、原模型证据、公开快照及自动接纳来源。本地已有身份或版本冲突时拒绝覆盖；导入不创建新的模型结论、人工批准或独立题计数。

Windows 建议把完整工具解压到较短路径，例如 `D:/finlab`。交接清单使用包内相对路径，不能依赖发送机器的绝对目录。避免在资源管理器预览、同步客户端或编辑器锁住审查文件时连续提交；写入失败应保留旧文件并返回错误，不跳过审查或伪造成功。

## 7. 批次预检与研发执行

批次计划中每项指定 `binding`、`episode`、`submission`。先进行整批只读检查：

```json
{
  "schema_version": "finlab.batch_plan.v1",
  "items": [
    {
      "binding": "work_v2/imported_task/binding.imported.private.json",
      "episode": "work_v2/new_episode",
      "submission": "work_v2/imported_task/submission"
    }
  ]
}
```

```powershell
finlab delivery batch work_v2/batch.private.json --output work_v2/batch_preflight.private.json
```

只有整批检查没有阻塞后才执行：

```powershell
finlab delivery batch work_v2/batch.private.json --output work_v2/batch_run.private.json --execute
```

默认命令只预检。`--execute`只执行研发验收，不自动推进正式七段，不代替题目接纳或忽略失败项。批次报告逐题保留规格、数据、运行和执行状态；未开始的题不能记为运行成功。正式七段按已创建的episode和维护协议单独运行。

## 公开给策略参与者的内容

```powershell
finlab backtest task-package --episode work_v2/episode --output exports/bound_task_public
```

公开包仍严格使用 [TASKS.md](TASKS.md) 中的十一文件白名单。它不包含真实行情、私有映射、冻结数据库、批准审查依据、访问令牌或平台交接 ZIP。策略参与者从桥接取得当前允许的数据，提交 `strategy.py`、`backtest.py`、`params.json`、`RESEARCH.md` 和 `CHANGELOG.md`。

## 从现有工作恢复需要的最小材料

|现有成果|平台需要接入的材料|
|---|---|
|已有接纳题|准确的任务/候选ID、不可变规格哈希、接纳来源和真实模型证据；由可信出题端导出私有交接包，原人工变体链保留母题证据|
|只有候选和复审|原生v2规格及对应真实审查证据；先用task accept核验，过期或缺失才重做模型复核，不再等待人工审批|
|已有冻结数据|`manifest.private.json` 与对应 `snapshot.sqlite`，保持原始哈希和可见性依据|
|已有交易规则|绑定文件中的日历、逐日规则、合约规格、公司行为及核验依据；不能只提供汇总行数|
|已有代码|完整五份提交文件及对应成功研发记录；代码哈希变化后重新验收|

没有这些材料时输出精确缺口和当前状态。API连接成功可以用于补齐数据；它不能替代历史冻结证据。自动接纳解决规格流转，不代填首次资源选择、不取消数据和时点检查，也不将静态检查写成真实回测通过。整个流程不需要重新生成同义题目来绕开数据或环境缺口。
