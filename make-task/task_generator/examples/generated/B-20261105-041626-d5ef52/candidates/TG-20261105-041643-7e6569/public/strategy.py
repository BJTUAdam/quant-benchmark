"""接口演示：仅保持空仓，不构成题目参考答案。"""
from finlab.sdk import Strategy as BaseStrategy

class Strategy(BaseStrategy):
    def initialize(self, ctx):
        ctx.memory.setdefault("observations", 0)

    def on_bars(self, ctx):
        ctx.memory["observations"] += 1
        # ctx.order("真实代码", 100, side="buy")
        # 日期均为虚拟日期。查询格式与任务授权见DATA_API.md。
