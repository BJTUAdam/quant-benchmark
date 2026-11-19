# fut_rcpt_mat · 标准仓单到期数量

来源：https://tushare.pro/document/2?doc_id=496

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 标准仓单到期数量

### 接口介绍

接口：fut_rcpt_mat
描述：获取上海期货标准仓单到期数量数据（其他交易所上线中）
限量：单次最大3000条，可根据代码或日期循环提取
积分：需要5000积分可以调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13)

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | N | 交易日期（YYYYMMDD） |
| ts_code | str | N | 期货代码 |
| start_date | str | N | 开始日期（YYYYMMDD） |
| end_date | str | N | 结束日期（YYYYMMDD） |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | Y | 交易日期（YYYYMMDD） |
| exchange | str | Y | 交易所 |
| fut_code | str | Y | 期货代码 |
| fut_name | str | Y | 品种名称 |
| unit | str | Y | 单位 |
| cur_month | float | Y | 当月到期数量 |
| next_month | float | Y | 一个月到期数量 |
| next_two_month | float | Y | 两个月到期数量 |

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

# 拉取接口(fut_rcpt_mat)数据
#获取AD所有的数据
df = pro.fut_rcpt_mat(ts_code='AD')

# 获取2026年9月所有数据
df = pro.fut_rcpt_mat(trade_date='20260904')
print(df)

```

### 数据结果

```text
  trade_date exchange ts_code   fut_name unit cur_month next_month next_two_month
0   20260904     SHFE       AD      铸造铝合金    吨      0.00      60.00         713.00
1   20260904     SHFE       AO    氧化铝(仓库)    吨  59415.00   44721.00       72858.00
2   20260904     SHFE       AO    氧化铝(厂库)    吨   1200.00    3000.00           0.00
3   20260904     SHFE       BR  丁二烯橡胶(仓库)    吨      0.00       0.00           0.00
4   20260904     SHFE       BR  丁二烯橡胶(厂库)    吨      0.00       0.00           0.00
5   20260904     SHFE       BU   石油沥青(厂库)    吨      0.00   26100.00           0.00
6   20260904     SHFE       FU        燃料油    吨      0.00       0.00           0.00
7   20260904     SHFE       HC   热轧卷板(仓库)    吨  39254.00    6482.00       12099.00
8   20260904     SHFE       HC   热轧卷板(厂库)    吨      0.00       0.00           0.00
9   20260904     SHFE       LU  低硫燃料油(仓库)    吨      0.00       0.00        8330.00

```
