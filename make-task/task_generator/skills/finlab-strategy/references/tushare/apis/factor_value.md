# factor_value · 因子值

来源：https://tushare.pro/document/2?doc_id=490

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 因子值

### 接口介绍

接口：factor_value
描述：获取Tushare因子库里各因子的数值，目前是基于daily的时间序列数值。数据开始于2010-01-01，每日18点半~19点半之间更新。
限量：单次最大提取6000条数据，可根据股票代码或日期或因子名循环提取
权限：本接口数据需单独开通权限，对个人用户2000元一年，机构用户2万元一年，5000积分以上可试用（不用申请，可直接调用），请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“因子库”）。

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 股票代码 |
| factor_name | str | N | 因子名称（基于因子列表的名称） |
| trade_date | str | N | 交易日期：YYYYMMDD格式 |
| start_date | str | N | 开始日期：YYYYMMDD格式 |
| end_date | str | N | 结束日期：YYYYMMDD格式 |

注：ts_code和factor_name至少要输入一个参数

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| factor_name | str | Y | 因子名称 |
| ts_code | str | Y | 证券代码 |
| trade_date | str | Y | 交易日期，格式YYYYMMDD |
| factor_value | float | Y | 因子值 |

### 代码示例

```text
# 导入sdk包
import tushare as ts


# 获取接口实例
pro = ts.pro_api()

# 按日期提取某日所有股票的某个因子的全部数据
df = pro.factor_value(trade_date='20260812', factor_name='MACD')

# 按股票提取全部因子某一天的数据
df = pro.factor_value(trade_date='20260812', ts_code='600000.SH')

print(df)

```

### 数据结果

```text
               factor_name    ts_code trade_date  factor_value
0   bias_std_turn_63d_504d  000566.SZ   20100104     -0.220831
1   bias_std_turn_63d_504d  000623.SZ   20100104     -0.341817
2   bias_std_turn_63d_504d  000731.SZ   20100104     -0.229821
3   bias_std_turn_63d_504d  000525.SZ   20100104     -0.073316
4  bias_std_turn_126d_504d  000767.SZ   20100104      0.115651
5       bias_turn_42d_252d  000713.SZ   20100104      0.026199
6        avg_turnover_252d  000100.SZ   20100104      0.000233
7   bias_std_turn_63d_504d  000571.SZ   20100104      0.291333
8   bias_std_turn_63d_504d  000629.SZ   20100104     -0.789451
9   bias_std_turn_63d_504d  000738.SZ   20100104     -0.228540

```

### 数据转换（宽表格式）

当按日期查询全市场因子值时，返回的是长表格式（每行一只股票）。可使用 pivot_table 转换为宽表格式（每列一只股票），便于横向对比：

```text
df = pro.factor_value(trade_date='20260812', factor_name='MACD')

df_pivot = df.pivot_table(
    index=['factor_name', 'trade_date'],
    columns='ts_code',
    values='factor_value'
).reset_index()

```

### 宽表效果：

```text
ts_code factor_name trade_date  000001.SZ  000002.SZ  000006.SZ  ...  920978.BJ    920992.BJ
0              MACD 20260812  -6.387727   0.33698   8.421715  ...     0.622232     0.305102

```
