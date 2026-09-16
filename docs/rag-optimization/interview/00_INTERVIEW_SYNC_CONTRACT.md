# 分源面试材料同步契约

每个 Source Design 与一份 Source Interview 文档一一对应。面试文档不是设计摘要，而是
“能被追问、能被复现、能定位代码”的证据材料。

## 每源必须包含

1. 15 秒来源定位；
2. 1 分钟架构讲解；
3. 当前问题，不把计划说成已实现；
4. 三个最重要的技术决策；
5. 候选方案与未选择原因；
6. 白板顺序；
7. 关键数据结构；
8. 关键算法；
9. 评测集与消融；
10. 性能、成本和安全；
11. 两个失败案例；
12. 高频追问；
13. 简历 bullet 占位；
14. Evaluation Run/commit/code/test 证据表。

## 指标使用

所有数字分为：

- `CURRENT_MEASURED`：当前系统实测；
- `TARGET`：设计门槛；
- `RESULT_MEASURED`：优化后实测；
- `PAPER_REPORTED`：论文结果，只能用于解释选型。

简历只能使用 `CURRENT_MEASURED` 和 `RESULT_MEASURED`，并同时保存数据集版本和 Run ID。

## 更新时机

- 设计评审：填写问题、决策和目标；
- 实现 PR：填写代码入口、测试和 Feature Flag；
- 实验完成：填写 baseline/treatment 和失败切片；
- 发布：填写 SLO、安全、回滚；
- 简历发布：删除所有未完成占位项。

