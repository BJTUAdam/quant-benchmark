# API与批次配置

default.json是APIClient直接读取的配置对象，当前工作区已配置DeepSeek服务与模型；三份厂商example预设仍要求填写实际模型。配置时填写服务商实际地址、生成模型、可选审查模型及API密钥环境变量名称；密钥值只保存在运行进程环境中，不写入JSON、题库、提示词或日志。

2026-11-05补充原生v2真实验收：主批次B-20261105-041626-d5ef52记录64次成功HTTP响应，实际返回deepseek-flash。18份候选中15份通过当前静态规格检查并完成有效整批复核，独立新题通过数为0（11份重复、4份变体）；3份错误交易所候选只保留失败摘要。详见[实测任务报告](../docs/v2/GENERATED_TASKS.md)。正常默认单批HTTP上限仍是48；针对实测修复的额外复核使用显式72次有限上限，未改变default.json。此次凭证仅在调用进程环境中使用，不包含在配置或交付包。

2026-10-30（北京时间）已完成DeepSeek真实连接与任务生成验收：`/models`认证请求成功，主批次`B-20261029-184828-56539a`有47次成功HTTP响应；初试另有4次成功响应及1次中断后的预留计数。服务实际返回模型为`deepseek-flash`。这是调用证据，不等于47道合格任务或自动人工批准。凭据仅由运行进程持有，该进程现已关闭，密钥未落盘。自动化测试使用临时目录和本机假HTTP服务，OneAPI与GLM目前也只有本机模拟协议验收。

## OneAPI、DeepSeek、GLM预设

三种接入均使用`adapter=chat_completions`，通过服务地址和模型标识区分；`provider_name`仅作配置标签，不选择适配器，也不参与请求。官方DeepSeek和GLM不需要本项目另造签名或专用SDK。

| 配置文件 | base_url | 密钥环境变量 |
|---|---|---|
| oneapi.example.json | http://127.0.0.1:3000/v1，本机OneAPI示例，需改为实际部署地址 | TASKGEN_API_KEY |
| deepseek.example.json | https://api.deepseek.com | DEEPSEEK_API_KEY |
| glm.example.json | https://open.bigmodel.cn/api/paas/v4 | GLM_API_KEY |

使用前在所选预设填写`generation_model`，填写账户实际可用模型标识；在`doctor`、`generate`或`review`子命令后使用`--config`指定该JSON文件。`review_model`可留空，默认回退为`generation_model`并执行独立审查调用；也可填写同一服务中的另一模型。生成模型留空时客户端在发起HTTP请求前报错，避免假定账户权限或悄悄采用其他默认模型。当前单份配置共用同一服务地址及密钥。不同命令可显式选择不同服务配置，例如DeepSeek生成、GLM全批复审；同一个generate命令内部共用一份服务配置。

`base_url`接受`.../v1`、`.../v1/`、`.../v1/chat/completions`或带结尾斜杠的完整端点，完整地址不会重复拼接`/chat/completions`。OneAPI中的模型名由部署者配置，不能假定与上游官方别名一致。GLM需使用匹配“模型标准API”产品的密钥；个人套餐或团队套餐的密钥不保证通用。

| 字段 | 含义 |
|---|---|
| adapter | chat_completions，或provider_plugin |
| provider_name | 可选配置标签，不影响适配器与请求格式 |
| base_url | 版本化API根地址或完整chat/completions地址；例如官方API根地址为https://api.openai.com/v1。当前default使用DeepSeek地址 |
| generation_model | 生成及修复阶段使用的实际模型标识；example预设留空，当前default使用deepseek-flash |
| review_model | stage=review使用的实际模型标识；为空或未设置时回退generation_model，并独立调用 |
| api_key_env | 保存密钥的环境变量名称，默认TASKGEN_API_KEY |
| timeout_seconds | 每个HTTP尝试的超时，默认60秒 |
| max_tokens | 可选正整数，限制单次生成token数；未配置不发送，由服务商决定默认值 |
| temperature | 可选0到2之间的有限数值；未配置不发送，部分推理模式会忽略此参数 |
| thinking | 可选字符串enabled或disabled，按已核验的DeepSeek协议发送为thinking对象的type；未配置不发送，其他厂商支持需独立确认 |
| max_retries | 每次请求可额外重试次数，仅429、5xx及超时；默认2 |
| format_retries | 非法JSON对象可追加格式修复次数，默认1；不是TaskSpec语义修复次数 |
| max_calls_per_batch | 同一APIClient在该批次的HTTP尝试总上限，含失败重试和JSON格式修复，默认48 |
| json_mode | off不传response_format；json_object传JSON对象模式；json_schema需传schema并要求服务商支持结构化输出。布尔true/false兼容映射为json_object/off |
| provider_plugin | 可信Python模块路径:类名，仅provider_plugin适配器使用 |

未支持JSON模式的兼容服务可显式使用off；不自动在400等错误后猜测参数或切换端点。配置json_schema时，传入的schema需符合所选服务商和模型实际支持的子集；APIClient只验证有限数值JSON对象，TaskSpec和审查对象的结构、业务条件另由工作流验证。

