# kpl_concept_cons · 开盘啦题材成分

来源：https://tushare.pro/document/2?doc_id=351

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 开盘啦题材成分

接口：kpl_concept_cons
描述：获取概念题材的成分股
限量：单次最大3000条，可根据代码和日期循环获取全部数据
积分：5000积分可提取数据，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| trade_date | str | N | 交易日期（YYYYMMDD格式） |
| ts_code | str | N | 题材代码（xxxxxx.KP格式） |
| con_code | str | N | 成分代码（xxxxxx.SH格式） |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 题材ID |
| name | str | Y | 题材名称 |
| con_name | str | Y | 股票名称 |
| con_code | str | Y | 股票代码 |
| trade_date | str | Y | 交易日期 |
| desc | str | Y | 描述 |
| hot_num | int | Y | 人气值 |

接口用法

```text
pro = ts.pro_api()

df = pro.kpl_concept_cons(trade_date='20241014')

```

数据样例

```text
        ts_code      name     ts_name con_code trade_date
0     000111.KP  化债概念    信达地产  600657.SH   20241014
1     000111.KP  化债概念    银宝山新  002786.SZ   20241014
2     000111.KP  化债概念    摩恩电气  002451.SZ   20241014
3     000111.KP  化债概念    光大嘉宝  600622.SH   20241014
4     000111.KP  化债概念    海德股份  000567.SZ   20241014
...         ...   ...     ...        ...        ...
2995  000229.KP    电力    特变电工  600089.SH   20241014
2996  000229.KP    电力    中国西电  601179.SH   20241014
2997  000229.KP    电力    金盘科技  688676.SH   20241014
2998  000229.KP    电力    思源电气  002028.SZ   20241014
2999  000229.KP    电力    明阳电气  301291.SZ   20241014

```
