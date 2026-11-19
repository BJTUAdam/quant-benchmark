# 桥接数据契约

仅从桥接器获取数据，无行情文件包。URL和令牌由运行系统注入；上游URL和Tushare令牌属于运营方。

```python
from finlab import tushare as ts
pro = ts.pro_api()  # 默认DataFrame；dataframe=False可返回列表
# pro.query("daily", ts_code="000001.SZ", end_date="虚拟YYYYMMDD")
```

|接口|允许字段|频率|
|---|---|---|
|daily|ts_code, trade_date, open, high, low, close, vol, amount|daily|
|adj_factor|ts_code, trade_date, adj_factor|daily|
|stk_limit|ts_code, trade_date, up_limit, down_limit|daily|
|trade_cal|exchange, cal_date, is_open, pretrade_date|daily|
|index_daily|ts_code, trade_date, close|daily|

字段含义与单位以绑定契约及官方定义为准。Tushare股票/ETF日线vol通常为手、amount为千元；回测输入由平台转成股/元。
期货vol为手，价格按合约报价单位；不能套用股票倍率。分钟接口的单位必须另行确认。
缺失值使用null，不填造价格；停牌、无交易和数据缺失分别处理。
未知字段、未冻结范围和未来查询会返回错误，不能将错误当作零值或无交易。
独立日期、时间戳、报告期和已登记时间文本统一映射，全部均为虚拟日期，不是实际历史年份。
不得根据虚拟年份套用已知股灾、政策、节假日或预设事件；只按桥接交易日历与当前可见信息运行。
名称代码及其嵌入年月保留，但不得用它们重建真实时间映射。
