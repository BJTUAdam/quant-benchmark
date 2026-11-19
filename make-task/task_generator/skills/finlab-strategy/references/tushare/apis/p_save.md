# p_save · 自选股组合保存

来源：https://tushare.pro/document/2?doc_id=445

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

# 自选股组合保存

### 接口介绍

接口：p_save
描述：创建或修改自选股组合
限量：每个用户最多200个组合，如需更多请联系管理员

### 输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| name | str | Y | 组合名称 |
| desc | str | N | 描述 |
| items | list | Y | 成份列表; 每个元素包含 / ts_code:str / ts_type:str / weight:float / name:str / desc:str |

### 输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| id | int | Y | 主键 |
| ts_code | str | Y | 成分代码 |
| ts_type | str | Y | 成份类型 |
| name | str | Y | 名称 |
| desc | str | Y | 描述 |
| weight | float | Y | 权重 |
| create_time | datetime | Y | 创建时间 |
| update_time | datetime | Y | 修改时间 |

### 代码示例

```text
import tushare as ts

pro = ts.pro_api()

# 选取股票列表
df = pro.stock_basic()
df_stock = df.head(10)
print(df_stock)

# 保存到自定义组合
p_items = df_stock[["ts_code", "name"]]
df_p1 = pro.p_save(name="我的股票池", items=p_items.to_dict(orient='records'))
print(df_p1)

```

### 数据结果

```text
   id    ts_code   name  desc  weight          create_time          update_time
0  44  000001.SZ   平安银行  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
1  45  000002.SZ    万科Ａ  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
2  46  000004.SZ  *ST国华  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
3  47  000006.SZ   深振业Ａ  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
4  48  000007.SZ    全新好  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
5  49  000008.SZ   神州高铁  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
6  50  000009.SZ   中国宝安  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
7  51  000010.SZ   美丽生态  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
8  52  000011.SZ   深物业A  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45
9  53  000012.SZ    南玻Ａ  None     0.0  2026-03-19 10:40:45  2026-03-19 10:40:45

```
