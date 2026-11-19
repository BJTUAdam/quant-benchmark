# us_adjfactor · 美股复权因子

来源：https://tushare.pro/document/2?doc_id=402

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 美股复权因子

接口：us_adjfactor
描述：获取美股2006年以来每日复权因子数据，在每天美股收盘后滚动刷新
限量：单次最大15000行数据，可以根据日期循环
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“美股历史日线”）。

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
| exchange | str | Y | 交易所 |
| cum_adjfactor | float | Y | 累计复权因子 |
| close_price | float | Y | 收盘价 |

接口示例

```text
pro = ts.pro_api()

#获取美股单一股票复权因子
df = pro.us_adjfactor(ts_code='AAPL', start_date='20240101', end_date='20251022')

#获取美股某一日全部股票的复权因子
df = pro.us_adjfactor(trade_date='20251031')

```

数据示例

```text
      ts_code trade_date exchange cum_adjfactor close_price
0       TAGOF   20251031      OTC      1.000000        None
1        BABA   20251031      NYS      1.000000  170.430000
2         CZR   20251031      NAS      1.000000   20.100000
3        DEEP   20251031      ARC      1.000000   35.025100
4        AVAL   20251031      NYS      1.000000    4.220000
...       ...        ...      ...           ...         ...
14995   MRETF   20251031      OTC      1.000000    7.150000
14996     CHI   20251031      NAS      1.000000   11.390000
14997    TBHC   20251031      NAS      1.000000    1.510000
14998   MQMIF   20251031      OTC      1.000000    0.127600
14999     TAC   20251031      NYS      1.000000   17.670000

```
