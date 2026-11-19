"""Native v2 task contracts and the public allowlist."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from .common import LabError, textfile, write
from .disclosure import PUBLIC_DISCLOSURE

PUBLIC_FILES = {"TASK.md", "DATA_API.md", "bridge_contract.json", "runtime_profile.json", "SUBMISSION.md",
                "strategy.py", "backtest.py", "params.json", "RESEARCH.md", "CHANGELOG.md", "task_input.draft.json"}
SUBMISSION_FILES = {"strategy.py", "backtest.py", "params.json", "RESEARCH.md", "CHANGELOG.md"}
VIRTUAL_DATE_NOTICE = "全部日期为虚拟日期，仅标识数据顺序与可见时点；年份不对应真实历史年份，不得据此预设股灾、政策、节假日或未来事件。"


def draft_contracts(environment_id, requirements):
    """Build new v2 defaults from the selected current environment, never migrate a task."""
    if environment_id not in {"akquant_stock_etf_v2", "akquant_futures_v2"}:
        raise LabError("V2_ENVIRONMENT_REQUIRED")
    futures = environment_id == "akquant_futures_v2"
    endpoints = ([("fut_daily", ["ts_code", "trade_date", "open", "high", "low", "close", "settle", "vol", "oi"]),
                  ("fut_basic", ["ts_code", "name", "list_date", "delist_date"])] if futures else
                 [("daily", ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]),
                  ("adj_factor", ["ts_code", "trade_date", "adj_factor"]),
                  ("stk_limit", ["ts_code", "trade_date", "up_limit", "down_limit"]),
                  ("trade_cal", ["exchange", "cal_date", "is_open", "pretrade_date"]),
                  ("index_daily", ["ts_code", "trade_date", "close"])])
    return {
        "bridge_contract": {
            "schema_version": "finlab.bridge_contract.v2", "identity_policy": "real_names_and_codes",
            "date_domain": "virtual", "calendar_semantics": "virtual_sequence_only_not_historical_years",
            "virtual_date_notice": VIRTUAL_DATE_NOTICE,
            "disclosure": copy.deepcopy(PUBLIC_DISCLOSURE),
            "url_env": "MARKET_BRIDGE_URL", "token_env": "MARKET_BRIDGE_TOKEN",
            "virtual_start": None, "virtual_stop": None, "minimum_years": requirements["minimum_years"],
            "symbols": copy.deepcopy(requirements["symbols"]),
            "endpoints": [{"api_name": name, "fields": fields, "frequency": "daily",
                           "visibility": "available_at_lte_controller_clock"} for name, fields in endpoints],
            "binding_status": "not_bound", "dataset_id": None,
            "required_checks": ["point_in_time", "coverage", "calendar", "units", "corporate_actions_or_settlement", "trading_rules"]},
        "runtime_profile": {
            "schema_version": "finlab.runtime.v2", "engine": "akquant", "engine_version": "0.3.76",
            "strategy_api": "finlab.strategy.v2", "asset_types": ["FUTURES"] if futures else ["STOCK", "FUND"],
            "index_role": "query_and_benchmark_only", "frequency": "daily", "fill_policy": "NextOpen",
            "initial_cash": 1000000, "currency": "CNY", "formal_segments": 7,
            "multi_leg_execution": "per_leg_partial_fills_possible", "state_continuity": ["cash", "positions", "open_orders", "memory"],
            "resources": None, "resource_status": "requires_human_setup",
            "resource_recommendations": {"cpus": 2, "memory_mb": 4096},
            "market_rules_status": "requires_binding", "instruments": None, "benchmark": None},
        "submission_contract": {
            "schema_version": "finlab.submission.v2", "required_files": sorted(SUBMISSION_FILES),
            "strategy_api": "finlab.strategy.v2", "dependencies": ["finlab", "pandas", "numpy"],
            "results_generated_by": "platform"},
        "protocol": {"research_fraction": 0.30, "formal_segments": 7,
                     "release_rule": "completed_segment_only_after_commit", "maintenance": ["hold", "parameters", "logic"],
                     "boundaries": None}}


def validate(spec, root=None):
    from taskgen.validation import shape_errors
    from .common import ROOT, read
    root = Path(root or ROOT)
    errors = shape_errors(spec, read(root / "schemas/task_spec.v2.schema.json"))
    if errors:
        return errors
    if "migration" in spec:
        errors.append("仅接收原生v2规格；不接受旧任务迁移字段")
    contract = spec["bridge_contract"]
    if "disclosure" in contract and contract["disclosure"] != PUBLIC_DISCLOSURE:
        errors.append("阶段披露规则必须由平台控制，越界整次拒绝并私有审计")
    if contract["minimum_years"] != spec["data_requirements"]["minimum_years"] or contract["symbols"] != spec["data_requirements"]["symbols"]:
        errors.append("桥接需求与运营方数据范围不一致")
    names = []
    from .catalog import endpoint
    for item in contract["endpoints"]:
        names.append(item["api_name"])
        try:
            ep = endpoint(item["api_name"])
            if set(item["fields"]) - set(ep["fields"]):
                errors.append("接口字段未登记：" + item["api_name"])
        except (LabError, KeyError):
            errors.append("接口未登记：" + item["api_name"])
    if len(names) != len(set(names)):
        errors.append("同一桥接接口需合并字段，不得重复声明")
    if set(spec["submission_contract"]["required_files"]) != SUBMISSION_FILES:
        errors.append("提交物不完整")
    expected = draft_contracts(spec["environment_id"], spec["data_requirements"])
    # These arrays describe membership, not execution order. Keep duplicate and
    # unexpected members invalid while accepting equivalent JSON permutations.
    unordered = {"runtime_profile": ("asset_types", "state_continuity"),
                 "submission_contract": ("required_files", "dependencies"),
                 "protocol": ("maintenance",)}
    for key in ("runtime_profile", "submission_contract", "protocol"):
        actual, target = copy.deepcopy(spec[key]), copy.deepcopy(expected[key])
        for field in unordered[key]:
            if isinstance(actual.get(field), list) and all(isinstance(v, str) for v in actual[field]):
                actual[field] = sorted(actual[field])
            target[field] = sorted(target[field])
        if actual != target:
            different = sorted(k for k in actual.keys() | target.keys() if actual.get(k) != target.get(k))
            errors.append(key + "必须符合当前平台固定运行协议；数据绑定前不得预填规则或分段；不同字段：" + ",".join(different))
    extra = set(spec["data_requirements"]["extra_fields"])
    declared = {field for ep in contract["endpoints"] for field in ep["fields"]}
    if extra - declared:
        errors.append("额外需求字段必须由已声明桥接接口提供：" + ",".join(sorted(extra - declared)))
    return errors


def templates():
    return {
        "strategy.py": '''"""接口演示：仅保持空仓，不构成题目参考答案。"""
from finlab.sdk import Strategy as BaseStrategy

class Strategy(BaseStrategy):
    def initialize(self, ctx):
        ctx.memory.setdefault("observations", 0)

    def on_bars(self, ctx):
        ctx.memory["observations"] += 1
        # ctx.order("真实代码", 100, side="buy")
        # 日期均为虚拟日期。查询格式与任务授权见DATA_API.md。
''',
        "backtest.py": 'from finlab.sdk import launch\n\nif __name__ == "__main__":\n    launch("strategy.py", "params.json")\n',
        "params.json": "{}\n",
        "RESEARCH.md": "# 研究记录\n\n说明研究决策、证据、验证与限制。尚未执行的检查须标明。\n",
        "CHANGELOG.md": "# 维护记录\n\n逐段记录保持、调参或改逻辑的依据及状态兼容。\n"}


def build(root, path, spec, state):
    path = Path(path)
    public, operator = path / "public", path / "operator"
    contract, runtime = copy.deepcopy(spec["bridge_contract"]), spec["runtime_profile"]
    # The enforced service capability is additive. Historical candidate specs
    # and review hashes stay unchanged; every new export describes this gate.
    contract["disclosure"] = copy.deepcopy(PUBLIC_DISCLOSURE)
    modules = "\n".join(f"- {m['description']}；相互影响：{m['interaction']}" for m in spec["research_modules"])
    params = "\n".join("- " + (p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)) for p in spec["parameter_space"])
    textfile(public / "TASK.md", f'''# {spec['title']}

> v2任务规格。规格接纳、数据绑定和运行接入分别记录，当前状态以task_input.draft.json与平台登记记录为准；不要求人工审批。

研究目标：{spec['research_question']}

## 运行协议

先在研发期开发首版，再经历七段逐步揭晓的正式期。每段运行时冻结代码；完成后可以保持、调参或修改逻辑。
现金、持仓、未完成订单和JSON memory连续。修改不重置账户，不覆盖过去结果。全部七段的连续账户用于评价。
实际虚拟时间范围、数据版本和分段边界在绑定后确定。

## 研究决策

{modules}

可研究的参数与结构选择：

{params}

持续维护依据：{spec['maintenance_rationale']}

## 数据与执行

行情通过Tushare协议桥接器查询。使用环境变量MARKET_BRIDGE_URL和MARKET_BRIDGE_TOKEN，详见DATA_API.md。
名称与代码保持真实；全部时间字段使用虚拟时间，仅表示事件顺序和可见时点。
虚拟年份不对应真实历史年份，不得按年份预设股灾、政策、节假日或未来事件，也不得反推实际时期。
交易日以服务返回日历为准，不按虚拟日期的星期猜测；策略只能根据当时已可见数据形成判断。
只能使用当前可见信息，不访问外网。指数仅用于查询与基准，不可下单。
桥接器按平台传入的阶段范围累计披露；正式策略另受当前事件时钟限制。
显式请求任何未披露日期会整次报TIME_RANGE_EXCEEDED并记录，不能将错误解释为空行情。
AKQuant 0.3.76执行撮合；收盘后决策、下一根行情开盘撮合。期货多腿逐腿撮合，可能部分成交，需处理残余敞口。
交易单位、T+0/T+1、费用、公司行为、价格限制、保证金与结算按绑定后的runtime_profile固定。
资源预算尚未选择，必须由本人先运行finlab setup；CPU、内存和时限由本人明确配置，AI不能代填。
任务只提供非生效的CPU/内存建议；单次运行时限由使用者本人明确选择，不预填。
规则未绑定时不能正式运行。原始价格用于撮合，研究复权数据不得重复计入公司行为收益。

## 提交与评价

提交strategy.py、backtest.py、params.json、RESEARCH.md、CHANGELOG.md，遵守SUBMISSION.md。
回测入口必须可执行，策略通过finlab.strategy.v2接口运行。系统生成权益、订单、成交、指标和运行记录。
统一记录扣除成本后的收益、风险和维护过程；评分配置在正式开始前固定，题目不提供答案或预期成绩。
''')
    table = "\n".join(f"|{e['api_name']}|{', '.join(e['fields'])}|{e['frequency']}|" for e in contract["endpoints"])
    textfile(public / "DATA_API.md", f'''# 桥接数据契约

仅从桥接器获取数据，无行情文件包。URL和令牌由运行系统注入；上游URL和Tushare令牌属于运营方。

```python
from finlab import tushare as ts
pro = ts.pro_api()  # 默认DataFrame；dataframe=False可返回列表
# pro.query("daily", ts_code="000001.SZ", end_date="虚拟YYYYMMDD")
```

|接口|允许字段|频率|
|---|---|---|
{table}

字段含义与单位以绑定契约及官方定义为准。Tushare股票/ETF日线vol通常为手、amount为千元；回测输入由平台转成股/元。
期货vol为手，价格按合约报价单位；不能套用股票倍率。分钟接口的单位必须另行确认。
缺失值使用null，不填造价格；停牌、无交易和数据缺失分别处理。
未知字段、未冻结范围和未来查询会返回错误，不能将错误当作零值或无交易。
阶段范围由平台控制器注入，采用[start,stop)，已提交阶段累计可查；正式策略还受当前事件时钟限制。
显式查询中任一时间选择越界，整次HTTP 403，error_code=TIME_RANGE_EXCEEDED；不返回部分行情或data。
错误包含中文msg与request_id，SDK的LabError保留code、message、request_id；应修正查询并在研究记录中保留编号，不能改成空DataFrame或反复试探未来范围。
省略日期仅查当前允许范围，分页和pro_bar同样受限。审计故障返回503 BRIDGE_AUDIT_UNAVAILABLE，不返回行情。
原始审计仅由平台保存；错误与公开结果均不包含真实时间映射、上游凭证或私有路径。
独立日期、时间戳、报告期和已登记时间文本统一映射，全部均为虚拟日期，不是实际历史年份。
不得根据虚拟年份套用已知股灾、政策、节假日或预设事件；只按桥接交易日历与当前可见信息运行。
名称代码及其嵌入年月保留，但不得用它们重建真实时间映射。
''')
    textfile(public / "SUBMISSION.md", '''# 提交协议 finlab.submission.v2

必需：strategy.py、backtest.py、params.json、RESEARCH.md、CHANGELOG.md。
Strategy继承finlab.sdk.Strategy，使用initialize(ctx)、on_bars(ctx)、on_order(ctx,event)、on_trade(ctx,event)、on_finish(ctx)。
ctx.now为虚拟时间；虚拟年份仅是事件索引，不对应真实历史年份，不据此预设股灾、政策或未来事件。
ctx.bars为同时间戳已完成行情切片；ctx.portfolio为当前账户；ctx.params为本轮参数；ctx.memory必须是JSON对象。
ctx.order(symbol,quantity,side="buy"/"sell",limit_price=None,tif="GTC"/"DAY")按股或合约手数下单；ctx.cancel(order_id)撤销未完成订单。
ctx.query通过受限桥接器查询。回调返回前产生的订单依次校验，不能修改平台费用、成交价格或账户。
backtest.py调用finlab.sdk.launch。只能使用预装finlab、pandas、numpy及标准库；如需依赖由运营方构建审核后的新镜像。
资源预算由本人先运行finlab setup明确设置；AI不能代填CPU、内存或时限。未配置时不能启动正式运行。
提交检查不在宿主机执行代码；真正可运行性由隔离Docker运行验证。每轮代码与参数取内容哈希。
系统结果包括equity.csv、orders.csv、trades.csv、metrics.json、run_manifest.json；可信检查点仅在平台保存。
''')
    for name, value in templates().items(): textfile(public / name, value)
    write(public / "bridge_contract.json", contract)
    write(public / "runtime_profile.json", runtime)
    write(public / "task_input.draft.json", {"schema_version": "finance_task_input.draft.v2",
        "task_id": state.get("task_id", state["id"]), "candidate_id": state["id"], "task_version": state.get("version", 0),
        "initial_message": {"role": "user", "content_file": "TASK.md"},
        "expected_outputs": sorted(SUBMISSION_FILES), "dataset_id": None, "formal_intervals": None,
        "spec_status": state["status"], "data_status": "not_bound", "runtime_status": "not_connected",
        "binding_contract": "bridge_contract.json", "runtime_profile": "runtime_profile.json"})
    write(operator / "task_card.json", {**spec, "production_state": state})
    textfile(operator / "DATA_BINDING_PROMPT.md", '''# 运营方数据准备要求

根据task_card的数据需求与公开bridge_contract，使用可配置Tushare上游采集到私有数据库。
逐接口确认权限、分区完整性、字段类型与单位、历史发布/可见时点、版本修订、成分及交易规则。
提供采集plan、冻结plan和运行binding。明确coverage_confirmed依据，不将下载时间当历史发布时间。
真实起止范围、年份偏移和原始缓存仅保存在私有目录，不进入public。
冻结后不得静默补采；缺口须建立新数据版本并在正式运行前重新绑定。不得提供参考策略、未来行情或虚构成绩。
''')
    from .agent_reference import write_references
    write_references(root, public, contract)
