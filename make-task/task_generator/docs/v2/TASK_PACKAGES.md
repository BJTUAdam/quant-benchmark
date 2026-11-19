# 完整系统整合版与12个题包

本次发行版本为 `v2-integrated-task12-20261110`。题包入口、离线SDK说明、期货品种池匹配已合并进源码。解压这份完整系统即可使用，不需要另外下载或应用补丁；固定Docker镜像、策略SDK和可信引擎的版本保持已验收的锁定标识。

## 目录与读取顺序

```text
开始阅读.md
VERSION_MANIFEST.json
DELIVERY_MANIFEST.json
images/finlab_images_v2.tar
task_generator/
  finlab/、taskgen/           已合并修复的程序
  skills/finlab-strategy/    策略AI技能、官方接口离线资料
  docs/system_v2.html        可离线打开的系统说明
  docs/v2/AI_README.md        平台AI阅读入口
  task_packages/20261110/
    题包索引.md
    任务清单.json
    可用性检查报告.json
    Task_01_F01/ … Task_12_F06/
      public/               给策略AI的十一文件
      operator/             平台规格、核验和接入待办
      README.md、MANIFEST.json
    zips/                   十二个可单独分发的题目ZIP
```

首次打开本项目的README和本页，再按角色读取AI_README或策略技能。十二份题面可以从HTML的“12个题包”直接展开查看，或在task_packages/20261110/题包索引.md中选择单独ZIP。

只给策略AI时，提供某题的public目录和通用策略技能；operator、冻结缓存、上游凭证及真实时间映射留给平台。task_input.draft.json中的相对路径以该JSON所在目录为准，首轮文件为同目录TASK.md。

## 已合并的修复

- public独立分发后，题面、桥接契约和运行配置入口都能定位；生成、接纳和导出沿用同一相对路径约定。
- 题包的SUBMISSION.md内嵌真实SDK字段、回调限制、订单与成交语义；DATA_API.md包含本题字段与参数、DataFrame/列表返回、分页和越界错误说明，不链接缺失的技能脚本。
- 期货品种池可在明确范围内解析为真实月份合约；其他品种、错误交易所、没有月份的品种代号与无效月份仍被拒绝。产品池授权不证明真实合约或历史数据存在。
- 从六份历史自动接纳记录复用或导出时，只规范化导出副本的入口元信息，原研究正文、规格和模型复核快照保持不变，变更在导出清单中记录。

## 新题包与历史记录的状态

|内容|规格与来源|数据/运行|
|---|---|---|
|task_packages内十二题|本轮模型交叉审阅和程序检查通过；未执行外部API复核，未写自动接纳库|未绑定真实数据，未完成实际研究策略和正式回测|
|examples/accepted内六份|保留真实自动接纳证据；三份重复、三份变体，独立新题计数为0|数据与运行状态另行核验|
|examples/generated内十五份|保留原始DeepSeek候选快照及审核事实|只读历史对照，不因重打包自动转正|

新十二题可以作为题面和策略开发接口规范交付。正式接入仍需要有效模型复核/自动接纳、实际数据冻结、交易规则、阶段权限及本人资源配置。没有人工题目审批步骤。

## 安装和镜像

在task_generator目录安装项目并导入已提供的固定镜像：

```text
python -m pip install -e .
finlab backtest load-images ../images/finlab_images_v2.tar
finlab task init
finlab doctor
```

本人在交互终端执行finlab setup后才能启动实际回测。CPU/内存只是建议，运行时限没有默认值，AI不得代填；生成、阅读、校验和导入题目规格不需要代做该设置。

需要外部模型复核时，将密钥放到配置指定的环境变量，不写进题包。自建Tushare地址由TUSHARE_UPSTREAM_URL或本机私有配置提供，令牌来自环境变量。完整包不含真实凭证或本机资源确认记录。

## 选择一个新任务接入

```text
finlab task import-spec task_packages/20261110/Task_01_F01/operator/spec.json
finlab task review --batch IMPORTED_BATCH_ID --api
```

系统按当前模型证据自动尝试接纳，随后按DELIVERY.md完成数据冻结、delivery prepare/check和研发验收。task_input.draft.json仍是草案入口，不代表未绑定数据已经可查，也不代表外部批跑管线的适配已验收。

共享的空仓模板仅用于接口连通性。股票、期货合成Docker测试验证过查询、分页、买卖成交、越界整次拒绝及两段状态延续，不能当作十二个研究策略或真实行情回测的成绩。
