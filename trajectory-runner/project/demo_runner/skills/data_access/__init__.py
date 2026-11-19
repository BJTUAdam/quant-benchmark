"""数据调用管理专项 Skill。"""
from __future__ import annotations

from skills._workflow import CoreWorkflowSkill, definition


def create(config: dict) -> CoreWorkflowSkill:
    if config:
        raise ValueError("data_access Skill 不接受 config")
    schema = definition("data_query_market", "按题目声明查询当前可见的私有化数据接口；结果会保存到公开 data/ 目录。",
        {"api": {"type": "string"}, "params": {"type": "object"}, "fields": {"type": "string"}}, ("api",))
    return CoreWorkflowSkill([schema], {"data_query_market": ("query_data", lambda args: args)}, """\
【数据调用管理 Skill】
仅用 data_query_market 查询 TASK.md、DATA_API.md 允许的私有化数据接口；它仍受当前研发可见边界、字段合约和查询额度限制。
查询前核对接口、字段、时间范围与分页参数；缺失数据必须在研究记录中报告，不得静默补零或以未来数据填充。结果保存后再由受控研究工具读取。""")
