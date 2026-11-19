"""策略评估专项 Skill。"""
from __future__ import annotations

from skills._workflow import CoreWorkflowSkill, definition


def create(config: dict) -> CoreWorkflowSkill:
    if config:
        raise ValueError("strategy_evaluation Skill 不接受 config")
    schema = definition("strategy_evaluate", "在禁网 Docker 中分析已有研究输出，生成策略质量与风险评估；不得执行策略回测。",
        {"code": {"type": "string"}}, ("code",))
    return CoreWorkflowSkill([schema], {"strategy_evaluate": ("run_research", lambda args: args)}, """\
【策略评估 Skill】
用 strategy_evaluate 对已保存的回测输出做收益、夏普、最大回撤、成本压力、区间和仓位归因分析；输入不足时必须明确说明 DSR、PBO 等统计量未计算，不能虚构结论。
多区间方向一致、且成本与参数扰动后仍成立，才可称为改善。不要因为单一指标更高而忽略回撤、交易成本、样本量或失败区间；如实把风险、限制和是否值得修改策略写入 RESEARCH.md。""")
