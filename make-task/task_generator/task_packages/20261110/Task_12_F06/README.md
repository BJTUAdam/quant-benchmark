# 12 · 同品种双价差共享中间月份：曲线意图归属、净头寸与滚动交接

本包修订号：integrated-r2。研究规格与原包相同，未生成新题或伪造API复核/自动接纳记录。

## 给策略AI

只提供public目录中的十一文件。可以把这个目录作为工作根目录；第一轮读取TASK.md，机器入口task_input.draft.json的所有相对路径均以该JSON所在目录为准。SUBMISSION.md已内嵌完整平台SDK调用规范，DATA_API.md已内嵌查询/分页/错误处理及本题字段表，无需读取operator材料。

当前可以设计研究、编写不依赖私有时间映射的可配置实现。实际数据查询/回测仍待平台绑定；题包不是完整运行工具包，也不自动启动桥接或Docker。五份策略文件是空仓接口模板，不是研究答案。

## 给平台AI或运行维护者

读取operator/spec.json、RUN_READINESS.json、RUN_CHECKLIST.md、DATA_BINDING_PROMPT.md。binding.selection.draft.private.json只是待填选择模板，不能把null、缺少费用等未绑定状态当作可运行配置。模板不预填真实日期、分段、运行资源或期货合约月份。

本次已在独立临时项目实际导入12份规格，并在固定镜像的股票/期货合成环境运行共用模板、跨两段恢复及接口探针；没有运行12道研究实现或真实行情回测。详情见总包可用性检查报告。

完整系统已合并题包入口、SDK说明和期货合约匹配修复，无需另行应用补丁。按完整系统的开始阅读.md操作；只给策略AI时提供public目录及通用策略技能，不提供operator目录。

本包仍未完成外部API复核或系统接纳。需要正式接入时，在完整工具包目录执行：

```text
finlab task import-spec PATH_TO_THIS_PACKAGE/operator/spec.json
finlab task review --batch IMPORTED_BATCH_ID --api
```

系统依据实际复核自动尝试接纳，无需人工审批。随后完成数据冻结、delivery prepare/check与实际研发验收；不能把会话审阅改成model_api调用记录。不要把原始行情、私有映射、上游令牌或参考答案加入public。
