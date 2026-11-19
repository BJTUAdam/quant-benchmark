# rt_etf_min · ETF实时分钟

来源：https://tushare.pro/document/2?doc_id=416

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## ETF实时分钟

接口：rt_etf_min
描述：获取ETF实时分钟数据，包括1~60min
限量：单次最大1000行数据，可以通过ETF代码提取数据，支持逗号分隔的多个代码同时提取
权限：正式权限请参阅 [权限说明](https://tushare.pro/document/1?doc_id=290) ，可以[在线开通](https://tushare.pro/weborder/#/permission)权限（勾选“ETF分钟RT”）

注：支持股票当日开盘以来的所有ETF历史分钟数据提取，接口名：rt_etf_min_daily（仅支持一个个代码提取，不同同时提取多个）

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| freq | str | Y | 1MIN,5MIN,15MIN,30MIN,60MIN （大写） |
| ts_code | str | Y | 支持单个和多个：589960.SH 或者 589960.SH,159100.SZ |

freq参数说明

| freq | 说明 |
| --- | --- |
| 1MIN | 1分钟 |
| 5MIN | 5分钟 |
| 15MIN | 15分钟 |
| 30MIN | 30分钟 |
| 60MIN | 60分钟 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| time | None | Y | 交易时间 |
| open | float | Y | 开盘价 |
| close | float | Y | 收盘价 |
| high | float | Y | 最高价 |
| low | float | Y | 最低价 |
| vol | float | Y | 成交量(股） |
| amount | float | Y | 成交额（元） |

接口用法

```text
pro = ts.pro_api()

#获取科创新能源ETF易方达589960.SH的实时分钟数据
df = pro.rt_etf_min(ts_code='589960.SH', freq='1MIN')

```
