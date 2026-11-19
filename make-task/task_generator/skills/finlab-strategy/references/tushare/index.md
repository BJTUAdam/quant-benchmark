# Tushare Pro 离线接口索引

当前版本：246个已登记Pro接口，284份官方正文；失败0页。
资料包括输入、输出、单位、权限、限额和官方示例。历史可见性、实际权限与任务授权另行判断。
旧版Tushare网络爬虫接口不在本系统Pro HTTP范围内；本机SDK1.4.29与文档快照日期分别记录。

用`scripts/api_docs.py --search 关键词`检索；用`--api daily`读单个接口。
完整出处和SHA-256见[snapshot.json](snapshot.json)。不要一次加载全部文档。

## 股票与其他接口

|接口或文档|说明|
|---|---|
|[adj_factor](apis/adj_factor.md)|复权因子|
|[anns_d](apis/anns_d.md)|上市公司全量公告|
|[bak_basic](apis/bak_basic.md)|股票历史列表（历史每天股票列表）|
|[bak_daily](apis/bak_daily.md)|备用行情|
|[block_trade](apis/block_trade.md)|大宗交易|
|[broker_recommend](apis/broker_recommend.md)|券商每月荐股|
|[bse_mapping](apis/bse_mapping.md)|北交所新旧代码对照表|
|[cctv_news](apis/cctv_news.md)|新闻联播|
|[cyq_chips](apis/cyq_chips.md)|每日筹码分布|
|[cyq_perf](apis/cyq_perf.md)|每日筹码及胜率|
|[daily](apis/daily.md)|A股日线行情|
|[daily_basic](apis/daily_basic.md)|每日指标|
|[daily_info](apis/daily_info.md)|市场交易统计|
|[dc_daily](apis/dc_daily.md)|概念板块行情|
|[dc_hot](apis/dc_hot.md)|DC热榜|
|[dc_member](apis/dc_member.md)|板块成分|
|[disclosure_date](apis/disclosure_date.md)|财报披露计划|
|[factor_list](apis/factor_list.md)|因子列表|
|[factor_value](apis/factor_value.md)|因子值|
|[fx_daily](apis/fx_daily.md)|外汇日线行情|
|[fx_obasic](apis/fx_obasic.md)|外汇基础信息（海外）|
|[gz_index](apis/gz_index.md)|广州民间借贷利率（暂停更新）|
|[hm_detail](apis/hm_detail.md)|游资每日明细|
|[hm_list](apis/hm_list.md)|游资名录|
|[hsgt_top10](apis/hsgt_top10.md)|沪深股通十大成交股|
|[irm_qa_sh](apis/irm_qa_sh.md)|上证E互动|
|[irm_qa_sz](apis/irm_qa_sz.md)|深证互动易|
|[kpl_concept_cons](apis/kpl_concept_cons.md)|开盘啦题材成分|
|[kpl_list](apis/kpl_list.md)|开盘啦榜单数据|
|[limit_cpt_list](apis/limit_cpt_list.md)|最强板块统计|
|[limit_list_d](apis/limit_list_d.md)|涨跌停列表（新）|
|[limit_list_ths](apis/limit_list_ths.md)|涨跌停榜单（同花顺）|
|[limit_step](apis/limit_step.md)|连板天梯|
|[major_news](apis/major_news.md)|新闻通讯|
|[margin](apis/margin.md)|融资融券交易汇总|
|[margin_detail](apis/margin_detail.md)|融资融券交易明细|
|[margin_secs](apis/margin_secs.md)|融资融券标的（盘前更新）|
|[mkt_idx_bmk](apis/mkt_idx_bmk.md)|公募基金业绩基准库|
|[monetary_policy](apis/monetary_policy.md)|央行货币政策执行报告|
|[moneyflow](apis/moneyflow.md)|个股资金流向|
|[moneyflow_cnt_ths](apis/moneyflow_cnt_ths.md)|同花顺概念板块资金流向（THS）|
|[moneyflow_dc](apis/moneyflow_dc.md)|个股资金流向（DC）|
|[moneyflow_ind_dc](apis/moneyflow_ind_dc.md)|东财概念及行业板块资金流向（DC）|
|[moneyflow_ind_ths](apis/moneyflow_ind_ths.md)|同花顺行业资金流向（THS）|
|[moneyflow_mkt_dc](apis/moneyflow_mkt_dc.md)|大盘资金流向（DC）|
|[moneyflow_ths](apis/moneyflow_ths.md)|个股资金流向（THS）|
|[monthly](apis/monthly.md)|月线行情|
|[namechange](apis/namechange.md)|股票曾用名|
|[new_share](apis/new_share.md)|IPO新股列表|
|[news](apis/news.md)|新闻快讯|
|[npr](apis/npr.md)|国家政策法规库|
|[opt_basic](apis/opt_basic.md)|期权合约信息|
|[opt_daily](apis/opt_daily.md)|期权日线行情|
|[opt_mins](apis/opt_mins.md)|期权历史分钟行情|
|[pledge_detail](apis/pledge_detail.md)|股权质押明细|
|[pledge_stat](apis/pledge_stat.md)|股权质押统计数据|
|[report_rc](apis/report_rc.md)|卖方盈利预测数据|
|[repurchase](apis/repurchase.md)|股票回购|
|[research_report](apis/research_report.md)|券商研究报告|
|[rt_fut_min](apis/rt_fut_min.md)|期货实时分钟行情|
|[rt_idx_k](apis/rt_idx_k.md)|交易所指数实时日线|
|[rt_idx_min](apis/rt_idx_min.md)|A股实时分钟|
|[rt_k](apis/rt_k.md)|A股实时日线|
|[rt_min](apis/rt_min.md)|A股实时分钟|
|[rt_min_daily](apis/rt_min_daily.md)|A股实时分钟-日累计|
|[rt_sw_k](apis/rt_sw_k.md)|申万实时行情|
|[share_float](apis/share_float.md)|限售股解禁|
|[slb_len](apis/slb_len.md)|转融资交易汇总|
|[slb_len_mm](apis/slb_len_mm.md)|做市借券交易汇总|
|[slb_sec](apis/slb_sec.md)|转融券交易汇总|
|[slb_sec_detail](apis/slb_sec_detail.md)|转融券交易明细|
|[st](apis/st.md)|ST风险警示板股票|
|[stk_account](apis/stk_account.md)|股票账户开户数据|
|[stk_account_old](apis/stk_account_old.md)|股票账户开户数据（旧）|
|[stk_ah_comparison](apis/stk_ah_comparison.md)|AH股比价|
|[stk_alert](apis/stk_alert.md)|交易所重点提示证券|
|[stk_auction](apis/stk_auction.md)|当日集合竞价|
|[stk_auction_c](apis/stk_auction_c.md)|股票收盘集合竞价数据|
|[stk_auction_o](apis/stk_auction_o.md)|股票开盘集合竞价数据|
|[stk_factor](apis/stk_factor.md)|股票技术因子（量化因子）|
|[stk_factor_pro](apis/stk_factor_pro.md)|股票技术面因子(专业版)|
|[stk_high_shock](apis/stk_high_shock.md)|个股严重异常波动|
|[stk_holdernumber](apis/stk_holdernumber.md)|股东人数|
|[stk_holdertrade](apis/stk_holdertrade.md)|股东增减持|
|[stk_limit](apis/stk_limit.md)|每日涨跌停价格|
|[stk_managers](apis/stk_managers.md)|上市公司管理层|
|[stk_mins](apis/stk_mins.md)|股票历史分钟行情|
|[stk_nineturn](apis/stk_nineturn.md)|神奇九转指标|
|[stk_premarket](apis/stk_premarket.md)|股本情况（盘前）|
|[stk_rewards](apis/stk_rewards.md)|管理层薪酬和持股|
|[stk_seasoned](apis/stk_seasoned.md)|股票增发|
|[stk_shock](apis/stk_shock.md)|个股异常波动|
|[stk_surv](apis/stk_surv.md)|机构调研表|
|[stk_week_month_adj](apis/stk_week_month_adj.md)|股票周/月线行情(复权--每日更新)|
|[stk_weekly_monthly](apis/stk_weekly_monthly.md)|股票周/月线行情(每日更新)|
|[stock_basic](apis/stock_basic.md)|股票基础信息|
|[stock_company](apis/stock_company.md)|上市公司基本信息|
|[stock_hsgt](apis/stock_hsgt.md)|沪深港通股票列表|
|[stock_st](apis/stock_st.md)|ST股票列表|
|[suspend_d](apis/suspend_d.md)|每日停复牌信息|
|[sz_daily_info](apis/sz_daily_info.md)|深圳市场每日交易概况|
|[top10_cb_holders](apis/top10_cb_holders.md)|可转债十大持有人|
|[top10_floatholders](apis/top10_floatholders.md)|前十大流通股东|
|[top10_holders](apis/top10_holders.md)|前十大股东|
|[top_inst](apis/top_inst.md)|龙虎榜机构明细|
|[top_list](apis/top_list.md)|龙虎榜每日明细|
|[trade_cal](apis/trade_cal.md)|交易日历|
|[vix_index](apis/vix_index.md)|美股恐慌指数行情|
|[weekly](apis/weekly.md)|周线行情|
|[yc_cb](apis/yc_cb.md)|国债收益率曲线|

