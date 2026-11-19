# fut_trade_param_gfe · 广州期货交易参数

来源：https://tushare.pro/document/2?doc_id=501

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 广州期货交易参数

### 接口介绍

接口：fut_trade_param_gfe
描述：获取广州期货交易参数数据
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
| rise_limit_rate | float | Y | 涨停板比例(%) |
| rise_limit | float | Y | 涨停板价格(元) |
| fall_limit | float | Y | 跌停板价格(元) |
| agent_tot_buy_posi_quota | float | Y | 经纪会员总买持仓限额(手) |
| self_tot_buy_posi_quota | float | Y | 自营总买持仓限额(手) |
| client_buy_posi_quota | float | Y | 客户买持仓限额(手) |
| self_tot_buy_ser_limit | float | Y | 自营总买限仓(手) |
| client_buy_ser_limit | float | Y | 客户买限仓(手) |

说明：跌停板显示"-"时，当日最低报价为最小变动价位

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

#获取SI2707的行情
df = pro.fut_trade_param_cfx(ts_code='SI2707.GFE')

#获取2026年09月24日全部合约行情数据
df = pro.fut_trade_param_gfe(trade_date='20260924')

```

### 数据结果

```text
  trade_date             ts_code trade_type  spec_buy
0      20260924     PD2708-C-260.GFE         期权  119328.0
1      20260924     PT2702-P-288.GFE         期权   36122.0
2      20260924  LC2705-C-154000.GFE         期权   15367.5
3      20260924  LC2705-C-156000.GFE         期权   14997.5
4      20260924  LC2611-C-106000.GFE         期权   40110.0
2995   20260924     PT2702-C-680.GFE         期权   36122.0
2996   20260924     PT2702-C-688.GFE         期权   35972.0
2997   20260924     PT2702-C-696.GFE         期权   35822.0
2998   20260924     PT2702-C-704.GFE         期权   35672.0
2999   20260924     PT2702-C-712.GFE         期权   35572.0

```
