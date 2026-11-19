"""Model-facing view of a staged task. The operator package stays untouched."""
from __future__ import annotations

import json
import copy
import math
import pathlib
import re


PROTOCOL = "environment_feedback_v1"
SYSTEM = ("你是量化策略研发 Agent。请以 TASK.md、BACKTEST.md 和本轮消息为准，"
          "基于提供的资料自主研究并作出策略决策。你可以使用提供的工具研究和进行研发回测。"
          "策略实现写入 strategy.py，研究依据写入 RESEARCH.md，决策及改动追加到 CHANGELOG.md。"
          "新建或修改策略时，请在交付前对最终文件完成一次完整默认参数研发回测。"
          "研究和验证额度有限；请先完成一个可验证的交付版本，再考虑是否继续改进。"
          "工具会报告当前交付条件与剩余额度；全部满足后可立即给出不带工具调用的交付回复。"
          "请如实记录结果与风险。")
FORBIDDEN = re.compile(r"解锁|逐段揭晓|尚未揭晓|未解锁|下一段|新一段|剩余.{0,8}段|运营方|data/splits\.json|part_0[1-7]|正式仿真|仿真实盘|跨段|分段维护|研发期|阶段仿真|第\s*\d+\s*段")


def public_tools(tools: list[dict]) -> list[dict]:
    """Keep the tool API while removing operator vocabulary from descriptions."""
    result = copy.deepcopy(tools)
    for tool in result:
        tool["description"] = (tool["description"].replace("当前已解锁", "当前提供的")
                               .replace("已解锁数据", "当前提供的数据")
                               .replace("参赛工作区", "当前工作区")
                               .replace("运行标准回测", "运行研发回测"))
    if FORBIDDEN.search("\n".join(tool["description"] for tool in result)):
        raise ValueError("公开工具说明仍包含环境控制信息")
    return result


def _section(text: str, number: int) -> str:
    match = re.search(rf"(?ms)^## {number}\. .*?(?=^## \d+\.|\Z)", text)
    if not match:
        raise ValueError(f"任务文档缺少第 {number} 节")
    return match.group().strip()


def public_task(original: str) -> str:
    title = re.search(r"\*\*(请[^\n]+?)\*\*", original)
    if not title:
        raise ValueError("任务文档缺少目标")
    objective = re.sub(r"，?并在之后逐段揭晓的仿真实盘中持续维护(?:它)?", "", title.group(1))
    objective = objective.replace("整个仿真期", "后续策略运行")
    sections = [_section(original, number) for number in (3, 4, 5, 7)]
    text = ("# 策略研发任务\n\n**" + objective + "**\n\n"
            "请根据当前提供的资料自主研究，并按下列规则交付策略。"
            "标识符仅用于本任务，请只依据提供的数据研究。"
            "策略接口、数据字段和研发回测方法见 BACKTEST.md；研发回测只用于策略研究。\n\n"
            "工作区包含 TASK.md、BACKTEST.md、data/、backtester/、run_backtest.py、"
            "strategy_example.py、RESEARCH.md、CHANGELOG.md 和策略运行反馈 reports/（如有）。\n\n"
            + "\n\n".join(sections) + "\n")
    text = text.replace("跨仿真段", "跨策略版本").replace("跨段", "跨策略版本")
    text = text.replace("已解锁", "当前可用").replace("正式段", "评测区间")
    text = text.replace("正式仿真中", "策略评测中").replace("仿真实盘中", "策略评测中")
    text = text.replace("## 7. 本轮交付物（研发期）", "## 7. 交付物").replace("研发期回测结果", "研究回测结果")
    text = text.replace("研究回测", "研发回测")
    text = text.replace("策略评测", "策略运行")
    text = "\n".join(line for line in text.split("\n")
                     if "仿真中第一个决策点" not in line and "正式首个决策点" not in line)
    if FORBIDDEN.search(text):
        raise ValueError("公开任务文档仍包含环境控制说明")
    return text


