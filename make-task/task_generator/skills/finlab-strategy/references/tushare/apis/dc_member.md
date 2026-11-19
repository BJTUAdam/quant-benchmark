# dc_member · 板块成分

来源：https://tushare.pro/document/2?doc_id=363

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 板块成分

接口：dc_member
描述：获取板块每日成分数据，可以根据概念板块代码和交易日期，获取历史成分，起始日期2024-12-20。
限量：单次最大获取5000条数据，可以通过日期和代码循环获取
权限：用户积累6000积分可调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 板块指数代码 |
| con_code | str | N | 成分股票代码 |
| trade_date | str | N | 交易日期（YYYYMMDD格式） |
| start_date | str | N | 开始日期（YYYYMMDD格式） |
| end_date | str | N | 结束日期（YYYYMMDD格式） |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | Y | 交易日期 |
| ts_code | str | Y | 概念代码 |
| con_code | str | Y | 成分代码 |
| name | str | Y | 成分股名称 |

接口示例

```text
#获取2025年1月2日的人形机器人概念板块成分列表
df = pro.dc_member(trade_date='20250102', ts_code='BK1184.DC')

```

数据示例

```text
     trade_date  ts_code   con_code   name
0    20250102  BK1184.DC  002117.SZ   东港股份
1    20250102  BK1184.DC  603662.SH   柯力传感
2    20250102  BK1184.DC  688165.SH  埃夫特-U
3    20250102  BK1184.DC  300660.SZ   江苏雷利
4    20250102  BK1184.DC  873593.BJ   鼎智科技
..        ...        ...        ...    ...
59   20250102  BK1184.DC  002139.SZ   拓邦股份
60   20250102  BK1184.DC  301236.SZ   软通动力
61   20250102  BK1184.DC  601727.SH   上海电气
62   20250102  BK1184.DC  300432.SZ   富临精工
63   20250102  BK1184.DC  300843.SZ   胜蓝股份

```
