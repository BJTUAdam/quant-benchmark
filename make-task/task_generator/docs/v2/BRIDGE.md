# Tushare桥接、时间转换与冻结

## 配置两个地址

```powershell
$env:TUSHARE_UPSTREAM_URL = "http://127.0.0.1:9000"
$env:TUSHARE_TOKEN = "本机环境中设置实际令牌"
$env:MARKET_BRIDGE_URL = "http://127.0.0.1:8766"
$env:MARKET_BRIDGE_TOKEN = "本任务单独生成的访问令牌"
```

不设置上游地址时使用 https://api.tushare.pro。官方协议POST包含api_name、token、params、fields，返回code/msg/data.fields/items。令牌不跟随HTTP重定向，不将上游错误正文返回策略。

127.0.0.1指当前进程所在环境：宿主机采集可用本机上游；容器访问宿主机服务使用host.docker.internal；正式策略使用内部服务名engine，不直接访问上游。

## 全部Pro接口的含义

通用传输不硬编码行情接口名单。catalog/tushare_pro.v2.json保存官方目录快照，catalog/tushare_overrides.v2.json保存明确时间格式覆盖规则。catalog update重新抓取目录，catalog check逐接口输出字段、时间规则和回放状态。

目录收录不等于账号授权、数据存在、所有返回值格式已实测或历史可见性已证明。Pro组合写入类操作不作为参赛策略的实时上游操作；公开桥接只查询已冻结记录。实时接口可以采集，缺少历史证据的接口不能绑定成正式历史回放。

字段未知或格式无法可靠转换时返回UNCLASSIFIED_FIELD/UNCLASSIFIED_TIME_FORMAT，不能回退为真实时间透传。新上游扩展可登记同样的参数、字段、键和可见性规则。

## 时间映射

运营方默认年份偏移＋376，可配置。例：真实2024-02-29映射为虚拟2400-02-29，月份和季度保持一致。该值只出现在平台侧，不写入参与者任务包或技能。请求按逆映射查询，返回和公开结果按正映射输出。

公开任务和AI技能必须明确说明：未来年份仅为虚拟时间标签，不代表真实年份或未来预测，不能根据年份预设股灾、政策、节假日或查找历史答案。仅使用当前可见信息和返回的交易日历。未来日期可能超过Pandas默认纳秒时间范围，公开日期保留字符串；使用标准库datetime/date或明确支持的时间单位，不能静默转成NaT。引擎内部用真实交易时钟汇总指标。

时间戳必须登记epoch_s、epoch_ms、epoch_us或epoch_ns精度，不按位数猜测；未来的毫秒值可能与14位紧凑日期时间重叠，纳秒值可能超过64位整数。协议按Python整数精确传输，其他客户端不能转成JavaScript浮点数后再还原。

覆盖YYYYMMDD、ISO日期/日期时间、年/月/季度、秒/毫秒/微秒/纳秒时间戳、嵌套字段和登记的时间文本。时长与日内交易时刻不偏移。世纪闰年导致无效日期时拒绝映射，不将2月29日改成2月28日。

文本中的完整日期、年月、季度、独立年份和紧凑日期时间转换。可能是时间戳的长数字但没有精度规则时阻断；无法判定格式不直接返回。名称代码例外，M2509.DCE及其名称中的2025年9月仍保持原值。

整年映射会改变星期；服务返回的交易日历具有权威性。不要根据虚拟公历周末重新构造交易日。

## 采集与版本

data collect接受多个查询分区，每项显式声明接口、真实参数、字段、分页模式、唯一键、事件字段和availability依据。支持offset分页及单次已拆分查询；offset被忽略、重复整页或达到上限时失败。单次查询不自动声明覆盖完整。

允许四类时点依据：operator_supplied、publication_field、market_close_assumption、download_time。最后一种仅说明何时下载，不能证明过去可见，冻结时禁止用于正式历史回放。只有日期的公告默认日末可见，保守避免提前使用。

数据修订按相同唯一键、不同available_at保存。查询先过滤可见时点，再取最新可见版本，避免未来修订覆盖过去版本。

采集时保存请求分区与数据行的关联。freq等仅存在于请求中的条件据此回放；未知频率不会用其他频率替代。多个频率在相同唯一键上出现不同价格时，须拆分采集源/任务版本，不能把不同频率伪装为数据修订。

冻结必须明确确认coverage_confirmed和point_in_time，复制一致SQLite快照并保存内容哈希。正式回放不重新访问上游；缓存缺失、接口或字段未绑定、范围越界分别报错。

每接口默认event_scope=task_range，即使调用时不传日期，也限制在任务冻结区间。上市资料、已公告未来到期日等元数据可显式声明event_scope=visible_history；它允许历史事件记录，但仍要求available_at不晚于当前时钟。不能用此选项扩大行情窗口。

规则登记支持嵌套对象和数组；把dict/list误放进普通text/identity字段会阻断，不能先转换为字符串逃过时间检查。公开订单标识中的已登记到期日期也映射，UUID保持稳定；未知携带日期的标识会要求补规则。

## Python兼容入口

```python
from finlab import tushare as ts
pro = ts.pro_api()
df = pro.daily(ts_code="000001.SZ", trade_date="虚拟YYYYMMDD")
df = pro.query("daily", fields="ts_code,trade_date,close", limit=2000, offset=0)
# 列表兼容：ts.pro_api(dataframe=False)
# 通用包装：ts.pro_bar(..., api=pro, asset="E", freq="D", adj="qfq", ma=[5,20])
```

pro_bar支持已绑定的股票、基金、指数和期货日线及常用分钟接口，股票/指数周月线，日线复权、均线、换手率与量比。复权只使用当前可见因子；不支持的资产/频率组合或参数明确报错，不绕过桥接访问官方SDK。

分钟查询按目录声明的完整日期时间传start_date/end_date，例如虚拟2400-01-02 09:31:00；不能把日线YYYYMMDD格式用于已声明datetime的参数。行情、复权因子和daily_basic均自动按桥接分页获取；只获取任务范围及已可见数据。

## 可见性

阶段披露采用平台传入的累计 `[start, stop)` 范围。研发查询边界来自episode已提交状态；策略还受可信引擎当前事件时钟限制。显式请求任一部分越界，整次返回 `403 / TIME_RANGE_EXCEEDED`、中文错误信息和 `request_id`，不返回部分行情；SDK保留错误，不能转换为空DataFrame。省略日期、分页、动态接口和 `pro_bar` 都经过同一检查。客户端没有推进时钟或修改披露范围的路由。

披露策略与episode绑定并校验哈希；逐次审计写入平台私有SQLite。审计写入失败返回 `503 / BRIDGE_AUDIT_UNAVAILABLE`，不返回行情。配置、返回示例、审计位置及失败恢复见 [DISCLOSURE.md](DISCLOSURE.md)。

已经公告的未来到期日可以返回；不能简单删除所有包含未来日期的记录。历史成分、当前静态资料和修订财务记录分别声明时点证据。

范围有数据缺口时应在冻结前补齐，不能把空结果自动解释为停牌。市场数据的真实单位保留在接口契约，进入引擎时按feed的volume_multiplier、amount_multiplier规范化。
