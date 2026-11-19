"""回测专项 Skill。"""
from __future__ import annotations

from skills._workflow import CoreWorkflowSkill, definition


def create(config: dict) -> CoreWorkflowSkill:
    if config:
        raise ValueError("backtesting Skill 不接受 config")
    schema = definition("backtest_run_local", "在禁网 Docker 中运行完整的本地研究回测；只可读取已查询的公开数据。",
        {"code": {"type": "string"}}, ("code",))
    return CoreWorkflowSkill([schema], {"backtest_run_local": ("run_local_backtest", lambda args: args)}, """\
【回测 Skill】
用 backtest_run_local 编写完整、可复现的本地研究回测。比较基线、参数扫描或分段独立账户时，统一使用同一手续费、滑点和仓位口径。
回测只能读取已由 query_data 保存的当前可见数据，不能调用平台评测器，也不能把正式连续账户结果当作研究回测结果。将区间、参数、成本假设和输出指标写入 RESEARCH.md。""")
