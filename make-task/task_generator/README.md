# 金融长程任务系统 v2

当前完整发行：`v2-integrated-task12-20261110`。入口、离线SDK说明和期货合约范围匹配修复已合并，无需另行应用补丁。十二个修订题包位于[task_packages/20261110](task_packages/20261110/题包索引.md)，每题有展开目录及单独ZIP。先读[完整系统与题包说明](docs/v2/TASK_PACKAGES.md)。

Task生产、Tushare桥接与AKQuant Docker回测共用`finlab`入口。公开任务包只提供数据接口契约和提交模板，数据、真实时间映射和账户检查点留在平台侧。

题目规格已取消人工审批，默认流程为：程序检查与真实模型复核 → 自动接纳不可变版本 → 数据准备与冻结 → 批跑预检 → 实际研发验收 → 平台私有交接。已有有效复核的候选可以直接接纳，不需重新生成或等待人工批准。重复／变体可作为执行规格保存，但不计独立新题；历史人工记录和旧快照保留原来源。

```powershell
finlab task accept CANDIDATE_ID
finlab task accept --batch BATCH_ID
finlab task approved
```

完整流程见[自动接纳与批跑交付](docs/v2/DELIVERY.md)。新生成与最终批次复核会自动接纳；`accept`也可使用已保存且仍有效的真实模型证据。缺失或过期证据、未解决检查问题、数据缺口和运行环境问题仍会明确阻断。

本版加入[阶段披露范围与越界审计](docs/v2/DISCLOSURE.md)：平台传入累计`[start,stop)`，正式策略还受当前事件时钟限制。显式请求任何部分越界，整次HTTP403并返回`TIME_RANGE_EXCEEDED`、中文信息和请求编号，不返回部分行情；SQLite审计失败时503且禁止返回数据。新镜像与源码须配套使用，私有策略和日志不进入参与者包。

先打开[中文离线HTML](docs/system_v2.html)，开发AI先读[AI入口](docs/v2/AI_README.md)。六类种子规格在[examples/v2](examples/v2)，固定镜像及依赖在deployment。

2026-11-05的DeepSeek真实API候选批次及原始状态见[历史任务与生成证据](docs/v2/GENERATED_TASKS.md)。examples/generated保留原快照，不被新自动接纳记录回写；后续生成继续纳入只读去重对照。当前接纳重试与数据查询结果分别见docs/automatic_acceptance_retry_v2.json和docs/automatic_upstream_retry_v2.json。临时API key不随包保存。

编写参与者策略的AI先读[finlab-strategy技能](skills/finlab-strategy/SKILL.md)。技能附带246个Pro接口的官方离线全文、Tushare与回测常见错误、平台SDK、五文件模板及离线检索/预检脚本。Flash等模型的接入方法见[技能使用说明](docs/v2/SKILLS.md)，人可打开[离线API全文浏览器](docs/tushare_api_offline.html)。该技能不包含任务私有数据或时间映射。

```powershell
python -m pip install -e .
finlab setup
finlab doctor
finlab backtest load-images ../images/finlab_images_v2.tar
finlab demo build --asset STOCK --output work_v2/my_demo
finlab backtest init --binding work_v2/my_demo/binding.private.json --episode work_v2/my_demo/episode
finlab backtest run --episode work_v2/my_demo/episode --submission work_v2/my_demo/submission --research
finlab backtest run --episode work_v2/my_demo/episode --submission work_v2/my_demo/submission
```

不加`--research`执行下一正式段；每次新建两个容器，七段连续恢复账户及JSON memory。以上全部为合成数据演示。完整教程、上游自定义地址、模型配置、审核与归档见HTML和docs/v2。

股票、ETF、期货可交易，指数可查询或作为冻结基准；名称代码保留，独立时间字段在平台侧默认向未来整年偏移＋376；公开日期是虚拟标签，不代表现实年份或未来预测。接口目录定义、模拟类型检查、真实接口查询和历史可回放证据分别记录，不能互相替代。自建Tushare服务已执行样本与区间查询；有返回不代表全部接口、覆盖范围或历史可见性已经核验。期货采用结算价估值，当前不模拟交易所逐日现金划转。

```powershell
finlab test
finlab verify
finlab docs build
finlab bundle --output dist
```

`test`运行自动测试并写报告；`verify`实际创建Docker容器运行合成验收；工具包不含秘密、行情缓存、运行日志或平台检查点。临时模型密钥不包含在交付内容中。


## 首次使用与任务版本

新任务直接使用v2结构，v1任务不再作为生成、校验或导出输入。历史机制用于去重与研究关系；自动接纳不为历史v1补造可运行版本或独立新题身份。

首次使用由实际使用者本人运行 `finlab setup`，选择CPU、内存和单次运行时限。2CPU与4GB仅为推荐，留空不采纳；时限无默认或推荐值，必须输入正整数秒或明确输入none（不限时）。AI不能代选、代填或写入本人确认文件。未配置时回测入口返回SETUP_REQUIRED。详情见[首次使用规范](docs/v2/SETUP.md)。

本机.finlab配置不随工具包分发；新任务resources为null。人工选择在创建episode时冻结，已有七段任务不会随本机重配而改变。20秒回调协议限制独立保留。

未来数百年的日期仅表示事件先后及可见边界。策略不按虚拟年份推定真实股灾、政策或节假日，也不搜索或猜还原真实时期；只依据当前可见数据和返回日历。2400附近日期可用标准库datetime或等宽字符串处理，不能用Pandas默认纳秒解析后静默变NaT。
