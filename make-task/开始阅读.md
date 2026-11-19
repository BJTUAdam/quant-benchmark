# 金融长程任务系统 v2 · 修复整合版（含12个题包）

发行版本：v2-integrated-task12-20261110。代码已经合并入口、离线SDK说明和期货合约匹配修复，使用本完整包无需另行打补丁。本说明在总包根目录保存为“开始阅读.md”。

完整解压到较短路径后，首先打开 `task_generator/docs/system_v2.html`，阅读“完整包与12题包”。开发/平台AI从 `task_generator/docs/v2/AI_README.md` 开始；策略AI读取通用策略技能及某一题的public目录。

## 十二题包的位置

`task_generator/task_packages/20261110/` 内有：

- `Task_01_F01` 至 `Task_12_F06`：展开后的十二题，六个方向各两题。
- `zips/`：十二个可单独分发的题目ZIP。
- `题包索引.md`、`任务清单.json`、`可用性检查报告.json`：选择题目及查看实际状态。

只给策略AI时，提供某题public目录和 `skills/finlab-strategy` 通用技能，不提供operator目录。public中的TASK.md是首轮题面；task_input.draft.json中的相对路径以该JSON所在目录为基准。

## 安装与镜像

在task_generator目录安装程序、载入已经提供的固定镜像：

```text
python -m pip install -e .
finlab backtest load-images ../images/finlab_images_v2.tar
finlab task init
finlab doctor
```

`images/finlab_images_v2.tar` 包含可信AKQuant引擎和策略SDK容器。本次修复位于主机端及题包说明，引擎、SDK和镜像标识保持已验收的锁定版本，不需重建镜像。

## 规格和运行状态

题目不要求人工审批，生成或最终复核后自动尝试接纳；有效历史候选可使用task accept。十二份新题已完成程序检查及会话模型交叉审阅，尚未执行外部API复核、自动接纳、真实数据绑定和正式回测。

`examples/accepted` 内六份历史自动接纳记录保留原事实，与本批十二题分开。重复/变体不计独立新题。`examples/generated` 内十五份历史候选快照保持原字节和审核事实。

实际回测前，仍需平台配置模型/数据接口，完成数据冻结与运行绑定，并由使用者本人执行finlab setup选择CPU、内存和运行时限；AI不得代填。CPU/内存只有非生效建议，时限无默认值。任务日期是未来虚拟事件标签，不能按年份套用历史事件。

## 数据与AI资料

所有市场数据经受限桥接器取得。平台上游支持TUSHARE_UPSTREAM_URL自定义地址（包括127.0.0.1），令牌来自环境或私有配置。策略只使用MARKET_BRIDGE_URL与任务令牌，不获取真实上游凭证或私有时间映射。显式越界整次403，审计失败503，均不返回行情。

离线手册为 `docs/tushare_api_offline.html`。Skills附有246个Pro接口全文、13份AKQuant资料、策略SDK、检索和预检脚本。具体接口支持、权限、覆盖和历史可见性须逐题核验。回测引擎为AKQuant；AKShare是数据获取库。

正式接入教程：`docs/v2/TASK_PACKAGES.md`、`DELIVERY.md`、`DISCLOSURE.md`、`OPERATIONS.md`。合成回测验证接口与隔离机制，不是十二个研究策略或真实市场成绩。

## 清单与私有内容

本包包括完整源码、配置模板、Skills、离线HTML/Markdown、十二题包、固定镜像及检查报告。不包括真实行情缓存、模型/Tushare密钥、私有映射、账户检查点、运行日志、个人资源配置或私有登记库。

VERSION_MANIFEST.json记录发行版本、镜像及题包位置，DELIVERY_MANIFEST.json记录逐成员SHA-256。使用这份整合版，无需叠加旧ZIP。
