# 本题提交与策略SDK离线说明

首轮读取同目录TASK.md，接口契约见DATA_API.md、bridge_contract.json与runtime_profile.json。所有task_input.draft.json中的路径均相对于该JSON所在目录；把public目录单独分发后仍应能找到TASK.md。

当前可以据题面设计并编写可配置代码；只有平台已提供冻结数据、任务桥接连接、事件时钟和运行规则后，才能实际查询和回测。策略容器预装finlab SDK，单独的题目ZIP不包含整个运行系统。不要直接导入官方tushare/akshare/akquant，不连接平台上游服务。

订单意图不等于成交：ctx.order和ctx.cancel返回None，账户不在同一回调里同步改变。on_trade没有order_id/client_tag，on_order的id和累计filled_quantity才可用于订单台账。可选择每个真实合约只保留一笔活动净订单，或定义确定的映射队列；不能读取不存在的事件字段完成影子仓位归属。

现货股票仅做多或减持实际可卖数量；期货buy/sell改变同一真实合约的有符号净仓，平台没有虚拟子账户或价差级原子订单。产品池中的FU/M等不能直接作为下单代码，需由平台先绑定真实合约，并在生成代码/参数时以已绑定的公开runtime_profile为准。

---

# 策略 SDK · finlab.strategy.v2

本文件是策略接口的共用说明。它适用于参与者提交，依据当前 `finlab/sdk.py`、`finlab/engine.py` 和 `finlab/tushare.py` 的实际接口编写。底层引擎固定为 AKQuant 0.3.76；原生AKQuant方法不自动成为平台接口。

## 入口与最小实现

`strategy.py` 必须提供名为 `Strategy` 的类，继承平台基类即可继承正确的协议版本：

```python
from finlab.sdk import Strategy as BaseStrategy

class Strategy(BaseStrategy):
    def initialize(self, ctx):
        ctx.memory.setdefault("observations", 0)

    def on_bars(self, ctx):
        ctx.memory["observations"] += 1
```

这段代码始终空仓，只验证回调和memory。实际策略根据题目另行实现。`backtest.py` 的可执行入口是：

```python
from finlab.sdk import launch

if __name__ == "__main__":
    launch("strategy.py", "params.json")
```

宿主机直接运行该入口只显示平台运行提示，不能据此声称已经完成回测。实际执行由平台在新策略容器中启动。

## 回调

|方法签名|触发时机|能否下单或撤单|
|---|---|---|
|`initialize(self, ctx)`|每个新容器启动，已有memory已装入|否|
|`on_bars(self, ctx)`|当前时刻的已完成行情切片齐备|是|
|`on_order(self, ctx, event)`|订单状态通知|否|
|`on_trade(self, ctx, event)`|实际成交通知|否|
|`on_finish(self, ctx)`|本次运行结束|否|

回调是同步调用。不要写成 `async def`；不要以 `on_bar(self, bar)` 替代平台的 `on_bars(self, ctx)`。同时间戳的实际行情合并成一次切片；停牌或没有该时点行情的标的可能不在切片中。

默认单回调20秒，回复与memory合计不超过2MB，每回调最多256条下单/撤单指令；以任务运行契约为准。回调异常、协议错误和非法订单会使本轮不提交。

上述单回调响应限制是策略协议的一部分，与整次运行时限不同。整次运行的CPU、内存和时限必须由使用者本人首次执行 `finlab setup` 选择，没有默认确认值；时限是正整数秒或 `none`（不限时）。遇到 `SETUP_REQUIRED` 先报告缺口，AI不得代填或创建确认文件，也不能用 `--timeout` 覆盖人的选择。详见 [submission.md](SUBMISSION.md)。

## 上下文

