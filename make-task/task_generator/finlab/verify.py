"""Actual Docker acceptance run using generated synthetic fixtures only."""
from __future__ import annotations
import csv
from pathlib import Path
import uuid
from .common import ROOT, LabError, read, write, textfile, now
from .data import ingest, freeze, db_open
from contextlib import closing
import json
from .demo import build
from .temporal import DEFAULT_YEAR_OFFSET
from .runtime import create_episode, run, status, load_episode, EpisodeResearchView
from .disclosure import read_audit


def verify(output=None, *, resources):
    from .setup import validate_resources
    resources = validate_resources(resources)
    root = Path(output or ROOT / "work_v2" / ("acceptance_" + uuid.uuid4().hex[:8])).resolve()
    results = []
    for asset in ("STOCK", "FUND", "FUTURES"):
        p = root / asset.lower()
        build(p, asset, resources=resources)
        create_episode(read(p / "binding.private.json"), p / "episode")
        research = run(p / "episode", p / "submission", research=True)
        for n in range(1, 8):
            if n == 3:
                with (p / "submission/strategy.py").open("a", encoding="utf-8") as f:
                    f.write("\n# Synthetic code-version change; JSON memory remains compatible.\n")
            r = run(p / "episode", p / "submission")
            assert r["segment"] == n
            print(asset, "segment", n, "committed", flush=True)
        _, binding, state, _ = load_episode(p / "episode")
        cp = state["checkpoint"]
        memory = read(p / "episode" / cp["memory_file"])
        account = read(p / "episode" / cp["account_file"])
        expected = 1 if asset == "FUTURES" else 100
        assert list(account["positions"].values()) == [float(expected)], account
        assert memory["bars_seen"] == 28, memory
        public = Path(r["public_results"])
        with (public / "equity.csv").open(encoding="utf-8", newline="") as stream: equity = list(csv.DictReader(stream))
        assert len(equity) >= 28
        virtual_year = 2024 + DEFAULT_YEAR_OFFSET
        assert all(row["timestamp"].startswith(f"{virtual_year:04d}-") for row in equity)
        assert len({c["images"]["engine"] for c in state["commits"]}) == 1
        metrics = read(public / "metrics.json")
        assert "benchmark_total_return" in metrics and (public / "benchmark.csv").is_file()
        with (public / "trades.csv").open(encoding="utf-8",newline="") as stream: trades=list(csv.DictReader(stream))
        assert trades and all(float(t["commission"])>0 for t in trades),trades
        results.append({"asset": asset, "research": "passed", "formal_segments": 7, "memory_bars": memory["bars_seen"],
                        "continuous_position": expected, "equity_points": len(equity), "code_change": "passed",
                        "container_recreation": "passed", "frozen_index_benchmark": "passed", "fees_deducted":"passed", "synthetic": True,
                        "virtual_year": virtual_year, "future_virtual_dates": "passed"})
    # A GTC order is deliberately larger than per-bar liquidity. One opening
    # boundary is blocked, then partial fills must survive a new container.
    p = root / "partial"
    build(p, "STOCK", resources=resources)
    binding = read(p / "binding.private.json")
    binding["runtime"]["volume_limit_pct"] = .00015
    binding["runtime"]["execution_rules"][13]["up_limit"] = 10.65
    create_episode(binding, p / "episode")
    textfile(p / "submission/strategy.py", '''from finlab.sdk import Strategy as BaseStrategy
class Strategy(BaseStrategy):
    def on_bars(self,ctx):
        if not ctx.memory.get("sent"):
            ctx.order("000001.SZ",1000,side="buy");ctx.memory["sent"]=True
''')
    run(p / "episode", p / "submission")
    _,_,state,_ = load_episode(p / "episode")
    first = read(p / "episode" / state["checkpoint"]["account_file"])
    assert first["positions"]["000001.SZ"] == 200, first
    assert first["open_orders"][0]["filled_quantity"] == 200
    run(p / "episode", p / "submission")
    _,_,state,_ = load_episode(p / "episode")
    second = read(p / "episode" / state["checkpoint"]["account_file"])
    assert second["positions"]["000001.SZ"] == 600, second
    results.append({"partial_fills": "passed", "limit_boundary_wait": "passed", "open_order_cross_container": "passed",
                    "position_after_segments": [200,600]})
    # Two simultaneous legs share one callback. The fund is unavailable during
    # segment 1, so it fills only after the stock leg and the checkpoint restore.
    p = root / "multi_leg"
    build(p, "STOCK", resources=resources)
    binding = read(p / "binding.private.json")
    with closing(db_open(p / "source.private.sqlite")) as con:
        rows = [json.loads(x[0]) for x in con.execute("select body from records where api='daily' order by event_at")]
    for row in rows:
        row["ts_code"] = "510300.SH"
        row["_available_at"] = row["trade_date"][:4]+'-'+row["trade_date"][4:6]+'-'+row["trade_date"][6:]+'T15:00:00+08:00'
    ingest(p / "source.private.sqlite", "fund_daily", rows, {"timing_basis":"operator_supplied", "key_fields":["ts_code","trade_date"],"event_field":"trade_date"})
    plan = read(p / "frozen/manifest.private.json")
    plan["symbols"].append("510300.SH")
    plan["apis"] = {k: v["binding"] for k,v in plan["apis"].items()}
    plan["apis"]["fund_daily"] = dict(plan["apis"]["daily"])
    freeze(p / "source.private.sqlite",plan,p / "frozen_multi")
    binding["frozen_dir"] = str(p / "frozen_multi")
    instrument = dict(binding["runtime"]["instruments"][0]);instrument.update(symbol="510300.SH",asset_type="FUND",sellable_after_days=0)
    binding["runtime"]["instruments"].append(instrument)
    binding["runtime"]["sessions"]["510300.SH"] = [["09:00","16:00","continuous"]]
    binding["runtime"]["execution_rules"] += [{**r,"symbol":"510300.SH","tradable": i>=16} for i,r in enumerate(binding["runtime"]["execution_rules"][:])]
    binding["feeds"].append({**binding["feeds"][0],"api_name":"fund_daily"})
    create_episode(binding,p / "episode")
    textfile(p / "submission/strategy.py", '''from finlab.sdk import Strategy as BaseStrategy
class Strategy(BaseStrategy):
    def on_bars(self,ctx):
        ctx.memory["slice_size"]=len(ctx.bars)
        if not ctx.memory.get("sent"):
            ctx.order("000001.SZ",100,side="buy");ctx.order("510300.SH",100,side="buy")
            ctx.memory["sent"]=True
''')
    run(p / "episode",p / "submission")
    _,_,s,_ = load_episode(p / "episode")
    first = read(p / "episode" / s["checkpoint"]["account_file"])
    assert first["positions"].get("000001.SZ")==100 and first["positions"].get("510300.SH",0)==0,first
    run(p / "episode",p / "submission")
    _,_,s,_ = load_episode(p / "episode")
    second = read(p / "episode" / s["checkpoint"]["account_file"])
    memory = read(p / "episode" / s["checkpoint"]["memory_file"])
    assert second["positions"]["510300.SH"]==100 and memory["slice_size"]==2,(second,memory)
    results.append({"synchronous_multi_symbol_slice":"passed","per_leg_non_atomic_fills":"passed"})
    for asset in ("STOCK", "FUND"):
        p = root / ("intraday_" + asset.lower())
        build(p,asset, resources=resources)
        binding = read(p / "binding.private.json")
        symbol = binding["runtime"]["instruments"][0]["symbol"]
        daily_api = "daily" if asset=="STOCK" else "fund_daily"
        minute_api = "stk_mins" if asset=="STOCK" else "etf_mins"
        with closing(db_open(p / "source.private.sqlite")) as con:
            daily = [json.loads(x[0]) for x in con.execute("select body from records where api=? order by event_at",(daily_api,))]
        minutes = []
        for row in daily:
            s = row["trade_date"];day=s[:4]+'-'+s[4:6]+'-'+s[6:]
            for m in (31,32,33):
                at=f'{day} 09:{m}:00'
                minutes.append({"ts_code":symbol,"trade_time":at,**{k:row[k] for k in ('open','high','low','close','vol')},
                                "_available_at":at.replace(' ','T')+'+08:00'})
        ingest(p / "source.private.sqlite",minute_api,minutes,
               {"timing_basis":"operator_supplied","key_fields":["ts_code","trade_time"],"event_field":"trade_time"},
               partition={"api_name":minute_api,"params":{"freq":"1min","ts_code":symbol}})
        old=read(p / "frozen/manifest.private.json")
        plan={"synthetic":True,"symbols":[symbol],"real_start":old["real_start"],"real_stop":old["real_stop"],
              "apis":{minute_api:{"fields":[k for k in minutes[0] if not k.startswith('_')],"coverage_confirmed":True,"replayability":"point_in_time"}}}
        freeze(p / "source.private.sqlite",plan,p / "frozen_minutes")
        binding["frozen_dir"]=str(p / "frozen_minutes")
        binding["runtime"].update(frequency="minute",benchmark=None)
        binding["feeds"]=[{"api_name":minute_api,"event_field":"trade_time","volume_multiplier":100,"amount_multiplier":1000}]
        create_episode(binding,p / "episode")
        textfile(p / "submission/strategy.py",f'''from finlab.sdk import Strategy as BaseStrategy
class Strategy(BaseStrategy):
    def on_bars(self,ctx):
        symbol={symbol!r}
        if not ctx.memory.get("bought"):
            ctx.order(symbol,100,side="buy");ctx.memory["bought"]=True
        elif ctx.portfolio["positions"].get(symbol,0)>0:
            available=ctx.portfolio["available_positions"].get(symbol,0)
            ctx.memory.setdefault("first_sellable",available)
            if available>=100 and not ctx.memory.get("sold"):
                ctx.order(symbol,100,side="sell");ctx.memory["sold"]=True
''')
        result=run(p / "episode",p / "submission",research=True)
        memory=read(Path(result["public_results"]).parent / "memory.json")
        account=read(Path(result["public_results"]).parent / "account.private.json")
        assert memory["first_sellable"]==(0 if asset=="STOCK" else 100),memory
        assert account["positions"].get(symbol,0)==0 and memory["sold"],(account,memory)
        with (Path(result["public_results"])/"trades.csv").open(encoding="utf-8",newline="") as stream: trades=list(csv.DictReader(stream))
        assert len(trades)==2 and trades[0]["timestamp"][11:16]=="09:32",trades
        assert (trades[0]["timestamp"][:10] != trades[1]["timestamp"][:10]) == (asset=="STOCK"),trades
        expected_cash=1000000-sum((1 if t["side"]=="buy" else -1)*float(t["quantity"])*float(t["price"])+float(t["commission"]) for t in trades)
        assert abs(account["cash"]-expected_cash)<.00001,(account,expected_cash)
        results.append({"intraday_asset":asset,"sellable_rule":"T+1" if asset=="STOCK" else "T+0","actual_docker_test":"passed"})
    # Malicious code can bypass static import screening. Docker confinement and
    # the scoped service, not AST screening, form the tested boundary.
    p = root / "isolation"
    build(p, "STOCK", resources=resources); create_episode(read(p / "binding.private.json"), p / "episode")
    researcher = EpisodeResearchView(p / "episode")
    private_binding = read(p / "episode/binding.private.json")
    first_formal = researcher.view.mapping.shift(private_binding["windows"][1]["start"], "date").replace("-", "")
    try:
        researcher.query("daily", {"trade_date": first_formal}, "")
        raise AssertionError("uncommitted segment was disclosed")
    except LabError as exc:
        assert exc.code == "TIME_RANGE_EXCEEDED" and exc.request_id and "超出" in exc.message, exc
    source = '''from finlab.sdk import Strategy as BaseStrategy
class Strategy(BaseStrategy):
    def initialize(self, ctx):
        path = __import__("os").path
        ctx.memory["private_paths_absent"] = all(not path.exists(p) for p in ["/frozen", "/input", "/out", "/var/run/docker.sock", "/opt/finlab/finlab/engine.py", "/opt/finlab/finlab/temporal.py", "/opt/finlab/finlab/runtime.py"])
        try:
            __import__("socket").getaddrinfo("example.com",80)
            ctx.memory["dns_egress_blocked"]=False
        except OSError:ctx.memory["dns_egress_blocked"]=True
        sock = __import__("socket").socket(); sock.settimeout(1)
        try:
            sock.connect(("1.1.1.1", 80)); ctx.memory["egress_blocked"] = False
        except OSError: ctx.memory["egress_blocked"] = True
        finally: sock.close()
    def on_bars(self, ctx):
        try:
            ctx.query("daily", trade_date=str(int(ctx.now[:4]) + 1) + "0101")
            ctx.memory["future_blocked"] = False
        except ValueError as exc:
            ctx.memory["future_blocked"] = True
            ctx.memory["future_error_code"] = exc.code
            ctx.memory["future_error_message"] = exc.message
            ctx.memory["future_request_id"] = exc.request_id
        frame = ctx.query("daily", limit=10000)
        ctx.memory["omitted_dates_clock_scoped"] = all(str(day) <= ctx.now[:10].replace("-", "") for day in frame["trade_date"])
'''
    textfile(p / "submission/strategy.py", source)
    result = run(p / "episode", p / "submission")
    memory = read(Path(result["public_results"]).parent / "memory.json")
    assert all(memory[k] for k in ["private_paths_absent", "egress_blocked", "future_blocked", "dns_egress_blocked"]), memory
    assert memory["future_error_code"] == "TIME_RANGE_EXCEEDED" and "超出" in memory["future_error_message"]
    assert memory["omitted_dates_clock_scoped"] is True
    audit_path = Path(result["public_results"]).parent / "bridge_audit.private.sqlite"
    events = read_audit(audit_path)["events"]
    assert any(e["request_id"] == memory["future_request_id"] and e["error_code"] == "TIME_RANGE_EXCEEDED" for e in events)
    assert all(e["stage"] == 1 and e["job_id"] == result["job_id"] for e in events)
    assert all(e["submission_hash"] for e in events)
    assert researcher.query("daily", {"trade_date": first_formal}, "")["data"]["items"]
    before = read(p / "episode/state.private.json")
    textfile(p / "submission/strategy.py", 'from finlab.sdk import Strategy as BaseStrategy\nclass Strategy(BaseStrategy):\n    def on_bars(self,ctx): raise RuntimeError("synthetic failure")\n')
    try:
        run(p / "episode", p / "submission")
        raise AssertionError("expected callback failure")
    except LabError:
        assert read(p / "episode/state.private.json") == before
    textfile(p / "submission/strategy.py", source)
    retry = run(p / "episode", p / "submission")
    assert retry["segment"] == 2
    _,_,saved,_ = load_episode(p / "episode")
    checkpoint = p / "episode" / saved["checkpoint"]["file"]
    original = checkpoint.read_bytes()
    checkpoint.write_bytes(original+b'synthetic tamper')
    try:
        try: run(p / "episode",p / "submission")
        except LabError as exc: assert exc.code=="CHECKPOINT_CHANGED",exc
        else: raise AssertionError("tampered checkpoint accepted")
        assert read(p / "episode/state.private.json")==saved
    finally: checkpoint.write_bytes(original)
    from .archive import archive_episode
    exported = archive_episode(p / "episode",root / "synthetic_results.zip")
    import zipfile
    with zipfile.ZipFile(exported["archive"]) as public_archive:
        assert not any("audit" in n or "disclosure.private" in n or n.endswith(".sqlite") for n in public_archive.namelist())
    results.append({"isolation": memory, "callback_failure_no_commit": "passed", "retry_segment": 2,
                    "checkpoint_tamper_blocked":"passed","public_archive":"passed",
                    "disclosure_403_sdk_details": "passed", "container_audit_persisted": "passed",
                    "audit_request_id_correlated": "passed", "current_event_clock": "passed",
                    "uncommitted_segment_blocked": "passed", "committed_segment_released": "passed",
                    "private_audit_not_exported": "passed"})
    report = {"schema_version": "finlab.acceptance.v2", "checked_at": now(), "status": "passed", "synthetic_only": True,
              "release": "v2-delivery-20261110", "image_ids": read(ROOT / "deployment/images.lock.json")["images"],
              "tushare_live_tested": False, "results": results,
              "private_evidence": "retained_on_operator_host_not_distributed",
              "resource_budget": resources, "resource_scope": "explicit_synthetic_acceptance_only_not_operator_setup",
              "limits": ["本次Docker合成验收未使用真实Tushare；自建服务样本见upstream_probe_v2.json", "未声明所有接口账号权限或所有返回格式均已实测",
                         "当前支持按成交额费率；非零按手费用、平今差异费率与动态保证金需扩展，不能静默按0处理",
                         "期货结算价用于逐日估值；AKQuant保证金账户仍保留现金/浮盈结构，不模拟交易所逐日现金划转",
                         "交易规则为绑定的实验规则；真实历史规则缺口须补齐后才能启动正式任务"]}
    write(ROOT / "docs" / "acceptance_v2.json", report)
    return report


if __name__ == "__main__":
    from .setup import load_settings
    print(verify(resources=load_settings()["resources"]))
