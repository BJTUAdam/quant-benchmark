# dividend · 分红送股

来源：https://tushare.pro/document/2?doc_id=103

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 分红送股

接口：dividend
描述：分红送股数据
权限：起始时间2000-01-01，单次查询返回2000行，每日20点~21点更新，用户需要至少2000积分才可以调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | TS代码 |
| ann_date | str | N | 公告日 |
| record_date | str | N | 股权登记日期 |
| ex_date | str | N | 除权除息日 |
| imp_ann_date | str | N | 实施公告日 |

以上参数至少有一个不能为空

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | TS代码 |
| end_date | str | Y | 分红年度 |
| ann_date | str | Y | 公告日(预案，决案) |
| div_proc | str | Y | 实施进度 |
| stk_div | float | Y | 每股送转 |
| stk_bo_rate | float | Y | 每股送股比例 |
| stk_co_rate | float | Y | 每股转增比例 |
| cash_div | float | Y | 每股分红（税后） |
| cash_div_tax | float | Y | 每股分红（税前） |
| record_date | str | Y | 股权登记日 |
| ex_date | str | Y | 除权除息日 |
| pay_date | str | Y | 派息日 |
| div_listdate | str | Y | 红股上市日 |
| imp_ann_date | str | Y | 实施公告日 |
| base_date | str | N | 基准日 |
| base_share | float | N | 基准股本（万） |

接口示例

```text
pro = ts.pro_api()

df = pro.dividend(ts_code='600848.SH', fields='ts_code,div_proc,stk_div,record_date,ex_date')

```

数据样例

```text
             ts_code div_proc  stk_div record_date   ex_date
    0  600848.SH       实施     0.10    19950606  19950607
    1  600848.SH       实施     0.10    19970707  19970708
    2  600848.SH       实施     0.15    19960701  19960702
    3  600848.SH       实施     0.10    19980706  19980707
    4  600848.SH       预案     0.00        None      None
    5  600848.SH       实施     0.00    20180522  20180523

```
