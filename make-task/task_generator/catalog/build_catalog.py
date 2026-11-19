"""Rebuild the explicitly human-curated catalog and unapproved examples.

This script does not call a model, approve a task, or supply market data.
"""
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from finlab.tasks import draft_contracts


def save(relative, value):
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


FAMILIES = [
    {"id": "F01", "name": "趋势与突破", "description": "趋势启动、延续、失败与再入场；不以更换均线窗口增加独立题数。"},
    {"id": "F02", "name": "区间与均值回归", "description": "偏离后的回归参与、趋势否决和关系失效，区别于持续跟随方向。"},
    {"id": "F03", "name": "横截面相对强弱", "description": "同一时点多只股票间的选择、继续持有、换仓和资金分配。"},
    {"id": "F04", "name": "状态与风险管理", "description": "状态识别和风险控制器本身是核心研究对象；普通止损附加项不自动成新题。"},
    {"id": "F05", "name": "跨品种相对价值", "description": "不同期货品种的统计或加工关系、真实多腿头寸、关系重估与保证金。"},
    {"id": "F06", "name": "同品种跨期结构", "description": "同一期货品种不同月份的相对关系、期限匹配、配对选择与移仓。"},
]

SOURCES = [
    {"id": "SRC_STOCK_TRENDS", "title": "Technical Analysis of Stock Trends，英文第11版", "status": "official_catalog_verified", "url": "https://www.taylorfrancis.com/books/mono/10.4324/9781315115719/technical-analysis-stock-trends-robert-edwards-john-magee-bassetti", "verified_scope": "仅核验出版社简介与目录：趋势、整理形态、支撑阻力、量价、止损及资金管理。未读取全书；不套用未知中文译本页码。种子为人工形式化改写，不是书中现成策略或盈利结论。"},
    {"id": "SRC_CHAN", "title": "Ernest P. Chan: Algorithmic Trading: Winning Strategies and Their Rationale", "status": "official_catalog_verified", "url": "https://www.wiley.com/en-us/Algorithmic+Trading%3A+Winning+Strategies+and+Their+Rationale-p-9781118460146", "verified_scope": "仅核验Wiley书页与官方目录，覆盖均值回归、日内/日间动量、执行与风险管理。具体国内规则、多腿/跨期种子为人工设计，不宣称书中原样给出。"},
    {"id": "SRC_QLIB", "title": "Microsoft Qlib Alpha158/Alpha360", "status": "official_source_verified", "url": "https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py", "verified_scope": "核验loader.py、handler.py与MIT许可。特征配置并非完整任务；未来收益LABEL不能作为当时特征。官方数据暂时停用，不据此声称已补齐国内历史成分数据。"},
    {"id": "SRC_RDAGENT", "title": "R&D-Agent-Quant及因子实现benchmark", "status": "official_paper_and_repository_verified", "url": "https://arxiv.org/abs/2505.15155", "verified_scope": "核验论文、微软官方仓库、MIT许可及含gt_code的benchmark例子。用于生产/验证流程参考；gt_code属于私有答案。未将原benchmark计作长期维护题库。"},
    {"id": "SRC_ALPHAAGENT", "title": "AlphaAgent，2025论文", "status": "paper_verified_code_license_unconfirmed", "url": "https://arxiv.org/abs/2502.16789", "verified_scope": "核验论文及论文关联仓库。借鉴去重、假设一致性和复杂度控制；2026仓库已重构且未见明确根许可，不复制打包其实现。"},
    {"id": "SRC_PROJECT_PLAN", "title": "持续Task生产方案，人工讨论稿v1，2026-10-30", "status": "internal_human_design", "url": "", "verified_scope": "内部方案第3—4节的六方向与12种子。来源路径output/持续Task生产方案_20261030.md；具体任务关系、状态控制和验收规则为本项目人工设计，不归因于外部书籍。"},
]

ENVIRONMENTS = [{'id': 'akquant_stock_etf_v2', 'name': '国内股票ETF日线桥接与AKQuant环境', 'profile_id': 'stock_etf_daily', 'supported_families': ['F01', 'F02', 'F03', 'F04'], 'directions': ['long_only'], 'decision_frequency': '已完成日线收盘后', 'execution': 'NextOpen，下一根可执行行情开盘', 'hard_constraints': ['股票ETF按绑定后交易单位及T+0/T+1规则运行', '停牌、价格限制、费用和公司行为必须绑定', '指数仅查询及基准，不下单', '现金、持仓、未完成订单和JSON memory跨段连续', '资源预算由本人运行finlab setup选择，任务草案不预填生效值'], 'available_data_evidence': '新任务通过Tushare桥接获取数据，未绑定真实冻结版本。', 'readiness': '规格草案；真实名称与代码保留，所有事件日期均为虚拟日期，不映射真实年份事件。', 'unsupported_without_extension': ['股票卖空', '期权', '缺少历史可见性依据的数据', '未绑定交易规则']}, {'id': 'akquant_futures_v2', 'name': '国内期货真实分合约日线桥接与AKQuant环境', 'profile_id': 'futures_daily', 'supported_families': ['F01', 'F02', 'F04', 'F05', 'F06'], 'supported_products': ['B', 'M', 'Y', 'SC', 'FU', 'LU', 'FG', 'SA'], 'supported_quote_units': ['CNY/tonne', 'CNY/barrel'], 'directions': ['long', 'short'], 'decision_frequency': '已完成交易日日线收盘后', 'execution': 'NextOpen；多腿逐腿撮合，可部分成交，不保证原子成交', 'hard_constraints': ['真实分合约有符号整数手数，不交易合成价差', '乘数、保证金、结算、费用、夜盘交易日及到期规则须绑定', '需处理部分成交、残余敞口、移仓及失效后的旧腿', '现金、持仓、未完成订单和JSON memory跨段连续', '资源预算由本人运行finlab setup选择，任务草案不预填生效值'], 'available_data_evidence': '新任务通过Tushare桥接获取真实分合约数据；同时月份覆盖和规则证据待绑定。', 'readiness': '规格草案；虚拟年份不代表真实历史年份。', 'unsupported_without_extension': ['交割', '跨品种保证金优惠', '连续合约拼接价直接交易', '缺失规则的正式运行']}]

