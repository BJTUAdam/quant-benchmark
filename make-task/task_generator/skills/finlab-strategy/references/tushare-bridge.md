# Tushare兼容桥接调用

本页解释本系统的调用方式；每个官方API的参数、输出单位、权限和限额见 [离线索引](tushare/index.md)。使用顺序是：当前任务契约 → 本页 → 所需单个官方接口正文。

## 客户端与返回值

```python
from finlab import tushare as ts

pro = ts.pro_api()
```

客户端从平台提供的 `MARKET_BRIDGE_URL` 和 `MARKET_BRIDGE_TOKEN` 读取连接信息。这个token是任务桥接令牌，不能替换为真实Tushare账号令牌。策略不要硬编码地址或修改容器环境；本机的 `127.0.0.1` 不等于平台提供的容器服务。

三种等价入口：

```python
# 以下symbol与virtual_day来自当前任务和当前虚拟时钟。
df = pro.daily(ts_code=symbol, trade_date=virtual_day,
               fields="ts_code,trade_date,close")
df = pro.query("daily", ts_code=symbol, trade_date=virtual_day,
               fields="ts_code,trade_date,close")
df = ctx.query("daily", ts_code=symbol, trade_date=virtual_day,
               fields="ts_code,trade_date,close")
```

默认返回DataFrame。需要列表及分页元数据时使用 `ts.pro_api(dataframe=False)`；返回的Rows对象可迭代为行字典，另有 `.meta`。不要假定DataFrame包含 `.meta`，也不要以为动态方法存在就说明接口已授权。

`fields` 是逗号分隔字符串，只列契约明确允许的字段。参数一般使用关键字；不要把字典作为未声明的位置参数。字段在官方文档里存在、官方账号有权限，都不能替代本任务的字段白名单。

## 虚拟日期格式

桥接请求参数、返回记录、报告期、事件时钟和公开结果使用同一虚拟时间域，默认年份位于未来数百年。它们是回放时间标签，不表示该现实年份发生过或将发生相同市场事件。不得按虚拟年份安排“股灾回避”、套用已知政策或节假日、查询外部历史事件，也不依据真实名称或合约代码反推真实年份。策略只使用当前可见行情与平台提供的交易日历；实际映射不提供给参与者。

```python
from datetime import datetime

at = datetime.fromisoformat(ctx.now)
virtual_day = at.strftime("%Y%m%d")
virtual_minute = at.strftime("%Y-%m-%d %H:%M:%S")
```

日线常用 `YYYYMMDD`；历史分钟接口通常要求完整 `YYYY-MM-DD HH:MM:SS`。年、月、季度及报告期各有自己的格式，逐个看API参数说明。不要把 `ctx.now[:10]` 当作所有接口通用的日期格式，也不要直接用机器当前日期。

Python标准库 `datetime/date` 可以处理任务中的未来年份。Pandas默认纳秒时间类型的上界在2262年附近，直接 `pd.to_datetime` 可能越界；`errors="coerce"` 会让有效虚拟日期变成缺失，不能这样隐藏问题。保持契约规定的等宽日期字符串进行排序，或先用标准库解析后比较、做日期运算；如自行选择其他日期类型，须先确认其年份范围和时间精度。行情数值的DataFrame计算不要求把日期列转换为纳秒时间。

年月不代表数据已可见：`end_date` 是报告期时，记录还需在当前时钟已经公告。已经公告的未来到期日可以出现，不能简单删除所有大于当前日期的字段。桥接器处理时间域，策略不自行还原真实时期。

名称与代码是任务中的原值，包括代码里可能存在的月份。不要把合约代码当成可用数据时钟。虚拟日期的星期可能与交易日历不一致，以任务可查询的交易日历和事件顺序为准。

## 阶段披露与越界

当前任务的数据按阶段累计披露：研发期和此前已成功提交的段可以继续用于研究；正式策略只能查询当前事件时点已经可见的信息。进入某段不表示可以在段初读取段末行情。任务契约中的总虚拟范围也不代表当前已全部开放。

披露范围使用 `[start, stop)`，开始包含、结束不包含。日线参数按接口日期精度检查，分钟和时间戳按登记的精度检查。只传年月或季度同样需要满足范围规则；不能通过更换日期字段、省略日期、分页或包装接口扩大权限。省略日期时，服务端只查询当前有效范围。

