# fut_trade_param_dce · 大连商品交易所交易参数

来源：https://tushare.pro/document/2?doc_id=502

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 大连商品交易所交易参数

### 接口介绍

接口：fut_trade_param_dce
描述：获取大连商品交易所交易参数数据
限量：单次最大3000条，可根据代码或日期循环提取
积分：需要5000积分可以调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13)

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 期货代码 |
| trade_date | str | N | 交易日期（YYYYMMDD） |
| start_date | str | N | 开始日期（YYYYMMDD） |
| end_date | str | N | 结束日期（YYYYMMDD） |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 合约代码或类型 |
| trade_date | str | Y | 交易日期（YYYYMMDD） |
| exchange | str | Y | 期货公司会员简称 |
| trade_type | str | Y | 交易类型 |
| type | str | Y | 限仓模式 |
| spec_buy_rate | float | Y | 投机买保证金率(%) |
| spec_buy | float | Y | 投机买保证金金额(元/手) |
| hedge_buy_rate | float | Y | 套保买保证金率(%) |
| hedge_buy | float | Y | 套保买保证金金额(元/手) |
| rise_limit_rate | float | Y | 涨跌停板比例(%) |
| rise_limit | float | Y | 涨停板价格(元) |
| fall_limit | float | Y | 跌停板价格(元) |
| self_tot_buy_posi_quota | float | Y | 非期货公司会员-期货 |
| client_buy_posi_quota | float | Y | 客户-持仓限额(手)-期货 |
| self_tot_buy_posi_quota_ser_limit | float | Y | 非期货公司会员-期权 |
| client_buy_posi_quota_ser_limit | float | Y | 客户-持仓限额(手)-期权 |
| fut_code_limit | str | Y | 合约限额 |
| variety_limit | str | Y | 品种限额 |

说明：跌停板显示"-"时，当日最低报价为最小变动价位

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

#获取BZ2609的行情
df = pro.fut_trade_param_dce(ts_code='BZ2609.DCE')

#获取2026年09月14日全部合约行情数据
df = pro.fut_trade_param_dce(trade_date='20260914')

```

### 数据结果

```text
 trade_date    ts_code exchange trade_type  type
0     20260914  JM2703.DCE      DCE         期货  分开限仓
1     20260914  PG2703.DCE      DCE         期货  分开限仓
2     20260914  EG2701.DCE      DCE         期货  分开限仓
3     20260914  EG2702.DCE      DCE         期货  分开限仓
4     20260914   A2707.DCE      DCE         期货  分开限仓
245   20260914   Y2703.DCE      DCE         期货  分开限仓
246   20260914   Y2705.DCE      DCE         期货  分开限仓
247   20260914  PG2701.DCE      DCE         期货  分开限仓
248   20260914  PG2702.DCE      DCE         期货  分开限仓
249   20260914   A2611.DCE      DCE         期货  分开限仓

```