DECISIONS = {
    "mechanisms": {
        "consolidation_breakout": "整理区间突破及失败后的趋势跟踪",
        "trend_pullback": "已形成趋势内的回撤介入与分批退出",
        "range_boundary_reversion": "区间边缘回归与趋势否决",
        "shock_path_reversion": "异常放量冲击后恢复路径与延续辨识",
        "relative_strength_rotation": "横截面相对强弱及换仓缓冲",
        "cross_sectional_reversal": "横截面反转与成交活跃度联合选择",
        "regime_participation_control": "状态切换控制器选择参与方式",
        "drawdown_risk_budget": "波动/回撤下组合预算缩减、暂停与恢复",
        "cross_product_residual": "跨品种滚动对冲残差及关系失效",
        "crush_component_aggregation": "大豆加工关系与分项信号联合管理",
        "calendar_spread_pairing": "同品种近远月价差与期限配对",
        "term_curve_rotation": "同品种多月份曲线斜率与动态配对",
        "short_horizon_trend": "历史题的宽泛短周期趋势策略",
        "oversold_rebound": "历史题的宽泛短期超跌反弹",
        "multi_product_oil_relative_value": "历史SC/FU/LU双腿或三腿相对价值",
        "glass_soda_residual": "历史FG/SA统计对冲与关系重估",
        "dynamic_constituent_multifactor": "历史动态成分股多因子组合草稿",
    },
    "objects": {
        "breakout_position_state": "突破候选及持仓的状态生命周期",
        "trend_pullback_inventory": "趋势回撤位置和分批持仓库存",
        "range_position_lifecycle": "区间有效期及边缘头寸生命周期",
        "shock_event_inventory": "冲击事件后的恢复路径与隔日库存",
        "ranked_stock_portfolio": "同刻排名股票组合与换仓队列",
        "cross_sectional_reversal_portfolio": "反转强度与可交易成本共同决定的股票组合",
        "participation_controller": "趋势/震荡参与控制器与状态转移",
        "portfolio_risk_controller": "组合预算、暂停和风险恢复控制器",
        "cross_product_leg_portfolio": "不同品种实际合约腿及统计残差",
        "crush_leg_portfolio": "B/M/Y加工关系的真实多腿头寸",
        "calendar_pair_inventory": "同品种近远月真实配对与移仓库存",
        "term_curve_pair_selection": "同品种多月份结构与可交易配对选择",
        "stock_trend_portfolio": "历史短趋势股票组合",
        "oversold_stock_portfolio": "历史超跌反弹股票组合",
        "oil_leg_portfolio": "历史SC/FU/LU相对关系的多腿组合",
        "glass_soda_leg_portfolio": "历史FG/SA对冲头寸",
        "constituent_stock_portfolio": "历史时点成分内多因子组合",
    },
    "decisions": {
        "range_detection": "仅用已完成数据界定区间边界、有效期和更新规则",
        "entry_confirmation": "选择确认依据、等待时机与可成交入场",
        "failure_exit": "定义机制失效并处置已有持仓",
        "reentry_policy": "失败或退出后的冷却、恢复与再入场",
        "capital_allocation": "多个机会间分配资金和现金预算",
        "trend_qualification": "识别既有趋势及继续适用条件",
        "pullback_location": "定位趋势内回撤深度和回撤结束",
        "staged_inventory": "分批入场/退出及T+1库存管理",
        "exit_policy": "止盈、止损、持有时限和跟踪退出",
        "reference_estimation": "估计当时可见的参考水平与更新方法",
        "deviation_measurement": "偏离尺度、方向及波动标准化",
        "trend_veto": "区分区间回归机会与持续趋势，决定否决",
        "shock_detection": "识别价量冲击事件，避免普通波动混入",
        "recovery_path": "辨认冲击后的回摆、延续与未决状态",
        "overnight_inventory": "隔日退出与T+1不可卖库存的风险处理",
        "feature_definition": "量价特征定义、方向与标准化",
        "rank_aggregation": "跨股票得分合成、排名和候选排序",
        "holding_buffer": "买入与继续持有门槛、排名缓冲和换仓条件",
        "turnover_budget": "换手、成本及单次换仓预算",
        "liquidity_filter": "依据已知成交活跃度筛选和限制参与",
        "state_identification": "仅凭当时数据识别趋势/震荡或风险状态",
        "switch_hysteresis": "状态切换迟滞、确认与最短驻留",
        "participation_policy": "状态到参与、降低参与或停用的映射",
        "state_migration": "更新逻辑时迁移旧持仓和持久化状态",
        "risk_budget": "将已识别风险映射到组合和模块预算",
        "drawdown_response": "回撤出现时减仓/暂停与未成交处理",
        "recovery_policy": "风险恢复和重新参与条件",
        "hedge_estimation": "估计或设定对冲关系，处理计价单位和整数手数",
        "month_selection": "按当时已知条件选择实际合约月份",
        "entry_exit": "价差/残差参与、退出与持有时限",
        "relationship_monitoring": "监控对冲关系失效，暂停与重新估计",
        "margin_budget": "按真实腿风险、费用与保证金分配手数",
        "signal_aggregation": "整体价差与分项信号的一致性及组合决策",
        "roll_policy": "到期前平旧开新、迁移旧仓并核算移仓成本",
        "tenor_normalization": "期限差、计价与曲线变化的可比表达",
        "pair_selection": "同品种合约对选择、流动性和期限匹配",
        "curve_signal": "多月份期限曲线及斜率变化的信号定义",
        "constituent_eligibility": "使用当时有效成分并处置调出持仓",
    },
    "maintenance": {
        "structure_validity": "区间/趋势结构在后续阶段的有效性与失败路径",
        "signal_execution_gap": "信号与实际可成交持仓的差异",
        "reference_drift": "参考水平和偏离尺度漂移",
        "reversion_failure": "回归假设失效与停用恢复",
        "ranking_stability": "排名和持有名单稳定性及换仓成本",
        "cost_sensitivity": "成本和换手对组合行为的影响",
        "regime_transition": "状态误判、切换频繁与驻留时间变化",
        "risk_recovery": "预算缩减/恢复是否兼容已有持仓",
        "state_compatibility": "版本间持仓、现金、memory连续兼容",
        "hedge_drift": "跨品种关系、残差尺度和对冲手数漂移",
        "margin_exposure": "真实腿敞口、费用和保证金变化",
        "expiry_roll": "合约生命周期、移仓和配对库存过渡",
        "curve_stability": "期限结构、期限匹配与流动性格局变化",
        "constituent_change": "时点成分变更与旧持仓退出",
    },
}