def public_backtest(original: str, futures: bool) -> str:
    if futures:
        text = re.sub(r"(?ms)^## 分段维护与评分.*\Z", "", original)
        text = re.sub(r"(?m)^这是本次新建的日线教学执行器；.*\n",
                      "本系统按日线执行策略；每天收盘后决策，下一交易日开盘执行。\n", text)
        text = re.sub(r"(?m)^外部 CSV 交付格式见 .*\n",
                      "交易日以连续整数 t 表示，证券和合约使用匿名代码；价格、乘数和单位保持原始数值。\n", text)
        text = re.sub(r"(?m)^按实际交易日数 N，.*\n", "", text)
        text = re.sub(r"(?m)^\*\*每天仅在收盘后决定一次，下一交易日开盘执行。\*\*.*\n",
                      "**每天仅在收盘后决定一次，下一交易日开盘执行。**\n", text)
        text = re.sub(r"(?m)^模块导入白名单和输入截断是正确使用约束，.*\n", "", text)
        text = "\n".join(line for line in text.split("\n") if "运营方" not in line)
        text = text.replace("## 数据、切分和时间", "## 数据和时间")
        text = re.sub(r"part_\d+", "history", text)
        text = text.replace("跨日、跨段持续", "跨日和策略版本持续")
        text = text.replace("task_config.json", "data/market.json")
        text = text.replace("研发期", "研究阶段").replace("正式仿真", "策略评测").replace("仿真实盘", "策略评测")
        text = text.replace("跨段", "跨策略版本")
    else:
        text = re.sub(r"(?ms)^## 10\. 正式仿真如何使用本系统.*\Z", "", original)
        text = re.sub(r"(?m)^数据按时间分片：.*\n", "引擎读取 data/ 下当前提供的行情文件。\n", text)
        text = text.replace("你自己做研发回测和正式的分段仿真实盘用的是**同一套引擎、同一套规则**，所以自测结果和正式结果的口径一致。",
                            "研究回测与策略评测采用相同的交易规则。")
        text = text.replace("正式仿真中跨段保留，换了新版本也会沿用", "策略版本更新后也会沿用")
        text = text.replace("正式仿真中", "策略评测中").replace("正式仿真", "策略评测")
        text = text.replace("仿真实盘", "策略评测").replace("跨段", "跨策略版本")
        text = text.replace("研发期", "研究阶段")
    text = text.replace("研究回测", "研发回测")
    text = text.replace("策略评测", "策略运行")
    text = re.sub(r"part_\d+", "history", text)
    text = text.replace("part_*", "history")
    text = text.replace("part_XX", "history")
    text = text.replace("分片缺失或不配对", "行情文件缺失或不配对")
    if FORBIDDEN.search(text):
        raise ValueError("公开回测文档仍包含环境控制说明")
    return text.rstrip() + "\n"


def public_market(original: dict) -> dict:
    return {key: value for key, value in original.items()
            if key not in {"research_fraction", "segments", "data_status", "cost_policy", "min_days"}}