## 财务与公司行为

|接口或文档|说明|
|---|---|
|[balancesheet](apis/balancesheet.md)|资产负债表|
|[cashflow](apis/cashflow.md)|现金流量表|
|[dividend](apis/dividend.md)|分红送股|
|[express](apis/express.md)|业绩快报|
|[fina_audit](apis/fina_audit.md)|财务审计意见|
|[fina_indicator](apis/fina_indicator.md)|财务指标数据|
|[fina_mainbz](apis/fina_mainbz.md)|主营业务构成|
|[forecast](apis/forecast.md)|业绩预告|
|[income](apis/income.md)|利润表|

## 债券与其他资产

|接口或文档|说明|
|---|---|
|[bc_bestotcqt](apis/bc_bestotcqt.md)|柜台流通式债券最优报价|
|[bc_otcqt](apis/bc_otcqt.md)|柜台流通式债券报价|
|[bond_blk](apis/bond_blk.md)|债券大宗交易|
|[bond_blk_detail](apis/bond_blk_detail.md)|大宗交易明细|
|[cb_basic](apis/cb_basic.md)|可转债基本信息|
|[cb_call](apis/cb_call.md)|可转债赎回信息|
|[cb_daily](apis/cb_daily.md)|可转债行情|
|[cb_factor_pro](apis/cb_factor_pro.md)|可转债技术因子(专业版)|
|[cb_issue](apis/cb_issue.md)|可转债发行|
|[cb_price_chg](apis/cb_price_chg.md)|可转债转股价变动|
|[cb_rate](apis/cb_rate.md)|可转债票面利率|
|[cb_rating](apis/cb_rating.md)|获取可转债评级历史记录|
|[cb_share](apis/cb_share.md)|可转债转股结果|
|[repo_daily](apis/repo_daily.md)|债券回购日行情|
|[sge_basic](apis/sge_basic.md)|黄金现货基础信息|
|[sge_daily](apis/sge_daily.md)|现货黄金日行情|