def seed(sid, fid, title, mechanism, obj, decisions, dependencies, maintenance,
         question, sources, related, warning):
    return {"id": sid, "family_id": fid, "title": title, "source_ids": sources,
            "core_mechanism": mechanism, "decision_object": obj,
            "required_decisions": decisions, "decision_dependencies": dependencies,
            "maintenance_targets": maintenance, "research_question": question,
            "related_historical_ids": related, "novelty_watchouts": warning,
            "status": "curated_seed_unvalidated", "source_interpretation": "仅以来源概念为启发，经整理转化为研究问题；未规定最优方法或参数。"}


SEEDS = [
    seed("S01", "F01", "整理区间突破后的跟踪、失败退出与再入场", "consolidation_breakout", "breakout_position_state",
         ["range_detection", "entry_confirmation", "failure_exit", "reentry_policy", "capital_allocation"],
         [["range_detection", "entry_confirmation"], ["entry_confirmation", "failure_exit"], ["failure_exit", "reentry_policy"], ["reentry_policy", "capital_allocation"]],
         ["structure_validity", "signal_execution_gap", "state_compatibility"], "如何使区间识别、突破确认、失败处置与再次参与形成可维护的状态流程？", ["SRC_STOCK_TRENDS", "SRC_PROJECT_PLAN"], ["HT01"], "与HT01趋势旧题明显相邻，旧题已经允许突破、过滤、冷却和再入场；细化生命周期不自动证明独立新题，需人工语义审或登记原题扩展。"),
    seed("S02", "F01", "已有趋势中的回撤入场与分批退出", "trend_pullback", "trend_pullback_inventory",
         ["trend_qualification", "pullback_location", "entry_confirmation", "staged_inventory", "exit_policy"],
         [["trend_qualification", "pullback_location"], ["pullback_location", "entry_confirmation"], ["entry_confirmation", "staged_inventory"], ["staged_inventory", "exit_policy"]],
         ["structure_validity", "signal_execution_gap", "state_compatibility"], "如何在既有趋势回撤中进入，并将趋势否定与T+1分批持仓退出衔接？", ["SRC_STOCK_TRENDS", "SRC_PROJECT_PLAN"], ["HT01", "HT02"], "应区别突破创新高与逆势超跌，不能只将入场阈值改名为回撤。"),
    seed("S03", "F02", "区间边缘的回归参与与趋势否决", "range_boundary_reversion", "range_position_lifecycle",
         ["range_detection", "reference_estimation", "deviation_measurement", "trend_veto", "exit_policy", "reentry_policy"],
         [["range_detection", "reference_estimation"], ["reference_estimation", "deviation_measurement"], ["deviation_measurement", "trend_veto"], ["trend_veto", "exit_policy"], ["exit_policy", "reentry_policy"]],
         ["reference_drift", "reversion_failure", "state_compatibility"], "何时仍是区间边缘回归，何时应判定结构进入趋势并停止逆势参与？", ["SRC_STOCK_TRENDS", "SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT02"], "与超跌反弹共享回归思想；候选应以区间有效期和趋势否决作为决策对象，若最终仅换偏离指标则合并。"),
    seed("S04", "F02", "放量冲击后的回摆、延续判断与隔日持仓", "shock_path_reversion", "shock_event_inventory",
         ["shock_detection", "recovery_path", "entry_confirmation", "overnight_inventory", "exit_policy"],
         [["shock_detection", "recovery_path"], ["recovery_path", "entry_confirmation"], ["entry_confirmation", "overnight_inventory"], ["overnight_inventory", "exit_policy"]],
         ["reversion_failure", "signal_execution_gap", "state_compatibility"], "能否区分异常价量冲击后的回摆与延续，并约束不能当日卖出的隔夜库存？", ["SRC_CHAN", "SRC_QLIB", "SRC_PROJECT_PLAN"], ["HT02"], "HT02已含价量和反转确认；只有事件定义、恢复路径及隔日库存共同成为核心研究对象时才可能构成不同模板。"),
    seed("S05", "F03", "相对强弱排名、继续持有门槛与换仓缓冲", "relative_strength_rotation", "ranked_stock_portfolio",
         ["feature_definition", "rank_aggregation", "holding_buffer", "turnover_budget", "capital_allocation"],
         [["feature_definition", "rank_aggregation"], ["rank_aggregation", "holding_buffer"], ["holding_buffer", "turnover_budget"], ["turnover_budget", "capital_allocation"]],
         ["ranking_stability", "cost_sensitivity", "state_compatibility"], "同一股票池内如何让选入排名、留存门槛、换仓预算和资金分配协同？", ["SRC_QLIB", "SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT06", "HT01"], "HT06草稿已含排名缓冲和换手；改用45股只算数据实例，不能独自证明新任务。本题需人工决定为草稿改编还是独立模板。"),
    seed("S06", "F03", "短期反转与成交活跃度联合选股", "cross_sectional_reversal", "cross_sectional_reversal_portfolio",
         ["deviation_measurement", "liquidity_filter", "rank_aggregation", "turnover_budget", "capital_allocation"],
         [["deviation_measurement", "rank_aggregation"], ["liquidity_filter", "rank_aggregation"], ["rank_aggregation", "turnover_budget"], ["turnover_budget", "capital_allocation"]],
         ["ranking_stability", "cost_sensitivity", "reversion_failure"], "如何跨股票比较反转强度，并将成交活跃度和换仓成本纳入同一选择？", ["SRC_QLIB", "SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT02", "HT06"], "应区别单只股票超跌触发；不能仅给HT02候选做既有排序就计独立任务。"),
    seed("S07", "F04", "趋势/震荡切换下的策略参与控制", "regime_participation_control", "participation_controller",
         ["state_identification", "switch_hysteresis", "participation_policy", "risk_budget", "state_migration"],
         [["state_identification", "switch_hysteresis"], ["switch_hysteresis", "participation_policy"], ["participation_policy", "risk_budget"], ["risk_budget", "state_migration"]],
         ["regime_transition", "risk_recovery", "state_compatibility"], "状态辨识与切换控制如何决定策略参与和预算，并接住旧状态和未清仓头寸？", ["SRC_STOCK_TRENDS", "SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT01", "HT02"], "复用趋势/回归思路但研究对象为控制器；若只在HT01加同一波动过滤，应退回扩展项。"),
    seed("S08", "F04", "波动与回撤触发的减仓、暂停和恢复", "drawdown_risk_budget", "portfolio_risk_controller",
         ["state_identification", "risk_budget", "drawdown_response", "recovery_policy", "state_migration"],
         [["state_identification", "risk_budget"], ["risk_budget", "drawdown_response"], ["drawdown_response", "recovery_policy"], ["recovery_policy", "state_migration"]],
         ["risk_recovery", "signal_execution_gap", "state_compatibility"], "在固定公开风险目标下，何时缩减或暂停风险预算，何时以及以何速度恢复？", ["SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT01", "HT02"], "与S07可能重叠；须预先固定风险目标，区分市场状态参与控制与组合损失预算控制。"),
    seed("S09", "F05", "FU/LU相对关系的滚动对冲和暂停重估", "cross_product_residual", "cross_product_leg_portfolio",
         ["month_selection", "hedge_estimation", "entry_exit", "relationship_monitoring", "margin_budget", "state_migration"],
         [["month_selection", "hedge_estimation"], ["hedge_estimation", "entry_exit"], ["entry_exit", "relationship_monitoring"], ["relationship_monitoring", "margin_budget"], ["margin_budget", "state_migration"]],
         ["hedge_drift", "margin_exposure", "expiry_roll", "state_compatibility"], "FU/LU关系变化时如何重估对冲、暂停参与并迁移实际双腿头寸？", ["SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT04", "HT05"], "HT04已允许FU/LU双腿、滚动对冲与状态切换；不能因为去掉SC就称新题。优先登记现有题的聚焦改编，待人工语义判定。"),
    seed("S10", "F05", "B/M/Y联合价差与分项信号的组合管理", "crush_component_aggregation", "crush_leg_portfolio",
         ["hedge_estimation", "signal_aggregation", "month_selection", "entry_exit", "margin_budget", "state_migration"],
         [["hedge_estimation", "signal_aggregation"], ["month_selection", "signal_aggregation"], ["signal_aggregation", "entry_exit"], ["entry_exit", "margin_budget"], ["margin_budget", "state_migration"]],
         ["hedge_drift", "margin_exposure", "expiry_roll", "state_compatibility"], "整体加工关系与分项信号冲突时如何配置实际手数并处理旧组合？", ["SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT03"], "与HT03大豆压榨题高度重叠，不能默认新增独立题；保留为家族覆盖/改编测试种子。固定生产系数不能作为可调答案。"),
    seed("S11", "F06", "FG近远月价差的配对、到期和移仓", "calendar_spread_pairing", "calendar_pair_inventory",
         ["pair_selection", "tenor_normalization", "entry_exit", "margin_budget", "roll_policy", "state_migration"],
         [["pair_selection", "tenor_normalization"], ["tenor_normalization", "entry_exit"], ["entry_exit", "margin_budget"], ["margin_budget", "roll_policy"], ["roll_policy", "state_migration"]],
         ["curve_stability", "expiry_roll", "margin_exposure", "state_compatibility"], "同品种近远月价差如何随剩余期限、流动性与到期迁移保持可交易？", ["SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT05"], "HT05的FG/SA跨品种与本题同品种跨期在腿关系和期限决策上不同；但仍需检查同日多月份覆盖及执行器支持，不能据此宣称已可运行。"),
    seed("S12", "F06", "M期限曲线的斜率变化与滚动配对", "term_curve_rotation", "term_curve_pair_selection",
         ["curve_signal", "tenor_normalization", "pair_selection", "entry_exit", "roll_policy", "state_migration"],
         [["tenor_normalization", "curve_signal"], ["curve_signal", "pair_selection"], ["pair_selection", "entry_exit"], ["entry_exit", "roll_policy"], ["roll_policy", "state_migration"]],
         ["curve_stability", "expiry_roll", "margin_exposure", "state_compatibility"], "多月份曲线变化如何决定选哪一对合约，而非固定近远月对？", ["SRC_CHAN", "SRC_PROJECT_PLAN"], ["HT03"], "若仅把S11换成豆粕或换门槛，合并为同模板实例；只有多月份曲线→动态配对确实改变决策结构才保留候选。"),
]


