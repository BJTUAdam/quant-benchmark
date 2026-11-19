# AKShare、AKQuant与Finlab的关系

|组件|作用|本系统策略代码的使用方式|
|---|---|---|
|Tushare Pro|官方市场与基础数据接口|通过平台的Tushare兼容桥接查询已允许数据|
|AKShare|另一套金融数据采集工具|本系统策略不直接调用它获取数据|
|AKQuant 0.3.76|底层事件驱动回测引擎|由平台可信环境调用，参与者不直接创建引擎|
|Finlab策略SDK|参与者提交协议与数据/订单入口|继承 `finlab.sdk.Strategy`，使用ctx查询及下单|

AKQuant官方快速开始可能展示 `import akshare as ak` 下载行情，再用 `akquant.run_backtest` 回测。那是库的独立使用示例。当前项目已经选择Tushare桥接与平台SDK，所以不能把该示例直接复制到 `backtest.py`。

参与者的 `backtest.py` 调用 `finlab.sdk.launch`；平台处理行情输入、撮合、连续账户和系统结果。参与者的 `strategy.py` 写 `on_bars(self, ctx)`，不是原生AKQuant的 `on_bar(self, bar)`。

查底层概念时可以阅读 [AKQuant官方版本资料](akquant/index.md)；实现参与者策略时，以当前任务契约、[strategy-sdk.md](strategy-sdk.md) 和 [submission.md](submission.md) 为准。官方资料的“库具备该能力”不等于本平台已开放该能力。
