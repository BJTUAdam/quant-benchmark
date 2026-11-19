# monetary_policy · 央行货币政策执行报告

来源：https://tushare.pro/document/2?doc_id=465

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 央行货币政策执行报告

### 接口介绍

接口：monetary_policy
描述：获取央行季度更新的货币政策执行报告，历史数据开始于2001年每年四篇，提供原始PDF下载链接，可用于分析过去20多年央行货币政策的动向、宏观以及金融市场的情况。
限量：单次最大1000条，一次可以拉取全部
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“央行货币政策执行报告”）

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| start_date | str | N | 发布开始日期（YYYYMMDD格式）示例:20260312 |
| end_date | str | N | 发布结束日期（YYYYMMDD格式）示例:20260312 |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| pub_date | str | Y | 发布日期 |
| title | str | Y | 标题 |
| url | str | Y | 原文链接 |
| pdf_url | str | Y | pdf链接 |
| content_html | str | Y | 带标签的正文内容 |

### 代码示例

```text
# 导入sdk包
import tushare as ts

# 配置凭据，如果已全局配置了，可以忽略。
ts.set_token('<--your-token-->')

# 获取接口实例
pro = ts.pro_api()

# 一次拉取全部央行货币政策执行报告原始数据
df = pro.monetary_policy()
print(df)

# 指定输出字段拉取央行货币政策执行报告原始数据
df = pro.monetary_policy(start_date='20250101', end_date='20251231', fields='pub_date,title,url')
print(df)

```

### 数据结果

```text
   pub_date                title                                                url
0  20251111  2025年第三季度中国货币政策执行报告  http://www.pbc.gov.cn/zhengcehuobisi/125207/12...
1  20250815  2025年第二季度中国货币政策执行报告  http://www.pbc.gov.cn/zhengcehuobisi/125207/12...
2  20250509  2025年第一季度中国货币政策执行报告  http://www.pbc.gov.cn/zhengcehuobisi/125207/12...
3  20250213  2024年第四季度中国货币政策执行报告  http://www.pbc.gov.cn/zhengcehuobisi/125207/12...

```