def data_request(profile, symbols, historical=False):
    return {"profile_id": profile, "symbols": symbols,
            "date_start": None if historical else "2022-01-01", "date_end": None if historical else "2025-12-31",
            "minimum_years": 2, "extra_fields": [],
            "notes": (["历史旧题记录；匿名交易日不能反推真实日期，新需求范围不代表旧数据范围。"] if historical else ["2022—2025为申请整理范围，尚未确认已持有；若不足需报告实际覆盖，不能编造补齐。", "至少2年只是数据需求底线；有效交易日/多月份重叠和足够研发、逐段维护样本均待盘点。", "分段边界需数据盘点后冻结；未完成数据或执行器验收。"])}


def make_spec(seed_item, symbols, modules, rationale, novelty, parameters, lineage):
    s = seed_item
    futures = s["family_id"] in ("F05", "F06")
    environment = "akquant_futures_v2" if futures else "akquant_stock_etf_v2"
    requirements = data_request("futures_daily" if futures else "stock_etf_daily", symbols)
    item = {"schema_version": "finance_task_spec.v2", "title": s["title"], "family_id": s["family_id"],
            "source_ids": s["source_ids"], "core_mechanism": s["core_mechanism"], "decision_object": s["decision_object"],
            "required_decisions": s["required_decisions"], "decision_dependencies": s["decision_dependencies"],
            "maintenance_targets": s["maintenance_targets"], "research_question": s["research_question"],
            "research_modules": [{"decision_id": did, "description": desc, "interaction": interaction} for did, desc, interaction in modules],
            "maintenance_rationale": rationale, "environment_id": environment,
            "data_requirements": requirements, "parameter_space": parameters, "novelty_claim": novelty, "lineage": lineage}
    item.update(draft_contracts(environment, requirements))
    return item


