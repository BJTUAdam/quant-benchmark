---
name: finlab-strategy
description: 为金融长程任务系统编写和检查策略提交，按任务契约通过Tushare桥接查询数据，并使用平台SDK在AKQuant环境运行。适用于策略代码生成、接口查阅和分段维护。
---

# Finlab 策略生成

先读取当前任务的 `TASK.md`、`bridge_contract.json`、`runtime_profile.json` 和 `SUBMISSION.md`。任务契约决定可用标的、接口、字段、频率和虚拟时间范围；本技能不扩大权限。范围为 `null` 或状态为未绑定时，记录缺口，不猜日期或行情。

任务中的日期、年份和事件时钟均为**虚拟时间**，默认显示为未来数百年的年份。这只是市场数据的时间轴标签，不是对应公历年份的真实市场，也不是未来预测行情。不得按这些年份套用真实股灾、政策、节假日或其他历史事件，不据此搜索、猜测或还原真实时期；只依据当前可见数据、平台事件及返回的交易日历研究和决策。真实映射只由平台持有。

这是 `finlab.strategy.v2` 策略接口。引擎是 AKQuant 0.3.76；AKShare是数据工具。提交代码使用 `finlab.sdk`，不要直接导入 `tushare`、`akshare`、`akquant` 或照搬它们的原生回测入口。

首次使用运行工具前，必须由使用者本人执行 `finlab setup`，自行选择CPU、内存和单次运行时限。资源没有默认确认值；时限由人输入正整数秒或 `none`（不限时）。遇到 `SETUP_REQUIRED` 时说明缺少本人配置，请使用者完成后再运行；AI不得代选数值、代操作交互或生成确认文件，也不通过 `--timeout` 覆盖已确认设置。静态代码生成与离线文档检索可以先完成。

## 按需读取

|当前工作|读取资料|
|---|---|
|首次生成或修复策略|[strategy-sdk.md](references/strategy-sdk.md)、[submission.md](references/submission.md)|
|查询行情、财务或元数据|[tushare-bridge.md](references/tushare-bridge.md)，再查需要的单个接口|
|检查Tushare易错处|[tushare-pitfalls.md](references/tushare-pitfalls.md)|
|检查交易、状态或回测问题|[backtest-pitfalls.md](references/backtest-pitfalls.md)|
|分不清AKShare与AKQuant|[akquant-vs-akshare.md](references/akquant-vs-akshare.md)|
|给上下文较小的模型分配代码任务|[flash-prompt.md](references/flash-prompt.md)|
|查官方Pro参数、单位、权限、限额和示例|[Tushare离线索引](references/tushare/index.md)|
|理解底层引擎与原生库概念|[AKQuant官方版本资料](references/akquant/index.md)，不直接照搬其入口|

离线检索命令从本技能目录运行：

```text
python scripts/api_docs.py --search 分钟
python scripts/api_docs.py --api daily
```

246个Pro接口的官方文档是知识资料，不代表任务已冻结或允许全部调用。官方示例日期与输出不是任务数据；策略只使用任务虚拟时间和当前可见数据。名称与代码保持原值，不自行更换或从名称推断时钟。

平台按阶段累计披露数据，已绑定总范围不表示现在可查全部日期；策略还受当前事件时钟限制。显式请求任一部分越界都会整次拒绝，返回 `TIME_RANGE_EXCEEDED`、错误信息和 `request_id`，不返回合法部分；不能当作空行情。详见 [tushare-bridge.md](references/tushare-bridge.md#阶段披露与越界)。

未来虚拟年份可能超出 Pandas `datetime64[ns]` 范围。解析与格式化日期使用标准库 `datetime/date`；不要默认用 `pd.to_datetime`，更不能把越界日期悄悄转换为 `NaT`。完整处理方式见 [tushare-bridge.md](references/tushare-bridge.md)。

## 编写与自检

1. 将任务用到的接口、字段、单位、日期格式和可见性先列清；只读取相应官方文档，不一次加载整套手册。
2. 使用 `assets/submission/` 的五文件空仓骨架，或运行 `python scripts/make_submission.py SUBMISSION_DIR`。目标目录必须尚不存在、其父目录必须已存在；相对路径从当前工作目录解析。骨架只演示协议，不是任务答案。
3. 实现 `Strategy` 类和 `on_bars(ctx)`。只在 `on_bars` 下单或撤单；`ctx.order` 返回 `None`，订单ID来自通知或账户的未完成订单列表。
4. 用 `ctx.memory` 保存需要跨段延续的JSON状态；`initialize` 用 `setdefault` 或显式迁移，不能每段重置记忆。
5. 运行 `python scripts/preflight.py SUBMISSION_DIR`。平台侧再执行 `finlab submission check SUBMISSION_DIR` 和授权的Docker研发运行。静态通过不等于已执行，也不等于策略正确。

遇到未授权接口、未冻结数据、字段/频率缺失或需要新依赖，返回明确缺口；不要改用外网、填造数据或用捕获所有异常的方式隐藏问题。技能中的官方引擎资料只解释底层能力；本平台SDK与已绑定任务契约决定实际可调用接口。
