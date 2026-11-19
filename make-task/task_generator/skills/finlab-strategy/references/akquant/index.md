# AKQuant 0.3.76 · 官方版本参考

本目录是引擎官方资料的离线补充，帮助理解撮合、资产和恢复语义。参与者代码先读上层技能的 `strategy-sdk.md`、`submission.md`。

本系统使用 AKQuant 回测，AKShare 是另一套行情获取库。官方入门中用 AKShare 获取数据的示例不适用于本系统的受限策略容器。

原生 `Strategy.on_bar`、`buy/sell`、`run_backtest`、`run_from_checkpoint` 等入口由可信引擎适配；参与者使用 `finlab.sdk.Strategy.on_bars(ctx)`、`ctx.order` 和平台启动入口。不能把官方引擎功能直接等同于当前平台开放的能力。

固定版本 `v0.3.76`，源码提交 `269fab31a389a607da5c5d4872ef6b54390de6d4`。共 13 份中文官方文档；不是整个上游网站的镜像，也不声明所有原生功能均在本平台开放。

|文档|适用阅读场景|
|---|---|
|[API 参考](official/reference/api.md)|`docs/zh/reference/api.md`|
|[快速开始](official/start/quickstart.md)|`docs/zh/start/quickstart.md`|
|[手把手教程：编写你的第一个量化策略](official/start/first_strategy.md)|`docs/zh/start/first_strategy.md`|
|[策略编写指南](official/guide/strategy.md)|`docs/zh/guide/strategy.md`|
|[数据准备与加载指南 (Data Guide)](official/guide/data.md)|`docs/zh/guide/data.md`|
|[测试指南](official/guide/testing.md)|`docs/zh/guide/testing.md`|
|[热启动 (Warm Start) 指南](official/advanced/warm_start.md)|`docs/zh/advanced/warm_start.md`|
|[时区处理指南 (Timezone Handling Guide)](official/advanced/timezone.md)|`docs/zh/advanced/timezone.md`|
|[Tick Size 对齐指南](official/advanced/tick_size_alignment.md)|`docs/zh/advanced/tick_size_alignment.md`|
|[第 6 章：A 股市场微观结构与策略实战](official/textbook/06_stock_a.md)|`docs/zh/textbook/06_stock_a.md`|
|[第 7 章：期货市场与衍生品策略](official/textbook/07_futures.md)|`docs/zh/textbook/07_futures.md`|
|[第 9 章：基金投资与资产配置理论](official/textbook/09_funds.md)|`docs/zh/textbook/09_funds.md`|
|[附录 C：常见误区对照表](official/textbook/appendix_pitfalls.md)|`docs/zh/textbook/appendix_pitfalls.md`|

[来源、版本及逐文件SHA-256](snapshot.json) · [上游许可证](LICENSE.upstream.txt)

正文中的数字、证券代码、日期和策略例子只用于官方教学，不是本任务数据或答案；不要复制到任务契约或用来声称回测成绩。

当前平台对期货逐日现金结算、按手费用、平今差异费率及动态保证金仍有限制，详见技能的回测注意事项。以平台能力和当前任务绑定规则为准。

未下载的图片和延伸文档仅保留固定提交来源链接，阅读这些本地Markdown不需要外部资源加载。
