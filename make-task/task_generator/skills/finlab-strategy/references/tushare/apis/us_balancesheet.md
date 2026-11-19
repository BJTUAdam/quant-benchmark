# us_balancesheet · 美股资产负债表

来源：https://tushare.pro/document/2?doc_id=395

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 美股资产负债表

接口：us_balancesheet，可以通过[数据工具](https://tushare.pro/webclient/)调试和查看数据。
描述：获取美股上市公司资产负债表（目前只覆盖主要美股和中概股）
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“美股财报”）。

提示：当前接口按单只股票获取其历史数据，单次请求最大返回10000行数据，可循环提取

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| period | str | N | 报告期（格式：YYYYMMDD，每个季度最后一天的日期，如20241231) |
| ind_name | str | N | 指标名(如：新增借款） |
| report_type | str | N | 报告期类型(Q1一季报Q2半年报Q3三季报Q4年报) |
| start_date | str | N | 报告期开始时间（格式：YYYYMMDD） |
| end_date | str | N | 报告结束始时间（格式：YYYYMMDD） |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| end_date | str | Y | 报告期 |
| ind_type | str | Y | 报告期类型(Q1一季报Q2半年报Q3三季报Q4年报) |
| name | str | Y | 股票名称 |
| ind_name | str | Y | 财务科目名称 |
| ind_value | float | Y | 财务科目值 |
| report_type | str | Y | 报告类型 |

接口用法

```text
pro = ts.pro_api()

#获取美股英伟达NVDA股票Q4的资产负债表数据
df = pro.us_balancesheet(ts_code='NVDA', report_type='Q4')

#获取美股英伟达NVDA股票历年应收帐款指标数据
df = pro.us_balancesheet(ts_code='NVDA', ind_name='应收帐款')

```

数据样例

```text
         ts_code  end_date ind_type name     ind_name     ind_value report_type
0       NVDA  20250427       Q1  英伟达    负债及股东权益合计  1.252540e+11         一季报
1       NVDA  20250427       Q1  英伟达       股东权益合计  8.384300e+10         一季报
2       NVDA  20250427       Q1  英伟达   归属于母公司股东权益  8.384300e+10         一季报
3       NVDA  20250427       Q1  英伟达       其他综合收益  1.860000e+08         一季报
4       NVDA  20250427       Q1  英伟达         股本溢价  1.147500e+10         一季报
...      ...       ...      ...  ...          ...           ...         ...
2459    NVDA  20060129       Q4  英伟达     预付款项(流动)  2.438700e+07          年报
2460    NVDA  20060129       Q4  英伟达  递延所得税资产(流动)  2.682000e+06          年报
2461    NVDA  20060129       Q4  英伟达           存货  2.548700e+08          年报
2462    NVDA  20060129       Q4  英伟达         应收账款  3.181860e+08          年报
2463    NVDA  20060129       Q4  英伟达     现金及现金等价物  5.517560e+08          年报

```
