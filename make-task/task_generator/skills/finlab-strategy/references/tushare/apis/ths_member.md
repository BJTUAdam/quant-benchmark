# ths_member · 概念板块成分

来源：https://tushare.pro/document/2?doc_id=261

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 概念板块成分

接口：ths_member
描述：获取概念板块成分列表
限量：用户积累6000积分可调取，每分钟可调取200次，是最新的概念板块成分。

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 板块指数代码 |
| con_code | str | N | 股票代码 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 指数代码 |
| con_code | str | Y | 股票代码 |
| con_name | str | Y | 股票名称 |
| weight | float | N | 权重(暂无) |
| in_date | str | N | 纳入日期(暂无) |
| out_date | str | N | 剔除日期(暂无) |
| is_new | str | N | 是否最新Y是N否 |

接口示例

```text
pro = ts.pro_api()

df = pro.ths_member(ts_code='885800.TI')

```

数据样例

```text
  ts_code         con_code     con_name
0   885800.TI  000016.SZ  深康佳A
1   885800.TI  000049.SZ  德赛电池
2   885800.TI  002008.SZ  大族激光
3   885800.TI  002036.SZ  联创电子
4   885800.TI  002055.SZ  得润电子
..        ...        ...   ...
87  885800.TI  688127.SH  蓝特光学
88  885800.TI  688157.SH  松井股份
89  885800.TI  688286.SH  敏芯股份
90  885800.TI  688312.SH  燕麦科技
91  885800.TI  688386.SH  泛亚微透

[92 rows x 3 columns]

```