def publish_initial(workspace: pathlib.Path, futures: bool) -> None:
    task = public_task((workspace / "TASK.md").read_text(encoding="utf-8"))
    backtest = public_backtest((workspace / "BACKTEST.md").read_text(encoding="utf-8"), futures)
    (workspace / "TASK.md").write_text(task, encoding="utf-8")
    (workspace / "BACKTEST.md").write_text(backtest, encoding="utf-8")
    command = workspace / "run_backtest.py"
    if command.is_file():
        source = command.read_text(encoding="utf-8")
        source = source.replace("分段仿真实盘", "策略运行").replace("正式仿真实盘", "策略运行")
        command.write_text(source, encoding="utf-8")
    if futures:
        (workspace / "task_config.json").unlink(missing_ok=True)
        market = workspace / "data/market.json"
        market.write_text(json.dumps(public_market(json.loads(market.read_text(encoding="utf-8"))),
                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_public(workspace: pathlib.Path, futures: bool) -> None:
    for name in ("TASK.md", "BACKTEST.md"):
        if FORBIDDEN.search((workspace / name).read_text(encoding="utf-8")):
            raise ValueError("Agent 可见说明含环境控制信息：" + name)
    for name in ("TASK.md", "BACKTEST.md", "run_backtest.py", "backtester/engine.py"):
        source = workspace / name
        if source.is_file() and re.search(r"part_(?:\d+|\*|XX)|segment_\d+|feedback_\d+|分段仿真|正式仿真|逐段|解锁", source.read_text(encoding="utf-8")):
            raise ValueError("Agent 可见文件含环境投放信息：" + name)
    if futures:
        if (workspace / "task_config.json").exists():
            raise ValueError("Agent 工作区含运营侧任务配置")
        # Committed checkpoints exclude data/ by design (restored from the verified
        # package), so verify the market view only when it is present.
        market_path = workspace / "data/market.json"
        if market_path.is_file():
            market = json.loads(market_path.read_text(encoding="utf-8"))
            if {"research_fraction", "segments", "data_status", "cost_policy", "min_days"} & set(market):
                raise ValueError("Agent 可见市场配置含环境控制字段")
            visible = workspace / "data/history"
            if visible.is_dir():
                if list((workspace / "data").glob("part_*")) or not (visible / "quotes.csv").is_file():
                    raise ValueError("Agent 行情视图存在分片或缺少当前行情")
    else:
        daily, minute = workspace / "data/daily", workspace / "data/minute"
        if (daily / "history.csv").is_file():
            if (list(daily.glob("part_*")) or list(minute.glob("part_*")) or
                    not (minute / "history.csv.gz").is_file()):
                raise ValueError("Agent 行情视图存在分片或缺少当前行情")
    reports = workspace / "reports"
    if reports.exists():
        if list(reports.glob("segment_*")):
            raise ValueError("Agent 工作区含原始分段报告")
        for report in reports.glob("*.md"):
            if FORBIDDEN.search(report.read_text(encoding="utf-8")):
                raise ValueError("Agent 可见报告含环境控制信息")
        if any(re.search(r"(?:segment|feedback)_\d+", p.name) for p in reports.iterdir()):
            raise ValueError("Agent 可见报告文件名含环境轮次")


def publish_feedback(workspace: pathlib.Path, eval_run: pathlib.Path, number: int) -> None:
    """Replace operator-named report files with a model-facing feedback packet."""
    reports = workspace / "reports"
    source = reports / f"segment_{number}.md"
    raw = source.read_text(encoding="utf-8")
    cleaned = re.sub(r"(?m)^# .*\n", "# 策略运行反馈\n", raw, count=1)
    cleaned = re.sub(r"第\s*\d+\s*段", "本次", cleaned)
    cleaned = cleaned.replace("本段", "本次").replace("仿真以来累计", "累计")
    cleaned = re.sub(r"segment_\d+", "feedback", cleaned)
    cleaned = cleaned.replace("已解锁", "当前可用")
    if FORBIDDEN.search(cleaned):
        raise ValueError("公开评测报告仍包含环境控制说明")
    target = reports / "feedback.md"
    if target.is_file():
        history = reports / "history.md"
        with history.open("a", encoding="utf-8") as handle:
            handle.write("\n\n---\n\n" + target.read_text(encoding="utf-8"))
    target.write_text(cleaned, encoding="utf-8")
    source.unlink()
    for suffix in ("_equity.csv", "_trades.csv"):
        (reports / f"segment_{number}{suffix}").rename(reports / f"feedback{suffix}")


def review_prompt(workspace: pathlib.Path, eval_run: pathlib.Path, number: int) -> str:
    log = json.loads((eval_run / "log.json").read_text(encoding="utf-8"))
    if len(log) != number or log[-1]["segment"] != number:
        raise ValueError("评测结果与复盘轮次不一致")
    metrics = log[-1]["metrics"]
    errors = log[-1].get("errors", log[-1].get("num_errors", 0))
    error_count = len(errors) if isinstance(errors, list) else int(errors)
    report = "reports/feedback.md"
    if not (workspace / report).is_file():
        raise ValueError("公开评测报告缺失")
    display = "\n".join(f"- {key}: {value}" for key, value in metrics.items()
                         if isinstance(value, (int, float, str)) and not isinstance(value, bool))
    return ("# 策略运行反馈\n\n你上次交付的策略已有新的运行结果。"
            "以下是策略运行结果，不是你自行调用工具得到的研发回测结果：\n"
            + display + f"\n- 策略运行错误次数: {error_count}\n\n"
            + f"详细报告和交易明细见 {report} 及同名 CSV 文件。"
            "请结合当前提供的资料复盘，自主决定保留原策略、仅调整参数或修改逻辑。"
            "无论如何决定，都在 CHANGELOG.md 追加本次决定及理由；"
            "如果修改 strategy.py，请使用当前提供的行情对最终文件完成一次默认参数研发回测。"
            "完成后回复你的决定和一句话理由。")


def delivery_feedback(state: dict, quota: dict, initial: bool, closing: bool) -> str:
    missing = []
    if not state["strategy_exists"]:
        missing.append("strategy.py")
    if initial and not state["research_ready"]:
        missing.append("非空 RESEARCH.md")
    if not state["changelog_appended"]:
        missing.append("追加 CHANGELOG.md")
    if (initial or state["strategy_changed"]) and not state["full_default_backtest_matches"]:
        missing.append("当前策略的完整默认参数验证")
    mode = closing or quota["exploration_remaining"] == 0
    status = ("已满足交付要求，可以立即回复交付；是否继续改进由你决定。" if state["ready"] else
              "尚缺：" + "、".join(missing) + "。")
    return ("交付状态：" + status +
            f"评估已用 {quota['used']}/{quota['total']} 组；探索还可用 {quota['exploration_remaining']} 组；"
            f"全部验证还可用 {quota['remaining']} 组（包含预留，不是额外额度）。" +
            ("当前为收尾模式：停止新增研究和扫描，仅整理文件、必要修正及完整默认参数验证。" if mode else "") +
            ("验证额度为零，不再修改策略。" if quota["remaining"] == 0 else ""))


def reminder(call_number: int, max_calls: int, initial: bool, missing: list[str]) -> str:
    items = "、".join(missing) if missing else "最后交付回复"
    action = ("请从已有研究中自行选定当前最可信的策略版本，并完成交付。" if initial else
              "请自行决定保留策略、调参或修改逻辑，并完成本轮记录。")
    grace = ("正常回复预算已用完；后续容错只用于收尾交付。" if call_number >= max_calls else "")
    return (f"交付进度提醒：本轮已用 {call_number}/{max_calls} 次正常模型回复，"
            f"剩余 {max_calls - call_number} 次。请核对并完成：{items}。"
            + grace + action + "研究方向与策略决定仍由你自主确定；完成后请给出不带工具调用的交付回复。")


def time_reminder(elapsed_seconds: float, hard_seconds: float, initial: bool,
                  urgent: bool = False) -> str:
    """Model-facing wall-clock reminder without exposing the evaluation protocol."""
    elapsed_minutes = max(1, math.ceil(elapsed_seconds / 60))
    remaining_minutes = max(0, math.floor((hard_seconds - elapsed_seconds) / 60))
    action = ("请停止扩展搜索，从已有结果中选定当前最可信的策略，立即补齐策略、研究记录、"
              "变更记录和最终默认参数研发回测。" if initial else
              "请停止扩展搜索，立即在保留原策略、调参或修改逻辑中作出决定，补齐变更记录；"
              "如果修改策略，只完成必要的最终默认参数研发回测。")
    level = "紧急" if urgent else "时间"
    return (f"交付{level}提醒：本轮已持续约 {elapsed_minutes} 分钟，"
            f"可用时间约剩 {remaining_minutes} 分钟。{action}"
            "不要再进行大规模参数扫描或重复研究；请尽快给出不带工具调用的交付回复。")


def finalization_reminder(initial: bool) -> str:
    action = ("请基于当前已有文件和结果立即完成最后交付；不得再调用工具。" if initial else
              "请立即确认保留原策略、调参或修改逻辑，并完成最后交付；不得再调用工具。")
    return ("交付最终提醒：本轮研究时间已经用完。" + action +
            "若当前方案并非最优，也请提交此刻可验证的最好方案，并如实说明风险。")
