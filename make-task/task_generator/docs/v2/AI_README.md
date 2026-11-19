# 金融长程系统 v2 · AI 阅读入口

本次完整包为`v2-integrated-task12-20261110`。源码已合并修复，镜像、SDK及十二个题包同包交付，不需要补丁安装。先读[TASK_PACKAGES.md](TASK_PACKAGES.md)，从`task_packages/20261110/题包索引.md`选择任务。十二份新规格尚未外部API复核或自动接纳；六份已接纳历史版本与十五份原始候选快照分别保留。

本目录供开发、数据准备和运行平台的 AI 阅读。题目生产默认使用程序检查、真实模型复核和自动接纳入库，不再等待人工审批。参赛策略 agent 接收接纳后导出的 public 任务包、通用 [finlab-strategy 技能](../../skills/finlab-strategy/SKILL.md) 和受限桥接环境；不提供本目录、控制器配置、原始缓存或真实时间映射。

技能覆盖当前版本的246个Pro接口官方全文、常见调用问题、平台策略SDK和提交代码生成规范。接入Flash等模型的方法见 [SKILLS.md](SKILLS.md)：运行管线须主动加载入口与按需检索资料，目录本身不会自动进入模型上下文。

先读 SYSTEM.md，再按职责读 TASKS.md、[DELIVERY.md](DELIVERY.md)、BRIDGE.md、[DISCLOSURE.md](DISCLOSURE.md)、STRATEGY_SDK.md、SUBMISSION.md、CONTROLLER.md、OPERATIONS.md。DELIVERY.md连接自动接纳规格及历史版本复用、数据准备、整批预检、实际研发和私有交接；DISCLOSURE.md说明阶段范围、整次越界拒绝和私有审计。策略SDK与提交共用正文已集中在技能内，旧文档保留链接。实际测试状态以对应报告的日期、范围和执行版本为准；旧报告中的“真实账号未实测”不能替代本次具体接口探测，本次探测通过也不能反过来宣布全部接口都已验证。

DeepSeek历史生成候选及原始初筛状态见[GENERATED_TASKS.md](GENERATED_TASKS.md)和上级live_generation_v2.json。examples/generated中的原快照保留其未人工批准的历史事实；有效复核证据可用于新增自动接纳记录，不回写原快照。新生成任务不要求先运行Docker或替使用者选择资源预算。

security_dns_v2.json单独记录收紧DNS后实际执行的容器检查。browser_qa_v2.json记录离线页面功能与窄窗口检查；分工具包使用同一套说明，跨工具命令以完整源码包为准。

## 执行约定

- 从项目根目录运行；推荐 Python 3.12，`python -m pip install -e .`。所有命令也支持 `python -m finlab` 替代 `finlab`。
- 模型通过已有 taskgen 适配层调用，支持 OpenAI 兼容地址、DeepSeek、GLM；密钥读取环境变量。不要复制对话中曾出现的密钥。
- 接到“交付可批跑任务”时先查已接纳版本与已有冻结版本，使用task approved/reuse-options/reuse和delivery流程；已有新鲜真实模型复核的候选可用task accept直接接纳，不默认重新生成或重复复审。相似历史ID只证明研究关系，不证明本地有可运行实体。
- 自动接纳记录注明`approval_method=automated`、`status=spec_accepted`和新颖性分类。重复、变体可以进入可复用执行目录，但不能计入独立新题。没有可运行母题时保存候选本身的v2规格与历史关系，不为旧母题或旧人工审批补造事实。
- accept不产生新模型结论：须匹配规格哈希、公开内容哈希、查重上下文和已记录的真实模型审查。过期或缺失时重新复核；缺少模型API密钥时明确报未执行，不复用过期证据或编造调用记录。
- 上游只在运营采集侧使用 `TUSHARE_UPSTREAM_URL` 和 `TUSHARE_TOKEN`。策略侧只使用 `MARKET_BRIDGE_URL` 和任务令牌。
- 只生产、检查和导出原生v2任务，不接收v1规格，不执行环境迁移。既有历史机制、人工批准和旧审查记录保留原始来源，不自动继承到新题；新自动接纳不写成一次人工批准。
- 首次资源配置由使用者本人执行finlab setup；AI不能代选CPU、内存或时限，不能填交互、伪造确认文件或通过命令覆盖。缺配置返回SETUP_REQUIRED时，应请使用者本人完成，不自行给预算。
- 公开时间是未来数百年的虚拟事件标签，不能按虚拟年份套用真实历史事件；只依据任务可见数据和平台日历。平台实际偏移量不放入public或策略技能。
- 任务规格只承诺明确绑定的接口、字段和交易规则。没有数据、权限或时点证据时记录缺口；不生成价格、答案或实测成绩。
- 新接口先更新目录或增加版本化字段规则；未识别字段与时间格式不得透传。
- 不在宿主机执行提交代码，不加载用户提供的 pickle，不把 Docker socket 挂载给策略。
- 不直接修改 episode 的 state.private.json 推进段号。使用回测控制器提交成功结果；失败从上一个已提交状态重试。
- 不改写 disclosure.private.json 扩大阶段范围；该文件与episode绑定。越界整次返回TIME_RANGE_EXCEEDED并留request_id；审计失败返回BRIDGE_AUDIT_UNAVAILABLE，二者都不能当作空行情或改为上游补抓。私有审计不进入公开任务包。
- 平台私有交接包包含冻结行情及真实时间，仅交给可信运营方；策略仅接收public白名单。ZIP哈希不证明来源，导入需要运营方明确判断可信来源，不代填信任说明或本人资源选择。

