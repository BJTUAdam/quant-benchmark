# hk_adjfactor · 港股复权因子

来源：https://tushare.pro/document/2?doc_id=401

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 港股复权因子

接口：hk_adjfactor
描述：获取港股每日复权因子数据，每天滚动刷新
限量：单次最大6000行数据，可以根据日期循环
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“港股历史日线”）。

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 股票代码 |
| trade_date | str | N | 交易日期（格式：YYYYMMDD，下同） |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| trade_date | str | Y | 交易日期 |
| cum_adjfactor | float | Y | 累计复权因子 |
| close_price | float | Y | 收盘价 |

接口示例

```text
pro = ts.pro_api()

#获取港股单一股票复权因子
df = pro.hk_adjfactor(ts_code='00001.HK', start_date='20240101', end_date='20251022')

#获取港股某一日全部股票的复权因子
df = pro.hk_adjfactor(trade_date='20251031')

```

数据示例

```text
       ts_code trade_date cum_adjfactor close_price
0     00380.HK   20251031      1.000000    0.150000
1     00698.HK   20251031      1.000000    4.610000
2     00865.HK   20251031      1.000000    0.038000
3     08111.HK   20251031      1.000000    0.068000
4     00039.HK   20251031      1.000000    0.088000
...        ...        ...           ...         ...
4086  01384.HK   20251031      1.000000  113.700000
4087  02954.HK   20251031      1.000000    0.265000
4088  03460.HK   20251031      1.000000    7.440000
4089  83460.HK   20251031      1.000000    6.840000
4090  09460.HK   20251031      1.000000    0.960000

```
