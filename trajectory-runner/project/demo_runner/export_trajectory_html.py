"""Export committed transcripts as a standalone, escaped HTML timeline; no API calls."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import pathlib
import re
import tempfile


def display(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    # Exported views must never render source text as HTML or expose a recognizable key.
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    return html.escape(text)


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("检查点路径越界")
    return path


def message_blocks(messages, user_events, calls):
    body = []
    actor_count = 0
    tool_names = {}
    event_labels = {"initial": "发布任务", "review": "发起复盘", "delivery": "补充交付",
                    "candidate": "复核候选方案", "tool_error": "处理工具错误"}
    for index, message in enumerate(messages):
        role = message.get("role", "unknown")
        label = {"system": "任务规则", "user": "任务／环境反馈", "assistant": "被测 Agent", "tool": "工具返回"}.get(role, role)
        record = user_events.get(index)
        if record:
            event = record["context"]["event"]
            label = "UserAgent · " + event_labels.get(event, event)
        if role == "assistant":
            actor_count += 1
            label += f" · 第 {actor_count} 次回复"
        if role == "tool" and message.get("tool_call_id") in tool_names:
            label += " · " + tool_names[message["tool_call_id"]]
        content = message.get("content") or ""
        preview = " ".join(content.split()) if role in {"assistant", "user"} else ""
        # Keep complete tool arguments/results behind expanders for detailed review.
        if message.get("tool_calls"):
            names = []
            for call in message["tool_calls"]:
                name = call.get("name") or (call.get("function") or {}).get("name")
                if isinstance(name, str):
                    tool_names[call.get("id")] = name
                    names.append(name)
            if names:
                label += " · " + "、".join(dict.fromkeys(names))
            content += "\n\n工具调用\n" + json.dumps(message["tool_calls"], ensure_ascii=False, indent=2)
        if role == "assistant" and actor_count in calls:
            content += "\n\n执行记录\n" + json.dumps(calls[actor_count], ensure_ascii=False, indent=2)
        group = "user-agent" if record else role
        opened = " open" if index <= 1 and role == "user" else ""
        excerpt = f'<span class="excerpt">{display(preview[:160])}{"…" if len(preview) > 160 else ""}</span>' if preview else ""
        body.append(f'<details class="message" data-role="{display(group)}"{opened}>'
                    f'<summary><span class="ordinal">{index + 1:03d}</span>{display(label)}{excerpt}</summary>'
                    f'<pre>{display(content)}</pre></details>')
    return body


def page(root, lead, sections):
    nav = " ".join(f'<a href="#event-{i}">{i}</a>' for i in range(1, len(sections) + 1))
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>长期任务交互轨迹</title>"
            "<style>body{max-width:1100px;margin:32px auto;padding:0 20px;font:16px/1.65 system-ui;background:#f5f7fa;color:#203040}"
            "section{background:white;padding:20px;margin:20px 0;border-radius:12px}summary{cursor:pointer;padding:10px;background:#eef4f6}"
            "details{margin:8px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 monospace;padding:12px}a{margin-right:12px}</style>"
            f"<h1>长期任务交互轨迹</h1><p>运行：{display(root.name)} · {lead}</p>"
            "<p>按顺序查看任务、用户推动、研发回复、工具执行和交付结果。展开条目可查看完整内容。交付检查不代表策略质量评分。</p>"
            f"<nav>{nav}</nav>{''.join(sections)}</html>")


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def series(path):
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {row["timestamp"]: float(row["equity"]) for row in csv.DictReader(stream)
                if row.get("timestamp") and row.get("equity") and math.isfinite(float(row["equity"]))}


def percent(value):
    return f"{value:+.2%}" if isinstance(value, (int, float)) and math.isfinite(value) else "—"


def curve_data(root, final):
    points, segments = [], {}
    last = ""
    for number in range(1, 8):
        folder = root / "workspace" / "reports" / f"segment_{number}"
        equity, benchmark = series(folder / "equity.csv"), series(folder / "benchmark.csv")
        metrics = json_file(folder / "metrics.json")
        if not equity:
            continue
        # Each FinLab CSV is cumulative. Append only dates newly disclosed in this segment.
        added = [(date, equity[date], benchmark.get(date), number) for date in sorted(equity) if date > last]
        if not added:
            continue
        previous = points[-1][1] if points else None
        points.extend(added)
        last = added[-1][0]
        segments[number] = {"start": added[0][0][:10], "end": last[:10], "days": len(added),
                            "previous": previous, "equity": added[-1][1], "metrics": metrics}
    ret, value = final.get("total_return"), final.get("final_equity")
    base = value / (1 + ret) if isinstance(ret, (int, float)) and ret > -1 and isinstance(value, (int, float)) else None
    if not base and segments:
        metrics = list(segments.values())[-1]["metrics"]
        ret, value = metrics.get("total_return"), metrics.get("final_equity")
        if isinstance(ret, (int, float)) and ret > -1 and isinstance(value, (int, float)):
            base = value / (1 + ret)
    if base and math.isfinite(base) and base > 0:
        for segment in segments.values():
            previous = segment.pop("previous") or base
            segment["return"] = segment["equity"] / previous - 1 if previous else None
        points = [[date[:10], 100 * (equity / base - 1),
                   100 * (bench / base - 1) if bench is not None else None, number]
                  for date, equity, bench, number in points]
    else:
        points = []
    return points, segments


def build_stages(root, stages):
    summary = json_file(root / "summary.json")
    state = json_file(root / "state.json")
    final = summary.get("final_continuous_account_metrics") or {}
    points, segments = curve_data(root, final)
    if not final and segments:
        final = list(segments.values())[-1]["metrics"]
    mode = {True: "合成行情", False: "真实行情"}.get(state.get("synthetic"), "行情类型未记录")
    statuses = {"delivered": "已交付", "running": "运行中", "failed": "失败"}
    stage_map = {int(path.stem) + 1: json_file(path) for path in stages}
    sections = []
    buttons = []
    for number in range(1, 8):
        stage, segment = stage_map.get(number), segments.get(number, {})
        result = percent(segment.get("return")) if segment else "待评测"
        buttons.append(f'<button class="stage" data-segment="{number}" type="button">'
                       f'<span>第 {number} 段</span><strong>{result}</strong></button>')
        status = statuses.get(stage.get("status"), "状态未记录") if stage else "尚未开始"
        user = (stage or {}).get("user_agent") or {}
        user_events = {r["message_index"]: r for r in user.get("events", [])}
        messages = (stage or {}).get("messages", [])
        stats = f'{status} · 研发 {(stage or {}).get("calls", 0)} 次 · UserAgent {user.get("model_calls", 0)} 次'
        period = (f'{segment["start"]} 至 {segment["end"]} · {segment["days"]} 个交易日'
                  if segment else "尚无本段评测数据")
        body = "".join(message_blocks(messages, user_events, {})) or "<p>尚无交互记录。</p>"
        sections.append(f'<section class="trace" data-trace="{number}" hidden>'
                        f'<h3>第 {number} 段 · {("策略研发" if number == 1 else "复盘与策略交付")}</h3>'
                        f'<p class="muted">{stats} · {period}</p>{body}</section>')
    kpis = [("累计收益", percent(final.get("total_return"))), ("超额收益", percent(final.get("excess_return"))),
            ("最大回撤", percent(final.get("max_drawdown"))), ("完成评测", f'{len(segments)} / 7')]
    values = {"NAME": display(root.name), "MODE": mode, "STAGES": "".join(buttons), "TRACES": "".join(sections),
              "KPI": "".join(f'<div><span>{label}</span><strong>{value}</strong></div>' for label, value in kpis),
              "POINTS": json.dumps(points, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")}
    template = pathlib.Path(__file__).with_name("trajectory_v2.html").read_text(encoding="utf-8")
    return re.sub(r"@@([A-Z]+)@@", lambda match: values[match[1]], template)


def build(run_root):
    root = pathlib.Path(run_root).resolve()
    stages = sorted((root / "stages").glob("*.json"))
    if stages:
        return build_stages(root, stages)
    events = sorted((root / "events").glob("*.json"))
    if not events:
        raise ValueError("运行目录没有已提交的事件")
    sections = []
    for number, path in enumerate(events, 1):
        event = json.loads(path.read_text(encoding="utf-8"))
        checkpoint = inside(root, event["checkpoint"])
        transcript_path = checkpoint / "transcript.json"
        phase = event.get("phase", "unknown")
        title = display(phase)
        body = []
        if transcript_path.is_file():
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            user_events = {r["message_index"]: r for r in transcript.get("user_agent", {}).get("events", [])}
            calls = {c["call"]: c for c in transcript.get("call_history", [])}
            body.append("<p>交付来源：" + display(transcript.get("delivery", transcript.get("status"))) + "</p>")
            for key, label in (("delivery_review", "交付检查、语义审阅与耗时"), ("fallback", "程序回退记录"),
                               ("memory_input", "本轮继承的历史记忆"), ("research_evidence", "研究实验与工具证据")):
                if key in transcript:
                    body.append(f"<details><summary>{label}</summary><pre>{display(transcript[key])}</pre></details>")
            body.extend(message_blocks(transcript.get("messages", []), user_events, calls))
        else:
            body.append("<p>环境推进／行情运行检查点，未调用研发 Agent。</p>")
        sections.append(f'<section id="event-{number}"><h2>{number}. {title}</h2>{"".join(body)}</section>')
    return page(root, f"已提交事件：{len(events)}", sections)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    output = args.output or args.run_root / "trajectory.html"
    content = build(args.run_root)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        scratch = pathlib.Path(stream.name)
        stream.write(content)
    try:
        scratch.replace(output)
    finally:
        scratch.unlink(missing_ok=True)
    print(output.resolve())


if __name__ == "__main__":
    main()