显式请求只要有一部分越界，整次返回 HTTP `403`，不返回其中合法部分，也不返回空DataFrame冒充成功。错误响应示例：

```json
{
  "code": -1,
  "error_code": "TIME_RANGE_EXCEEDED",
  "msg": "查询时间超出本阶段允许披露范围，本次请求未返回数据。",
  "request_id": "请求唯一编号"
}
```

平台SDK抛出 `LabError`（`ValueError` 子类），`.code`、`.message`、`.request_id` 分别保留错误码、错误信息及请求编号。缩小错误查询范围，或等待平台完成该段提交后再开展后续研究。不要捕获后返回空表、补零或把越界当作停牌；不要切换上游地址或修改令牌补抓。运营方可用请求编号查私有审计，参与者不读取或修改平台披露配置。

`BRIDGE_AUDIT_UNAVAILABLE` 表示审计存储故障，HTTP状态为 `503`，同样没有行情返回；报告请求编号和错误，由平台处理。越界和审计故障都不会推进阶段。

范围限制针对查询与信息可见性，不是删除记录中的所有未来日期。已公告的未来到期日等元信息在明确许可时可以返回，名称和代码保持原值。

## 分页与排序

单次DataFrame查询可能只是第一页。列表客户端的分页示例：

```python
from finlab import tushare as ts

def read_pages(api_name, **params):
    client = ts.pro_api(dataframe=False)
    rows, offset = [], 0
    for _ in range(1000):
        page = client.query(api_name, limit=2000, offset=offset, **params)
        rows.extend(page)
        if not page.meta.get("has_more", False):
            return rows
        next_offset = page.meta.get("next_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise ValueError("bridge pagination did not advance")
        offset = next_offset
    raise ValueError("query too large for this callback")
```

示例的1000页是防止死循环的上限，不表示应该在20秒回调里拉取1000页。更合理的做法是按需查询、缩小范围，并在memory中保存必要的有限历史。请求参数不能同时由 `params` 再提供 `limit` 或 `offset`。官方上游的分页能力与当前桥接的冻结分页不同，平台采集覆盖由平台核验。

排序不是策略时间轴的保证。按 `ts_code` 与日期/时间显式升序排列后再做rolling、shift、diff和连接。合并时检查重复键，不能把财务多报告版本与行情做无约束多对多连接。

## pro_bar包装范围

本系统实现 `ts.pro_bar(...)` 包装，它在本地调用任务桥接，不通过官方SDK补抓数据。官方 `pro_bar` 是组合包装，不是一个通用Pro HTTP `api_name`。

|参数/情形|当前实现|
|---|---|
|`asset`|`E`股票、`FD`基金、`I`指数、`FT`期货|
|日线 `freq="D"`|映射到 `daily/fund_daily/index_daily/fut_daily`|
|分钟 `1min/5min/15min/30min/60min`|映射到对应股票、ETF、指数、期货分钟接口|
|周/月 `W/M`|当前只支持股票和指数的相应接口|
|`adj="qfq"` 或 `"hfq"`|仅股票/基金日线；需冻结并授权复权因子|
|`adjfactor=True`|仅股票/基金日线，返回复权因子列|
|`ma=[5,20]`|由当前可见数据计算价格和成交量均线；窗口不足为NaN|
|`factors=["tor","vr"]`|需允许的 `daily_basic` 字段；其他因子名不支持|
|其他包装参数、资产或频率组合|明确报错，不回退为官方网络查询|

调用 `pro_bar` 可能需要多个API，不只行情接口。先逐项检查任务契约；不能因缺复权因子就补成1。其返回结果为日期倒序，计算其他因子前重新按时间升序；不能依赖官方SDK其他版本的行为。

## 错误如何处理

桥接查询失败会抛出包含稳定错误码的 `ValueError` 子类。服务端错误还保留错误信息和 `request_id`，用于定位平台审计。区分：连接失败、接口或字段未授权、`TIME_RANGE_EXCEEDED`、`BRIDGE_AUDIT_UNAVAILABLE`、未冻结、时间字段未登记和包装参数不支持。它们不能统一解释为“无行情”。

只针对业务已定义的空结果做分支。关键数据缺失应让当前研究明确失败并说明所需字段；不要写 `except Exception: return 0` 把接口错误变成交易信号。详细排查见 [tushare-pitfalls.md](tushare-pitfalls.md)。
