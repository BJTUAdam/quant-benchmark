# fut_trade_param · 期货交易参数

来源：https://tushare.pro/document/2?doc_id=497

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 期货交易参数

### 接口介绍

接口：fut_trade_param
描述：获取郑商所期货交易参数数据
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
| trade_unit | str | Y | 交易单位 |
| tick_size | str | Y | 最小变动价位 |
| pre_settle_price | str | Y | 上日结算价 |
| price_limit | str | Y | 涨跌停板幅度(%) |
| position_limit | str | Y | 日持仓限额 |
| trade_limit | str | Y | 交易限额 |
| min_order_qty | str | Y | 最小开仓量下单量 |
| max_limit_order_qty | str | Y | 限价单最大下单量 |
| max_market_order_qty | str | Y | 市价单最大下单量 |

说明：
(1) 最小变动价位、结算价：元/吨。
(2) 新挂牌合约上日结算价为新挂盘合约当日的挂盘价。
(3) 该报表只展示交易和挂起状态的合约，不展示已到期的合约

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

# 拉取接口(fut_trade_param)数据
获取PX2612.ZCE的数据
df=pro.fut_trade_param(ts_code='PX2612.ZCE')

#获取20260914的数据
df=pro.fut_trade_param(trade_date='20260914',fields='trade_date,fut_code, exchange, trade_unit, tick_size')
print(df)

```

### 数据结果

```text
  trade_date    ts_code exchange trade_unit tick_size
0     20260914  PX2612.ZCE     CZCE          5       2.0
1     20260914  SM2610.ZCE     CZCE          5       2.0
2     20260914  OI2609.ZCE     CZCE         10       1.0
3     20260914  OI2611.ZCE     CZCE         10       1.0
4     20260914  AP2703.ZCE     CZCE         10       1.0
..         ...         ...      ...        ...       ...
234   20260914  ZC2705.ZCE     CZCE        100       0.2
235   20260914  ZC2706.ZCE     CZCE        100       0.2
236   20260914  SH2708.ZCE     CZCE         30       1.0
237   20260914  SM2609.ZCE     CZCE          5       2.0
238   20260914  PX2701.ZCE     CZCE          5       2.0

```