## 指数与成分

|接口或文档|说明|
|---|---|
|[ci_daily](apis/ci_daily.md)|中信行业指数行情|
|[ci_index_member](apis/ci_index_member.md)|中信行业成分|
|[dc_index](apis/dc_index.md)|概念板块|
|[idx_anns](apis/idx_anns.md)|指数公告|
|[idx_factor_pro](apis/idx_factor_pro.md)|指数技术因子(专业版)|
|[idx_mins](apis/idx_mins.md)|指数历史分钟行情|
|[index_basic](apis/index_basic.md)|指数基本信息|
|[index_classify](apis/index_classify.md)|申万行业分类|
|[index_daily](apis/index_daily.md)|指数日线行情|
|[index_dailybasic](apis/index_dailybasic.md)|大盘指数每日指标|
|[index_global](apis/index_global.md)|国际指数|
|[index_member_all](apis/index_member_all.md)|申万行业成分构成(分级)|
|[index_monthly](apis/index_monthly.md)|指数月线行情|
|[index_weekly](apis/index_weekly.md)|指数周线行情|
|[index_weight](apis/index_weight.md)|指数成分和权重|
|[sw_daily](apis/sw_daily.md)|申万行业日线行情|
|[sw_mins](apis/sw_mins.md)|SW指数历史分钟|
|[tdx_daily](apis/tdx_daily.md)|TDX板块行情|
|[tdx_index](apis/tdx_index.md)|TDX板块信息|
|[tdx_member](apis/tdx_member.md)|TDX板块成分|
|[ths_daily](apis/ths_daily.md)|板块指数行情|
|[ths_hot](apis/ths_hot.md)|THS热榜|
|[ths_index](apis/ths_index.md)|概念和行业指数|
|[ths_member](apis/ths_member.md)|概念板块成分|

## 宏观与利率

