# index_dailybasic · 大盘指数每日指标

来源：https://tushare.pro/document/2?doc_id=128

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 大盘指数每日指标

接口：index_dailybasic，可以通过[数据工具](https://tushare.pro/webclient/)调试和查看数据。
描述：目前只提供上证综指，深证成指，上证50，中证500，中证1000，中小板指，创业板50 ，创业板指，科创50的每日指标数据。
数据来源：Tushare社区统计计算
数据历史：从2004年1月开始提供
数据权限：用户需要至少2000积分才可以调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | N | 交易日期 （格式：YYYYMMDD，比如20181018，下同） |
| ts_code | str | N | TS代码 |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

注：trade_date，ts_code 至少要输入一个参数，单次限量3000条（即，单一指数单次可提取超过12年历史），总量不限制。

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | TS代码 |
| trade_date | str | Y | 交易日期 |
| total_mv | float | Y | 当日总市值（元） |
| float_mv | float | Y | 当日流通市值（元） |
| total_share | float | Y | 当日总股本（股） |
| float_share | float | Y | 当日流通股本（股） |
| free_share | float | Y | 当日自由流通股本（股） |
| turnover_rate | float | Y | 换手率 |
| turnover_rate_f | float | Y | 换手率(基于自由流通股本) |
| pe | float | Y | 市盈率 |
| pe_ttm | float | Y | 市盈率TTM（最新价除以最近4个季度的每股收益） |
| pb | float | Y | 市净率 |

接口示例

```text
pro = ts.pro_api()

df = pro.index_dailybasic(trade_date='20181018', fields='ts_code,trade_date,turnover_rate,pe')

```

数据示例

```text
    ts_code  trade_date  turnover_rate     pe
0  000001.SH   20181018           0.38  11.92
1  000300.SH   20181018           0.27  11.17
2  000905.SH   20181018           0.82  18.03
3  399001.SZ   20181018           0.88  17.48
4  399005.SZ   20181018           0.85  21.43
5  399006.SZ   20181018           1.50  29.56
6  399016.SZ   20181018           1.06  18.86
7  399300.SZ   20181018           0.27  11.17

```
