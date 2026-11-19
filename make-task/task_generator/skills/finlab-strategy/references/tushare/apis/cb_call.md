# cb_call · 可转债赎回信息

来源：https://tushare.pro/document/2?doc_id=269

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 可转债赎回信息

接口：cb_call，可以通过[数据工具](https://tushare.pro/webclient/)调试和查看数据。
描述：获取可转债到期赎回、强制赎回等信息。数据来源于公开披露渠道，供个人和机构研究使用，请不要用于数据商业目的。
限量：单次最大2000条数据，可以根据日期循环提取，本接口需5000积分。

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 转债代码，支持多值输入 |
| ann_date | str | N | 公告日期(YYYYMMDD格式，下同) |
| start_date | str | N | 公告开始日期 |
| end_date | str | N | 公告结束日期 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 转债代码 |
| call_type | str | Y | 赎回类型：到赎、强赎 |
| is_call | str | Y | 是否赎回：已满足强赎条件、公告提示强赎、公告实施强赎、公告到期赎回、公告不强赎 |
| ann_date | str | Y | 公告/提示日期 |
| call_date | str | Y | 赎回日期 |
| call_price | float | Y | 赎回价格(含税，元/张) |
| call_price_tax | float | Y | 赎回价格(扣税，元/张) |
| call_vol | float | Y | 赎回债券数量(张) |
| call_amount | float | Y | 赎回金额(万元) |
| payment_date | str | Y | 行权后款项到账日 |
| call_reg_date | str | Y | 赎回登记日 |

接口示例

```text

pro = ts.pro_api('your token')

#获取可转债行情
df = pro.cb_call(fields='ts_code,call_type,is_call,ann_date,call_date,call_price')

```

数据示例

```text
    ts_code call_type is_call  ann_date call_date call_price
0    123069.SZ        强赎   公告不强赎  20210821      None       None
1    113621.SH        强赎   公告不强赎  20210821      None       None
2    113528.SH        强赎   公告不强赎  20210821      None       None
3    113012.SH        强赎    公告强赎  20210818  20210903   100.6700
4    128113.SZ        强赎   公告不强赎  20210818      None       None
..         ...       ...     ...       ...       ...        ...
466  125069.SZ        强赎    公告强赎  20050429  20050422   101.8000
467  125630.SZ        强赎   公告不强赎  20040624      None       None
468  100009.SH        强赎    公告强赎  20040511  20040423   100.1300
469  125002.SZ        强赎    公告强赎  20040430  20040423   101.5000
470  125629.SZ        强赎    公告强赎  20040414  20040406   105.0000

```
