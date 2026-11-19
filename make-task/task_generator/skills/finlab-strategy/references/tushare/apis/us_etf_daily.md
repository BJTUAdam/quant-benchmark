# us_etf_daily · 美股ETF日行情表

来源：https://tushare.pro/document/2?doc_id=506

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 美股ETF日行情表

### 接口介绍

接口：us_etf_daily
描述：获取美股ETF基金每日收盘后成交行情数据，包含部分OTC的盘后行情数据。
限量：单次最大6000行数据，可根据日期参数循环提取，开通正式权限后也可支持分页提取全部历史
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“美股历史日线”）。

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 基金代码 |
| trade_date | str | N | 交易日期（YYYYMMDD） |
| start_date | str | N | 开始日期（YYYYMMDD） |
| end_date | str | N | 结束日期（YYYYMMDD） |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| trade_date | str | Y | 交易日期 |
| close | float | Y | 收盘价 |
| open | float | Y | 开盘价 |
| high | float | Y | 最高价 |
| low | float | Y | 最低价 |
| pre_close | float | Y | 昨收价 |
| change | float | Y | 涨跌额 |
| pct_change | float | Y | 涨跌幅 |
| vol | None | Y | 成交量 |
| amount | float | Y | 成交额 |
| vwap | float | Y | 平均价 |
| turnover_ratio | float | N | 换手率 |
| total_mv | float | N | 总市值 |

### 代码示例

```text
pro = ts.pro_api()

#获取单一股票行情
df = pro.us_etf_daily(ts_code='AAAU', start_date='20260101', end_date='20260904')

#获取某一日所有股票
df = pro.us_etf_daily(trade_date='20261022')

```

### 数据结果

```text
  ts_code trade_date  close   open   high    low pre_close change pct_change     vol           amount   vwap
0    TSIE   20261022  50.08  50.02  50.15  50.02     50.01   0.08       0.15  873819  43789626.000000  50.11
1    AHYM   20261022  25.06  25.06  25.12  25.06     25.04   0.03       0.10   48118   1207667.000000  25.10
2    AHIC   20261022  25.12  25.05  25.12  25.04     25.08   0.04       0.16    1907     47783.000000  25.06
3    TLCE   20261022  24.88  24.92  24.92  24.88     25.31  -0.43      -1.68     606     15103.000000  24.92
4    SCIN   20261022  25.09  25.03  25.09  25.02     24.99   0.09       0.38    3457     86650.000000  25.07
5    RBTX   20261022  24.03  25.01  25.01  24.03     25.57  -1.54      -6.02   87765   2145565.000000  24.45
6    GMIE   20261022  25.04  24.99  25.04  24.89     25.25  -0.21      -0.84   68376   1707616.000000  24.97
7    POMG   20261022  25.51  25.52  25.52  25.22     25.52  -0.01      -0.04  738363  18725949.000000  25.36
8    WBIE   20261022  23.53  23.54  23.54  23.53     24.14  -0.61      -2.54    5158    121429.000000  23.54
9    AVUC   20261022  49.68  49.51  50.09  49.51     49.97  -0.29      -0.59   23737   1176805.000000  49.58

```
