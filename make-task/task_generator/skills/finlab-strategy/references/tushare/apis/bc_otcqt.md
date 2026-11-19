# bc_otcqt · 柜台流通式债券报价

来源：https://tushare.pro/document/2?doc_id=322

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 柜台流通式债券报价

接口：bc_otcqt
描述：柜台流通式债券报价
限量：单次最大2000条，可多次提取，总量不限制
积分：用户需要至少500积分可以试用调取，2000积分以上频次相对较高，积分越多权限越大，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | N | 交易日期(YYYYMMDD格式，下同) |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |
| ts_code | str | N | TS代码 |
| bank | str | N | 报价机构 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | N | 报价日期 |
| qt_time | str | N | 报价时间 |
| bank | str | N | 报价机构 |
| ts_code | str | N | 债券编码 |
| name | str | N | 债券简称 |
| maturity | str | N | 期限 |
| remain_maturity | str | N | 剩余期限 |
| bond_type | str | N | 债券类型 |
| coupon_rate | float | N | 票面利率（%） |
| buy_price | float | N | 投资者买入全价 |
| sell_price | float | N | 投资者卖出全价 |
| buy_yield | float | N | 投资者买入到期收益率（%） |
| sell_yield | float | N | 投资者卖出到期收益率（%） |

接口示例

```text
pro = ts.pro_api(your token)
#柜台流通式债券报价
df = pro.bc_otcqt(start_date='20240325',end_date='20240329',ts_code='200013.BC',fields='trade_date,qt_time,bank,ts_code,name,remain_maturity,buy_yield,sell_yield')

```

数据示例

```text
        trade_date   qt_time  bank    ts_code      name remain_maturity buy_yield sell_yield
0   20240329  08:11:02  浦发银行  200013.BC  20附息国债13          1年207天    1.9263     1.7977
1   20240329  09:05:28  招商银行  200013.BC  20附息国债13          1年207天    1.8950     1.8350
2   20240329  09:10:24  工商银行  200013.BC  20附息国债13          1年207天    1.8850     1.8528
3   20240329  09:14:48  建设银行  200013.BC  20附息国债13          1年207天    1.8837     1.8451
4   20240329  09:18:18  中国银行  200013.BC  20附息国债13          1年207天    1.9040     1.8200
5   20240329  10:40:09  北京银行  200013.BC  20附息国债13          1年207天    1.9043     1.8271
6   20240329  15:46:38  农业银行  200013.BC  20附息国债13          1年207天    1.8697     1.8054
7   20240329  18:36:29  交通银行  200013.BC  20附息国债13          1年207天    1.8464     1.8142

```
