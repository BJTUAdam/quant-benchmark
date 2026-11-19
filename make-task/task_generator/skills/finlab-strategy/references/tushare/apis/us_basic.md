# us_basic · 美股列表

来源：https://tushare.pro/document/2?doc_id=252

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 美股列表

接口：us_basic
描述：获取美股列表信息
限量：单次最大6000，可分页提取。覆盖的证券类型：ADR-美国存托凭证；GDR-全球存托凭证；EQ-普通股；PF-优先股（本次新增）。
注：美股代码存在代码复用，变更，多市场交易等，可能存在数据重复情况，需要加入上市状态判断数据情况
积分：120积分可以试用，5000积分有正式权限

输入参数

| 名称 | 类型 | 必选 | 描述 | 示例 |
| --- | --- | --- | --- | --- |
| ts_code | str | N | 股票代码 | AAPL（苹果） |
| classify | str | N | 股票分类 | ADR-美国存托凭证；GDR-全球存托凭证；EQ-普通股；PF-优先股 |
| list_stauts | str | N | 上市状态 | L上市D退市P暂停上市 |
| offset | str | N | 开始行数 | 1：第一行 |
| limit | str | N | 每页最大行数 | 500：每页500行 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 美股代码 |
| name | str | Y | 中文名称 |
| enname | str | N | 英文名称 |
| classify | str | Y | 分类:ADR-美国存托凭证；GDR-全球存托凭证；EQ-普通股；PF-优先股 |
| list_date | str | Y | 上市日期 |
| delist_date | str | Y | 退市日期 |

接口示例

```text
pro = ts.pro_api()

#获取默认美国股票基础信息，单次6000行
df = pro.us_basic()

```

数据示例

```text
     ts_code  name classify list_date delist_date
0       ONCY  None      EQT  20011005        None
1       SCCO  None      EQT  19950124        None
2      KAOCF  None      EQT  19740319        None
3      BOIRF  None      EQT  19880628        None
4      SDXOF  None      EQT  19830304        None
...      ...   ...      ...       ...         ...
5995   ESESQ  None      EQT  20031014        None
5996    TRKX  None      EQT  20000718        None
5997   ELAMF  None      EQT  19960320        None
5998    CZNB  None      EQT  20120724        None
5999   CRRSQ  None      EQT  20010619        None

```
