# 提交协议 · finlab.submission.v2

本文件是五文件提交与结果说明的共用正文。策略回调的完整定义见 [strategy-sdk.md](strategy-sdk.md)，这里不重复维护方法签名。

## 五个必交文件

|文件|要求|
|---|---|
|`strategy.py`|提供 `Strategy` 类，使用 `finlab.strategy.v2` 接口|
|`backtest.py`|以 `finlab.sdk.launch("strategy.py", "params.json")` 启动|
|`params.json`|有限、可序列化的JSON对象，保存本次策略参数|
|`RESEARCH.md`|研究问题、决策依据、所用数据、可见性判断、检验记录和局限|
|`CHANGELOG.md`|记录各轮保持、调参或逻辑修改，以及memory兼容方式|

当前容器只复制以上五个文件。不要依赖额外模块、CSV、模型权重、`requirements.txt` 或宿主机绝对路径。需要新依赖时，由平台审核并建立新的固定环境后使用。

研究与维护记录按任务虚拟时间引用观察。虚拟年份默认位于未来数百年，不对应同年真实市场历史；不要写入按年份猜测的股灾、政策、节假日或真实时期假设，也不尝试恢复私有时间映射。日期解析及交易日判断遵循 [tushare-bridge.md](tushare-bridge.md)。

每文件不超过1MB，使用普通文件，不能用符号链接。Python使用UTF-8；JSON不能包含NaN或Infinity。输出目录中有额外文件不意味着它会进入运行容器。

## 当前允许的导入

直接导入的顶层包限定为：

```text
finlab, pandas, numpy, math, statistics, collections, datetime, json,
typing, itertools, functools, decimal, random, re, dataclasses, enum,
copy, bisect, heapq
```

平台所用Pandas、NumPy版本以运行环境锁为准。策略不直接导入官方 `tushare`、`akshare` 或 `akquant`；使用 `from finlab import tushare as ts` 或 `ctx.query`。不要运行安装命令或动态导入来绕过依赖检查。

## 模板与预检

从技能目录执行：

```text
python scripts/make_submission.py SUBMISSION_DIR
python scripts/preflight.py SUBMISSION_DIR
```

目标目录必须尚不存在，父目录须预先创建；脚本不会覆盖已有文件，也不接受带 `..` 的输出路径。模板始终空仓，仅验证入口、回调和JSON记忆。它不提供策略答案、行情或回测成绩。预检只静态检查，不导入用户策略，也不证明回调已经在容器中执行。

平台侧正式检查入口：

```text
finlab submission check SUBMISSION_DIR
```

平台静态检查覆盖五文件完整性、大小、Python语法、导入依赖和JSON对象，并计算内容哈希。容器实际运行才检查类能否载入、回调能否完成、协议版本和订单是否合法。

## 授权研发运行

首次使用运行工具前，必须由使用者本人执行 `finlab setup`，自行选择CPU、内存和单次运行时限。2 CPU、4 GB只是可供人评估的推荐起点，不是默认值，也不构成已经确认的任务规范。时限不提供默认或推荐值，由人明确输入正整数秒，或输入 `none` 表示不限时。

AI发现 `SETUP_REQUIRED` 时应报告需要本人配置，等待使用者完成；可以继续不依赖运行环境的代码准备和静态检查。不得替人选择、自动填交互、生成确认文件，或将推荐资源写成已确认规则。运行命令不再接受 `--timeout` 作为覆盖设置的入口。

平台或有运行环境权限的使用者执行：

```text
finlab backtest run --episode EPISODE_DIR --submission SUBMISSION_DIR --research
```

`EPISODE_DIR` 是平台已经绑定的任务，不由策略代码创建。研发从初始账户重跑当前允许的研发范围，不延续正式持仓、不推进正式段号。直接运行 `python backtest.py` 只显示平台入口提示。

正式运行由平台去掉 `--research` 后执行下一段，成功后保留账户和memory。运行中不能更新策略文件；代码和参数只能在段间更新。每次正式提交应在CHANGELOG写明是否改变逻辑、参数和memory结构。

## 系统结果

|系统文件|含义|
|---|---|
|`equity.csv`|累计正式账户权益曲线，使用平台虚拟时间|
|`orders.csv`|实际系统订单与累计成交状态|
|`trades.csv`|平台撮合产生的逐笔成交及费用|
|`benchmark.csv`|绑定冻结指数基准时生成，与账户估值时点对齐|
|`metrics.json`|收益、最大回撤、日收益Sharpe，以及配置基准时的相关指标|
|`run_manifest.json`|协议、段号、提交哈希、数据版本和虚拟运行窗口|

只有成功提交的系统结果才是正式记录。参与者自己的图表或报告是研究材料，不能替换正式结果。不要向params或策略memory填入自报净值来影响成绩。

正式结果使用连续账户口径，不把每段重置成初始资金。零波动或样本不足时Sharpe可能为 `null`，不应改为0来表示已完成估计。基准默认以账户窗口第一个可见指数收盘价归一化；缺少所需可见基准值时平台停止运行，不人工补成平滑曲线。

## 失败与维护

回调异常、超时、无效JSON、非法订单或协议错误使本轮不提交；下一次仍从上一成功段开始。修复原因并记录，不手写段号或账户状态，不提供Python序列化文件来代替平台状态。

单次资源预算以使用者本人确认并绑定的配置为准，两个容器共享预算。平台的单回调20秒响应限制属于SDK协议，不等于单次运行时限，也不能被 `none` 取消。研究记录应区分“静态检查通过”“合成协议验证”“本任务实际研发运行”，没有执行的检查保持未执行状态。