## 稳定入口

```text
finlab doctor
finlab setup
finlab task generate --batch-config config/batch.json --config config/deepseek.example.json
finlab task check --batch BATCH_ID
finlab task review --batch BATCH_ID --api
finlab task snapshot --batch BATCH_ID
finlab task accept CANDIDATE_ID
finlab task accept --batch BATCH_ID
finlab task export CANDIDATE_ID
finlab task approved
finlab task reuse-options CANDIDATE_ID
finlab task reuse TASK_ID --output work_v2/reused_spec
finlab data probe --plan work_v2/probe.private.json --output work_v2/probe_report.private.json
finlab delivery prepare --task TASK_ID --selection work_v2/selection.private.json --output work_v2/prepared
finlab delivery check --binding work_v2/prepared/binding.private.json --submission work_v2/submission --output work_v2/preflight.private.json
finlab delivery export --episode work_v2/episode --submission work_v2/submission --output work_v2/platform_handoff.private.zip
finlab delivery import work_v2/platform_handoff.private.zip --output work_v2/imported_task --trust-note "运营方填写可信来源核验说明"
finlab delivery batch work_v2/batch.private.json --output work_v2/batch_preflight.private.json
finlab catalog check --output docs/catalog_coverage_v2.json
finlab data init work_v2/source.sqlite
finlab data collect --db work_v2/source.sqlite --plan config/v2/collection.example.json
finlab data freeze --db work_v2/source.sqlite --plan config/v2/freeze.example.json --output work_v2/frozen
finlab bridge serve --episode EPISODE_DIR
finlab bridge audit --episode EPISODE_DIR --limit 100
finlab submission check SUBMISSION_DIR
finlab backtest build-images
finlab backtest init --binding BINDING_PRIVATE_JSON --episode EPISODE_DIR
finlab backtest run --episode EPISODE_DIR --submission SUBMISSION_DIR --research
finlab backtest run --episode EPISODE_DIR --submission SUBMISSION_DIR
finlab backtest status --episode EPISODE_DIR
finlab backtest task-package --episode EPISODE_DIR --output BOUND_PUBLIC_DIR
finlab backtest archive --episode EPISODE_DIR --output RESULTS_ZIP
finlab verify
finlab test
finlab docs build
finlab bundle
```

`config/v2/*.example.json` 是未确认的配置模板，不能直接当作已验收数据。最快验证方式见 OPERATIONS.md 的合成演示。

取消人工审批前，本地登记库人工批准数为0，原有冻结版本均为合成样本；反馈中的三股票真实冻结版本未在本地存档中找到。这是历史核查结果，不作为当前自动接纳数量。当前重试是否完成接纳、冻结、研发和导出，分别由实际命令和本轮报告记录；不得因为去掉人工审批就声明真实数据或运行环境也已齐备。最小接入材料见DELIVERY.md。

## 修改后的检查

运行 `finlab test` 检查当前工具包的全部自动测试。改动撮合、容器、状态恢复或可见性边界后，重新构建镜像并运行 `finlab verify`，保存真实输出。HTML 由 Markdown 与当前报告构建，更新内容后执行 `finlab docs build`。源码工具包不包含运行私有目录或镜像二进制；镜像通过单独命令导出。
