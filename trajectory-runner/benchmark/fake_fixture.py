"""Offline interface fixture. It makes no investment claim and no API call."""
from __future__ import annotations

import io
import pathlib
import tarfile


def make_client(package: pathlib.Path):
    archive = package / "agent_workspace.tar.gz"
    with tarfile.open(archive, "r:gz") as tar:
        examples = [m for m in tar.getmembers() if m.isfile() and m.name.endswith("/strategy_example.py")]
        if len(examples) != 1:
            raise RuntimeError("假模型缺少唯一的公开格式示例")
        example = tar.extractfile(examples[0]).read().decode("utf-8")

    class FakeClient:
        def __init__(self, cfg):
            self.turn = cfg.get("_usage_turn", "initial")
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1 and self.turn == "initial":
                actions = [
                    ("write_file", {"path": "strategy.py", "content": example}),
                    ("write_file", {"path": "RESEARCH.md", "content": "离线接口测试：使用题包公开的格式示例；不代表投资策略。"}),
                    ("append_changelog", {"content": "## 离线初始化\n- 决策：使用公开格式示例验证接口。"}),
                    ("run_backtest", {}),
                ]
                return {"content": "", "tool_calls": [
                    {"id": f"init-{i}", "name": name, "arguments": args}
                    for i, (name, args) in enumerate(actions)]}
            if self.calls == 1:
                return {"content": "", "tool_calls": [{"id": "review-log", "name": "append_changelog",
                         "arguments": {"content": f"## {self.turn} 离线接口测试\n- 决策：不改。"}}]}
            return {"content": "离线接口测试完成", "tool_calls": []}

    return FakeClient