budget是每个APIClient实例的累计计数，不是全局账户限额。工作流一批候选共用一个实例，不能每题重新创建以重置配额。调用方可传`reserve_attempt`回调，每次真正HTTP尝试前（含重试、JSON修复）先持久化预留；回调失败时不发送请求，也不回显原异常。批次跨命令累计与并发互斥由工作流负责。max_candidates是最多尝试多少份候选，与HTTP调用上限独立；默认每个F01至F06家族各收1题、最多18份候选。重试和修复可能先耗尽HTTP预算，不能保证一批收满6题。

## 原生接口扩展

provider_plugin_example.json和taskgen/provider_example.py演示原生接口契约。该示例使用自定义请求/响应格式，不对应任何真实厂商，不可不经适配直接作为真实服务调用。

可信适配器类提供：

```python
class Adapter:
    def __init__(self, config: dict): ...
    def build_request(self, messages, *, model, api_key, json_mode, schema):
        # 返回urllib.request.Request；禁止自行联网，否则绕过统一配额。
        ...
    def extract_content(self, response_object):
        # 返回模型正文str或JSON对象dict。
        ...
```

APIClient负责网络、重试、超时、调用计数和安全日志。插件是运营方信任的可执行Python代码，不能采用模型输出或用户数据中的导入路径。每次HTTP尝试日志记录stage、配置model、status、duration_seconds和UTC结束时间ended_at_utc；成功响应可附response_id、response_model、finish_reason及白名单整数token用量usage（含已知缓存与推理token明细）。响应标识先脱敏再限制字符与长度，日志不记录地址、消息、生成正文、推理正文、请求头或密钥；错误不回显服务商响应。为避免携带认证重定向，HTTP重定向被拒绝。

当`finish_reason=length`时，日志记录`completion_status=truncated`并直接返回固定APIError；该输出已截断，客户端不会继续用格式修复掩盖缺失内容。需增加max_tokens或减少上下文后重新运行。HTTP日志中的200只表示收到响应，不表示TaskSpec通过校验或已经人工批准。自定义原生插件仍需自行映射max_tokens、temperature和thinking到厂商字段。内置Chat Completions适配器按显式配置发送这些参数，不自动添加stream或reasoning_effort。

## 官方文档核对记录

2026-10-30按OpenAI Docs工作流先请求下列OpenAI官方页面；当前环境全部返回HTTP 403，未能取得页面正文。因此这里记录协议依据入口及抓取限制，不声称本次已在线核验OpenAI全部字段。实现按Chat Completions兼容协议构建，经过本地HTTP契约测试及上述DeepSeek真实调用；OneAPI、GLM及其他服务的实际连接仍需各自验证。

- Chat Completions创建接口：https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
- 官方旧接口入口：https://platform.openai.com/docs/api-reference/chat/create
- Structured Outputs：https://developers.openai.com/api/docs/guides/structured-outputs
- Error Codes：https://platform.openai.com/docs/guides/error-codes/api-errors

同日已取得下列DeepSeek与智谱官方页面正文（HTTP 200），核对了DeepSeek的`/chat/completions`路径、官方SDK示例中的`https://api.deepseek.com`根地址及`response_format={"type":"json_object"}`；智谱官方页核对了`https://open.bigmodel.cn/api/paas/v4/chat/completions`端点和`Authorization: Bearer`认证。DeepSeek文档另提示JSON模式需要在提示中明确要求JSON，可能返回空正文，客户端会对空或非法模型输出执行有限次格式修复。

- DeepSeek接口：https://api-docs.deepseek.com/api/create-chat-completion
- DeepSeek JSON模式：https://api-docs.deepseek.com/guides/json_mode
- GLM接口与认证：https://docs.bigmodel.cn/cn/api/introduction

DeepSeek当前模型表（同日HTTP 200）：https://api-docs.deepseek.com/quick_start/model_list 。页面列出`deepseek-flash`与`deepseek-v4-pro`，并说明`deepseek-flash`由DeepSeek-V4.1-Flash-1017提供。接口文档明确支持`thinking={"type":"disabled"}`和enabled，当前默认enabled；配置JSON可写`"thinking":"disabled"`以显式选择非思考模式。temperature在thinking模式无效；max_tokens可包含该模式生成的推理token，较低上限可能在JSON正文完成前截断。预设仍保留模型名为空，实际使用时按账户当前可用模型填写。

这三份example预设均不含真实密钥。DeepSeek真实验收使用独立运行进程环境中的凭据与工作区配置；OneAPI和GLM没有真实服务调用证据，不能由本机模拟推断账户权限或模型可用性。各厂商的原生`json_schema`模式均未实测，DeepSeek本轮使用`json_object`。GLM模型的JSON模式支持以当前模型文档为准，若不支持可显式设`json_mode=off`并保留客户端JSON修复。

所选服务商的base_url、模型访问权限、JSON Schema支持、限额和计费不能由“OpenAI-compatible”这一名称自动推出。首次配置只做小规模连接验收，再启动整批候选。
