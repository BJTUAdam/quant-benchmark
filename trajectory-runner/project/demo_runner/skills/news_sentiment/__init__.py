"""新闻情绪评估专项 Skill。"""
from __future__ import annotations

from skills._workflow import CoreWorkflowSkill, definition


def create(config: dict) -> CoreWorkflowSkill:
    if config:
        raise ValueError("news_sentiment Skill 不接受 config")
    query = definition("news_query_text", "按题目允许的接口查询当前可见的新闻、公告或研报文本。",
        {"api": {"type": "string"}, "params": {"type": "object"}, "fields": {"type": "string"}}, ("api",))
    score = definition("news_score_sentiment", "在禁网 Docker 中将已查询文本整理为可复核的情绪评分或时间序列；不得执行策略回测。",
        {"code": {"type": "string"}}, ("code",))
    return CoreWorkflowSkill([query, score], {
        "news_query_text": ("query_data", lambda args: args),
        "news_score_sentiment": ("run_research", lambda args: args),
    }, """\
【新闻情绪评估 Skill】
先用 news_query_text 获取任务合约明确允许、且发布时间不晚于当前研发可见边界的新闻、公告或研报文本；接口不提供文本时应报告限制，不得联网补取。
再用 news_score_sentiment 输出可复核的评分规则、原始记录标识、发布时间与品种或个股聚合方式。情绪序列成为交易信号前，必须在不重叠的样本外区间用本地回测检验，不能把同一文本同时用于拟合和验证。""")