SAMPLE_DETAILS = [
    ("S01", ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH", "601318.SH"], [
        ("range_detection", "研究只使用已完成K线的整理区间、边界更新和失效状态。", "边界更新改变确认门槛及失败判断，不可事后重画区间。"),
        ("entry_confirmation", "比较突破后的等待、量价确认与可成交时点。", "等待会改变持有期、T+1库存和机会排序。"),
        ("failure_exit", "研究突破失败与正常回踩的区别及已有仓位退出。", "退出时刻约束再次参与，不能用未来最低价标记假突破。"),
        ("reentry_policy", "设计失败后冷却与结构重建后的再次参与。", "memory保留旧失败状态，与新区间编号和旧持仓兼容。"),
        ("capital_allocation", "在同时触发的候选间分配现金和权重。", "资金竞争与T+1不可卖仓位影响信号实际实现。")],
     "后续数据可能使区间结构和失败模式变化，需要判断调整结构、确认还是退出；每次维护必须接住已持仓和旧失败状态。这是可维护性假设，尚无轨迹证明长程难度。",
     "从宽泛趋势题聚焦到区间→确认→失败→重入生命周期，但HT01已允许这些方向，不能声称已证实独立；待人工语义审，可能归并为HT01扩展。",
     [{"name": "range_construction", "type": "choice", "options": ["rolling_boundary", "causal_pivot_zone", "adaptive_consolidation"], "description": "可替换结构定义，不指定唯一指标。"}, {"name": "confirmation_and_reentry", "type": "structured", "description": "确认、失败、冷却及重入规则相互作用；由Agent研究，不给最佳阈值。"}],
     {"derived_from": ["S01", "HT01"], "kind": "focused_adaptation_pending_review"}),
    ("S03", ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH", "601318.SH"], [
        ("range_detection", "研究何种历史结构足以认定当前仍在区间内。", "结构有效期决定参考水平是否继续适用。"),
        ("reference_estimation", "研究区间参考水平、边缘及其更新方式。", "参考水平改变偏离尺度；更新不能抹掉已经发生的亏损。"),
        ("deviation_measurement", "比较偏离边界与波动标准化的回归机会。", "偏离只有在趋势否决通过时才可参与。"),
        ("trend_veto", "研究何时否决逆势入场，并处理区间向趋势的失效。", "否决应同时考虑旧仓退出，不能只禁止新仓。"),
        ("exit_policy", "研究回归完成、时间耗尽和失败退出。", "T+1限制使退出不能假设当日新仓即时止损。"),
        ("reentry_policy", "区间失效后何时恢复或重建参考。", "旧区间memory与新参考版本需明确兼容。")],
     "后续阶段可能从区间进入趋势；需要维护参考水平、否决条件和恢复规则。维护是否有效须数据和实际轨迹另验。",
     "相对HT02，以区间有效期和趋势否决为核心，避免仅换一个超跌指标；仍共享回归逻辑，若实现退化为阈值替换应合并。",
     [{"name": "reference_family", "type": "choice", "options": ["rolling_range", "robust_center_band", "causal_support_zone"], "description": "研究参考与边缘的联合定义。"}, {"name": "veto_and_recovery", "type": "structured", "description": "否决、持有时限、冷却及恢复条件允许替换逻辑。"}],
     {"derived_from": ["S03", "HT02"], "kind": "mechanism_refinement_pending_review"}),
    ("S05", ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH", "601318.SH"], [
        ("feature_definition", "定义基于已知量价的相对强弱特征并统一比较尺度。", "特征尺度影响合成排名和持股集中度。"),
        ("rank_aggregation", "研究多个强弱尺度如何合成以及候选并列处理。", "排名变化不必立即转成交易。"),
        ("holding_buffer", "分别研究新选入和继续持有门槛。", "缓冲影响换手和机会占用。"),
        ("turnover_budget", "研究单次换仓预算与成本敏感性。", "预算下优先替换谁，需要综合旧仓和新排名。"),
        ("capital_allocation", "决定股票权重、闲置现金和集中度。", "实际T+1可卖量与拒单可能使目标组合无法一步达到。")],
     "市场中强弱持续性、排名稳定性和换手代价可能改变；可维护因子组合、缓冲和资金分配，但不要求每段都修改。",
     "相对单股信号，核心是同刻跨股票选择和留换；但HT06已含多因子排名和缓冲，改变股票池不能证明独立，本示例保留为待审的研究范围草案。",
     [{"name": "relative_strength_features", "type": "structured", "description": "特征、标准化、组合与方向均可研究；不使用未知基本面。"}, {"name": "ranking_policy", "type": "structured", "description": "持股数、选入/留存门槛、换手与权重相互影响。"}],
     {"derived_from": ["S05", "HT06"], "kind": "data_and_scope_adaptation_pending_review"}),
    ("S07", ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH", "601318.SH"], [
        ("state_identification", "用当时已知量价定义趋势、震荡或不确定状态。", "状态定义需与参与规则一起研究，不能用未来收益作标签输入。"),
        ("switch_hysteresis", "研究状态切换确认、迟滞和驻留。", "减少抖动会增加状态变化后的反应延迟。"),
        ("participation_policy", "把状态映射到趋势/回归模块的参与或停用。", "需记录透明模块定义；暂停新增不等于立即清空旧仓。"),
        ("risk_budget", "研究各参与模块与现金的预算。", "预算服从固定硬上限，不允许优化账户规则。"),
        ("state_migration", "控制器更新时迁移模块状态及旧持仓。", "不能重置memory、现金或用新规则覆盖过去正式结果。")],
     "状态分界可能漂移，切换迟滞与风险预算会产生不同延迟；维护核心是控制器而非反复调单个入场阈值。实际长程区分度待管线验证。",
     "复用HT01/HT02的基础交易思想，以参与控制器和持久化状态转移作为研究对象；如仅增加波动过滤或统一止损则不算独立新题。",
     [{"name": "state_model", "type": "choice", "options": ["transparent_rule_state", "rolling_statistic_state"], "description": "允许选择可因果计算的状态形式，不使用未来状态标注。"}, {"name": "participation_controller", "type": "structured", "description": "切换迟滞、模块参与、预算、暂停与状态迁移共同研究。"}],
     {"derived_from": ["S07", "HT01", "HT02"], "kind": "controller_candidate_pending_review"}),
    ("S09", ["FU", "LU"], [
        ("month_selection", "研究FU/LU月份匹配与共同可交易条件。", "月份选择改变对冲关系和移仓成本。"),
        ("hedge_estimation", "仅用历史估计滚动对冲关系并转成整数手数。", "残差信号和真实腿名义敞口不能混用。"),
        ("entry_exit", "研究残差参与、退出、止损和时间限制。", "信号变化需落实为真实腿目标，不能买卖负价差本身。"),
        ("relationship_monitoring", "研究何时判定关系失效、暂停和重估。", "重估改变对冲比例，必须明确处置旧腿。"),
        ("margin_budget", "按乘数、费用和保证金计算组合手数。", "预算、逐腿部分成交和残余敞口会改变信号实际实现。"),
        ("state_migration", "关系版本更新和换月时处理旧仓及memory。", "只有未来决策采用新定义，过去正式结果不可重写。")],
     "对冲关系、残差尺度和月份流动性可能改变；维护需要同时修订估计和迁移真实头寸。现有数据结算/规则质量及新格式适配仍未验收。",
     "HT04明确允许FU/LU双腿及滚动对冲，本候选只是聚焦暂停/重估的改编，不能计为已确认独立新题；人工可能直接归并HT04。",
     [{"name": "hedge_model", "type": "choice", "options": ["fixed_interpretable", "rolling_regression", "robust_rolling"], "description": "模型选择和估计方法可研究，不调整合约规格。"}, {"name": "relationship_lifecycle", "type": "structured", "description": "暂停、重估、恢复、整数手数与旧仓过渡联合研究。"}],
     {"derived_from": ["S09", "HT04"], "kind": "focused_adaptation_pending_review"}),
    ("S11", ["FG"], [
        ("pair_selection", "按当时可见到期、成交和持仓信息选同品种近远月。", "不能用全样本最活跃合约或事后主力映射。"),
        ("tenor_normalization", "研究期限差和剩余到期变化下价差的可比表达。", "同一价差点位在不同期限上不必具有相同意义。"),
        ("entry_exit", "研究近远月相对关系的参与和退出。", "价差只产生信号，交易实际合约手数。"),
        ("margin_budget", "按实际双腿计算风险和保证金。", "不假设跨期保证金优惠，服从固定执行器上限。"),
        ("roll_policy", "研究配对到期前退出、迁移和新对建立。", "必须计入平旧开新成本并处理逐腿部分成交。"),
        ("state_migration", "配对标识和逻辑更新时迁移旧腿状态。", "旧合约不能因新配对选中而从目标头寸中被遗忘。")],
     "近远月关系受期限缩短、流动性切换和到期迁移影响，维护对象同时包括信号与合约生命周期；同日多月份数据与适配器仍待确认。",
     "相对HT05的FG/SA跨品种关系，改为同品种两月份的期限匹配及移仓库存，决策结构有所变化；仍须人工核查并验证数据覆盖，不保证已经是独立可运行题。",
     [{"name": "pair_policy", "type": "structured", "description": "期限区间、可见流动性门槛和配对切换规则联动研究。"}, {"name": "spread_and_roll", "type": "structured", "description": "期限表达、进退场、到期缓冲和移仓状态管理可改逻辑。"}],
     {"derived_from": ["S11", "HT05"], "kind": "calendar_structure_candidate_pending_review"}),
]

