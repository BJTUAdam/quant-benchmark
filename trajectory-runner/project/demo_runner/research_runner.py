"""Trusted entry point for research inside the existing read-only sandbox."""
import pathlib
import sys

sys.path.insert(0, "/workspace")
if pathlib.Path("/workspace/backtester/engine.py").is_file():
    import backtester
    from backtester import engine
    if hasattr(engine, "load_bars"):
        original_load_bars = engine.load_bars

        def load_visible_bars(data_dir="data", use_cache=True, max_part=None):
            return original_load_bars(data_dir, use_cache=False, max_part=max_part)

        # Both public Python import styles must work in a read-only workspace.
        engine.load_bars = load_visible_bars
        backtester.load_bars = load_visible_bars

    def strategy_evaluation_blocked(*args, **kwargs):
        raise RuntimeError("run_research 不执行策略回测；请使用受计量的 run_backtest/sweep")

    # A model must not bypass the host-side evaluation budget by importing the
    # workspace backtester from arbitrary research Python.
    for module in (backtester, engine):
        for name in ("load_strategy", "backtest", "run"):
            if hasattr(module, name):
                setattr(module, name, strategy_evaluation_blocked)

code = sys.argv[1]
sys.argv = ["<research>"]
exec(compile(code, "<research>", "exec"), {"__name__": "__main__"})
