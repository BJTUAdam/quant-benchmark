# news · 新闻快讯

来源：https://tushare.pro/document/2?doc_id=143

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 新闻快讯

接口：news
描述：获取主流新闻网站的快讯新闻数据,提供超过6年以上历史新闻。
限量：单次最大1500条新闻，可根据时间参数循环提取历史
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“新闻资讯”）。

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| start_date | datetime | Y | 开始日期(格式：2018-11-20 09:00:00） |
| end_date | datetime | Y | 结束日期 |
| src | str | Y | 新闻来源 见下表 |

数据源

| 来源名称 | src标识 | 描述 |
| --- | --- | --- |
| 新浪财经 | sina | 获取新浪财经实时资讯 |
| 华尔街见闻 | wallstreetcn | 华尔街见闻快讯 |
| 同花顺 | 10jqka | 同花顺财经新闻 |
| 东方财富 | eastmoney | 东方财富财经新闻 |
| 云财经 | yuncaijing | 云财经新闻 |
| 凤凰新闻 | fenghuang | 凤凰新闻 |
| 金融界 | jinrongjie | 金融界新闻 |
| 财联社 | cls | 财联社快讯 |
| 第一财经 | yicai | 第一财经快讯 |

日期输入说明： 

- 时间参数格式例子：start_date='2018-11-20 09:00:00', end_date='2018-11-20 22:05:03' 

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| datetime | str | Y | 新闻时间 |
| content | str | Y | 内容 |
| title | str | Y | 标题 |
| channels | str | N | 分类 |

接口调用

```text
pro = ts.pro_api()

df = pro.news(src='sina', start_date='2018-11-21 09:00:00', end_date='2018-11-22 10:10:00')

```

数据样例

[原文图片：示意图] 

更多数据预览，请点击网站头部菜单的[资讯数据](https://tushare.pro/news)。