EXAMPLES = []
for i, details in enumerate(SAMPLE_DETAILS, 1):
    sid, symbols, modules, rationale, novelty, parameters, lineage = details
    item = make_spec(next(s for s in SEEDS if s["id"] == sid), symbols, modules, rationale, novelty, parameters, lineage)
    item.update({"sample_id": "EX%02d" % i, "seed_id": sid, "status": "pending_human_review",
                 "provenance": {"authoring": "assistant_curated_example", "api_called": False, "data_supplied": False, "runtime_verified": False, "human_approved": False},
                 "validation_notes": ["内置示例，由当前助手整理，未调用配置的模型API，未获得人工批准。", "六个方向候选不等于六个独立任务；相似项可能合并或退回。", "原生v2接口规格，尚未绑定真实数据、交易规则、本人资源预算或正式分段。"]})
    EXAMPLES.append(item)


def historical(hid, title, family, mechanism, obj, decisions, maintenance, path, symbols, status, notes, research):
    profile = "futures_daily" if family == "F05" else "stock_minute"
    modules = [{"decision_id": d, "description": DECISIONS["decisions"][d], "interaction": "历史TASK允许研究此项，须结合其他模块和持续账户；此登记不新增运行验收。"} for d in decisions]
    item = {"schema_version": "finance_task_spec.v1", "id": hid, "title": title, "family_id": family,
            "source_ids": ["SRC_PROJECT_PLAN"], "core_mechanism": mechanism, "decision_object": obj,
            "required_decisions": decisions, "decision_dependencies": [[decisions[n], decisions[n + 1]] for n in range(len(decisions) - 1)],
            "maintenance_targets": maintenance, "research_question": research, "research_modules": modules,
            "maintenance_rationale": "原题允许逐段不改/调参/改逻辑，并保持持仓、现金和memory连续；登记其设计，不宣称实际维护轨迹已验证。",
            "environment_id": "futures_daily_v1" if profile == "futures_daily" else "stock_minute_v1",
            "data_requirements": data_request(profile, symbols, historical=True),
            "parameter_space": [{"name": "historical_open_research_scope", "type": "structured", "description": "以原TASK及BACKTEST为准；研究窗口、阈值和规则组合，不反向指定参考答案。"}],
            "novelty_claim": "历史对照，不能作为本次新生成数。", "lineage": {"derived_from": [], "kind": "historical_reference"},
            "status": status, "source_path": path, "review_notes": notes}
    return item