|接口或文档|说明|
|---|---|
|[cn_cpi](apis/cn_cpi.md)|居民消费价格指数|
|[cn_gdp](apis/cn_gdp.md)|GDP数据|
|[cn_m](apis/cn_m.md)|货币供应量|
|[cn_pmi](apis/cn_pmi.md)|采购经理人指数|
|[cn_ppi](apis/cn_ppi.md)|工业生产者出厂价格指数|
|[cn_schedule](apis/cn_schedule.md)|中国经济数据发布日程|
|[eco_cal](apis/eco_cal.md)|财经日历|
|[hibor](apis/hibor.md)|Hibor利率|
|[libor](apis/libor.md)|Libor拆借利率|
|[sf_month](apis/sf_month.md)|社融数据（月度）|
|[shibor](apis/shibor.md)|Shibor利率数据|
|[shibor_lpr](apis/shibor_lpr.md)|LPR贷款基础利率|
|[shibor_quote](apis/shibor_quote.md)|Shibor报价数据|
|[wz_index](apis/wz_index.md)|温州民间借贷利率（暂停更新）|

## 基金与ETF

|接口或文档|说明|
|---|---|
|[etf_auction](apis/etf_auction.md)|ETF当日集合竞价|
|[etf_basic](apis/etf_basic.md)|ETF基础信息|
|[etf_index](apis/etf_index.md)|ETF基准指数列表|
|[etf_limit](apis/etf_limit.md)|ETF每日涨跌停价格|
|[etf_mins](apis/etf_mins.md)|ETF历史分钟行情|
|[etf_sh_cons](apis/etf_sh_cons.md)|ETF每日持仓组合(沪市）|
|[etf_share_size](apis/etf_share_size.md)|ETF份额规模|
|[etf_sz_cons](apis/etf_sz_cons.md)|ETF每日持仓组合(深市）|
|[fund_adj](apis/fund_adj.md)|基金复权因子|
|[fund_basic](apis/fund_basic.md)|公募基金列表|
|[fund_company](apis/fund_company.md)|公募基金公司|
|[fund_daily](apis/fund_daily.md)|ETF日线行情|
|[fund_div](apis/fund_div.md)|公募基金分红|
|[fund_factor_pro](apis/fund_factor_pro.md)|场内基金技术因子(专业版)|
|[fund_manager](apis/fund_manager.md)|基金经理|
|[fund_nav](apis/fund_nav.md)|公募基金净值|
|[fund_portfolio](apis/fund_portfolio.md)|公募基金持仓数据|
|[fund_share](apis/fund_share.md)|基金规模数据|
|[rt_etf_k](apis/rt_etf_k.md)|ETF实时日线|
|[rt_etf_min](apis/rt_etf_min.md)|ETF实时分钟|
|[rt_etf_min_daily](apis/rt_etf_min_daily.md)|ETF实时分钟-日累计|
|[rt_etf_sz_iopv](apis/rt_etf_sz_iopv.md)|ETF实时参考|

## 期货

|接口或文档|说明|
|---|---|
|[ft_limit](apis/ft_limit.md)|期货合约涨跌停价格（盘前）|
|[ft_mins](apis/ft_mins.md)|期货历史分钟行情|
|[fut_adjfactor](apis/fut_adjfactor.md)|期货复权因子|
|[fut_basic](apis/fut_basic.md)|期货合约信息表|
|[fut_daily](apis/fut_daily.md)|期货日线行情|
|[fut_daily_adj](apis/fut_daily_adj.md)|期货复权行情|
|[fut_holding](apis/fut_holding.md)|每日成交持仓排名|
|[fut_index_daily](apis/fut_index_daily.md)|南华期货指数日线行情|
|[fut_inv_weekly](apis/fut_inv_weekly.md)|期货交易所库存周报|
|[fut_mapping](apis/fut_mapping.md)|期货主力与连续合约|
|[fut_rcpt_mat](apis/fut_rcpt_mat.md)|标准仓单到期数量|
|[fut_settle](apis/fut_settle.md)|结算参数|
|[fut_trade_cal](apis/fut_trade_cal.md)|交易日历|
|[fut_trade_param](apis/fut_trade_param.md)|期货交易参数|
|[fut_trade_param_cfx](apis/fut_trade_param_cfx.md)|中金期货交易参数|
|[fut_trade_param_dce](apis/fut_trade_param_dce.md)|大连商品交易所交易参数|
|[fut_trade_param_gfe](apis/fut_trade_param_gfe.md)|广州期货交易参数|
|[fut_weekly_detail](apis/fut_weekly_detail.md)|期货主要品种交易周报|
|[fut_weekly_monthly](apis/fut_weekly_monthly.md)|期货周/月线行情(每日更新)|
|[fut_wsr](apis/fut_wsr.md)|仓单日报|

## 港股

|接口或文档|说明|
|---|---|
|[hk_adjfactor](apis/hk_adjfactor.md)|港股复权因子|
|[hk_balancesheet](apis/hk_balancesheet.md)|港股资产负债表|
|[hk_basic](apis/hk_basic.md)|港股列表|
|[hk_cashflow](apis/hk_cashflow.md)|港股现金流量表|
|[hk_daily](apis/hk_daily.md)|港股行情|
|[hk_daily_adj](apis/hk_daily_adj.md)|港股复权行情|
|[hk_fina_indicator](apis/hk_fina_indicator.md)|港股财务指标数据|
|[hk_income](apis/hk_income.md)|港股利润表|
|[hk_mins](apis/hk_mins.md)|港股分钟行情|
|[hk_tradecal](apis/hk_tradecal.md)|港股交易日历|
|[rt_hk_k](apis/rt_hk_k.md)|港股实时日线|

## 组合管理（非策略回放调用）

|接口或文档|说明|
|---|---|
|[p_delete](apis/p_delete.md)|自选股组合删除|
|[p_get](apis/p_get.md)|自选股组合查询|
|[p_list](apis/p_list.md)|自选股组合查询|
|[p_save](apis/p_save.md)|自选股组合保存|

## 美股

|接口或文档|说明|
|---|---|
|[us_adjfactor](apis/us_adjfactor.md)|美股复权因子|
|[us_balancesheet](apis/us_balancesheet.md)|美股资产负债表|
|[us_basic](apis/us_basic.md)|美股列表|
|[us_cashflow](apis/us_cashflow.md)|美股现金流量表|
|[us_daily](apis/us_daily.md)|美股行情|
|[us_daily_adj](apis/us_daily_adj.md)|美股复权行情|
|[us_etf_basic](apis/us_etf_basic.md)|美股ETF列表|
|[us_etf_daily](apis/us_etf_daily.md)|美股ETF日行情表|
|[us_fina_indicator](apis/us_fina_indicator.md)|美股财务指标数据|
|[us_income](apis/us_income.md)|美股利润表|
|[us_tbr](apis/us_tbr.md)|短期国债利率|
|[us_tltr](apis/us_tltr.md)|国债长期利率|
|[us_tradecal](apis/us_tradecal.md)|美股交易日历|
|[us_trltr](apis/us_trltr.md)|国债实际长期利率平均值|
|[us_trycr](apis/us_trycr.md)|国债实际收益率曲线利率|
|[us_tycr](apis/us_tycr.md)|国债收益率曲线利率（日频）|

## 官方使用说明与包装

|接口或文档|说明|
|---|---|
|[doc-14](guides/14.md)|上市公司数据|
|[doc-15](guides/15.md)|行情数据|
|[doc-16](guides/16.md)|财务数据|
|[doc-17](guides/17.md)|市场参考数据|
|[doc-18](guides/18.md)|基金数据|
|[doc-24](guides/24.md)|基础数据|
|[doc-31](guides/31.md)|官方说明 31|
|[doc-93](guides/93.md)|Tushare 指数代码标准化说明|
|[doc-104](guides/104.md)|官方说明 104|
|[doc-109](guides/109.md)|通用行情接口|
|[doc-134](guides/134.md)|Tushare期货数据|
|[doc-142](guides/142.md)|特色大数据|
|[doc-146](guides/146.md)|A股复权行情|
|[doc-147](guides/147.md)|宏观经济数据|
|[doc-148](guides/148.md)|利率数据|
|[doc-157](guides/157.md)|Tushare期权数据|
|[doc-177](guides/177.md)|Tushare外汇数据|
|[doc-184](guides/184.md)|Tushare债券数据|
|[doc-190](guides/190.md)|Tushare港股数据|
|[doc-217](guides/217.md)|国际宏观经济数据|
|[doc-218](guides/218.md)|美国利率统计数据|
|[doc-224](guides/224.md)|国内宏观经济|
|[doc-225](guides/225.md)|国民经济核算|
|[doc-226](guides/226.md)|价格指数|
|[doc-240](guides/240.md)|官方说明 240|
|[doc-241](guides/241.md)|官方说明 241|
|[doc-251](guides/251.md)|美股数据|
|[doc-283](guides/283.md)|现货数据|
|[doc-291](guides/291.md)|股票特色数据|
|[doc-309](guides/309.md)|官方说明 309|
|[doc-314](guides/314.md)|期货Tick行情数据|
|[doc-324](guides/324.md)|官方说明 324|
|[doc-330](guides/330.md)|官方说明 330|
|[doc-342](guides/342.md)|官方说明 342|
|[doc-346](guides/346.md)|官方说明 346|
|[doc-384](guides/384.md)|官方说明 384|
|[doc-474](guides/474.md)|自定义组合管理 —— 从选股到跟踪的"最后一公里"|
|[doc-485](guides/485.md)|Tushare 量化因子库介绍|
