"""空仓协议示例。ctx.now是虚拟时间，不用于推断真实年份或历史事件。"""
from finlab.sdk import Strategy as BaseStrategy


class Strategy(BaseStrategy):
    def initialize(self, ctx):
        ctx.memory.setdefault("schema", 1)
        ctx.memory.setdefault("observations", 0)

    def on_bars(self, ctx):
        ctx.memory["observations"] += 1
        ctx.memory["last_virtual_time"] = ctx.now