HISTORICAL = [
    historical("HT01", "45股短周期趋势追踪", "F01", "short_horizon_trend", "stock_trend_portfolio",
               ["range_detection", "entry_confirmation", "exit_policy", "reentry_policy", "capital_allocation"],
               ["structure_validity", "signal_execution_gap", "state_compatibility"], "demo_packages/t01_short_trend_v1/TASK.md", ["S%02d" % n for n in range(1, 46)], "historical_package_unrevalidated",
               ["原题已有突破、均线方向、量价/波动过滤、冷却、排序及仓位；不能把S01的细化直接宣称全新。", "原题479交易日，研发144日，七段；匿名t不反推真实日期。"], "从突破和趋势入手管理数日持仓并持续维护。"),
    historical("HT02", "45股短周期超跌反转", "F02", "oversold_rebound", "oversold_stock_portfolio",
               ["reference_estimation", "deviation_measurement", "entry_confirmation", "staged_inventory", "exit_policy", "capital_allocation"],
               ["reference_drift", "reversion_failure", "signal_execution_gap"], "demo_packages/t02_short_reversal_v1/TASK.md", ["S%02d" % n for n in range(1, 46)], "historical_package_unrevalidated",
               ["原题分离偏离与止跌确认，含量价/趋势筛选、分批、冷却和仓位；S03/S04需与此比较。", "原题匿名日/分钟479日，真实日期未知。"], "研究短期超跌后的反弹机会及T+1退出。"),
    historical("HT03", "国内B/M/Y大豆压榨价差", "F05", "crush_component_aggregation", "crush_leg_portfolio",
               ["hedge_estimation", "signal_aggregation", "month_selection", "entry_exit", "margin_budget", "roll_policy", "state_migration"],
               ["hedge_drift", "margin_exposure", "expiry_roll", "state_compatibility"], "demo_packages/a01_soy_crush_v1/TASK.md", ["B", "M", "Y"], "historical_package_quality_pending",
               ["真实合约及固定生产假设不可作为可调参数；S10与本题高度相邻。", "结算、规则生效及历史到期等口径仍待复核。"], "研究整体压榨关系、分项信号、实际多腿与资金占用。"),
    historical("HT04", "国内SC/FU/LU原油燃料油相对价值", "F05", "multi_product_oil_relative_value", "oil_leg_portfolio",
               ["month_selection", "hedge_estimation", "signal_aggregation", "entry_exit", "relationship_monitoring", "margin_budget", "roll_policy", "state_migration"],
               ["hedge_drift", "margin_exposure", "expiry_roll", "state_compatibility"], "demo_packages/a03_domestic_oil_spread_v1/TASK.md", ["SC", "FU", "LU"], "historical_package_quality_pending",
               ["原题明确允许SC/FU、SC/LU、FU/LU、双腿或三腿、滚动对冲与状态切换；S09不能仅因少一条SC腿算新。", "SC桶与FU/LU吨不可直接相减称炼化利润。"], "研究国内油品相对价格关系、真实合约腿和对冲模型。"),
    historical("HT05", "FG/SA玻璃纯碱统计价差", "F05", "glass_soda_residual", "glass_soda_leg_portfolio",
               ["month_selection", "hedge_estimation", "entry_exit", "relationship_monitoring", "margin_budget", "roll_policy", "state_migration"],
               ["hedge_drift", "margin_exposure", "expiry_roll", "state_compatibility"], "demo_packages/a04_glass_soda_v1/TASK.md", ["FG", "SA"], "historical_package_quality_pending",
               ["跨品种统计对冲，包含暂停/重估/恢复；不将生产用量强行当唯一手数。", "S11的同品种跨期需要额外确认多月份重叠和生命周期字段。"], "在允许关系失效前提下研究FG/SA统计对冲与头寸维护。"),
    historical("HT06", "中证500动态成分多因子组合草稿", "F03", "dynamic_constituent_multifactor", "constituent_stock_portfolio",
               ["constituent_eligibility", "feature_definition", "rank_aggregation", "holding_buffer", "turnover_budget", "capital_allocation"],
               ["ranking_stability", "cost_sensitivity", "constituent_change", "state_compatibility"], "demo_packages/c01_csi500_v1/TASK.md", ["CSI500_PIT_UNIVERSE_REQUIRED"], "draft_data_missing",
               ["动态历史成分与可比指数缺失，不可用当前45股冒充。", "原稿日线接口；此catalog的stock_minute_v1仅代表可讨论收盘调用承载，未适配验收。", "本草稿已包含量价因子、排名缓冲、状态调整和换手；S05即使换股票池也不自动独立。"], "从各时点有效中证500成分中选股并控制换手、风险和基准超额。"),
]
HISTORICAL[-1]["data_requirements"]["extra_fields"] = ["historical_index_membership", "benchmark_total_return"]


if __name__ == "__main__":
    for name, data in [("families", FAMILIES), ("sources", SOURCES), ("environments", ENVIRONMENTS),
                       ("decisions", DECISIONS), ("seeds", SEEDS), ("historical_tasks", HISTORICAL)]:
        save("catalog/" + name + ".json", data)
    save("examples/first_batch.json", EXAMPLES)
    print("Wrote 6 families, 12 unvalidated seeds, 6 historical references and 6 unapproved examples.")
