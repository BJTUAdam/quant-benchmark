# DeepSeek 实测生成任务 · 2026-11-05

已经用有效凭证真实调用DeepSeek，完成原生v2生成、程序检查和修正后的整批模型复核。凭证只在调用进程环境中使用，不随交付包保存。

本轮生成18份候选，其中15份通过当前静态规格校验并保留完整候选包，3份因错误的FG.SHF交易所后缀仅保留失败摘要。覆盖六个方向。

**本轮独立新题初筛通过：0；人工批准：0。** 这些是新生成的待审草案，其中包含重复和变体，不能计作已通过的新母题。不能声称已经补齐六道独立新题。

## 实际调用与修复

- 服务实际返回模型：deepseek-flash。
- 主批次HTTP预留尝试64次、已记录64次、成功响应64次；生成与审查是独立调用。
- 正常配置仍为单批48次HTTP尝试。为验证实测发现的修复，本轮明确使用72次的有限复核上限；最终实际使用量见上述记录。未再追加候选来凑新题。
- 停止过一个早期基线批次：相同提交文件只因顺序不同被误拒。其预留、已记录和未观测尝试单独计数，未把预留数当成成功数。
- 修复固定协议集合数组的顺序误判；重复、遗漏或额外成员仍被拒绝。
- 增加期货品种与交易所后缀校验。
- 修复变体候选反复退出对照集造成审核记录过期的问题。有效候选稳定参与比较，完成后再次整批复核。

[机器可读实测报告](../live_generation_v2.json) · [批次逐文件哈希与清单](../../examples/generated/B-20261105-041626-d5ef52/manifest.json) · [批次检查报告](../../examples/generated/B-20261105-041626-d5ef52/REPORT.md)

## 六类覆盖与缺口

|方向|保留草案|当前独立初筛通过|目标缺口|
|---|---|---|---|
|F01 趋势与突破|3|0|1|
|F02 区间与均值回归|2|0|1|
|F03 横截面相对强弱|3|0|1|
|F04 状态与风险管理|3|0|1|
|F05 跨品种相对价值|3|0|1|
|F06 同品种跨期结构|1|0|1|

## 交付候选清单

每个目录含spec.json、state.json、11个public文件和内部检查/初筛证据。只有public目录可以交给策略参与者；operator材料留给出题与审核方。

|方向|候选与首轮正文|模型分类|模型审核|
|---|---|---|---|
|F01|[整理区间突破后的确认缺失处置：区间失效与突破未确认的区分、失败退出与再入场预算回压](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041643-7e6569/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041643-7e6569/operator/review.json)|
|F03|[相对强弱排名中的候选池资格回溯：流动性集合边界与留存门槛的反向协商](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041728-6227b7/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041728-6227b7/operator/review.json)|
|F04|[风险预算表达与恢复执行链：暴露刻度选择、分步减仓可行性与逐级恢复准入](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041755-b4949b/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041755-b4949b/operator/review.json)|
|F05|[跨品种加工链条的关系重估与残差腿组合：月份选择、对冲比例、分项信号汇总与保证金预算的联合维护](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041820-c67182/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041820-c67182/operator/review.json)|
|F06|[同品种多月份期限曲线的斜率变化与动态配对选择：可交易合约对集合、整数手数与到期迁移的联合维护](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041844-918b61/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041844-918b61/operator/review.json)|
|F01|[期货趋势回撤中的资格复核、回撤定位与分批库存连续维护](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041906-253472/public/TASK.md)|变体|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041906-253472/operator/review.json)|
|F02|[区间边缘放量冲击的恢复路径：冲击识别、回摆与延续的区分、隔日库存处置与失效停用](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041931-972c77/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041931-972c77/operator/review.json)|
|F03|[横截面相对强弱排名中候选池资格的反向协商：流动性集合边界、留存门槛与现金预算的联合维护](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041955-cc1583/public/TASK.md)|变体|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-041955-cc1583/operator/review.json)|
|F04|[风险预算的回撤证据等级与恢复准入：预算档位下调、未成交待办清理与分步回升](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042021-e6bb50/public/TASK.md)|变体|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042021-e6bb50/operator/review.json)|
|F05|[B/M/Y加工链条的分项信号冲突裁决与真实腿库存交接](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042048-66d9a5/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042048-66d9a5/operator/review.json)|
|F01|[期货单品种趋势回撤中的资格复核与预算耦合库存：逐腿撮合下的分批建仓、资格撤回与退出联动](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042135-678759/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042135-678759/operator/review.json)|
|F02|[单品种真实分合约的放量冲击恢复路径与隔日库存：冲击定位、回摆与延续辨识、T+1库存处置及失效后停用恢复](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042202-a4ef6a/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042202-a4ef6a/operator/review.json)|
|F03|[相对强弱排名的候选池边界协商：流动性资格集合、留存门槛与换仓预算的联合维护](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042228-c84215/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042228-c84215/operator/review.json)|
|F04|[参与档位与回撤档位的双档位交接：证据分级、降档待办清理与分步回升](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042307-e14bb5/public/TASK.md)|变体|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042307-e14bb5/operator/review.json)|
|F05|[FG/SA跨品种关系重估中的残差参与与保证金预算维护](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042329-afee5f/public/TASK.md)|重复|[needs_attention](../../examples/generated/B-20261105-041626-d5ef52/candidates/TG-20261105-042329-afee5f/operator/review.json)|

## 使用与持续去重

- 打开上表首轮正文或HTML中的可展开正文查看题目；各候选都保持数据未绑定、运行未接入、资源未选择。
- 安装项目后，后续generate会校验examples/generated中的清单哈希，并将有效候选作为只读对照。无需把私有SQLite登记库打包。
- 快照不可直接批准；修订其中spec.json时，使用import-spec创建新的活动候选，重新校验、API初筛和人工确认。
- [逐题辅助审阅待办](CANDIDATE_REVIEW_NOTES.md)记录ETF接口、基础持仓来源、期货订单语义及保证金能力等问题；原模型输出未被悄悄改写。
- 当前种子/机制枚举已经拥挤。下一轮应先扩展来源卡和必需研究决策，再生成；本轮没有证明持续生产已经达到所需产量。

数据、真实账号权限、正式运行与策略成绩均未由本轮生成验收。
