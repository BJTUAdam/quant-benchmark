# cb_price_chg · 可转债转股价变动

来源：https://tushare.pro/document/2?doc_id=246

> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。
> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。
> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。
> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。

## 官方正文

## 可转债转股价变动

接口：cb_price_chg
描述：获取可转债转股价变动
限量：单次最大2000，总量不限制
权限：本接口数据需单独开通权限，请参考[权限列表](https://tushare.pro/document/1?doc_id=290)，前往[权限中心](https://tushare.pro/weborder/#/permission)申请（需勾选“可转债价格变动”）。

输入参数

| 名称 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 转债代码，支持多值输入 |

输出参数

| 名称 | 类型 | 默认显示 | 描述 |
| --- | --- | --- | --- |
| ts_code | str | Y | 转债代码 |
| bond_short_name | str | Y | 转债简称 |
| publish_date | str | Y | 公告日期 |
| change_date | str | Y | 变动日期 |
| convert_price_initial | float | Y | 初始转股价格 |
| convertprice_bef | float | Y | 修正前转股价格 |
| convertprice_aft | float | Y | 修正后转股价格 |

接口示例

```text
pro = ts.pro_api(your token)
#获取可转债转股价变动
df = pro.cb_price_chg(ts_code="113556.SH,128114.SZ,128110.SZ",fields="ts_code,bond_short_name,change_date,convert_price_initial,convertprice_bef,convertprice_aft")

```

数据示例

```text
 ts_code bond_short_name change_date convert_price_initial convertprice_bef convertprice_aft
0  113556.SH    至纯转债    20191220           29.4700             None             None
1  113556.SH    至纯转债    20200629           29.4700          29.4700          29.3800
2  128110.SZ    永兴转债    20200609           17.1600             None             None
3  128114.SZ    正邦转债    20200617           16.0900             None             None

```
