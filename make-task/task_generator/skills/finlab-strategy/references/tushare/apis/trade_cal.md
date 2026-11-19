# trade_cal · 交易日历

来源：https://tushare.pro/document/2?doc_id=26

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 交易日历

接口：trade_cal，可以通过[数据工具](https://tushare.pro/webclient/)调试和查看数据。
描述：获取各大交易所交易日历数据,默认提取的是上交所。
三大交易所的交易日历都是一样的，北交所交易日历参考上交所和深交所。
积分：需2000积分

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| exchange | str | N | 交易所 SSE上交所,SZSE深交所,CFFEX 中金所,SHFE 上期所,CZCE 郑商所,DCE 大商所,INE 上能源 |
| start_date | str | N | 开始日期 （格式：YYYYMMDD 下同） |
| end_date | str | N | 结束日期 |
| is_open | str | N | 是否交易 '0'休市 '1'交易 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| exchange | str | Y | 交易所 SSE上交所 SZSE深交所 |
| cal_date | str | Y | 日历日期 |
| is_open | str | Y | 是否交易 0休市 1交易 |
| pretrade_date | str | Y | 上一个交易日 |

接口示例

```text
pro = ts.pro_api()


df = pro.trade_cal(exchange='', start_date='20180101', end_date='20181231')

```

或者

```text
df = pro.query('trade_cal', start_date='20180101', end_date='20181231')

```

数据样例

```text
    exchange  cal_date  is_open
0           SSE  20180101        0
1           SSE  20180102        1
2           SSE  20180103        1
3           SSE  20180104        1
4           SSE  20180105        1
5           SSE  20180106        0
6           SSE  20180107        0
7           SSE  20180108        1
8           SSE  20180109        1
9           SSE  20180110        1
10          SSE  20180111        1
11          SSE  20180112        1
12          SSE  20180113        0
13          SSE  20180114        0
14          SSE  20180115        1
15          SSE  20180116        1
16          SSE  20180117        1
17          SSE  20180118        1
18          SSE  20180119        1
19          SSE  20180120        0
20          SSE  20180121        0

```
