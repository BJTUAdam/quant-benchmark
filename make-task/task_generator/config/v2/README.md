# 运营配置模板

这些配置尚未确认完整性或历史可见性，默认会阻止正式冻结或运行。以docs/v2/BRIDGE.md与OPERATIONS.md为准补齐。不要在配置内填写令牌。

binding模板的resources为null；实际预算由使用者本人在交互终端运行finlab setup选择并冻结到新episode。2CPU/4GB仅为推荐，无默认时限。AI不代填资源或本人确认文件。freeze模板的年份偏移属于运营方私有时间映射，不向策略公开。

创建episode时将已验证分段编译为disclosure.private.json，与binding/state中的哈希绑定，桥接启动必须有该策略。绑定文件可显式提供stage_windows数组，包含segment=0至7的连续[start,stop)真实YYYY-MM-DD边界，每段至少两个交易日且覆盖完整任务；不提供时沿用现有交易日分段程序，不预填任何真实日期。

freeze中每接口可提供query_time_rules，键为已登记参数路径，值为role（lower/upper/select）与target（event_at或登记字段）。日期选择语义不明时保持阻断；不能通过填写特殊角色绕过披露范围。只有审核过的基础元信息接口可以声明event_scope=visible_history，行情、复权、估值、涨跌停和成分序列不得使用该例外。详见docs/v2/DISCLOSURE.md。
