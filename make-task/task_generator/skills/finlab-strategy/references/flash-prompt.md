# 给较小模型的策略生成提示包

本页给出可复用的工作提示，不包含交易策略答案。先将本技能目录和当前公开任务目录放到模型能读取的位置；不需要把整套API文档塞进一轮上下文。

## 首轮提示

```text
使用 finlab-strategy 技能为当前任务生成可提交代码。
先读 SKILL.md、任务的TASK.md、bridge_contract.json、runtime_profile.json、SUBMISSION.md。
运行资源必须由使用者本人执行finlab setup选择；CPU/内存推荐不是默认或已确认规范。
遇到SETUP_REQUIRED时报告需要本人配置，不代填、不创建确认文件、不用--timeout覆盖。
单次运行时限由人选择正整数秒或none，没有默认或推荐时限；不与SDK单回调响应限制混淆。
只允许使用任务已授权的标的、接口、字段、频率和虚拟时间。
所有任务年份都是虚拟时间标签，默认在未来数百年，不是同一现实年份的行情。
不要按虚拟年份套用股灾、政策、节假日或历史事件；不搜索或猜测真实时期。
只依据当前可见数据、平台事件和返回交易日历决策。
数据按阶段累计披露，任务总范围不代表当前全部开放；正式策略另受当前事件时钟限制。
范围为[start,stop)；显式请求部分越界也整次报TIME_RANGE_EXCEEDED，没有行情返回。
保留错误信息和request_id，不转换为空表、补零或当作停牌；审计故障BRIDGE_AUDIT_UNAVAILABLE交平台处理。
未来年份可能超过Pandas纳秒日期上限；用标准库datetime/date处理日期，不将越界值转NaT。
先给出简短的调用计划：所需接口、字段、单位、日期格式、可见性和缺口。
再只检索这些接口的离线文档，读取strategy-sdk.md、submission.md和相关陷阱表。
用五文件模板实现strategy.py、backtest.py、params.json、RESEARCH.md、CHANGELOG.md。
遵循finlab.strategy.v2，不直接导入tushare、akshare或akquant。
不猜测ctx API；只有on_bars可下单，ctx.order返回None。
状态存入有限JSON memory，初始化不重置上一段记忆。
生成后执行技能preflight；能运行平台研发回测时，再执行实际Docker验证。
区分静态通过和实际回测通过；没有执行的检查写为未执行。
发现未绑定或缺失数据时准确列出缺口，不伪造行情或成绩。
```

## 按问题追加资料

|问题|给模型的最小补充|
|---|---|
|不知道某接口怎么调用|该接口离线正文与任务对应契约项，不给全库|
|分不清年月、分钟日期格式|tushare-bridge.md对应段及该API输入表|
|误把虚拟年份当历史事件线索，或日期转成NaT|tushare-bridge.md的虚拟日期格式段、原始虚拟时间字符串和实际错误|
|写成其他回测框架|strategy-sdk.md的回调、上下文和订单签名|
|JSON memory报错|实际错误码、相关memory字段及SDK状态段|
|有订单但没成交|订单/成交系统结果及绑定交易规则，不提供未来行情|
|段间重置状态|上一成功段的可见行为、CHANGELOG及初始化代码|
|出现SETUP_REQUIRED|报告缺口，提醒使用者本人运行finlab setup；AI不代选资源或生成确认文件|
|出现TIME_RANGE_EXCEEDED|tushare-bridge.md的阶段披露与越界段、虚拟查询范围、当前时钟及request_id；不提供未来数据|
|出现BRIDGE_AUDIT_UNAVAILABLE|报告错误信息与request_id，由平台检查审计存储，不换上游补抓|

## 修复提示

```text
根据下面的预检或实际运行错误修复当前提交。
先定位到任务契约、技能中对应API和当前代码，不凭记忆发明新方法。
只修改与错误有关的实现，保留已经正确的账户与memory连续性。
报告根因、修改文件、执行的检查和剩余缺口，并更新CHANGELOG。
实际错误与相关文件：由调用方在此处提供。
```

调用方应只向模型提供已解锁的研究材料和运行反馈。官方文档示例不构成任务数据；离线手册中不相关的原生引擎示例不应改变提交协议。
