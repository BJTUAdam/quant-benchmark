# moneyflow_cnt_ths · 同花顺概念板块资金流向（THS）

来源：https://tushare.pro/document/2?doc_id=371

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 同花顺概念板块资金流向（THS）

接口：moneyflow_cnt_ths
描述：获取同花顺概念板块每日资金流向
限量：单次最大可调取5000条数据，可以根据日期和代码循环提取全部数据
积分：6000积分可以调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 板块代码 |
| trade_date | str | N | 交易日期(格式：YYYYMMDD，下同) |
| start_date | str | N | 开始日期 |
| end_date | str | N | 结束日期 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | Y | 交易日期 |
| ts_code | str | Y | 板块代码 |
| name | str | Y | 板块名称 |
| lead_stock | str | Y | 领涨股票名称 |
| close_price | float | Y | 最新价 |
| pct_change | float | Y | 行业涨跌幅 |
| industry_index | float | Y | 板块指数点位 |
| company_num | int | Y | 公司数量 |
| pct_change_stock | float | Y | 领涨股涨跌幅 |
| net_buy_amount | float | Y | 流入资金(亿元) |
| net_sell_amount | float | Y | 流出资金(亿元) |
| net_amount | float | Y | 净额(亿元) |

接口示例

```text
#获取当日同花顺板块资金流向
df = pro.moneyflow_cnt_ths(trade_date='20250320')

```

数据示例

```text
     trade_date    ts_code     name lead_stock close_price pct_change industry_index  company_num pct_change_stock net_buy_amount net_sell_amount net_amount
0     20250320  885748.TI      可燃冰       海默科技        7.99       4.76        1307.56           12             4.76          21.00           19.00       1.00
1     20250320  886008.TI      减速器       大叶股份       21.22       2.60        1862.58          103             2.60         227.00          235.00      -8.00
2     20250320  885426.TI     海工装备       天海防务        6.97       2.56        2711.31           85             2.56         171.00          148.00      23.00
3     20250320  885372.TI      页岩气       海默科技        7.99       2.21        2103.88           40             2.21          53.00           42.00      10.00
4     20250320  886000.TI    一体化压铸       今飞凯达        5.57       1.78        1213.60           50             1.78          95.00           86.00       9.00
..         ...        ...      ...        ...         ...        ...            ...          ...              ...            ...             ...        ...
389   20250320  885881.TI      云办公      *ST鹏博        1.72      -1.36        1862.72           45            -1.36          54.00           63.00      -9.00
390   20250320  885947.TI  DRG/DIP       国新健康       12.82      -1.38        1092.62           23            -1.38          25.00           30.00      -5.00
391   20250320  885975.TI    电子身份证        拓尔思       24.16      -1.40        1438.42           40            -1.40          28.00           39.00     -11.00
392   20250320  885874.TI      云游戏      *ST鹏博        1.72      -1.75        1330.68           27            -1.75          67.00           91.00     -23.00
393   20250320  886091.TI     华为手机       凯格精机       37.23      -2.25        1183.33           35            -2.25          49.00           68.00     -18.00

```
