# bse_mapping · 北交所新旧代码对照表

来源：https://tushare.pro/document/2?doc_id=375

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 北交所新旧代码对照表

接口：bse_mapping
描述：获取北交所股票代码变更后新旧代码映射表数据
限量：单次最大1000条（本接口总数据量300以内）
积分：2000积分即可调取

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| o_code | str | N | 旧代码 |
| n_code | str | N | 新代码 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| name | str | Y | 股票名称 |
| o_code | str | Y | 原代码 |
| n_code | str | Y | 新代码 |
| list_date | str | Y | 上市日期 |

接口示例

```text
#获取方大新材新旧代码对照数据
df = pro.bse_mapping(o_code='838163.BJ')


#获取全部变更的股票代码对照表
df = pro.bse_mapping()

```

数据示例

```text
      name     o_code   n_code    list_date
0  方大新材  838163.BJ  920163.BJ  20200727

```
