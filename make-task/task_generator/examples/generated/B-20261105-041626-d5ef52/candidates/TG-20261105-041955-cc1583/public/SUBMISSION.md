# 提交协议 finlab.submission.v2

必需：strategy.py、backtest.py、params.json、RESEARCH.md、CHANGELOG.md。
Strategy继承finlab.sdk.Strategy，使用initialize(ctx)、on_bars(ctx)、on_order(ctx,event)、on_trade(ctx,event)、on_finish(ctx)。
ctx.now为虚拟时间；虚拟年份仅是事件索引，不对应真实历史年份，不据此预设股灾、政策或未来事件。
ctx.bars为同时间戳已完成行情切片；ctx.portfolio为当前账户；ctx.params为本轮参数；ctx.memory必须是JSON对象。
ctx.order(symbol,quantity,side="buy"/"sell",limit_price=None,tif="GTC"/"DAY")按股或合约手数下单；ctx.cancel(order_id)撤销未完成订单。
ctx.query通过受限桥接器查询。回调返回前产生的订单依次校验，不能修改平台费用、成交价格或账户。
backtest.py调用finlab.sdk.launch。只能使用预装finlab、pandas、numpy及标准库；如需依赖由运营方构建审核后的新镜像。
资源预算由本人先运行finlab setup明确设置；AI不能代填CPU、内存或时限。未配置时不能启动正式运行。
提交检查不在宿主机执行代码；真正可运行性由隔离Docker运行验证。每轮代码与参数取内容哈希。
系统结果包括equity.csv、orders.csv、trades.csv、metrics.json、run_manifest.json；可信检查点仅在平台保存。
