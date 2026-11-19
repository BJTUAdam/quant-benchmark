# 给策略生成 AI 的技能包

## 两种 AI 入口

平台开发与数据运营 AI 阅读 [AI_README.md](AI_README.md)。负责写参与者策略的 AI 阅读 [finlab-strategy/SKILL.md](../../skills/finlab-strategy/SKILL.md)。两者职责不同；不要把私有时间配置、原始缓存或控制器文件交给参与者。

技能包位于 `skills/finlab-strategy/`，复制整个文件夹后仍可离线使用。它包含短入口、Tushare 全量版本目录的官方正文、常见错误说明、平台策略 SDK 协议、提交规范、AKQuant 固定版本的官方参考、检索/预检脚本和空仓模板。

这里的回测引擎是 AKQuant 0.3.76。AKShare 是常见的数据获取库；本项目由 Tushare 桥接提供数据，不把 AKShare 当作回测引擎，也不允许策略绕过桥接抓取外网数据。

## 离线资料的覆盖范围

Tushare 文档按本系统的版本化 Pro 目录构建。246 个已登记接口各有完整官方正文；同时记录官方导航中的 284 个页面。部分页面只有分类或空白入口，单独标记，不当作完整 API 文档。旧版 Tushare 网络爬虫接口不属于当前 Pro HTTP 兼容范围。

每份接口资料保留输入参数、输出字段、单位、权限、频率或限额说明，以及官方调用示例。出处、抓取时间、正文哈希和原始网页哈希记录在 `references/tushare/snapshot.json`。官方示例中的日期和输出是说明材料，不是当前任务行情、参考答案或本平台测试成绩。账号权限、接口实际返回格式与历史可见性仍须另验。

AKQuant 参考包含固定 0.3.76 源码版本的 13 份中文文档、许可证和来源清单。原生库的 `on_bar`、`run_backtest` 与平台的 `on_bars(ctx)`、`finlab.sdk.launch` 不同；代码生成优先使用平台 SDK 规范，原生文档用于理解机制。

“踩坑”资料覆盖当前实现和官方资料中已知的常见问题，不承诺穷尽未来版本或所有上游服务的特殊行为。

## 如何接入 Flash 等模型

技能文件不会自动扩展模型知识。运行管线要主动读取并提供它，或把读取文件/检索命令作为模型可用工具。无需依赖某一种模型服务商。

- 明确告知模型：任务日期、报告期和事件时钟均为虚拟时间，默认年份位于未来数百年；不是同年真实市场历史或未来预测。不得按年份套用真实股灾、政策或节假日，不猜测或还原真实时期。只依据当前可见数据和平台返回交易日历。
- 开始代码生成时，加载技能的 `SKILL.md`、`references/flash-prompt.md` 和当前任务的公开契约。
- 按问题提供 `strategy-sdk.md`、`submission.md`、`tushare-bridge.md`。不要将全部 API 正文一次塞进上下文。
- 明确说明数据按阶段累计披露、正式策略另受当前事件时钟限制；`TIME_RANGE_EXCEEDED`整次拒绝，无合法部分返回。模型应保留错误信息和request_id，不能转为空表或补零。`BRIDGE_AUDIT_UNAVAILABLE`交由平台处理，不切换上游。
- 通过离线检索脚本找到具体接口，再按需读取该接口正文和相关错误说明。检查任务授权是否包含接口、字段、标的及虚拟范围。
- 从空仓模板生成五个提交文件。静态预检反馈给模型修复，之后由平台在 Docker 内执行真正的研发回测。
- 模型输出的自报收益或“检查通过”不能代替系统结果；未知字段、未支持规则和数据缺口按协议报告。

未来虚拟年份可能超过 Pandas 默认 `datetime64[ns]` 上限。技能示例使用标准库 `datetime/date` 处理日期；不建议直接 `pd.to_datetime`，不能以 `errors="coerce"` 把有效日期变成 `NaT`。实际偏移值和真实时间映射只保留在平台侧，不进入技能或公开任务包。后续任务只采用当前v2结构，不由技能兼容或升级v1任务。

使用支持技能发现的客户端时，可将整个 `finlab-strategy` 文件夹复制到该客户端的技能目录。其他客户端可直接读取 `SKILL.md` 并沿链接加载资料，不要求安装额外插件。

## 从项目根目录使用

首次使用运行功能前，由使用者本人在终端执行 `finlab setup`，自己选择CPU、内存以及单次运行时限。2 CPU、4 GB只作为可供评估的推荐起点，不是默认值，也不是已经确认的规范；时限没有默认或推荐值，人须选择正整数秒或 `none`（不限时）。

AI遇到 `SETUP_REQUIRED` 应报告需要使用者本人配置，可以继续离线检索、代码准备和静态检查。AI不得代选资源、填写交互、创建确认文件或把推荐值写进任务作为已确认资源；也不通过 `--timeout` 覆盖设置。SDK单回调20秒响应限制独立于单次运行时限。

```powershell
python skills/finlab-strategy/scripts/api_docs.py --search 分钟
python skills/finlab-strategy/scripts/api_docs.py --api daily
python skills/finlab-strategy/scripts/api_docs.py --api income --start 0 --max-chars 8000
New-Item -ItemType Directory -Force work_v2
python skills/finlab-strategy/scripts/make_submission.py work_v2/my_submission
python skills/finlab-strategy/scripts/preflight.py work_v2/my_submission
finlab submission check work_v2/my_submission
```

`preflight.py` 是离线静态辅助检查，不执行提交代码，不证明策略正确或隔离有效。平台正式检查与容器运行继续使用 `finlab`；参与者无需获得私有 episode 目录。

## 文档去重与保留内容

策略 SDK 与提交协议的共用正文只维护在技能的 `references/strategy-sdk.md`、`references/submission.md`。原 `docs/v2/STRATEGY_SDK.md`、`SUBMISSION.md` 保留入口链接，旧路径继续有效。HTML 在构建时读取同一正文，避免手工维护第二份。

`SYSTEM.md`、`TASKS.md`、`BRIDGE.md`、`CONTROLLER.md` 和 `OPERATIONS.md` 保留平台职责内容。机器目录 `catalog/tushare_pro.v2.json` 用于程序约束；技能内的官方说明用于模型理解，它们不是可互相删除的重复文件。每题公开包内的小型 `SUBMISSION.md` 继续作为独立任务的必要契约，不依赖平台源码才能阅读。

新的完整交付总包仅放一份已展开的源码和文档，再附 Docker 镜像；不再重复嵌套四个相同源码工具包。独立技能压缩包保留，便于直接发给代码生成管线。

## 更新与验证

```powershell
python deployment/build_strategy_skill.py
python deployment/build_engine_reference.py
python deployment/verify_strategy_skill.py
finlab docs build
finlab bundle --output dist
```

前两条命令默认核验已有离线快照；显式添加 `--refresh` 才从公开官方网站抓取资料，不需要 Tushare 账号。文档更新不会自动扩大任务权限、改变运行目录或把真实接口标成已实测。
