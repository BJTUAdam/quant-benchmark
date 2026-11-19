# tdx_member · TDX板块成分

来源：https://tushare.pro/document/2?doc_id=377

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## TDX板块成分

接口：tdx_member
描述：获取各板块成分股信息
限量：单次最大3000条数据，可以根据日期和板块代码循环提取
权限：用户积累6000积分可调取，具体请参阅[积分获取办法](https://tushare.pro/document/1?doc_id=13) 

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | N | 板块代码：xxxxxx.TDX |
| con_code | str | N | 成分股票代码 |
| trade_date | str | N | 交易日期：（YYYYMMDD格式） |
| start_date | str | N | 开始日期：（YYYYMMDD格式） |
| end_date | str | N | 结束日期：（YYYYMMDD格式） |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 板块代码 |
| trade_date | str | Y | 交易日期 |
| con_code | str | Y | 成分股票代码 |
| con_name | str | Y | 成分股票名称 |

接口示例

```text
#获取通达信板块2025年5月13日的航运概念板块成分股
df = pro.tdx_member(trade_date='20250513', ts_code='880728.TDX')

```

数据示例

```text
       ts_code trade_date   con_code     con_name
0   880728.TDX   20250513  000039.SZ     中集集团
1   880728.TDX   20250513  000088.SZ    盐 田 港
2   880728.TDX   20250513  000507.SZ      珠海港
3   880728.TDX   20250513  000520.SZ     凤凰航运
4   880728.TDX   20250513  000582.SZ     北部湾港
..         ...        ...        ...      ...
59  880728.TDX   20250513  603869.SH     ST智知
60  880728.TDX   20250513  603967.SH     中创物流
61  880728.TDX   20250513  605090.SH     九丰能源
62  880728.TDX   20250513  833171.BJ     国航远洋
63  880728.TDX   20250513  872351.BJ     华光源海

```
