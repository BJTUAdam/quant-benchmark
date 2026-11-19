# fut_adjfactor · 期货复权因子

来源：https://tushare.pro/document/2?doc_id=499

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 期货复权因子

### 接口介绍

接口：fut_adjfactor
描述：获取主力合约的复权因子
限量：单次最大3000条，可根据代码或日期循环提取
积分：需要5000积分可以调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13)

注：
前复权行情：当日收盘价 × 当日复权因子 / 最新复权因子
后复权行情：当日收盘价 × 当日复权因子 也可以直接取[期货复权行情](../apis/fut_daily_adj.md)

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | N | 交易日期（YYYYMMDD） |
| ts_code | str | N | 合约代码 |
| exchange | str | N | 交易所代码 |
| start_date | str | N | 开始日期（YYYYMMDD） |
| end_date | str | N | 结束日期（YYYYMMDD） |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| exchange | str | Y | 交易所 |
| trade_date | str | Y | 交易日（YYYYMMDD） |
| adj_factor | float | Y | 累计复权因子 |

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

# 拉取接口(fut_adjfactor)数据
#获取IF.CFX合约20180101～20181113期间的复权因子
df = pro.fut_adjfactor(ts_code='IF.CFX', start_date='20180101', end_date='20181113')

#获取2026年10月12日合约复权因子数据
df = pro.fut_daily_adj(trade_date='20261012')
print(df)

```

### 数据结果

```text
   ts_code exchange trade_date adj_factor
0    A.DCE      DCE   20261012   0.877641
1   AD.SHF     SHFE   20261012   0.976876
2  ADL.SHF     SHFE   20261012   1.014848
3   AG.SHF     SHFE   20261012   0.670013
4  AGL.SHF     SHFE   20261012   0.528900
5   AL.DCE      DCE   20261012   0.401399
6   AL.SHF     SHFE   20261012   0.708825
7  ALL.SHF     SHFE   20261012   1.081992
8   AO.SHF     SHFE   20261012   1.022086
9  AOL.SHF     SHFE   20261012   0.842868

```