|属性|类型与含义|
|---|---|
|`ctx.now`|平台虚拟时间的ISO字符串；按返回的时区与交易日历使用|
|`ctx.bars`|`{symbol: bar_dict}`；行情回调中含实际完成行情的标的|
|`bar_dict`|`open, high, low, close, volume, now`；数值已按运行契约转换为引擎单位|
|`ctx.portfolio`|账户快照；含 `cash, equity, positions, available_positions, open_orders`|
|`positions`|`{symbol: quantity}`；当前持仓数量，不是权重|
|`available_positions`|`{symbol: quantity}`；当前可卖数量，可能小于持仓|
|`open_orders`|未完成订单列表；含 `id, symbol, quantity, filled_quantity, side`|
|`ctx.params`|本次提交的 `params.json` 对象|
|`ctx.memory`|策略可维护的JSON对象，成功段间延续|

在非行情回调中，`ctx.bars`可能为空。不要从账户字典推测未声明的属性，不要把缺失标的当作价格为0。

`ctx.now` 和数据日期默认显示为未来数百年的虚拟年份，仅用于排序、窗口和事件推进。不能把虚拟年份与真实世界的股灾、政策、节假日或其他事件绑定，也不从名称代码猜测真实时期。只按已可见数据和平台交易日历判断。未来年份可能超过 Pandas 默认纳秒日期范围；用标准库 `datetime/date` 解析，详见 [tushare-bridge.md](DATA_API.md)。

## 数据查询

```python
from datetime import datetime

def daily_history(ctx, symbol):
    virtual_day = datetime.fromisoformat(ctx.now).strftime("%Y%m%d")
    return ctx.query(
        "daily",
        fields="ts_code,trade_date,close",
        ts_code=symbol,
        end_date=virtual_day,
    )
```

`ctx.query(api_name, fields="", **params)` 默认返回DataFrame。上述字段必须在当前契约中；示例不自动获得授权。分钟接口的日期时间通常是 `YYYY-MM-DD HH:MM:SS`，应按该接口文档生成，见 [tushare-bridge.md](DATA_API.md)。

查询只返回当前已可见版本。模型不能通过换日期、改URL或拿当前财务值替代历史可见值。需要多页时使用桥接分页规则；不要假定一次查询覆盖全部数据。

## 订单与撤单

实际签名：

```python
ctx.order(symbol, quantity, side="buy", limit_price=None, tif="GTC")
ctx.cancel(order_id)
```

`side`、`limit_price`、`tif` 为关键字参数。数量是正的有限数值：股票和ETF按股，期货按合约手数，并满足任务的 `lot_size`。卖出使用正数量配 `side="sell"`。指数只用于查询或基准，不能下单。

- `limit_price=None`：平台按下一根行情开盘及绑定规则尝试撮合；不保证全成。
- `limit_price` 为正有限数值：平台仍受涨跌限、流动性、交易单位和账户约束。
- `tif="GTC"`：未完成订单可以跨正式段延续。
- `tif="DAY"`：按平台交易日处理有效期；不要用虚拟日期的星期自行推算。
- 撤单ID应来自 `on_order` 的 `event["id"]` 或 `ctx.portfolio["open_orders"]`；撤销非活动订单会失败。

`ctx.order` 和 `ctx.cancel` 将指令加入本次回复，返回值为 `None`。它们不会同步更新 `ctx.portfolio`，也不会立即返回成交。重复调用会产生多条指令；每根行情重发前先看持仓与未完成订单。

当前没有 `ctx.buy`、`ctx.sell`、`ctx.history`、`ctx.order_target_percent`、`ctx.get_position` 或 `ctx.run_backtest`。不要从其他回测框架补写同名API。

## 通知事件

`on_order(ctx, event)` 当前字段：`id, symbol, status, quantity, filled_quantity`。`filled_quantity` 是累计成交数量。`status` 是底层状态的字符串表示，不应凭想象引用未导出的状态枚举。

`on_trade(ctx, event)` 当前字段：`symbol, quantity, price, side, now`。当前成交通知不提供 `order_id` 或完整费用明细，不能凭文档补造字段；正式逐笔结果由系统输出。订单与成交回调可更新memory，但不能在其中发出新订单或撤单。

## 状态连续性

