# fut_trade_param_cfx · 中金期货交易参数

来源：https://tushare.pro/document/2?doc_id=500

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 中金期货交易参数

### 接口介绍

接口：fut_trade_param_cfx
描述：获取中金期货交易参数数据
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
| trade_month | str | Y | 交易月份 |
| listing_date | str | Y | 上市日期 |
| product_id | str | Y | 产品id |
| upper_limit | str | Y | 涨停板幅度(%) |
| lower_limit | str | Y | 跌停板幅度(%) |
| end_trade_date | str | Y | 最后交易日 |
| basis_price | str | Y | 挂盘基准价 |
| long_limit | str | Y | 持仓限额 |
| upper_limit_price | str | Y | 涨停板价位 |
| lower_limit_price | str | Y | 跌停板价位 |

说明：股指期权合约持仓限额是指交易所规定的会员或者客户对某一月份合约的单边持仓最大数量。 股指期权合约单边持仓数量按买入看涨期权与卖出看跌期权持仓量之和、卖出看涨期权与买入看跌期权持仓量之和分别计算。

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

#获取TL2703的行情
df = pro.fut_trade_param_cfx(ts_code='TL2703.CFX')

#获取2026年09月14日全部合约行情数据
df=pro.fut_trade_param_cfx(trade_date='20260914',fields='trade_date,fut_code,upper_limit_price,lower_limit_price')
print(df)

```

### 数据结果

```text
   trade_date           ts_code upper_limit_price lower_limit_price
0     20260914  IO2703-C-4100.CFX             823.2               0.2
1     20260914  MO2610-P-8300.CFX            1718.6             233.4
2     20260914         IM2612.CFX            7890.2            6455.8
3     20260914         IM2703.CFX            7654.0            6262.4
4     20260914  HO2609-C-2700.CFX             454.8               0.2
711   20260914         TL2703.CFX             119.8             111.7
712   20260914         TL2706.CFX            123.85            107.65
713   20260914  MO2610-P-8100.CFX            1531.2              46.0
714   20260914  MO2610-P-8200.CFX            1623.6             138.4
715   20260914  HO2609-C-2500.CFX             653.6              80.0

```
