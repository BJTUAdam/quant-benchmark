# stk_surv · 机构调研表

来源：https://tushare.pro/document/2?doc_id=275

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 机构调研表

接口：stk_surv
描述：获取上市公司机构调研记录数据
限量：单次最大获取400条数据，可循环或分页提取
积分：用户积5000积分可使用

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 股票代码 |
| trade_date | str | N | 调研日期 |
| start_date | str | N | 调研开始日期 |
| end_date | str | N | 调研结束日期 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 股票代码 |
| name | str | Y | 股票名称 |
| surv_date | str | Y | 调研日期 |
| fund_visitors | str | Y | 机构参与人员 |
| rece_place | str | Y | 接待地点 |
| rece_mode | str | Y | 接待方式 |
| rece_org | str | Y | 接待的公司 |
| org_type | str | Y | 接待公司类型 |
| comp_rece | str | Y | 上市公司接待人员 |
| content | None | N | 调研内容 |

接口用法

```text
pro = ts.pro_api()

df = pro.stk_surv(ts_code='002223.SZ', trade_date='20211024', fields='ts_code,name,surv_date,fund_visitors,rece_place,rece_mode,rece_org')

```

数据样例

```text
      ts_code  name  surv_date fund_visitors rece_place      rece_mode                          rece_org
1   002223.SZ  鱼跃医疗  20211024            郝淼       电话会议    特定对象调研                              宝盈基金
2   002223.SZ  鱼跃医疗  20211024           秦瑶函       电话会议    特定对象调研                           贝莱德资产管理
3   002223.SZ  鱼跃医疗  20211024            谭飞       电话会议    特定对象调研                              博远基金
4   002223.SZ  鱼跃医疗  20211024            李晗       电话会议    特定对象调研                            创金合信基金
..        ...   ...       ...           ...        ...       ...                               ...
77  002223.SZ  鱼跃医疗  20211024           李虹达       电话会议    特定对象调研                              中信建投
78  002223.SZ  鱼跃医疗  20211024           李明蔚       电话会议    特定对象调研                              中银国际
79  002223.SZ  鱼跃医疗  20211024            王俊       电话会议    特定对象调研                            重庆穿石投资
80  002223.SZ  鱼跃医疗  20211024            李扬       电话会议    特定对象调研                              朱雀基金
81  002223.SZ  鱼跃医疗  20211024           徐烨程       电话会议    特定对象调研                            逐流资产管理

```
