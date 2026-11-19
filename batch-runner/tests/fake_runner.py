"""测试用的假轨迹入口：模拟 finlab_v2.py start 的命令行、产物和退出码，不调用任何模型。

STOCK 成功；FUTURES 首次尝试失败、重试成功；FUND 总是失败。
环境变量 FAKE_MODE=hang 时一直挂起（测试超时），FAKE_SLEEP 控制运行时长。
"""
import json
import os
import pathlib
import sys
import time


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def main():
    args = sys.argv[1:]
    assert args[0] == "start" and args[1] == "--config", args
    cfg = json.loads(pathlib.Path(args[2]).read_text(encoding="utf-8"))
    run_id, runs_root = cfg["run_id"], pathlib.Path(cfg["runs_root"])
    root = runs_root / run_id
    root.mkdir(parents=True, exist_ok=True)
    events = runs_root / "_events.log"
    with events.open("a", encoding="utf-8") as handle:
        handle.write(f"start {run_id} {time.time()}\n")
    if os.environ.get("FAKE_MODE") == "hang":
        time.sleep(120)
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0.1")))
    asset = cfg["task"].get("asset")
    fail = asset == "FUND" or (asset == "FUTURES" and "-r" not in run_id)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(f"end {run_id} {time.time()}\n")
    if fail:
        save(root / "state.json", {"status": "failed", "segments": 2, "synthetic": True, "last_error": "RuntimeError"})
        print("FAILED: BOOM", file=sys.stderr)
        raise SystemExit(1)
    save(root / "stages" / "00.json", {"usage": [{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}] * 2})
    save(root / "state.json", {"status": "completed", "segments": 7, "synthetic": True})
    save(root / "summary.json", {"final_continuous_account_metrics": {
        "total_return": 0.0635, "excess_return": 0.0545, "max_drawdown": 0.0, "final_equity": 1063500.0}})
    print(json.dumps({"status": "completed", "segments": 7, "synthetic": True}))


if __name__ == "__main__":
    main()