平台会延续现金、持仓、未完成订单及 `ctx.memory`。Python类实例属性、模块变量、DataFrame和打开的文件不会自动跨容器保存。需要历史窗口时，选择将有限长度的原生JSON列表放入memory，或按契约查询当前可见历史。

memory的顶层必须为字典，键和数据须可JSON序列化。NumPy标量、Timestamp、DataFrame、set、NaN、Infinity、函数和任意Python对象不可直接保存。把数值转为Python `int/float`，日期转字符串；缺失值用 `None` 并单独处理。

```python
def initialize(self, ctx):
    ctx.memory.setdefault("schema", 1)
    ctx.memory.setdefault("observations", 0)
```

如果改动memory结构，在 `initialize` 中根据版本迁移；不能用 `ctx.memory = {}` 抹掉上一段状态。只应在段间更新代码和参数。失败或超时的运行不会解锁后续段，也不会将失败memory作为下一段起点。

## 撮合、费用与已知边界

平台采用已完成行情后决策、下一根行情开盘撮合。最小价位、滑点、逐日价格限制、可交易状态、成交量占比、现金和保证金共同决定成交；受限或无量时订单可能等待，成交可以分批发生。

股票/ETF按绑定规则执行T+0或T+1；不要把所有ETF当作T+0。股票和ETF不能卖空。使用原始价格撮合，并按声明的公司行为处理股数和现金；研究用复权价格不能直接作为成交价。

期货按真实分合约和绑定乘数、保证金及交易时段运行。夜盘交易日由平台日历决定。多腿订单逐腿撮合：某一腿已成交时，另一腿可能仍等待，平台不会为实现原子成交回滚第一腿。

当前支持按成交额费率、最低佣金、卖出印花税和过户费。期货结算价用于估值，但不模拟交易所逐日现金划转；按手固定费用、平今差异费用及历史动态保证金尚未实现。任务需要这些规则时必须先扩展并验收，不能默认费用为0或声称精确模拟。

结果文件与提交要求见 [submission.md](SUBMISSION.md)；排查表见 [backtest-pitfalls.md](SUBMISSION.md)。


---

# 提交协议 · finlab.submission.v2

本文件是五文件提交与结果说明的共用正文。策略回调的完整定义见 [strategy-sdk.md](SUBMISSION.md)，这里不重复维护方法签名。

## 五个必交文件

|文件|要求|
|---|---|
|`strategy.py`|提供 `Strategy` 类，使用 `finlab.strategy.v2` 接口|
|`backtest.py`|以 `finlab.sdk.launch("strategy.py", "params.json")` 启动|
|`params.json`|有限、可序列化的JSON对象，保存本次策略参数|
|`RESEARCH.md`|研究问题、决策依据、所用数据、可见性判断、检验记录和局限|
|`CHANGELOG.md`|记录各轮保持、调参或逻辑修改，以及memory兼容方式|

当前容器只复制以上五个文件。不要依赖额外模块、CSV、模型权重、`requirements.txt` 或宿主机绝对路径。需要新依赖时，由平台审核并建立新的固定环境后使用。

研究与维护记录按任务虚拟时间引用观察。虚拟年份默认位于未来数百年，不对应同年真实市场历史；不要写入按年份猜测的股灾、政策、节假日或真实时期假设，也不尝试恢复私有时间映射。日期解析及交易日判断遵循 [tushare-bridge.md](DATA_API.md)。

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

本题的五个模板已放在当前public目录。将strategy.py、backtest.py、params.json、RESEARCH.md和CHANGELOG.md复制到新的提交目录，修改策略与参数；其他说明文件不进入运行容器。

在已安装完整Finlab工具包的环境执行：

```text
finlab submission check SUBMISSION_DIR
```

检查只验证文件、语法、依赖与JSON格式，不能代替容器回调执行。模板保持空仓，只用于接口连通性；不能把它当作完成本题的研究结果。当前目录不提供make_submission.py或preflight.py脚本。

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
