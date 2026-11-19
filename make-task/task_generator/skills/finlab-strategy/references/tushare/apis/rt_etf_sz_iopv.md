# rt_etf_sz_iopv · ETF实时参考

来源：https://tushare.pro/document/2?doc_id=454

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# ETF实时参考

### 接口介绍

接口：rt_etf_sz_iopv
描述：ETF实时净值和申购赎回数据参考，目前只提供深市
限量：单次最大5000条，完全覆盖当前总量
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“深市IOPV和申赎”）。

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | ETF代码（默认为空，即一次全市场。支持单个和多个ETF过滤提取） |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| trade_time | datetime | Y | 交易时间 |
| ts_code | str | Y | ETF代码 |
| vol | float | Y | 成交量（份） |
| num | int | Y | 成交笔数 |
| amount | float | Y | 成交金额（元） |
| price | float | Y | 最新价（元） |
| iopv | float | Y | 最近参考净值 |
| pre_iopv | float | Y | 前一日参考净值 |
| buy_num | int | Y | 申购笔数 |
| buy_vol | float | Y | 申购买量(份) |
| sell_num | int | Y | 赎回笔数 |
| sell_vol | float | Y | 赎回买量（份） |

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

# 示例： 获取单个ETF（159103.SZ）的最新参考
df = pro.rt_etf_sz_iopv(ts_code="159103.SZ")
print(df)

# 示例： 获取两个ETF的最新参考（159103.SZ,159105.SZ）
df = pro.rt_etf_sz_iopv(ts_code="159103.SZ,159105.SZ")
print(df)

# 示例：获取深市ETF全部实时参考指标
df = pro.rt_etf_sz_iopv()

#示例：获取深市ETF指定指标的实时参考
df = pro.rt_etf_sz_iopv(fields='trade_time,ts_code,iopv,buy_num,buy_vol,sell_num,sell_vol')
print(df)

```

### 数据结果

```text
             trade_time    ts_code    iopv  buy_num   buy_vol  sell_num  sell_vol
0    2026-03-20 10:27:27  161039.SZ  0.0000        0    0.0000         0    0.0000
1    2026-03-20 10:28:45  159003.SZ  0.0000        7    1.1101        18    3.3692
2    2026-03-20 10:28:48  159005.SZ  0.0000       26    5.3240         4    0.5903
3    2026-03-20 10:29:03  159102.SZ  0.7776        0    0.0000         0    0.0000
4    2026-03-20 10:29:09  159108.SZ  0.9792        1  100.0000         0    0.0000
..                   ...        ...     ...      ...       ...       ...       ...
923  2026-03-20 10:28:00  180302.SZ  0.0000        0    0.0000         0    0.0000
924  2026-03-20 10:29:09  180402.SZ  0.0000        0    0.0000         0    0.0000
925  2026-03-20 10:29:06  180606.SZ  0.0000        0    0.0000         0    0.0000
926  2026-03-20 10:29:06  180701.SZ  0.0000        0    0.0000         0    0.0000
927  2026-03-20 10:29:00  180901.SZ  0.0000        0    0.0000         0    0.0000

```
